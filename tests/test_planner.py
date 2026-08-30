from dataclasses import replace

from mercswitch.models import CandidateConfig, LagConfig, VlanConfig
from mercswitch.planner import build_plan


def test_dependency_order_and_management_last(sample_state):
    target = CandidateConfig.from_state(sample_state)
    target.vlans[2] = VlanConfig(2, "WAN", tagged=(9,), untagged=(1, 2, 3, 4))
    for index in range(1, 5):
        target.ports[index] = replace(target.ports[index], pvid=2)
    target.lags[1] = LagConfig(1, (1, 2, 3, 4))
    target.management = replace(target.management, address="192.168.2.252")
    plan = build_plan(sample_state, target)
    phases = [change.phase for change in plan.changes]
    assert phases == sorted(phases)
    assert phases.index(10) < phases.index(20) < phases.index(40) < phases.index(50)
    assert plan.management_change


def test_stale_vlan_deleted_after_other_changes(sample_state):
    sample_state.vlans[3] = VlanConfig(3, "OLD", tagged=(9,))
    target = CandidateConfig.from_state(sample_state)
    del target.vlans[3]
    plan = build_plan(sample_state, target)
    assert [(change.phase, change.action) for change in plan.changes] == [(60, "delete_vlan")]
