from __future__ import annotations

import asyncio

import pytest

from mercswitch.command_engine import CommandSession
from mercswitch.errors import MercSwitchError
from mercswitch.models import VlanConfig


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
async def test_interactive_switchport_trunk_semantics(sample_state):
    session = CommandSession(FakeClient(), sample_state)  # type: ignore[arg-type]
    session.candidate.vlans[2] = VlanConfig(2, "WAN")
    await session.execute("configure terminal")
    await session.execute("interface ethernet 1/0/9")
    await session.execute("switchport mode trunk")
    await session.execute("switchport trunk native vlan 1")
    await session.execute("switchport trunk allowed vlan 1-2")

    assert session.candidate.ports[9].pvid == 1
    assert 9 in session.candidate.vlans[1].untagged
    assert 9 in session.candidate.vlans[2].tagged


@pytest.mark.asyncio
async def test_show_interface_summary_and_detail(sample_state):
    session = CommandSession(FakeClient(), sample_state)  # type: ignore[arg-type]

    summary = await session.execute("show int status")
    detail = await session.execute("sh int e 1/0/9")

    assert "Port    Admin Link" in summary
    assert "1/0/9" in summary
    assert "Ethernet 1/0/9" in detail
    assert "media: sfp" in detail
    assert "switchport mode: access" in detail


@pytest.mark.asyncio
async def test_show_vlan_ip_version_and_capabilities(sample_state):
    session = CommandSession(FakeClient(), sample_state)  # type: ignore[arg-type]

    assert "VLAN Name" in await session.execute("show vl br")
    assert "Default" in await session.execute("show vl id 1")
    assert "IP address: 192.168.2.251" in await session.execute("show ip int")
    assert "MERCURY SE109 Pro" in await session.execute("show ver")
    assert "maximum VLANs: 32" in await session.execute("show cap")
    assert "Group Members" in await session.execute("show port")


@pytest.mark.asyncio
async def test_show_rejects_missing_interface_and_ambiguous_v_prefix(sample_state):
    session = CommandSession(FakeClient(), sample_state)  # type: ignore[arg-type]

    with pytest.raises(MercSwitchError, match="does not exist"):
        await session.execute("show int 1/0/99")
    with pytest.raises(MercSwitchError, match="ambiguous command 'v'.*vlan, version"):
        await session.execute("show v")


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
