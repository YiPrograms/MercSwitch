from __future__ import annotations

from dataclasses import asdict

from .config import validate_candidate
from .models import CandidateConfig, Change, ChangePlan, SwitchState


def build_plan(current: SwitchState, target: CandidateConfig) -> ChangePlan:
    validate_candidate(target, current.capabilities)
    changes: list[Change] = []
    if target.dot1q_enabled and not current.dot1q_enabled:
        changes.append(Change(5, "enable_dot1q", "vlan-mode", False, True))

    for vid, vlan in sorted(target.vlans.items()):
        before = current.vlans.get(vid)
        if before != vlan:
            changes.append(Change(10, "upsert_vlan", f"vlan:{vid}", before, vlan))

    for group in sorted(set(current.lags) | set(target.lags)):
        before = current.lags.get(group)
        after = target.lags.get(group)
        if before == after:
            continue
        changes.append(
            Change(20, "set_lag" if after else "delete_lag", f"lag:{group}", before, after)
        )

    for index, after in sorted(target.ports.items()):
        before = current.ports[index]
        before_settings = (before.enabled, before.speed, before.flow_control)
        after_settings = (after.enabled, after.speed, after.flow_control)
        if before_settings != after_settings:
            changes.append(Change(30, "set_port", f"port:{index}", before, after))
        if before.pvid != after.pvid:
            changes.append(Change(40, "set_pvid", f"port:{index}", before.pvid, after.pvid))

    if current.management.fallback_enabled != target.management.fallback_enabled:
        changes.append(
            Change(
                45,
                "set_fallback_ip",
                "management:fallback",
                current.management.fallback_enabled,
                target.management.fallback_enabled,
            )
        )

    current_mgmt = asdict(current.management)
    target_mgmt = asdict(target.management)
    current_mgmt.pop("fallback_enabled", None)
    current_mgmt.pop("fallback_address", None)
    target_mgmt.pop("fallback_enabled", None)
    target_mgmt.pop("fallback_address", None)
    management_change = current_mgmt != target_mgmt
    if management_change:
        changes.append(
            Change(
                50,
                "set_management",
                "management",
                current.management,
                target.management,
                management_disruptive=True,
            )
        )

    for vid in sorted(set(current.vlans) - set(target.vlans)):
        if vid != 1:
            changes.append(Change(60, "delete_vlan", f"vlan:{vid}", current.vlans[vid], None))

    target_state = SwitchState(
        identity=current.identity,
        capabilities=current.capabilities,
        management=target.management,
        ports=target.ports,
        vlans=target.vlans,
        lags=target.lags,
        dot1q_enabled=target.dot1q_enabled,
    )
    return ChangePlan(
        base_hash=current.managed_hash(),
        target_hash=target_state.managed_hash(),
        changes=sorted(changes, key=lambda change: (change.phase, change.target)),
        management_change=management_change,
    )
