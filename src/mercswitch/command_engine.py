from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Literal

from .client import MercSwitchClient
from .config import (
    config_diff,
    format_ports,
    parse_config,
    parse_ports,
    port_vlan_memberships,
    render_config,
    set_port_vlan_memberships,
)
from .errors import MercSwitchError
from .models import CandidateConfig, LagConfig, PortConfig, SwitchState, VlanConfig

Role = Literal["viewer", "admin"]


def _resolve_keyword(word: str, choices: tuple[str, ...]) -> str:
    lowered = word.lower()
    if lowered in choices:
        return lowered
    matches = tuple(choice for choice in choices if choice.startswith(lowered))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise MercSwitchError(
            f"ambiguous command '{word}': could be {', '.join(matches)}"
        )
    raise MercSwitchError(f"unknown command keyword: {word}")


def normalize_exec_command(command: str) -> str:
    """Expand unambiguous IOS-style command abbreviations."""
    words = command.strip().split()
    if not words or words == ["?"]:
        return command.strip()
    root = _resolve_keyword(
        words[0],
        ("show", "configure", "commit", "abort", "write", "exit", "logout", "quit", "help"),
    )
    if root == "show":
        if len(words) < 2:
            raise MercSwitchError(
                "usage: show running-config|candidate-config|diff|status|interfaces|vlan|"
                "port-channel|ip|version|capabilities"
            )
        subcommand = _resolve_keyword(
            words[1],
            (
                "running-config",
                "candidate-config",
                "diff",
                "status",
                "interfaces",
                "vlan",
                "port-channel",
                "ip",
                "version",
                "capabilities",
            ),
        )
        rest = words[2:]
        if subcommand == "interfaces":
            if not rest:
                return "show interfaces"
            if rest[0].lower() in {"status", "summary"} or "status".startswith(
                rest[0].lower()
            ):
                if len(rest) != 1:
                    raise MercSwitchError("usage: show interfaces status")
                return "show interfaces status"
            if rest[0].lower() == "ethernet" or "ethernet".startswith(rest[0].lower()):
                rest = rest[1:]
            if len(rest) != 1:
                raise MercSwitchError("usage: show interfaces [ethernet] 1/0/PORT")
            return f"show interfaces ethernet {rest[0]}"
        if subcommand == "vlan":
            if not rest:
                return "show vlan brief"
            qualifier = _resolve_keyword(rest[0], ("brief", "id"))
            if qualifier == "brief" and len(rest) == 1:
                return "show vlan brief"
            if qualifier == "id" and len(rest) == 2:
                return f"show vlan id {rest[1]}"
            raise MercSwitchError("usage: show vlan [brief|id VLAN]")
        if subcommand == "ip":
            if len(rest) == 1 and _resolve_keyword(rest[0], ("interface",)) == "interface":
                return "show ip interface"
            raise MercSwitchError("usage: show ip interface")
        if rest:
            raise MercSwitchError(f"unexpected arguments after show {subcommand}")
        return f"show {subcommand}"
    if root == "configure":
        if len(words) < 2:
            raise MercSwitchError("usage: configure terminal|replace")
        subcommand = _resolve_keyword(words[1], ("terminal", "replace"))
        if subcommand == "terminal":
            if len(words) != 2:
                raise MercSwitchError("usage: configure terminal")
            return "configure terminal"
        if len(words) < 3:
            raise MercSwitchError("usage: configure replace <file>")
        return f"configure replace {' '.join(words[2:])}"
    if root == "commit":
        options = tuple(
            _resolve_keyword(word, ("check", "force", "allow-management-change"))
            for word in words[1:]
        )
        return " ".join(("commit", *options))
    if root == "write":
        if len(words) != 2 or _resolve_keyword(words[1], ("memory",)) != "memory":
            raise MercSwitchError("usage: write memory")
        return "write memory"
    if len(words) != 1:
        raise MercSwitchError(f"unexpected arguments after {root}")
    return root


