from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class DeviceSettings:
    url: str
    username: str = "admin"
    password_file: str = "/run/secrets/switch_password"
    verify_tls: bool = False

    def password(self) -> str:
        return Path(self.password_file).read_text().rstrip("\r\n")


@dataclass(slots=True)
class SshUserSettings:
    role: str = "viewer"
    password_hash: str = ""
    authorized_keys_file: str = ""


@dataclass(slots=True)
class SshSettings:
    host: str = "0.0.0.0"
    port: int = 2222
    host_key: str = "/var/lib/mercswitch/ssh_host_ed25519_key"
    users: dict[str, SshUserSettings] = field(default_factory=dict)


@dataclass(slots=True)
class SnmpSettings:
    host: str = "0.0.0.0"
    port: int = 1161
    community_file: str = "/run/secrets/snmp_community"
    name: str = "mercswitch"
    contact: str = ""
    location: str = ""

    def community(self) -> str:
        return Path(self.community_file).read_text().rstrip("\r\n")


@dataclass(slots=True)
class PollSettings:
    summary_interval: float = 15.0
    detail_interval: float = 10.0


@dataclass(slots=True)
class DaemonSettings:
    device: DeviceSettings
    ssh: SshSettings = field(default_factory=SshSettings)
    snmp: SnmpSettings = field(default_factory=SnmpSettings)
    poll: PollSettings = field(default_factory=PollSettings)
    data_dir: str = "/var/lib/mercswitch"

    @classmethod
    def load(cls, path: Path | str) -> DaemonSettings:
        data = tomllib.loads(Path(path).read_text())
        ssh_data = dict(data.get("ssh", {}))
        users = {
            name: SshUserSettings(**settings)
            for name, settings in ssh_data.pop("users", {}).items()
        }
        return cls(
            device=DeviceSettings(**data["device"]),
            ssh=SshSettings(users=users, **ssh_data),
            snmp=SnmpSettings(**data.get("snmp", {})),
            poll=PollSettings(**data.get("poll", {})),
            data_dir=data.get("data_dir", "/var/lib/mercswitch"),
        )
