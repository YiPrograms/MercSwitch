from __future__ import annotations

import asyncio

import pytest

from mercswitch.command_engine import CommandSession
from mercswitch.errors import MercSwitchError


class FakeClient:
    async def write_memory(self):
        return None


@pytest.mark.asyncio
async def test_ssh_candidates_are_isolated(sample_state):
    first = CommandSession(FakeClient(), sample_state)  # type: ignore[arg-type]
    second = CommandSession(FakeClient(), sample_state)  # type: ignore[arg-type]
    await first.execute("configure terminal")
    await first.execute("interface ethernet 1/0/1")
    await first.execute("shutdown")
    assert not first.candidate.ports[1].enabled
    assert second.candidate.ports[1].enabled


@pytest.mark.asyncio
async def test_viewer_cannot_configure_or_write(sample_state):
    session = CommandSession(FakeClient(), sample_state, role="viewer")  # type: ignore[arg-type]
    assert "mercswitch-config" in await session.execute("show running-config")
    with pytest.raises(MercSwitchError, match="admin role"):
        await session.execute("configure terminal")
    with pytest.raises(MercSwitchError, match="admin role"):
        await session.execute("write memory")


@pytest.mark.asyncio
async def test_unambiguous_exec_command_abbreviations(sample_state):
    session = CommandSession(FakeClient(), sample_state)  # type: ignore[arg-type]

    assert "mercswitch-config" in await session.execute("show ru")
    assert "ports " in await session.execute("sh st")
    await session.execute("conf t")
    assert session.config_mode


@pytest.mark.asyncio
async def test_ambiguous_exec_command_abbreviation_is_rejected(sample_state):
    session = CommandSession(FakeClient(), sample_state)  # type: ignore[arg-type]

    with pytest.raises(MercSwitchError, match="ambiguous command 'c'.*configure, commit"):
        await session.execute("c t")


@pytest.mark.asyncio
async def test_writer_lock_serializes_operations():
    lock = asyncio.Lock()
    active = 0
    maximum = 0

    async def operation():
        nonlocal active, maximum
        async with lock:
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0)
            active -= 1

    await asyncio.gather(operation(), operation(), operation())
    assert maximum == 1
