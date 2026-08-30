from __future__ import annotations

import time

from pyasn1.codec.ber import decoder, encoder
from pysnmp.proto import api

from mercswitch.models import PortMetrics, SwitchMetrics
from mercswitch.snmp_agent import OidTree, SnmpSystemConfig, SnmpV2cProtocol, build_oid_values


def request_bytes(pdu, varbinds):
    p = api.PROTOCOL_MODULES[api.SNMP_VERSION_2C]
    p.apiPDU.set_defaults(pdu)
    p.apiPDU.set_varbinds(pdu, varbinds)
    message = p.Message()
    p.apiMessage.set_defaults(message)
    p.apiMessage.set_community(message, "public")
    p.apiMessage.set_pdu(message, pdu)
    return encoder.encode(message)


def response_for(protocol, payload):
    p = api.PROTOCOL_MODULES[api.SNMP_VERSION_2C]
    encoded = protocol._respond(payload)
    assert encoded is not None
    message, trailing = decoder.decode(encoded, asn1Spec=p.Message())
    assert not trailing
    return p.apiMessage.get_pdu(message)


def protocol_for(sample_state):
    metrics = SwitchMetrics(
        sample_state.identity,
        {
            1: PortMetrics(
                1,
                True,
                True,
                1000,
                rx_good=12,
                tx_good=14,
                rx_bad=1,
                crc_errors=1,
                detail_available=True,
            )
        },
    )
    tree = OidTree()
    tree.replace(
        build_oid_values(
            sample_state,
            metrics,
            started_at=time.monotonic(),
            system=SnmpSystemConfig(name="lab-switch"),
        )
    )
    return SnmpV2cProtocol("public", tree)


def test_get_getnext_and_set_rejection(sample_state):
    p = api.PROTOCOL_MODULES[api.SNMP_VERSION_2C]
    protocol = protocol_for(sample_state)
    name_oid = p.ObjectIdentifier((1, 3, 6, 1, 2, 1, 1, 5, 0))

    get_response = response_for(
        protocol, request_bytes(p.GetRequestPDU(), [(name_oid, p.Null(""))])
    )
    assert str(p.apiPDU.get_varbinds(get_response)[0][1]) == "lab-switch"

    next_response = response_for(
        protocol,
        request_bytes(
            p.GetNextRequestPDU(),
            [(p.ObjectIdentifier((1, 3, 6, 1, 2, 1, 1, 4, 0)), p.Null(""))],
        ),
    )
    assert tuple(p.apiPDU.get_varbinds(next_response)[0][0]) == tuple(name_oid)

    set_response = response_for(
        protocol, request_bytes(p.SetRequestPDU(), [(name_oid, p.OctetString("bad"))])
    )
    assert int(p.apiPDU.get_error_status(set_response)) == 17


def test_getbulk_walk(sample_state):
    p = api.PROTOCOL_MODULES[api.SNMP_VERSION_2C]
    protocol = protocol_for(sample_state)
    pdu = p.GetBulkRequestPDU()
    p.apiBulkPDU.set_defaults(pdu)
    p.apiBulkPDU.set_non_repeaters(pdu, 0)
    p.apiBulkPDU.set_max_repetitions(pdu, 5)
    p.apiPDU.set_varbinds(pdu, [(p.ObjectIdentifier((1, 3, 6, 1, 2, 1, 1)), p.Null(""))])
    message = p.Message()
    p.apiMessage.set_defaults(message)
    p.apiMessage.set_community(message, "public")
    p.apiMessage.set_pdu(message, pdu)
    response = response_for(protocol, encoder.encode(message))
    oids = [tuple(oid) for oid, _ in p.apiPDU.get_varbinds(response)]
    assert len(oids) == 5
    assert oids == sorted(oids)


def test_never_exports_octets_or_unavailable_detailed_counters(sample_state):
    metrics = SwitchMetrics(
        sample_state.identity,
        {1: PortMetrics(1, True, True, 1000, rx_good=12, tx_good=14)},
    )
    values = build_oid_values(
        sample_state,
        metrics,
        started_at=time.monotonic(),
        system=SnmpSystemConfig(),
    )
    assert (1, 3, 6, 1, 2, 1, 2, 2, 1, 10, 1) not in values  # ifInOctets
    assert (1, 3, 6, 1, 2, 1, 2, 2, 1, 16, 1) not in values  # ifOutOctets
    assert (1, 3, 6, 1, 2, 1, 2, 2, 1, 11, 1) not in values  # ifInUcastPkts
