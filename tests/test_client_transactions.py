from __future__ import annotations

from dataclasses import replace

import pytest

from mercswitch.client import MercSwitchClient
from mercswitch.errors import ApplyError, DriftError
from mercswitch.models import CandidateConfig, SwitchMetrics
from mercswitch.planner import build_plan


class FakeAdapter:
    def __init__(self, state, *, fail_action: str = ""):
        self.state = state
        self.fail_action = fail_action
        self.applied = []
        self.backups = 0
        self.saved = 0
        self.target = None

    async def authenticate(self):
        return None

    async def probe(self):
        return self.state.identity

    async def read_capabilities(self):
        return self.state.capabilities

    async def read_state(self):
        if self.target is not None and self.applied:
            # The transaction tests model a device whose phase changes have
            # completed by the final read-back.
            plan = build_plan(self.state, self.target)
            if len(self.applied) == len(plan.changes):
                return replace(
                    self.state,
                    management=self.target.management,
                    ports=self.target.ports,
                    vlans=self.target.vlans,
                    lags=self.target.lags,
                    dot1q_enabled=self.target.dot1q_enabled,
                )
        return self.state

    async def read_metrics(self, detail_port=None):
        return SwitchMetrics(self.state.identity, {})

    def plan_changes(self, current, target):
        self.target = target
        return build_plan(current, target)

    async def apply_change(self, change):
        if change.action == self.fail_action:
            raise RuntimeError("injected failure")
        self.applied.append(change)

    async def backup(self):
        self.backups += 1
        return b"native-backup"

    async def restore(self, payload, filename="config.cfg"):
        return None

    async def write_memory(self):
        self.saved += 1

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_commit_backs_up_orders_and_verifies(tmp_path, sample_state):
    adapter = FakeAdapter(sample_state)
    target = CandidateConfig.from_state(sample_state)
    target.ports[1] = replace(target.ports[1], enabled=False)
    client = MercSwitchClient("http://unused", "x", "x", cache_dir=tmp_path, adapter=adapter)
    result = await client.commit(target)
    assert result.ok
    assert adapter.backups == 1
    assert adapter.saved == 1
    assert [change.action for change in adapter.applied] == ["set_port"]
    assert (tmp_path / "state.json").exists()
    assert list((tmp_path / "backups").glob("*.cfg"))


@pytest.mark.asyncio
async def test_commit_rejects_drift_before_backup(tmp_path, sample_state):
    adapter = FakeAdapter(sample_state)
    target = CandidateConfig.from_state(sample_state)
    target.base_hash = "stale"
    client = MercSwitchClient("http://unused", "x", "x", cache_dir=tmp_path, adapter=adapter)
    with pytest.raises(DriftError):
        await client.commit(target)
    assert adapter.backups == 0


@pytest.mark.asyncio
async def test_partial_failure_keeps_backup_and_failed_journal(tmp_path, sample_state):
    adapter = FakeAdapter(sample_state, fail_action="set_port")
    target = CandidateConfig.from_state(sample_state)
    target.ports[1] = replace(target.ports[1], enabled=False)
    client = MercSwitchClient("http://unused", "x", "x", cache_dir=tmp_path, adapter=adapter)
    with pytest.raises(ApplyError, match="partial failure"):
        await client.commit(target)
    assert list((tmp_path / "backups").glob("*.cfg"))
    journal = next((tmp_path / "journals").glob("*.json")).read_text()
    assert '"status": "failed"' in journal
