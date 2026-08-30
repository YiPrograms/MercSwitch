from __future__ import annotations

from dataclasses import replace

import pytest

from mercswitch.config import parse_config, parse_ports, render_config, validate_candidate
from mercswitch.errors import ParseError, ValidationError
from mercswitch.models import CandidateConfig, VlanConfig


def test_parse_render_round_trip(sample_state):
    rendered = render_config(sample_state)
    candidate = parse_config(rendered, base_hash=sample_state.managed_hash())
    expected = CandidateConfig.from_state(sample_state)
    assert candidate.management == replace(expected.management, fallback_address="")
    assert candidate.ports == expected.ports
    assert candidate.vlans == expected.vlans
    assert candidate.lags == expected.lags
    assert candidate.base_hash == sample_state.managed_hash()
    validate_candidate(candidate, sample_state.capabilities)


def test_render_uses_access_and_trunk_switchport_semantics(sample_state):
    sample_state.vlans[2] = VlanConfig(2, "WAN", tagged=(9,))
    rendered = render_config(sample_state)

    assert " tagged ports " not in rendered
    assert " untagged ports " not in rendered
    assert " switchport mode access\n switchport access vlan 1" in rendered
    assert " switchport mode trunk\n switchport trunk native vlan 1" in rendered
    assert " switchport trunk allowed vlan 1-2" in rendered

    candidate = parse_config(rendered)
    assert candidate.ports == CandidateConfig.from_state(sample_state).ports
    assert candidate.vlans == CandidateConfig.from_state(sample_state).vlans


def test_hybrid_switchport_round_trip_for_multiple_untagged_vlans(sample_state):
    sample_state.vlans[2] = VlanConfig(2, "Legacy", untagged=(1,))
    rendered = render_config(sample_state)

    assert " switchport mode hybrid" in rendered
    assert " switchport hybrid pvid vlan 1" in rendered
    assert " switchport hybrid untagged vlan 1-2" in rendered
    assert parse_config(rendered).vlans == CandidateConfig.from_state(sample_state).vlans


def test_legacy_vlan_membership_syntax_remains_readable():
    candidate = parse_config(
        """! mercswitch-config v1
interface vlan 1
 ip address dhcp
!
vlan 1
 name Default
 tagged ports none
 untagged ports 1
!
interface ethernet 1/0/1
 no shutdown
 speed auto
 no flow-control
 switchport pvid 1
!
"""
    )

    assert candidate.ports[1].pvid == 1
    assert candidate.vlans[1].untagged == (1,)


def test_port_range_parser():
    assert parse_ports("1-4,7,9") == (1, 2, 3, 4, 7, 9)


def test_configuration_version_header_is_required():
    with pytest.raises(ParseError, match="must begin"):
        parse_config("interface vlan 1\n ip address dhcp\n!\n")


def test_runtime_capability_validation(sample_state):
    candidate = CandidateConfig.from_state(sample_state)
    candidate.ports[9] = replace(candidate.ports[9], speed="10-full")
    candidate.vlans[2] = VlanConfig(2, "WAN", tagged=(10,))
    with pytest.raises(ValidationError) as exc:
        validate_candidate(candidate, sample_state.capabilities)
    assert "port 9 does not support" in str(exc.value)
    assert "unavailable ports 10" in str(exc.value)


def test_fallback_observation_does_not_cause_drift(sample_state):
    candidate = CandidateConfig.from_state(sample_state)
    candidate.management = replace(candidate.management, fallback_address="")
    target = replace(sample_state, management=candidate.management)
    assert target.managed_hash() == sample_state.managed_hash()
