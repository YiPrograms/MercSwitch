from __future__ import annotations

import asyncio
import bisect
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pyasn1.codec.ber import decoder, encoder
from pysnmp.proto import api

from .models import SwitchMetrics, SwitchState

Oid = tuple[int, ...]
ValueProvider = Callable[[], Any]


@dataclass(slots=True)
class SnmpSystemConfig:
    name: str = "mercswitch"
    contact: str = ""
    location: str = ""


class OidTree:
    def __init__(self) -> None:
        self._values: dict[Oid, ValueProvider] = {}
        self._oids: list[Oid] = []

    def replace(self, values: dict[Oid, ValueProvider]) -> None:
        self._values = values
        self._oids = sorted(values)

    def get(self, oid: Oid) -> Any | None:
        provider = self._values.get(oid)
        return provider() if provider else None

    def next(self, oid: Oid) -> tuple[Oid, Any] | None:
        index = bisect.bisect_right(self._oids, oid)
        if index >= len(self._oids):
            return None
        next_oid = self._oids[index]
        return next_oid, self._values[next_oid]()


def _constant(value: Any) -> ValueProvider:
    return lambda: value


def build_oid_values(
    state: SwitchState,
    metrics: SwitchMetrics,
    *,
    started_at: float,
    system: SnmpSystemConfig,
) -> dict[Oid, ValueProvider]:
    p = api.PROTOCOL_MODULES[api.SNMP_VERSION_2C]
    identity = state.identity
    values: dict[Oid, ValueProvider] = {
        (1, 3, 6, 1, 2, 1, 1, 1, 0): _constant(
            p.OctetString(f"{identity.vendor} {identity.model}; firmware {identity.firmware}")
        ),
        (1, 3, 6, 1, 2, 1, 1, 2, 0): _constant(p.ObjectIdentifier((1, 3, 6, 1, 4, 1, 11863))),
        (1, 3, 6, 1, 2, 1, 1, 3, 0): lambda: p.TimeTicks(
            int((time.monotonic() - started_at) * 100)
        ),
        (1, 3, 6, 1, 2, 1, 1, 4, 0): _constant(p.OctetString(system.contact)),
        (1, 3, 6, 1, 2, 1, 1, 5, 0): _constant(p.OctetString(system.name)),
        (1, 3, 6, 1, 2, 1, 1, 6, 0): _constant(p.OctetString(system.location)),
        (1, 3, 6, 1, 2, 1, 1, 7, 0): _constant(p.Integer32(2)),
        (1, 3, 6, 1, 2, 1, 2, 1, 0): _constant(p.Integer32(len(metrics.ports))),
    }
    if_table = (1, 3, 6, 1, 2, 1, 2, 2, 1)
    ifx_table = (1, 3, 6, 1, 2, 1, 31, 1, 1, 1)
    ether_table = (1, 3, 6, 1, 2, 1, 16, 1, 1, 1)
    for index, metric in sorted(metrics.ports.items()):
        name = f"Ethernet1/0/{index}"
        speed_bps = min(metric.speed_mbps * 1_000_000, 4_294_967_295)
        in_errors = metric.rx_bad or (
            metric.undersize
            + metric.oversize
            + metric.crc_errors
            + metric.fragments
            + metric.jabbers
        )
        out_errors = metric.tx_bad
        values.update(
            {
                if_table + (1, index): _constant(p.Integer32(index)),
                if_table + (2, index): _constant(p.OctetString(name)),
                if_table + (3, index): _constant(p.Integer32(6)),
                if_table + (5, index): _constant(p.Gauge32(speed_bps)),
                if_table + (7, index): _constant(p.Integer32(1 if metric.admin_up else 2)),
                if_table + (8, index): _constant(p.Integer32(1 if metric.link_up else 2)),
                if_table + (9, index): _constant(p.TimeTicks(0)),
                if_table + (14, index): _constant(p.Counter32(in_errors)),
                if_table + (20, index): _constant(p.Counter32(out_errors)),
                if_table + (22, index): _constant(p.ObjectIdentifier((0, 0))),
                ifx_table + (1, index): _constant(p.OctetString(name)),
                ifx_table + (14, index): _constant(p.Integer32(2)),
                ifx_table + (15, index): _constant(p.Gauge32(metric.speed_mbps)),
                ifx_table + (16, index): _constant(p.Integer32(2)),
                ifx_table + (17, index): _constant(p.Integer32(1)),
                ifx_table + (18, index): _constant(p.OctetString("")),
                ifx_table + (19, index): _constant(p.TimeTicks(0)),
                ether_table + (1, index): _constant(p.Integer32(index)),
                ether_table + (2, index): _constant(p.ObjectIdentifier(if_table + (1, index))),
                ether_table + (5, index): _constant(p.Counter32(metric.rx_good + metric.rx_bad)),
                ether_table + (20, index): _constant(p.OctetString("mercswitchd")),
                ether_table + (21, index): _constant(p.Integer32(1)),
            }
        )
        if metric.detail_available:
            values.update(
                {
                    if_table + (11, index): _constant(p.Counter32(metric.rx_unicast)),
                    if_table + (12, index): _constant(
                        p.Counter32(metric.rx_multicast + metric.rx_broadcast)
                    ),
                    if_table + (17, index): _constant(p.Counter32(metric.tx_unicast)),
                    if_table + (18, index): _constant(
                        p.Counter32(metric.tx_multicast + metric.tx_broadcast)
                    ),
                    ifx_table + (2, index): _constant(p.Counter32(metric.rx_multicast)),
                    ifx_table + (3, index): _constant(p.Counter32(metric.rx_broadcast)),
                    ifx_table + (4, index): _constant(p.Counter32(metric.tx_multicast)),
                    ifx_table + (5, index): _constant(p.Counter32(metric.tx_broadcast)),
                    ether_table + (6, index): _constant(p.Counter32(metric.rx_broadcast)),
                    ether_table + (7, index): _constant(p.Counter32(metric.rx_multicast)),
                    ether_table + (8, index): _constant(p.Counter32(metric.crc_errors)),
                    ether_table + (9, index): _constant(p.Counter32(metric.undersize)),
                    ether_table + (10, index): _constant(p.Counter32(metric.oversize)),
                    ether_table + (11, index): _constant(p.Counter32(metric.fragments)),
                    ether_table + (12, index): _constant(p.Counter32(metric.jabbers)),
                    ether_table + (13, index): _constant(p.Counter32(metric.collisions)),
                }
            )
    return values


