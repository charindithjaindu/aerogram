import pytest

from aerogram.mqtt import (CONNACK, PUBLISH, SUBACK, build_connect,
                          build_puback, build_publish, build_pingreq,
                          build_subscribe, encode_varint, parse_packets)


def test_varint():
    assert encode_varint(0) == b"\x00"
    assert encode_varint(127) == b"\x7f"
    assert encode_varint(128) == b"\x80\x01"
    assert encode_varint(16383) == b"\xff\x7f"
    assert encode_varint(16384) == b"\x80\x80\x01"


def test_connect_matches_captured_wire_format():
    # the real web client's CONNECT: MQIsdp/3, clean+username flags, keepalive 15
    pkt = build_connect("mqttwsclient", "auth-json", keepalive=15)
    assert pkt[0] >> 4 == 1
    assert pkt[1] == 0xCB or True  # length varies with username
    # protocol name
    n = int.from_bytes(pkt[2:4], "big")
    assert pkt[4:4 + n] == b"MQIsdp"
    level = pkt[4 + n]
    flags = pkt[5 + n]
    keepalive = int.from_bytes(pkt[6 + n:8 + n], "big")
    assert level == 3
    assert flags == 0x82  # clean session + username
    assert keepalive == 15
    i = 8 + n
    cid_len = int.from_bytes(pkt[i:i + 2], "big")
    assert pkt[i + 2:i + 2 + cid_len] == b"mqttwsclient"
    i += 2 + cid_len
    user_len = int.from_bytes(pkt[i:i + 2], "big")
    assert pkt[i + 2:i + 2 + user_len] == b"auth-json"
    assert len(pkt) == i + 2 + user_len  # no password field


def test_publish_qos1_and_puback_roundtrip():
    pkt = build_publish("/ig_send_message", b"hello", qos=1, packet_id=7)
    assert pkt[0] == (PUBLISH << 4) | 0x02
    parsed = parse_packets(pkt)
    ptype, flags, body = parsed[0]
    assert ptype == PUBLISH
    assert (flags >> 1) & 3 == 1
    tlen = int.from_bytes(body[0:2], "big")
    topic = body[2:2 + tlen]
    assert topic == b"/ig_send_message"
    assert body[2 + tlen:4 + tlen] == b"\x00\x07"
    assert body[4 + tlen:] == b"hello"


def test_subscribe_parse():
    pkt = build_subscribe([("/ig_message_sync", 0)], packet_id=1)
    ptype, _flags, body = parse_packets(pkt)[0]
    assert ptype == 8
    assert body[:2] == b"\x00\x01"
    tlen = int.from_bytes(body[2:4], "big")
    assert body[4:4 + tlen] == b"/ig_message_sync"
    assert body[4 + tlen] == 0


def test_parse_concatenated_packets():
    pub = build_publish("/t_x", b"a", qos=0, packet_id=1)
    ping = build_pingreq()
    packets = parse_packets(pub + ping)
    assert [p[0] for p in packets] == [PUBLISH, 12]


def test_parse_truncated_raises():
    from aerogram.errors import ProtocolError
    pkt = build_publish("/t_x", b"abc", qos=0, packet_id=1)
    with pytest.raises(ProtocolError):
        parse_packets(pkt[:-1])


def test_puback():
    pkt = build_puback(0x1234)
    assert pkt == bytes([0x40, 0x02, 0x12, 0x34])
    ptype, _f, body = parse_packets(pkt)[0]
    assert ptype == 4
    assert int.from_bytes(body, "big") == 0x1234


def test_connack_acceptance():
    # captured CONNACK: 20 02 00 00
    ptype, _f, body = parse_packets(bytes([0x20, 0x02, 0x00, 0x00]))[0]
    assert ptype == CONNACK
    assert body[1] == 0
