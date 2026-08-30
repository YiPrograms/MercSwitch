from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from ..errors import ApplyError, AuthenticationError, UnsupportedDeviceError
from ..jsparse import extract_assignment, extract_int, extract_token, first
from ..models import (
    CandidateConfig,
    Change,
    ChangePlan,
    DeviceCapabilities,
    DeviceIdentity,
    LagConfig,
    ManagementConfig,
    PortCapability,
    PortConfig,
    PortMetrics,
    Speed,
    SwitchMetrics,
    SwitchState,
    VlanConfig,
)
from ..planner import build_plan

PASSWORD_KEY = "RDpbLfCPsJZ7fiv"
PASSWORD_TABLE = (
    "yLwVl0zKqws7LgKPRQ84Mdt708T1qQ3Ha7xv3H7NyU84p21BriUWBU43odz3iP4rBL3cD02KZci"
    "XTysVXiV8ngg6vL48rPJyAUw0HurW20xqxv9aYb4M9wK1Ae0wlro510qXeU07kV57fQMc8L6aLg"
    "MLwygtc0F10a0Dg70TOoouyFhdysuRMO51yY5ZlOZZLEal1h0t9YQW0Ko7oBwmCAHoic4HYbUyVe"
    "U3sfQ1xtXcPcf1aT303wAQhv66qzW"
)

SPEED_CODE_TO_NAME: dict[int, Speed] = {
    1: "auto",
    2: "10-half",
    3: "10-full",
    4: "100-half",
    5: "100-full",
    6: "1000-full",
    7: "2500-full",
    8: "10000-full",
}
SPEED_NAME_TO_CODE = {value: key for key, value in SPEED_CODE_TO_NAME.items()}
ACTUAL_SPEED = {
    0: "down",
    1: "auto",
    2: "10-half",
    3: "10-full",
    4: "100-half",
    5: "100-full",
    6: "1000-full",
    7: "2500-full",
    8: "10000-full",
}
SPEED_MBPS = {0: 0, 1: 0, 2: 10, 3: 10, 4: 100, 5: 100, 6: 1000, 7: 2500, 8: 10000}


def security_encode(password: str, key: str = PASSWORD_KEY, table: str = PASSWORD_TABLE) -> str:
    output: list[str] = []
    for index in range(max(len(password), len(key))):
        left = 187 if index >= len(password) else ord(password[index])
        right = 187 if index >= len(key) else ord(key[index])
        output.append(table[(left ^ right) % len(table)])
    return "".join(output)


def _mask_to_ports(mask: int, port_count: int) -> tuple[int, ...]:
    return tuple(index for index in range(1, port_count + 1) if mask & (1 << (index - 1)))


def _ports_to_mask(ports: tuple[int, ...] | list[int]) -> int:
    mask = 0
    for index in ports:
        mask |= 1 << (index - 1)
    return mask


