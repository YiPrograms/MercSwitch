from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Self

import httpx

from .adapters import FirmwareAdapter, RpmCgiAdapter
from .errors import ApplyError, DriftError, ValidationError
from .models import (
    CandidateConfig,
    Change,
    ChangePlan,
    DeviceIdentity,
    OperationResult,
    SwitchState,
)
from .storage import CacheStore, default_cache_root, jsonable


class MercSwitchClient:
    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        *,
        cache_dir: Path | str | None = None,
        verify_tls: bool = False,
        adapter: FirmwareAdapter | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.store = CacheStore(cache_dir or default_cache_root() / "default")
        self.adapter: FirmwareAdapter = adapter or RpmCgiAdapter(
            url,
            username,
            password,
            verify_tls=verify_tls,
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self.adapter.close()

    async def probe(self) -> DeviceIdentity:
        await self.adapter.authenticate()
        return await self.adapter.probe()

    async def pull(self, *, cache: bool = True) -> SwitchState:
        await self.adapter.authenticate()
        state = await self.adapter.read_state()
        if cache:
            self.store.save_state(state)
        return state

    async def plan(
        self, target: CandidateConfig, *, force: bool = False
    ) -> tuple[SwitchState, ChangePlan]:
        current = await self.pull(cache=False)
        if target.base_hash and target.base_hash != current.managed_hash() and not force:
            raise DriftError(
                "switch state changed since the candidate was created; pull again or use --force"
            )
        return current, self.adapter.plan_changes(current, target)

    async def commit(
        self,
        target: CandidateConfig,
        *,
        check: bool = False,
        force: bool = False,
        allow_management_change: bool = False,
        progress: Callable[[str], None] | None = None,
    ) -> OperationResult:
        progress = progress or (lambda _: None)
        current, plan = await self.plan(target, force=force)
        if plan.management_change and not allow_management_change:
            raise ValidationError("management IP/VLAN change requires --allow-management-change")
        if check:
            return OperationResult(True, "configuration is valid", plan=plan, final_state=current)
        if plan.empty:
            self.store.save_state(current)
            return OperationResult(
                True, "configuration is already current", plan=plan, final_state=current
            )

        progress("downloading native rollback backup")
        backup_path = self.store.save_backup(await self.adapter.backup())
        journal = {
            "status": "started",
            "base_hash": plan.base_hash,
            "target_hash": plan.target_hash,
            "backup": str(backup_path),
            "changes": jsonable(plan.changes),
            "stages": [],
        }
        journal_path = self.store.save_journal(journal)
        try:
            for phase in sorted({change.phase for change in plan.changes}):
                stage = [change for change in plan.changes if change.phase == phase]
                progress(f"applying phase {phase}: {', '.join(change.target for change in stage)}")
                for change in stage:
                    await self.adapter.apply_change(change)
                incomplete = [change.target for change in stage]
                for attempt in range(3):
                    observed = await self.adapter.read_state()
                    incomplete = [
                        change.target for change in stage if not _change_observed(change, observed)
                    ]
                    if not incomplete:
                        break
                    if attempt < 2:
                        await asyncio.sleep(0.25)
                if incomplete:
                    raise ApplyError("phase read-back did not confirm: " + ", ".join(incomplete))
                journal["stages"].append(
                    {"phase": phase, "observed_hash": observed.managed_hash(), "ok": True}
                )
                self.store.save_journal(journal, name=journal_path.name)
            progress("saving configuration to flash")
            await self.adapter.write_memory()
            await asyncio.sleep(0)
            final_state = await self.adapter.read_state()
            if final_state.managed_hash() != plan.target_hash:
                raise ApplyError(
                    "read-back verification did not match the requested managed configuration"
                )
            self.store.save_state(final_state)
            journal["status"] = "complete"
            journal["final_hash"] = final_state.managed_hash()
            self.store.save_journal(journal, name=journal_path.name)
            return OperationResult(
                True,
                "configuration committed and saved",
                plan=plan,
                final_state=final_state,
                backup_path=str(backup_path),
                journal_path=str(journal_path),
            )
        except Exception as exc:
            journal["status"] = "failed"
            journal["error"] = str(exc)
            self.store.save_journal(journal, name=journal_path.name)
            raise ApplyError(
                f"configuration stopped after a partial failure; journal: {journal_path}; "
                f"backup: {backup_path}; error: {exc}"
            ) from exc

    async def sync(self, target: CandidateConfig, **kwargs: object) -> OperationResult:
        return await self.commit(target, **kwargs)  # type: ignore[arg-type]

    async def backup(self, output: Path | str | None = None) -> Path:
        await self.adapter.authenticate()
        payload = await self.adapter.backup()
        if output:
            path = Path(output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            return path
        return self.store.save_backup(payload)

    async def restore(self, path: Path | str) -> OperationResult:
        await self.adapter.authenticate()
        source = Path(path)
        await self.adapter.restore(source.read_bytes(), source.name)
        return OperationResult(True, "native configuration restore submitted")

    async def write_memory(self) -> None:
        await self.adapter.authenticate()
        await self.adapter.write_memory()


def _change_observed(change: Change, state: SwitchState) -> bool:
    if change.action == "enable_dot1q":
        return state.dot1q_enabled is True
    if change.action == "upsert_vlan":
        return state.vlans.get(change.after.vid) == change.after
    if change.action == "delete_vlan":
        return change.before.vid not in state.vlans
    if change.action == "set_lag":
        return state.lags.get(change.after.group) == change.after
    if change.action == "delete_lag":
        return change.before.group not in state.lags
    if change.action == "set_port":
        observed = state.ports.get(change.after.index)
        return observed is not None and (
            observed.enabled,
            observed.speed,
            observed.flow_control,
        ) == (change.after.enabled, change.after.speed, change.after.flow_control)
    if change.action == "set_pvid":
        return state.ports[int(change.target.split(":", 1)[1])].pvid == change.after
    if change.action == "set_fallback_ip":
        return state.management.fallback_enabled == change.after
    if change.action == "set_management":
        observed = state.management
        expected = change.after
        return (
            observed.vlan,
            observed.dhcp,
            observed.address if not observed.dhcp else "",
            observed.netmask if not observed.dhcp else "",
            observed.gateway,
        ) == (
            expected.vlan,
            expected.dhcp,
            expected.address if not expected.dhcp else "",
            expected.netmask if not expected.dhcp else "",
            expected.gateway,
        )
    return False
