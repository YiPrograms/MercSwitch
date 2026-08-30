from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

Speed = Literal[
    "auto",
    "10-half",
    "10-full",
    "100-half",
    "100-full",
    "1000-full",
    "2500-full",
    "10000-full",
]


@dataclass(slots=True)
class DeviceIdentity:
    vendor: str = "unknown"
    model: str = "unknown"
    hardware: str = "unknown"
    firmware: str = "unknown"
    mac: str = ""
    description: str = ""
    adapter: str = "rpm-cgi-v1"


@dataclass(slots=True)
class PortCapability:
    index: int
    media: Literal["copper", "sfp", "combo", "unknown"] = "unknown"
    speeds: tuple[Speed, ...] = ("auto",)
    supports_half_duplex: bool = False
    poe_capable: bool = False


@dataclass(slots=True)
class DeviceCapabilities:
    ports: tuple[PortCapability, ...] = ()
    max_vlans: int = 32
    lag_members: dict[int, tuple[int, ...]] = field(default_factory=dict)
    lag_min_members: int = 2
    lag_max_members: int = 4
    supports_fallback_ip: bool = False
    poe_capable: bool = False
    schema_writable: bool = False

    @property
    def port_count(self) -> int:
        return len(self.ports)


@dataclass(slots=True)
class ManagementConfig:
    vlan: int = 1
    dhcp: bool = False
    address: str = ""
    netmask: str = ""
    gateway: str = ""
    fallback_enabled: bool = False
    fallback_address: str = ""


@dataclass(slots=True)
class PortConfig:
    index: int
    enabled: bool = True
    speed: Speed = "auto"
    flow_control: bool = False
    pvid: int = 1
    link_up: bool = False
    actual_speed: str = "down"


@dataclass(slots=True)
class VlanConfig:
    vid: int
    name: str = ""
    tagged: tuple[int, ...] = ()
    untagged: tuple[int, ...] = ()


@dataclass(slots=True)
class LagConfig:
    group: int
    members: tuple[int, ...] = ()


@dataclass(slots=True)
class SwitchState:
    identity: DeviceIdentity
    capabilities: DeviceCapabilities
    management: ManagementConfig
    ports: dict[int, PortConfig]
    vlans: dict[int, VlanConfig]
    lags: dict[int, LagConfig]
    dot1q_enabled: bool = True
    observed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def managed_dict(self) -> dict[str, Any]:
        management = asdict(self.management)
        # The RPM/CGI schema exposes the fallback address but only permits
        # toggling the feature. Treat the address as observed, unmanaged state.
        management.pop("fallback_address", None)
        return {
            "management": management,
            "ports": {
                str(k): asdict(v) | {"link_up": False, "actual_speed": ""}
                for k, v in sorted(self.ports.items())
            },
            "vlans": {str(k): asdict(v) for k, v in sorted(self.vlans.items())},
            "lags": {str(k): asdict(v) for k, v in sorted(self.lags.items())},
            "dot1q_enabled": self.dot1q_enabled,
        }

    def managed_hash(self) -> str:
        payload = json.dumps(self.managed_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(slots=True)
class CandidateConfig:
    management: ManagementConfig
    ports: dict[int, PortConfig]
    vlans: dict[int, VlanConfig]
    lags: dict[int, LagConfig]
    dot1q_enabled: bool = True
    base_hash: str = ""

    @classmethod
    def from_state(cls, state: SwitchState) -> CandidateConfig:
        return cls(
            management=ManagementConfig(**asdict(state.management)),
            ports={k: PortConfig(**asdict(v)) for k, v in state.ports.items()},
            vlans={k: VlanConfig(**asdict(v)) for k, v in state.vlans.items()},
            lags={k: LagConfig(**asdict(v)) for k, v in state.lags.items()},
            dot1q_enabled=state.dot1q_enabled,
            base_hash=state.managed_hash(),
        )


@dataclass(slots=True)
class Change:
    phase: int
    action: str
    target: str
    before: Any = None
    after: Any = None
    management_disruptive: bool = False


@dataclass(slots=True)
class ChangePlan:
    base_hash: str
    target_hash: str
    changes: list[Change]
    management_change: bool = False

    @property
    def empty(self) -> bool:
        return not self.changes


@dataclass(slots=True)
class OperationResult:
    ok: bool
    message: str
    plan: ChangePlan | None = None
    final_state: SwitchState | None = None
    backup_path: str | None = None
    journal_path: str | None = None


@dataclass(slots=True)
class PortMetrics:
    index: int
    admin_up: bool
    link_up: bool
    speed_mbps: int
    tx_good: int = 0
    tx_bad: int = 0
    rx_good: int = 0
    rx_bad: int = 0
    rx_unicast: int = 0
    rx_multicast: int = 0
    rx_broadcast: int = 0
    tx_unicast: int = 0
    tx_multicast: int = 0
    tx_broadcast: int = 0
    undersize: int = 0
    oversize: int = 0
    crc_errors: int = 0
    fragments: int = 0
    jabbers: int = 0
    collisions: int = 0
    detail_available: bool = False


@dataclass(slots=True)
class SwitchMetrics:
    identity: DeviceIdentity
    ports: dict[int, PortMetrics]
    observed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


def state_to_dict(state: SwitchState) -> dict[str, Any]:
    return asdict(state)


def state_from_dict(data: dict[str, Any]) -> SwitchState:
    caps_data = data["capabilities"]
    caps = DeviceCapabilities(
        ports=tuple(
            PortCapability(**(port | {"speeds": tuple(port.get("speeds", ("auto",)))}))
            for port in caps_data.get("ports", [])
        ),
        max_vlans=caps_data.get("max_vlans", 32),
        lag_members={int(k): tuple(v) for k, v in caps_data.get("lag_members", {}).items()},
        lag_min_members=caps_data.get("lag_min_members", 2),
        lag_max_members=caps_data.get("lag_max_members", 4),
        supports_fallback_ip=caps_data.get("supports_fallback_ip", False),
        poe_capable=caps_data.get("poe_capable", False),
        schema_writable=caps_data.get("schema_writable", False),
    )
    return SwitchState(
        identity=DeviceIdentity(**data["identity"]),
        capabilities=caps,
        management=ManagementConfig(**data["management"]),
        ports={int(k): PortConfig(**v) for k, v in data["ports"].items()},
        vlans={
            int(k): VlanConfig(
                **(
                    v
                    | {
                        "tagged": tuple(v.get("tagged", ())),
                        "untagged": tuple(v.get("untagged", ())),
                    }
                )
            )
            for k, v in data["vlans"].items()
        },
        lags={
            int(k): LagConfig(**(v | {"members": tuple(v.get("members", ()))}))
            for k, v in data["lags"].items()
        },
        dot1q_enabled=data.get("dot1q_enabled", True),
        observed_at=data.get("observed_at", datetime.now(UTC).isoformat()),
    )
