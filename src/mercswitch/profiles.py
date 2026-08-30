from __future__ import annotations

import getpass
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .errors import ValidationError


@dataclass(slots=True)
class DeviceProfile:
    name: str
    url: str
    username: str = "admin"
    password_env: str = "MERCSWITCH_PASSWORD"
    password_file: str = ""
    keyring_service: str = ""
    verify_tls: bool = False

    def password(self, *, allow_prompt: bool = True) -> str:
        if self.password_file:
            return Path(self.password_file).read_text().rstrip("\r\n")
        if self.password_env and os.environ.get(self.password_env):
            return os.environ[self.password_env]
        if self.keyring_service:
            try:
                import keyring

                value = keyring.get_password(self.keyring_service, self.username)
                if value:
                    return value
            except ImportError:
                pass
        if allow_prompt:
            return getpass.getpass(f"Password for {self.username}@{self.url}: ")
        raise ValidationError("no switch password source is configured")


def default_profile_path() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "mercswitch" / "config.toml"


def load_profile(name: str = "default", path: Path | None = None) -> DeviceProfile:
    path = path or default_profile_path()
    if not path.exists():
        raise ValidationError(f"profile file does not exist: {path}")
    data = tomllib.loads(path.read_text())
    try:
        profile = data["profiles"][name]
    except KeyError as exc:
        raise ValidationError(f"profile {name!r} is not defined in {path}") from exc
    return DeviceProfile(name=name, **profile)
