from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from mercswitch.adapters.rpm_cgi import RpmCgiAdapter
from mercswitch.models import Change, DeviceCapabilities, PortCapability, PortConfig, VlanConfig


def page(variable: str, value: str, extra: str = "") -> str:
    return f"<html><script>var {variable}={value};{extra}</script></html>"


@pytest.mark.asyncio
async def test_login_probe_and_dynamic_state_parsing():
    calls: list[tuple[str, str]] = []
    authenticated = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal authenticated
        path = request.url.path
        calls.append((request.method, path))
        headers = {"content-type": "text/html"}
        if path == "/" and not authenticated:
            return httpx.Response(200, text='<form action="/logon.cgi"></form>', headers=headers)
        if path == "/cryp_new.js":
            return httpx.Response(404, text="", headers=headers)
        if path == "/logon.cgi":
            data = parse_qs(request.content.decode())
            assert data["username"] == ["admin"]
            assert data["password"][0] != "secret"
            authenticated = True
            return httpx.Response(200, text="var logonInfo=[0];", headers=headers)
        if path == "/":
            return httpx.Response(
                200,
                text='<script>var g_tid=991; var g_product=2;</script><a href="MainRpm.htm">x</a>',
                headers=headers,
            )
        pages = {
            "/SystemInfoRpm.htm": page(
                "info_ds",
                '{productModel:["SE109 Pro"],hardwareStr:["1.0"],firmwareStr:["1.0-test"],macStr:["00-11"],descriStr:["switch"]}',
            ),
            "/PortSettingRpm.htm": page(
                "all_info",
                "{state:[1,1,1],spd_cfg:[1,6,1],spd_act:[6,0,8],fc_cfg:[0,1,0],port_type:[0,0,1]}",
                'var max_port_num=3;var port_str="1";',
            ),
            "/Vlan8021QRpm.htm": page(
                "qvlan_ds",
                '{portNum:3,maxVids:16,state:1,vids:[1,2],names:["Default","WAN"],tagMbrs:[0,4],untagMbrs:[7,3]}',
            ),
            "/PortTrunkRpm.htm": page(
                "trunk_conf", "{maxTrunkNum:1,portNumPerTrunk:2,portNum:2,portStr_g1:[1,1,0]}"
            ),
            "/IpSettingRpm.htm": page(
                "ip_ds",
                '{state:0,ipStr:["192.168.2.251"],netmaskStr:["255.255.255.0"],gatewayStr:["192.168.2.254"]}',
                "var manage_vlan=1;",
            ),
            "/FixIpSettingRpm.htm": page("ip_ds", '{state:1,ipStr:["192.168.0.1"]}'),
            "/Vlan8021QPvidRpm.htm": page("pvid_ds", "{pvids:[1,2,2]}"),
        }
        if path in pages:
            return httpx.Response(200, text=pages[path], headers=headers)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    adapter = RpmCgiAdapter(
        "http://switch/", "admin", "secret", transport=httpx.MockTransport(handler)
    )
    try:
        await adapter.authenticate()
        identity = await adapter.probe()
        state = await adapter.read_state()
    finally:
        await adapter.close()

    assert identity.vendor == "MERCURY"
    assert identity.model == "SE109 Pro"
    assert state.capabilities.port_count == 3
    assert state.capabilities.ports[2].media == "sfp"
    assert "2500-full" in state.capabilities.ports[0].speeds
    assert "2500-full" not in state.capabilities.ports[1].speeds
    assert state.capabilities.max_vlans == 16
    assert state.lags[1].members == (1, 2)
    assert state.vlans[2].tagged == (3,)
    assert state.vlans[2].untagged == (1, 2)
    assert state.ports[2].pvid == 2
    assert calls.count(("POST", "/logon.cgi")) == 1


@pytest.mark.asyncio
async def test_unknown_login_schema_fails_safely():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="not a supported device", headers={"content-type": "text/html"}
        )

    adapter = RpmCgiAdapter(
        "http://switch/", "admin", "secret", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(Exception, match="login page"):
            await adapter.authenticate()
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_exact_cgi_forms_for_core_changes():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text="ok", headers={"content-type": "text/html"})

    adapter = RpmCgiAdapter(
        "http://switch/", "admin", "secret", transport=httpx.MockTransport(handler)
    )
    adapter._authenticated = True
    adapter.token = "991"
    adapter._capabilities = DeviceCapabilities(
        ports=(PortCapability(1), PortCapability(2), PortCapability(3)), schema_writable=True
    )
    try:
        await adapter.apply_change(
            Change(10, "upsert_vlan", "vlan:2", None, VlanConfig(2, "WAN", (3,), (1, 2)))
        )
        await adapter.apply_change(
            Change(30, "set_port", "port:2", None, PortConfig(2, False, "1000-full", True))
        )
        await adapter.apply_change(Change(40, "set_pvid", "port:2", 1, 2))
    finally:
        await adapter.close()

    vlan_query = parse_qs(requests[0].url.query.decode())
    assert requests[0].url.path == "/qvlanSet.cgi"
    assert vlan_query["vid"] == ["2"]
    assert [vlan_query[f"selType_{index}"][0] for index in (1, 2, 3)] == ["0", "0", "1"]
    port_query = parse_qs(requests[1].url.query.decode())
    assert requests[1].url.path == "/port_setting.cgi"
    assert (port_query["portid"], port_query["state"], port_query["speed"]) == (
        ["2"],
        ["0"],
        ["6"],
    )
    pvid_query = parse_qs(requests[2].url.query.decode())
    assert pvid_query["pbm"] == ["2"]
    assert pvid_query["pvid"] == ["2"]