class RpmCgiAdapter:
    name = "rpm-cgi-v1"

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        *,
        timeout: float = 15.0,
        verify_tls: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = url.rstrip("/") + "/"
        self.username = username
        self.password = password
        self.timeout = timeout
        self.verify_tls = verify_tls
        self._transport = transport
        self._client = self._new_client(self.url)
        self.token = ""
        self._identity: DeviceIdentity | None = None
        self._capabilities: DeviceCapabilities | None = None
        self._authenticated = False

    def _new_client(self, base_url: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=base_url,
            follow_redirects=True,
            timeout=self.timeout,
            verify=self.verify_tls,
            transport=self._transport,
            headers={"User-Agent": "mercswitch/0.1"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _rebase(self, address: str) -> None:
        parsed = urlparse(self.url)
        new_url = urlunparse((parsed.scheme, address, "/", "", "", ""))
        cookies = self._client.cookies
        await self._client.aclose()
        self.url = new_url.rstrip("/") + "/"
        self._client = self._new_client(self.url)
        self._client.cookies.update(cookies)

    @staticmethod
    def _text(response: httpx.Response) -> str:
        # Firmware pages do not consistently send a charset. Decode directly
        # so callers can inspect the same response more than once under HTTPX.
        return response.content.decode("utf-8", errors="replace")

    async def authenticate(self) -> None:
        root = await self._client.get("/")
        text = self._text(root)
        if "g_tid" in text and "MainRpm" in text:
            self.token = extract_token(text)
            self._authenticated = True
            return
        if "/logon.cgi" not in text:
            raise UnsupportedDeviceError("device does not expose the supported RPM/CGI login page")

        key, table = PASSWORD_KEY, PASSWORD_TABLE
        try:
            crypto_text = self._text(await self._client.get("/cryp_new.js"))
            match = re.search(
                r'function\s+hex_md5\([^)]*\)\{var\s+\w+="([^"]+)";var\s+\w+="([^"]+)"',
                crypto_text,
            )
            if match:
                key, table = match.group(1), match.group(2)
        except httpx.HTTPError:
            pass

        response = await self._client.post(
            "/logon.cgi",
            data={
                "username": self.username,
                "password": security_encode(self.password, key, table),
                "logon": "登录",
            },
        )
        login_text = self._text(response)
        info = extract_assignment(login_text, "logonInfo", [1])
        if not isinstance(info, list) or not info or int(info[0]) != 0:
            raise AuthenticationError("switch rejected the username or password")
        root = await self._client.get("/")
        root_text = self._text(root)
        self.token = extract_token(root_text)
        self._authenticated = True

    async def _ensure_authenticated(self) -> None:
        if not self._authenticated:
            await self.authenticate()

    async def _get(
        self, path: str, *, params: Any = None, retry_auth: bool = True
    ) -> httpx.Response:
        await self._ensure_authenticated()
        response = await self._client.get(path, params=params)
        text = (
            self._text(response)
            if response.headers.get("content-type", "").startswith("text")
            else ""
        )
        if retry_auth and "/logon.cgi" in text and "logonInfo" in text:
            self._authenticated = False
            await self.authenticate()
            return await self._get(path, params=params, retry_auth=False)
        response.raise_for_status()
        return response

    async def _post(
        self,
        path: str,
        *,
        data: Any = None,
        files: Any = None,
        retry_auth: bool = True,
    ) -> httpx.Response:
        await self._ensure_authenticated()
        response = await self._client.post(path, data=data, files=files)
        text = (
            self._text(response)
            if response.headers.get("content-type", "").startswith("text")
            else ""
        )
        if retry_auth and "/logon.cgi" in text and "logonInfo" in text:
            self._authenticated = False
            await self.authenticate()
            return await self._post(path, data=data, files=files, retry_auth=False)
        response.raise_for_status()
        return response

    async def _page(self, path: str) -> str:
        return self._text(await self._get(path))

    async def probe(self) -> DeviceIdentity:
        await self._ensure_authenticated()
        info = await self._page("/SystemInfoRpm.htm")
        info_ds = extract_assignment(info, "info_ds")
        if not isinstance(info_ds, dict):
            raise UnsupportedDeviceError("SystemInfoRpm.htm schema is not recognized")
        root = self._text(await self._get("/"))
        product = extract_int(root, "g_product", 2)
        # Shared RPM firmware IDs: 0 TP-Link, 1 FAST, 2 MERCURY.
        vendor = {0: "TP-Link", 1: "FAST", 2: "MERCURY"}.get(product, "unknown")
        self._identity = DeviceIdentity(
            vendor=vendor,
            model=str(
                first(info_ds.get("productModel"), first(info_ds.get("descriStr"), "unknown"))
            ),
            hardware=str(first(info_ds.get("hardwareStr"), "unknown")),
            firmware=str(first(info_ds.get("firmwareStr"), "unknown")),
            mac=str(first(info_ds.get("macStr"), "")),
            description=str(first(info_ds.get("descriStr"), "")),
            adapter=self.name,
        )
        return self._identity

    async def read_capabilities(self) -> DeviceCapabilities:
        await self._ensure_authenticated()
        if self._identity is None:
            await self.probe()
        port_page = await self._page("/PortSettingRpm.htm")
        vlan_page = await self._page("/Vlan8021QRpm.htm")
        trunk_page = await self._page("/PortTrunkRpm.htm")
        port_info = extract_assignment(port_page, "all_info")
        vlan_info = extract_assignment(vlan_page, "qvlan_ds")
        trunk_info = extract_assignment(trunk_page, "trunk_conf")
        if not all(isinstance(item, dict) for item in (port_info, vlan_info, trunk_info)):
            raise UnsupportedDeviceError("core RPM/CGI configuration schema is incomplete")
        port_count = int(vlan_info.get("portNum") or extract_int(port_page, "max_port_num"))
        port_types = list(port_info.get("port_type", []))
        multi_gig_copper = set()
        match = re.search(r'var\s+port_str\s*=\s*"([^"]*)"', port_page)
        if match:
            for part in match.group(1).split(","):
                if "-" in part:
                    start, end = (int(value) for value in part.split("-", 1))
                    multi_gig_copper.update(range(start, end + 1))
                elif part.strip().isdigit():
                    multi_gig_copper.add(int(part))
        ports: list[PortCapability] = []
        for index in range(1, port_count + 1):
            port_type = int(port_types[index - 1]) if index <= len(port_types) else 0
            if port_type == 1:
                media = "sfp"
                speeds: tuple[Speed, ...] = ("auto", "1000-full", "2500-full", "10000-full")
            elif port_type in {3, 4, 5}:
                media = "combo"
                speeds = ("auto", "100-full", "1000-full", "2500-full", "10000-full")
            else:
                media = "copper"
                speeds = (
                    ("auto", "10-full", "100-full", "1000-full", "2500-full")
                    if index in multi_gig_copper
                    else ("auto", "10-half", "10-full", "100-half", "100-full", "1000-full")
                )
            ports.append(
                PortCapability(
                    index=index,
                    media=media,  # type: ignore[arg-type]
                    speeds=speeds,
                    supports_half_duplex="10-half" in speeds,
                    poe_capable=False,
                )
            )
        max_lags = int(trunk_info.get("maxTrunkNum", 0))
        ports_per_lag = int(trunk_info.get("portNumPerTrunk", 4))
        lag_port_count = int(trunk_info.get("portNum", 0))
        lag_members: dict[int, tuple[int, ...]] = {}
        for group in range(1, max_lags + 1):
            start = (group - 1) * ports_per_lag + 1
            lag_members[group] = tuple(range(start, min(start + ports_per_lag, lag_port_count + 1)))
        model = self._identity.model if self._identity else ""
        poe = bool(re.search(r"\b[A-Z]+\d+P\s+Pro\b", model, re.IGNORECASE))
        self._capabilities = DeviceCapabilities(
            ports=tuple(ports),
            max_vlans=int(vlan_info.get("maxVids", 32)),
            lag_members=lag_members,
            lag_min_members=2,
            lag_max_members=ports_per_lag,
            supports_fallback_ip=True,
            poe_capable=poe,
            schema_writable=True,
        )
        return self._capabilities

    async def read_state(self) -> SwitchState:
        await self._ensure_authenticated()
        identity = self._identity or await self.probe()
        capabilities = self._capabilities or await self.read_capabilities()
        ip_page = await self._page("/IpSettingRpm.htm")
        fallback_page = await self._page("/FixIpSettingRpm.htm")
        port_page = await self._page("/PortSettingRpm.htm")
        trunk_page = await self._page("/PortTrunkRpm.htm")
        vlan_page = await self._page("/Vlan8021QRpm.htm")
        pvid_page = await self._page("/Vlan8021QPvidRpm.htm")

        ip_ds = extract_assignment(ip_page, "ip_ds", {})
        fallback_ds = extract_assignment(fallback_page, "ip_ds", {})
        port_ds = extract_assignment(port_page, "all_info", {})
        trunk_ds = extract_assignment(trunk_page, "trunk_conf", {})
        vlan_ds = extract_assignment(vlan_page, "qvlan_ds", {})
        pvid_ds = extract_assignment(pvid_page, "pvid_ds", {})
        management = ManagementConfig(
            vlan=extract_int(ip_page, "manage_vlan", 1),
            dhcp=int(ip_ds.get("state", 0)) == 1,
            address=str(first(ip_ds.get("ipStr"), "")),
            netmask=str(first(ip_ds.get("netmaskStr"), "")),
            gateway=str(first(ip_ds.get("gatewayStr"), "")),
            fallback_enabled=int(fallback_ds.get("state", 0)) == 1,
            fallback_address=str(first(fallback_ds.get("ipStr"), "")),
        )
        states = list(port_ds.get("state", []))
        speeds = list(port_ds.get("spd_cfg", []))
        actual = list(port_ds.get("spd_act", []))
        flows = list(port_ds.get("fc_cfg", []))
        pvids = list(pvid_ds.get("pvids", []))
        ports: dict[int, PortConfig] = {}
        for capability in capabilities.ports:
            offset = capability.index - 1
            speed_code = int(speeds[offset]) if offset < len(speeds) else 1
            actual_code = int(actual[offset]) if offset < len(actual) else 0
            ports[capability.index] = PortConfig(
                index=capability.index,
                enabled=bool(int(states[offset])) if offset < len(states) else True,
                speed=SPEED_CODE_TO_NAME.get(speed_code, "auto"),
                flow_control=bool(int(flows[offset])) if offset < len(flows) else False,
                pvid=int(pvids[offset]) if offset < len(pvids) else 1,
                link_up=actual_code != 0,
                actual_speed=ACTUAL_SPEED.get(actual_code, "unknown"),
            )
        vids = [int(value) for value in vlan_ds.get("vids", [])]
        names = list(vlan_ds.get("names", []))
        tagged = list(vlan_ds.get("tagMbrs", []))
        untagged = list(vlan_ds.get("untagMbrs", []))
        vlans: dict[int, VlanConfig] = {}
        for offset, vid in enumerate(vids):
            vlans[vid] = VlanConfig(
                vid=vid,
                name=str(names[offset]) if offset < len(names) else "",
                tagged=_mask_to_ports(int(tagged[offset]), capabilities.port_count)
                if offset < len(tagged)
                else (),
                untagged=_mask_to_ports(int(untagged[offset]), capabilities.port_count)
                if offset < len(untagged)
                else (),
            )
        lags: dict[int, LagConfig] = {}
        for group in capabilities.lag_members:
            values = list(trunk_ds.get(f"portStr_g{group}", []))
            members = tuple(index + 1 for index, value in enumerate(values) if int(value))
            if members:
                lags[group] = LagConfig(group=group, members=members)
        return SwitchState(
            identity=identity,
            capabilities=capabilities,
            management=management,
            ports=ports,
            vlans=vlans,
            lags=lags,
            dot1q_enabled=bool(int(vlan_ds.get("state", 0))),
        )

    async def read_metrics(self, detail_port: int | None = None) -> SwitchMetrics:
        state = await self.read_state()
        summary_page = await self._page("/PortStatisticsRpm.htm")
        summary = extract_assignment(summary_page, "all_info", {})
        packets = list(summary.get("pkts", []))
        link_status = list(summary.get("link_status", []))
        ports: dict[int, PortMetrics] = {}
        for index, config in state.ports.items():
            offset = index - 1
            link_code = int(link_status[offset]) if offset < len(link_status) else 0
            packet_offset = 4 * offset
            values = packets[packet_offset : packet_offset + 4]
            values += [0] * (4 - len(values))
            ports[index] = PortMetrics(
                index=index,
                admin_up=config.enabled,
                link_up=link_code != 0,
                speed_mbps=SPEED_MBPS.get(link_code, 0),
                tx_good=int(values[0]),
                tx_bad=int(values[1]),
                rx_good=int(values[2]),
                rx_bad=int(values[3]),
            )
        if detail_port is not None and detail_port in ports:
            detail_page = await self._page(
                f"/PortStatisticsAllRpm.htm?port={detail_port - 1}&token={self.token}"
            )
            rx = list(extract_assignment(detail_page, "pkts_rx_info", []))
            tx = list(extract_assignment(detail_page, "pkts_tx_info", []))
            rx += [0] * (10 - len(rx))
            tx += [0] * (10 - len(tx))
            metric = ports[detail_port]
            metric.rx_unicast, metric.rx_multicast, metric.rx_broadcast = map(int, rx[:3])
            metric.tx_unicast, metric.tx_multicast, metric.tx_broadcast = map(int, tx[:3])
            metric.undersize = int(rx[4] + tx[4])
            metric.oversize = int(rx[5] + tx[5])
            metric.crc_errors = int(rx[6] + tx[6])
            metric.fragments = int(rx[7] + tx[7])
            metric.jabbers = int(rx[8] + tx[8])
            metric.collisions = int(rx[9] + tx[9])
            metric.detail_available = True
        return SwitchMetrics(state.identity, ports)

    def plan_changes(self, current: SwitchState, target: CandidateConfig) -> ChangePlan:
        if not current.capabilities.schema_writable:
            raise UnsupportedDeviceError("adapter schema is read-only")
        return build_plan(current, target)

    async def _submit_get(self, path: str, params: list[tuple[str, str]]) -> None:
        response = await self._get(path, params=params)
        if response.status_code >= 400:
            raise ApplyError(f"{path} failed with HTTP {response.status_code}")

    async def apply_change(self, change: Change) -> None:
        token = self.token
        if change.action == "enable_dot1q":
            await self._submit_get(
                "/qvlanSet.cgi",
                [("qvlan_en", "1"), ("qvlan_mode", "应用"), ("token", token)],
            )
        elif change.action == "upsert_vlan":
            vlan = change.after
            params = [("vid", str(vlan.vid)), ("vname", vlan.name)]
            tagged, untagged = set(vlan.tagged), set(vlan.untagged)
            assert self._capabilities is not None
            for index in range(1, self._capabilities.port_count + 1):
                value = "1" if index in tagged else "0" if index in untagged else "2"
                params.append((f"selType_{index}", value))
            params.extend([("qvlan_add", "添加/编辑"), ("token", token)])
            await self._submit_get("/qvlanSet.cgi", params)
        elif change.action == "delete_vlan":
            vlan = change.before
            await self._submit_get(
                "/qvlanSet.cgi",
                [("selVlans", str(vlan.vid)), ("qvlan_del", "删除"), ("token", token)],
            )
        elif change.action == "set_lag":
            lag = change.after
            params = [("groupId", str(lag.group))]
            params.extend(("portid", str(port)) for port in lag.members)
            params.extend([("setapply", "应用"), ("token", token)])
            await self._submit_get("/port_trunk_set.cgi", params)
        elif change.action == "delete_lag":
            lag = change.before
            await self._submit_get(
                "/port_trunk_display.cgi",
                [("chk_trunk", str(lag.group)), ("setDelete", "删除"), ("token", token)],
            )
        elif change.action == "set_port":
            port = change.after
            await self._submit_get(
                "/port_setting.cgi",
                [
                    ("portid", str(port.index)),
                    ("state", "1" if port.enabled else "0"),
                    ("speed", str(SPEED_NAME_TO_CODE[port.speed])),
                    ("flowcontrol", "1" if port.flow_control else "0"),
                    ("apply", "应用"),
                    ("token", token),
                ],
            )
        elif change.action == "set_pvid":
            index = int(change.target.split(":", 1)[1])
            await self._submit_get(
                "/vlanPvidSet.cgi",
                [
                    ("pbm", str(_ports_to_mask([index]))),
                    ("pvid", str(change.after)),
                    ("token", token),
                ],
            )
        elif change.action == "set_fallback_ip":
            await self._submit_get(
                "/fix_ip_setting.cgi",
                [
                    ("fixIpSetting", "enable" if change.after else "disable"),
                    ("submit", "应用"),
                    ("token", token),
                ],
            )
        elif change.action == "set_management":
            management = change.after
            params = [
                ("manage_vlan", str(management.vlan)),
                ("dhcpSetting", "enable" if management.dhcp else "disable"),
                ("token", token),
            ]
            if not management.dhcp:
                params.extend(
                    [
                        ("ip_address", management.address),
                        ("ip_netmask", management.netmask),
                        ("ip_gateway", management.gateway),
                    ]
                )
            try:
                await self._submit_get("/ip_setting.cgi", params)
            except (httpx.HTTPError, ApplyError):
                pass
            if not management.dhcp and management.address:
                await self._rebase(management.address)
                self._authenticated = False
                await self.authenticate()
        elif change.action == "save":
            await self.write_memory()
        else:
            raise ApplyError(f"unsupported change action: {change.action}")

    async def write_memory(self) -> None:
        await self._post("/savingconfig.cgi", data={"action_op": "save", "token": self.token})

    async def backup(self) -> bytes:
        response = await self._get("/config_back.cgi", params={"token": self.token})
        return response.content

    async def restore(self, payload: bytes, filename: str = "config.cfg") -> None:
        await self._post(
            f"/conf_restore.cgi?token={self.token}",
            files={"configfile": (filename, payload, "application/octet-stream")},
        )
