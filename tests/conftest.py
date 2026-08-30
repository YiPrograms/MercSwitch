from __future__ import annotations

import pytest

from mercswitch.models import (
    DeviceCapabilities,
    DeviceIdentity,
    ManagementConfig,
    PortCapability,
    PortConfig,
    SwitchState,
    VlanConfig,
)


@pytest.fixture
def sample_state() -> SwitchState:
    capabilities = DeviceCapabilities(
        ports=tuple(
            PortCapability(
                index=index,
                media="sfp" if index == 9 else "copper",
                speeds=("auto", "1000-full", "10000-full")
                if index == 9
                else ("auto", "10-full", "100-full", "1000-full"),
            )
            for index in range(1, 10)
        ),
        max_vlans=32,
        lag_members={1: (1, 2, 3, 4), 2: (5, 6, 7, 8)},
        lag_max_members=4,
        supports_fallback_ip=True,
        schema_writable=True,
    )
    return SwitchState(
        identity=DeviceIdentity("MERCURY", "SE109 Pro", "1.0", "test"),
        capabilities=capabilities,
        management=ManagementConfig(
            vlan=1,
            address="192.168.2.251",
            netmask="255.255.255.0",
            gateway="192.168.2.254",
            fallback_enabled=True,
            fallback_address="192.168.0.1",
        ),
        ports={index: PortConfig(index=index, pvid=1) for index in range(1, 10)},
        vlans={1: VlanConfig(1, "Default", (), tuple(range(1, 10)))},
        lags={},
    )
