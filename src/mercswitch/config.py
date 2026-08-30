from __future__ import annotations

import ipaddress
import re
from dataclasses import replace
from difflib import unified_diff

from .errors import ParseError, ValidationError
from .models import (
    CandidateConfig,
    DeviceCapabilities,
    LagConfig,
    ManagementConfig,
    PortConfig,
    SwitchState,
    VlanConfig,
)

CONFIG_HEADER = "! mercswitch-config v2"
SPEEDS = {
    "auto",
    "10-half",
    "10-full",
    "100-half",
    "100-full",
    "1000-full",
    "2500-full",
    "10000-full",
}


def format_ports(ports: tuple[int, ...] | list[int]) -> str:
    values = sorted(set(ports))
    if not values:
        return "none"
    ranges: list[str] = []
    start = previous = values[0]
    for value in values[1:]:
        if value == previous + 1:
            previous = value
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = value
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(ranges)


def parse_ports(spec: str) -> tuple[int, ...]:
    spec = spec.strip().lower()
    if spec in {"", "none"}:
        return ()
    values: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start > end:
                raise ParseError(f"invalid port range: {part}")
            values.update(range(start, end + 1))
        else:
            values.add(int(part))
    return tuple(sorted(values))


def port_vlan_memberships(
    vlans: dict[int, VlanConfig], index: int
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    tagged = tuple(sorted(vid for vid, vlan in vlans.items() if index in vlan.tagged))
    untagged = tuple(sorted(vid for vid, vlan in vlans.items() if index in vlan.untagged))
    return tagged, untagged


def set_port_vlan_memberships(
    vlans: dict[int, VlanConfig],
    index: int,
    *,
    tagged: tuple[int, ...] = (),
    untagged: tuple[int, ...] = (),
) -> None:
    referenced = set(tagged) | set(untagged)
    missing = referenced - set(vlans)
    if missing:
        raise ParseError(f"port {index} references undefined VLANs {format_ports(sorted(missing))}")
    for vid, vlan in tuple(vlans.items()):
        tagged_ports = set(vlan.tagged)
        untagged_ports = set(vlan.untagged)
        tagged_ports.discard(index)
        untagged_ports.discard(index)
        if vid in tagged:
            tagged_ports.add(index)
        if vid in untagged:
            untagged_ports.add(index)
        vlans[vid] = replace(
            vlan, tagged=tuple(sorted(tagged_ports)), untagged=tuple(sorted(untagged_ports))
        )


def render_config(state: SwitchState | CandidateConfig) -> str:
    identity = getattr(state, "identity", None)
    lines = [CONFIG_HEADER]
    if identity:
        lines.append(
            f"! device {identity.vendor} {identity.model} hw {identity.hardware} fw {identity.firmware}"
        )
    lines.extend(["", f"interface vlan {state.management.vlan}"])
    if state.management.dhcp:
        lines.append(" ip address dhcp")
    else:
        lines.append(f" ip address {state.management.address} {state.management.netmask}")
    if state.management.gateway:
        lines.append(f" ip default-gateway {state.management.gateway}")
    else:
        lines.append(" no ip default-gateway")
    lines.append(
        " management fallback-ip enable"
        if state.management.fallback_enabled
        else " management fallback-ip disable"
    )
    lines.append("!")

    for vid, vlan in sorted(state.vlans.items()):
        lines.extend(["", f"vlan {vid}"])
        lines.append(f" name {vlan.name}" if vlan.name else " no name")
        lines.append("!")

    for index, port in sorted(state.ports.items()):
        tagged, untagged = port_vlan_memberships(state.vlans, index)
        lines.extend(["", f"interface ethernet 1/0/{index}"])
        lines.append(" no shutdown" if port.enabled else " shutdown")
        lines.append(f" speed {port.speed}")
        lines.append(" flow-control" if port.flow_control else " no flow-control")
        if untagged == (port.pvid,) and not tagged:
            lines.append(" switchport mode access")
            lines.append(f" switchport access vlan {port.pvid}")
        elif untagged == (port.pvid,):
            lines.append(" switchport mode trunk")
            lines.append(f" switchport trunk native vlan {port.pvid}")
            lines.append(
                f" switchport trunk allowed vlan {format_ports((*tagged, port.pvid))}"
            )
        else:
            lines.append(" switchport mode hybrid")
            lines.append(f" switchport hybrid pvid vlan {port.pvid}")
            lines.append(f" switchport hybrid tagged vlan {format_ports(tagged)}")
            lines.append(f" switchport hybrid untagged vlan {format_ports(untagged)}")
        lines.append("!")

    for group, lag in sorted(state.lags.items()):
        lines.extend(["", f"interface port-channel {group}"])
        lines.append(f" members ports {format_ports(lag.members)}")
        lines.append("!")
    return "\n".join(lines).rstrip() + "\n"


def parse_config(text: str, *, base_hash: str = "") -> CandidateConfig:
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if first_line != CONFIG_HEADER:
        raise ParseError(f"configuration must begin with {CONFIG_HEADER}")
    management: ManagementConfig | None = None
    ports: dict[int, PortConfig] = {}
    vlans: dict[int, VlanConfig] = {}
    lags: dict[int, LagConfig] = {}
    switchports: dict[int, dict[str, object]] = {}
    context: tuple[str, int] | None = None

    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("!"):
            if line == "!":
                context = None
            continue
        match = re.fullmatch(r"interface vlan (\d+)", line)
        if match:
            vid = int(match.group(1))
            management = ManagementConfig(vlan=vid)
            context = ("management", vid)
            continue
        match = re.fullmatch(r"vlan (\d+)", line)
        if match:
            vid = int(match.group(1))
            vlans[vid] = VlanConfig(vid=vid)
            context = ("vlan", vid)
            continue
        match = re.fullmatch(r"interface ethernet 1/0/(\d+)", line)
        if match:
            index = int(match.group(1))
            ports[index] = PortConfig(index=index)
            switchports[index] = {}
            context = ("port", index)
            continue
        match = re.fullmatch(r"interface port-channel (\d+)", line)
        if match:
            group = int(match.group(1))
            lags[group] = LagConfig(group=group)
            context = ("lag", group)
            continue
        if context is None:
            raise ParseError(f"line {number}: command outside a section: {line}")

        kind, ident = context
        try:
            if kind == "management":
                assert management is not None
                if line == "ip address dhcp":
                    management = replace(management, dhcp=True, address="", netmask="")
                elif line.startswith("ip address "):
                    _, _, address, netmask = line.split()
                    management = replace(management, dhcp=False, address=address, netmask=netmask)
                elif line.startswith("ip default-gateway "):
                    management = replace(management, gateway=line.split(maxsplit=2)[2])
                elif line == "no ip default-gateway":
                    management = replace(management, gateway="")
                elif line in {"management fallback-ip enable", "management fallback-ip disable"}:
                    management = replace(management, fallback_enabled=line.endswith("enable"))
                else:
                    raise ParseError(f"line {number}: unsupported management command: {line}")
            elif kind == "vlan":
                vlan = vlans[ident]
                if line.startswith("name "):
                    vlans[ident] = replace(vlan, name=line[5:])
                elif line == "no name":
                    vlans[ident] = replace(vlan, name="")
                else:
                    raise ParseError(f"line {number}: unsupported VLAN command: {line}")
            elif kind == "port":
                port = ports[ident]
                if line in {"shutdown", "no shutdown"}:
                    ports[ident] = replace(port, enabled=line == "no shutdown")
                elif line.startswith("speed "):
                    speed = line.split(maxsplit=1)[1]
                    if speed not in SPEEDS:
                        raise ParseError(f"line {number}: unsupported speed: {speed}")
                    ports[ident] = replace(port, speed=speed)  # type: ignore[arg-type]
                elif line in {"flow-control", "no flow-control"}:
                    ports[ident] = replace(port, flow_control=line == "flow-control")
                elif line.startswith("switchport mode "):
                    mode = line.rsplit(" ", 1)[1]
                    if mode not in {"access", "trunk", "hybrid"}:
                        raise ParseError(f"line {number}: unsupported switchport mode: {mode}")
                    switchports[ident]["mode"] = mode
                elif line.startswith("switchport access vlan "):
                    switchports[ident]["access"] = int(line.rsplit(" ", 1)[1])
                elif line.startswith("switchport trunk native vlan "):
                    switchports[ident]["native"] = int(line.rsplit(" ", 1)[1])
                elif line.startswith("switchport trunk allowed vlan "):
                    switchports[ident]["allowed"] = parse_ports(line[30:])
                elif line.startswith("switchport hybrid pvid vlan "):
                    switchports[ident]["pvid"] = int(line.rsplit(" ", 1)[1])
                elif line.startswith("switchport hybrid tagged vlan "):
                    switchports[ident]["tagged"] = parse_ports(line[30:])
                elif line.startswith("switchport hybrid untagged vlan "):
                    switchports[ident]["untagged"] = parse_ports(line[32:])
                else:
                    raise ParseError(f"line {number}: unsupported port command: {line}")
            elif kind == "lag":
                if line.startswith("members ports "):
                    lags[ident] = replace(lags[ident], members=parse_ports(line[14:]))
                else:
                    raise ParseError(f"line {number}: unsupported LAG command: {line}")
        except ValueError as exc:
            raise ParseError(f"line {number}: {exc}") from exc

    if management is None:
        raise ParseError("missing management interface")
    for index, settings in switchports.items():
        mode = settings.get("mode")
        if mode is None:
            raise ParseError(f"port {index} is missing switchport mode")
        if mode == "access":
            access = int(settings.get("access", 1))
            ports[index] = replace(ports[index], pvid=access)
            set_port_vlan_memberships(vlans, index, untagged=(access,))
        elif mode == "trunk":
            native = int(settings.get("native", 1))
            allowed = tuple(settings.get("allowed", tuple(sorted(vlans))))
            if native not in allowed:
                raise ParseError(f"port {index} trunk native VLAN must be in allowed VLANs")
            ports[index] = replace(ports[index], pvid=native)
            set_port_vlan_memberships(
                vlans,
                index,
                tagged=tuple(vid for vid in allowed if vid != native),
                untagged=(native,),
            )
        else:
            pvid = int(settings.get("pvid", 1))
            tagged = tuple(settings.get("tagged", ()))
            untagged = tuple(settings.get("untagged", (pvid,)))
            ports[index] = replace(ports[index], pvid=pvid)
            set_port_vlan_memberships(vlans, index, tagged=tagged, untagged=untagged)
    return CandidateConfig(management, ports, vlans, lags, True, base_hash)


def validate_candidate(candidate: CandidateConfig, capabilities: DeviceCapabilities) -> None:
    errors: list[str] = []
    if not candidate.dot1q_enabled:
        errors.append("802.1Q VLAN must be enabled")
    if 1 not in candidate.vlans:
        errors.append("VLAN 1 is required")
    if not 1 <= candidate.management.vlan <= 4094:
        errors.append("management VLAN must be 1-4094")
    if candidate.management.vlan not in candidate.vlans:
        errors.append("management VLAN must exist")
    if not candidate.management.dhcp:
        try:
            ipaddress.IPv4Address(candidate.management.address)
            ipaddress.IPv4Address(candidate.management.netmask)
        except ipaddress.AddressValueError:
            errors.append("invalid static management address or netmask")
    if candidate.management.gateway:
        try:
            ipaddress.IPv4Address(candidate.management.gateway)
        except ipaddress.AddressValueError:
            errors.append("invalid default gateway")

    expected_ports = {port.index for port in capabilities.ports}
    actual_ports = set(candidate.ports)
    if actual_ports != expected_ports:
        errors.append(f"configuration must contain ports {format_ports(sorted(expected_ports))}")
    if len(candidate.vlans) > capabilities.max_vlans:
        errors.append(f"device supports at most {capabilities.max_vlans} VLANs")
    for vid, vlan in candidate.vlans.items():
        if not 1 <= vid <= 4094:
            errors.append(f"VLAN {vid} is outside 1-4094")
        if len(vlan.name.encode("utf-8")) > 12:
            errors.append(f"VLAN {vid} name exceeds 12 UTF-8 bytes")
        members = set(vlan.tagged) | set(vlan.untagged)
        if set(vlan.tagged) & set(vlan.untagged):
            errors.append(f"VLAN {vid} has ports both tagged and untagged")
        unknown = members - expected_ports
        if unknown:
            errors.append(
                f"VLAN {vid} references unavailable ports {format_ports(sorted(unknown))}"
            )
    for index, port in candidate.ports.items():
        capability = next((item for item in capabilities.ports if item.index == index), None)
        if capability and port.speed not in capability.speeds:
            errors.append(f"port {index} does not support speed {port.speed}")
        if port.pvid not in candidate.vlans:
            errors.append(f"port {index} PVID {port.pvid} does not exist")
        elif index not in candidate.vlans[port.pvid].untagged:
            errors.append(f"port {index} must be untagged in its PVID {port.pvid}")
    used_ports: set[int] = set()
    for group, lag in candidate.lags.items():
        allowed = set(capabilities.lag_members.get(group, ()))
        members = set(lag.members)
        if group not in capabilities.lag_members:
            errors.append(f"LAG {group} is not supported")
        if not capabilities.lag_min_members <= len(members) <= capabilities.lag_max_members:
            errors.append(
                f"LAG {group} requires {capabilities.lag_min_members}-"
                f"{capabilities.lag_max_members} members"
            )
        if members - allowed:
            errors.append(
                f"LAG {group} contains ineligible ports {format_ports(sorted(members - allowed))}"
            )
        if used_ports & members:
            errors.append(
                f"ports {format_ports(sorted(used_ports & members))} occur in multiple LAGs"
            )
        used_ports |= members
    if errors:
        raise ValidationError("\n".join(errors))


def config_diff(before: SwitchState | CandidateConfig, after: SwitchState | CandidateConfig) -> str:
    return "".join(
        unified_diff(
            render_config(before).splitlines(keepends=True),
            render_config(after).splitlines(keepends=True),
            fromfile="running-config",
            tofile="candidate-config",
        )
    )
