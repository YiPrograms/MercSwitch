from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Literal

from .client import MercSwitchClient
from .config import config_diff, parse_config, parse_ports, render_config
from .errors import MercSwitchError
from .models import CandidateConfig, LagConfig, PortConfig, SwitchState, VlanConfig

Role = Literal["viewer", "admin"]


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
        if line in {"exit", "logout", "quit"}:
            self.closed = True
            return ""
        if line in {"help", "?"}:
            return (
                "show running-config|candidate-config|diff|status\n"
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
