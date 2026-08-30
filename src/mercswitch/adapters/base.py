from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import (
    CandidateConfig,
    Change,
    ChangePlan,
    DeviceCapabilities,
    DeviceIdentity,
    SwitchMetrics,
    SwitchState,
)


@runtime_checkable
class FirmwareAdapter(Protocol):
    async def probe(self) -> DeviceIdentity: ...

    async def authenticate(self) -> None: ...

    async def read_capabilities(self) -> DeviceCapabilities: ...

    async def read_state(self) -> SwitchState: ...

    async def read_metrics(self, detail_port: int | None = None) -> SwitchMetrics: ...

    def plan_changes(self, current: SwitchState, target: CandidateConfig) -> ChangePlan: ...

    async def apply_change(self, change: Change) -> None: ...

    async def backup(self) -> bytes: ...

    async def restore(self, payload: bytes, filename: str = "config.cfg") -> None: ...

    async def write_memory(self) -> None: ...

    async def close(self) -> None: ...
