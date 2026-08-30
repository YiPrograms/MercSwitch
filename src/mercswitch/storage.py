from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import render_config
from .models import SwitchState, state_from_dict, state_to_dict


def default_cache_root() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "mercswitch"


def jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    return value


class CacheStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.backups = self.root / "backups"
        self.journals = self.root / "journals"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.backups.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.journals.mkdir(parents=True, exist_ok=True, mode=0o700)

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def save_state(self, state: SwitchState) -> None:
        self.ensure()
        payload = json.dumps(state_to_dict(state), indent=2, sort_keys=True).encode()
        self._atomic_write(self.root / "state.json", payload)
        self._atomic_write(self.root / "running.cli", render_config(state).encode())
        self._atomic_write(self.root / "base.sha256", (state.managed_hash() + "\n").encode())

    def load_state(self) -> SwitchState:
        return state_from_dict(json.loads((self.root / "state.json").read_text()))

    def save_candidate(self, text: str) -> Path:
        self.ensure()
        path = self.root / "candidate.cli"
        self._atomic_write(path, text.encode())
        return path

    def load_candidate(self) -> str:
        return (self.root / "candidate.cli").read_text()

    def save_backup(self, payload: bytes) -> Path:
        self.ensure()
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        path = self.backups / f"config-{stamp}.cfg"
        self._atomic_write(path, payload)
        return path

    def save_journal(self, payload: dict[str, Any], *, name: str | None = None) -> Path:
        self.ensure()
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        path = self.journals / (name or f"operation-{stamp}.json")
        self._atomic_write(path, json.dumps(jsonable(payload), indent=2, sort_keys=True).encode())
        return path

    def write_health(self, payload: dict[str, Any]) -> None:
        self.ensure()
        self._atomic_write(
            self.root / "health.json", json.dumps(jsonable(payload), sort_keys=True).encode()
        )