class CommandSession:
    def __init__(
        self,
        client: MercSwitchClient,
        running: SwitchState,
        *,
        role: Role = "admin",
        allow_local_files: bool = False,
    ) -> None:
        self.client = client
        self.running = running
        self.candidate = CandidateConfig.from_state(running)
        self.role = role
        self.allow_local_files = allow_local_files
        self.config_mode = False
        self.context: tuple[str, int] | None = None
        self.closed = False

    @property
    def prompt(self) -> str:
        model = self.running.identity.model.replace(" ", "-") or "mercswitch"
        return f"{model}{'(config)' if self.config_mode else ''}# "

    def load_candidate(self, text: str) -> None:
        self.candidate = parse_config(text, base_hash=self.running.managed_hash())

    def _require_admin(self) -> None:
        if self.role != "admin":
            raise MercSwitchError("this command requires the admin role")

    def _switchport_description(self, index: int) -> tuple[str, str, str]:
        port = self.running.ports[index]
        tagged, untagged = port_vlan_memberships(self.running.vlans, index)
        if untagged == (port.pvid,) and not tagged:
            return "access", str(port.pvid), str(port.pvid)
        if untagged == (port.pvid,):
            allowed = format_ports((*tagged, port.pvid))
            return "trunk", str(port.pvid), allowed
        return "hybrid", str(port.pvid), format_ports((*tagged, *untagged))

    def _show_interfaces(self) -> str:
        lines = ["Port    Admin Link Configured Actual      Flow PVID Mode   VLANs"]
        for index, port in sorted(self.running.ports.items()):
            mode, pvid, vlans = self._switchport_description(index)
            lines.append(
                f"1/0/{index:<3} {'up' if port.enabled else 'down':<5} "
                f"{'up' if port.link_up else 'down':<4} {port.speed:<10} "
                f"{port.actual_speed:<11} {'on' if port.flow_control else 'off':<4} "
                f"{pvid:<4} {mode:<6} {vlans}"
            )
        return "\n".join(lines) + "\n"

    def _show_interface(self, name: str) -> str:
        match = re.fullmatch(r"(?:ethernet\s+)?1/0/(\d+)", name, re.IGNORECASE)
        if not match:
            raise MercSwitchError("interface must be ethernet 1/0/PORT")
        index = int(match.group(1))
        port = self.running.ports.get(index)
        if port is None:
            raise MercSwitchError(f"interface ethernet 1/0/{index} does not exist")
        capability = next(
            (item for item in self.running.capabilities.ports if item.index == index), None
        )
        mode, pvid, vlans = self._switchport_description(index)
        return (
            f"Ethernet 1/0/{index}\n"
            f"  admin state: {'up' if port.enabled else 'down'}\n"
            f"  link state: {'up' if port.link_up else 'down'}\n"
            f"  media: {capability.media if capability else 'unknown'}\n"
            f"  configured speed: {port.speed}\n"
            f"  operational speed: {port.actual_speed}\n"
            f"  flow control: {'enabled' if port.flow_control else 'disabled'}\n"
            f"  switchport mode: {mode}\n"
            f"  PVID/native VLAN: {pvid}\n"
            f"  VLANs: {vlans}\n"
        )

    def _show_vlans(self, vid: int | None = None) -> str:
        if vid is not None:
            vlan = self.running.vlans.get(vid)
            if vlan is None:
                raise MercSwitchError(f"VLAN {vid} does not exist")
            vlans = ((vid, vlan),)
        else:
            vlans = tuple(sorted(self.running.vlans.items()))
        lines = ["VLAN Name         Tagged     Untagged"]
        for vlan_id, vlan in vlans:
            lines.append(
                f"{vlan_id:<4} {vlan.name[:12]:<12} "
                f"{format_ports(vlan.tagged):<10} {format_ports(vlan.untagged)}"
            )
        return "\n".join(lines) + "\n"

    def _configure(self, line: str) -> str:
        if line in {"end", "exit"}:
            if self.context is not None:
                self.context = None
            else:
                self.config_mode = False
            return ""
        if line == "abort":
            self.candidate = CandidateConfig.from_state(self.running)
            self.context = None
            self.config_mode = False
            return "candidate discarded"
        if line.startswith("interface vlan "):
            vid = int(line.rsplit(" ", 1)[1])
            self.candidate.management = replace(self.candidate.management, vlan=vid)
            self.context = ("management", vid)
            return ""
        if line.startswith("vlan "):
            vid = int(line.split()[1])
            self.candidate.vlans.setdefault(vid, VlanConfig(vid))
            self.context = ("vlan", vid)
            return ""
        if line.startswith("no vlan "):
            self.candidate.vlans.pop(int(line.split()[2]), None)
            return ""
        if line.startswith("interface ethernet 1/0/"):
            index = int(line.rsplit("/", 1)[1])
            self.candidate.ports.setdefault(index, PortConfig(index))
            self.context = ("port", index)
            return ""
        if line.startswith("interface port-channel "):
            group = int(line.rsplit(" ", 1)[1])
            self.candidate.lags.setdefault(group, LagConfig(group))
            self.context = ("lag", group)
            return ""
        if line.startswith("no interface port-channel "):
            self.candidate.lags.pop(int(line.rsplit(" ", 1)[1]), None)
            return ""
        if self.context is None:
            raise MercSwitchError("enter an interface or VLAN context first")
        kind, ident = self.context
        if kind == "management":
            management = self.candidate.management
            if line == "ip address dhcp":
                self.candidate.management = replace(management, dhcp=True, address="", netmask="")
            elif line.startswith("ip address "):
                _, _, address, netmask = line.split()
                self.candidate.management = replace(
                    management, dhcp=False, address=address, netmask=netmask
                )
            elif line.startswith("ip default-gateway "):
                self.candidate.management = replace(management, gateway=line.split(maxsplit=2)[2])
            elif line == "no ip default-gateway":
                self.candidate.management = replace(management, gateway="")
            elif line in {"management fallback-ip enable", "management fallback-ip disable"}:
                self.candidate.management = replace(
                    management, fallback_enabled=line.endswith("enable")
                )
            else:
                raise MercSwitchError(f"unsupported management command: {line}")
        elif kind == "vlan":
            vlan = self.candidate.vlans[ident]
            if line.startswith("name "):
                self.candidate.vlans[ident] = replace(vlan, name=line[5:])
            elif line == "no name":
                self.candidate.vlans[ident] = replace(vlan, name="")
            elif line.startswith("tagged ports "):
                self.candidate.vlans[ident] = replace(vlan, tagged=parse_ports(line[13:]))
            elif line.startswith("untagged ports "):
                self.candidate.vlans[ident] = replace(vlan, untagged=parse_ports(line[15:]))
            else:
                raise MercSwitchError(f"unsupported VLAN command: {line}")
        elif kind == "port":
            port = self.candidate.ports[ident]
            if line in {"shutdown", "no shutdown"}:
                self.candidate.ports[ident] = replace(port, enabled=line == "no shutdown")
            elif line.startswith("speed "):
                self.candidate.ports[ident] = replace(port, speed=line.split()[1])  # type: ignore[arg-type]
            elif line in {"flow-control", "no flow-control"}:
                self.candidate.ports[ident] = replace(port, flow_control=line == "flow-control")
            elif line.startswith("switchport pvid "):
                self.candidate.ports[ident] = replace(port, pvid=int(line.split()[2]))
            elif line == "switchport mode access":
                set_port_vlan_memberships(
                    self.candidate.vlans, ident, untagged=(port.pvid,)
                )
            elif line in {"switchport mode trunk", "switchport mode hybrid"}:
                pass
            elif line.startswith("switchport access vlan "):
                vid = int(line.rsplit(" ", 1)[1])
                self.candidate.ports[ident] = replace(port, pvid=vid)
                set_port_vlan_memberships(self.candidate.vlans, ident, untagged=(vid,))
            elif line.startswith("switchport trunk native vlan "):
                native = int(line.rsplit(" ", 1)[1])
                tagged, untagged = port_vlan_memberships(self.candidate.vlans, ident)
                allowed = tuple(sorted(set(tagged) | set(untagged) | {native}))
                self.candidate.ports[ident] = replace(port, pvid=native)
                set_port_vlan_memberships(
                    self.candidate.vlans,
                    ident,
                    tagged=tuple(vid for vid in allowed if vid != native),
                    untagged=(native,),
                )
            elif line.startswith("switchport trunk allowed vlan "):
                allowed = parse_ports(line[30:])
                if port.pvid not in allowed:
                    raise MercSwitchError("trunk native VLAN must be in allowed VLANs")
                set_port_vlan_memberships(
                    self.candidate.vlans,
                    ident,
                    tagged=tuple(vid for vid in allowed if vid != port.pvid),
                    untagged=(port.pvid,),
                )
            elif line.startswith("switchport hybrid pvid vlan "):
                pvid = int(line.rsplit(" ", 1)[1])
                tagged, untagged = port_vlan_memberships(self.candidate.vlans, ident)
                self.candidate.ports[ident] = replace(port, pvid=pvid)
                set_port_vlan_memberships(
                    self.candidate.vlans,
                    ident,
                    tagged=tuple(vid for vid in tagged if vid != pvid),
                    untagged=tuple(sorted(set(untagged) | {pvid})),
                )
            elif line.startswith("switchport hybrid tagged vlan "):
                tagged = parse_ports(line[30:])
                _, untagged = port_vlan_memberships(self.candidate.vlans, ident)
                set_port_vlan_memberships(
                    self.candidate.vlans, ident, tagged=tagged, untagged=untagged
                )
            elif line.startswith("switchport hybrid untagged vlan "):
                untagged = parse_ports(line[32:])
                tagged, _ = port_vlan_memberships(self.candidate.vlans, ident)
                set_port_vlan_memberships(
                    self.candidate.vlans, ident, tagged=tagged, untagged=untagged
                )
            else:
                raise MercSwitchError(f"unsupported port command: {line}")
        elif kind == "lag":
            if not line.startswith("members ports "):
                raise MercSwitchError(f"unsupported port-channel command: {line}")
            self.candidate.lags[ident] = replace(
                self.candidate.lags[ident], members=parse_ports(line[14:])
            )
        return ""

    async def execute(self, command: str) -> str:
        line = command.strip()
        if not line:
            return ""
        if self.config_mode:
            self._require_admin()
            return self._configure(line)
        line = normalize_exec_command(line)
        if line in {"exit", "logout", "quit"}:
            self.closed = True
            return ""
        if line in {"help", "?"}:
            return (
                "show running-config|candidate-config|diff|status\n"
                "show interfaces [status|ethernet 1/0/PORT]\n"
                "show vlan [brief|id VLAN]\n"
                "show port-channel|ip interface|version|capabilities\n"
                "configure terminal\nconfigure replace <file>\n"
                "commit [check] [force] [allow-management-change]\nabort\nwrite memory\nexit"
            )
        if line == "show running-config":
            return render_config(self.running)
        if line == "show candidate-config":
            return render_config(self.candidate)
        if line == "show diff":
            return config_diff(self.running, self.candidate) or "no changes\n"
        if line == "show status":
            identity = self.running.identity
            return (
                f"{identity.vendor} {identity.model} hw {identity.hardware} fw {identity.firmware}\n"
                f"ports {self.running.capabilities.port_count}; state {self.running.managed_hash()}\n"
            )
        if line in {"show interfaces", "show interfaces status"}:
            return self._show_interfaces()
        if line.startswith("show interfaces ethernet "):
            return self._show_interface(line.split(maxsplit=3)[3])
        if line == "show vlan brief":
            return self._show_vlans()
        if line.startswith("show vlan id "):
            return self._show_vlans(int(line.rsplit(" ", 1)[1]))
        if line == "show port-channel":
            lines = ["Group Members"]
            lines.extend(
                f"{group:<5} {format_ports(lag.members)}"
                for group, lag in sorted(self.running.lags.items())
            )
            return "\n".join(lines) + "\n"
        if line == "show ip interface":
            management = self.running.management
            source = "DHCP" if management.dhcp else "static"
            return (
                f"Vlan{management.vlan}\n"
                f"  address source: {source}\n"
                f"  IP address: {management.address or 'unassigned'}\n"
                f"  netmask: {management.netmask or 'unassigned'}\n"
                f"  default gateway: {management.gateway or 'none'}\n"
                f"  fallback IP: {'enabled' if management.fallback_enabled else 'disabled'}\n"
            )
        if line == "show version":
            identity = self.running.identity
            return (
                f"MercSwitch adapter {identity.adapter}\n"
                f"{identity.vendor} {identity.model}\n"
                f"Hardware: {identity.hardware}\nFirmware: {identity.firmware}\n"
                f"MAC address: {identity.mac or 'unknown'}\n"
            )
        if line == "show capabilities":
            capabilities = self.running.capabilities
            media = ", ".join(
                f"1/0/{port.index}:{port.media}" for port in capabilities.ports
            )
            return (
                f"ports: {capabilities.port_count}\n"
                f"media: {media}\n"
                f"maximum VLANs: {capabilities.max_vlans}\n"
                f"LAG groups: {format_ports(sorted(capabilities.lag_members))}\n"
                f"fallback IP: {'supported' if capabilities.supports_fallback_ip else 'unsupported'}\n"
                f"PoE detected: {'yes' if capabilities.poe_capable else 'no'}\n"
            )
        if line == "configure terminal":
            self._require_admin()
            self.config_mode = True
            return ""
        if line.startswith("configure replace "):
            self._require_admin()
            if not self.allow_local_files:
                raise MercSwitchError(
                    "remote file access is disabled; use configure replace terminal or stdin"
                )
            path = Path(line.split(maxsplit=2)[2])
            self.load_candidate(path.read_text())
            return f"loaded candidate from {path}"
        if line == "abort":
            self._require_admin()
            self.candidate = CandidateConfig.from_state(self.running)
            return "candidate discarded"
        if line.startswith("commit"):
            self._require_admin()
            words = set(line.split()[1:])
            result = await self.client.commit(
                self.candidate,
                check="check" in words,
                force="force" in words,
                allow_management_change="allow-management-change" in words,
            )
            if result.final_state and not ("check" in words):
                self.running = result.final_state
                self.candidate = CandidateConfig.from_state(self.running)
            return result.message
        if line == "write memory":
            self._require_admin()
            await self.client.write_memory()
            return "configuration saved"
        raise MercSwitchError(f"unknown command: {line}")