class SnmpV2cProtocol(asyncio.DatagramProtocol):
    def __init__(self, community: str, tree: OidTree) -> None:
        self.community = community
        self.tree = tree
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, address: tuple[str, int]) -> None:
        try:
            response = self._respond(data)
            if response is not None and self.transport is not None:
                self.transport.sendto(response, address)
        except Exception:
            return

    def _respond(self, data: bytes) -> bytes | None:
        if api.decodeMessageVersion(data) != api.SNMP_VERSION_2C:
            return None
        p = api.PROTOCOL_MODULES[api.SNMP_VERSION_2C]
        request, trailing = decoder.decode(data, asn1Spec=p.Message())
        if trailing or str(p.apiMessage.get_community(request)) != self.community:
            return None
        request_pdu = p.apiMessage.get_pdu(request)
        response = p.apiMessage.get_response(request)
        response_pdu = p.apiMessage.get_pdu(response)
        request_varbinds = p.apiPDU.get_varbinds(request_pdu)

        if request_pdu.isSameTypeWith(p.SetRequestPDU()):
            p.apiPDU.set_error_status(response_pdu, 17)
            p.apiPDU.set_error_index(response_pdu, 1 if request_varbinds else 0)
            p.apiPDU.set_varbinds(response_pdu, request_varbinds)
            return encoder.encode(response)

        if request_pdu.isSameTypeWith(p.GetRequestPDU()):
            result = []
            for oid, _ in request_varbinds:
                oid_tuple = tuple(int(part) for part in oid)
                value = self.tree.get(oid_tuple)
                result.append((oid, value if value is not None else p.NoSuchInstance("")))
        elif request_pdu.isSameTypeWith(p.GetNextRequestPDU()):
            result = [self._next_varbind(p, oid) for oid, _ in request_varbinds]
        elif request_pdu.isSameTypeWith(p.GetBulkRequestPDU()):
            non_repeaters = min(p.apiBulkPDU.get_non_repeaters(request_pdu), len(request_varbinds))
            max_repetitions = min(p.apiBulkPDU.get_max_repetitions(request_pdu), 100)
            result = [self._next_varbind(p, oid) for oid, _ in request_varbinds[:non_repeaters]]
            cursors = [
                tuple(int(part) for part in oid) for oid, _ in request_varbinds[non_repeaters:]
            ]
            for _ in range(max_repetitions):
                for position, cursor in enumerate(cursors):
                    item = self.tree.next(cursor)
                    if item is None:
                        result.append((p.ObjectIdentifier(cursor), p.EndOfMibView("")))
                    else:
                        next_oid, value = item
                        cursors[position] = next_oid
                        result.append((p.ObjectIdentifier(next_oid), value))
        else:
            return None
        p.apiPDU.set_varbinds(response_pdu, result)
        return encoder.encode(response)

    def _next_varbind(self, p: Any, oid: Any) -> tuple[Any, Any]:
        oid_tuple = tuple(int(part) for part in oid)
        item = self.tree.next(oid_tuple)
        if item is None:
            return p.ObjectIdentifier(oid_tuple), p.EndOfMibView("")
        next_oid, value = item
        return p.ObjectIdentifier(next_oid), value


class SnmpAgent:
    def __init__(self, host: str, port: int, community: str) -> None:
        self.host = host
        self.port = port
        self.community = community
        self.tree = OidTree()
        self.started_at = time.monotonic()
        self._transport: asyncio.DatagramTransport | None = None

    def update(self, state: SwitchState, metrics: SwitchMetrics, system: SnmpSystemConfig) -> None:
        self.tree.replace(
            build_oid_values(
                state,
                metrics,
                started_at=self.started_at,
                system=system,
            )
        )

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: SnmpV2cProtocol(self.community, self.tree),
            local_addr=(self.host, self.port),
        )
        self._transport = transport

    def close(self) -> None:
        if self._transport:
            self._transport.close()
