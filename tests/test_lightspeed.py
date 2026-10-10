import base64
import json
import struct

import pytest

from aerogram.client import Client
from aerogram.lightspeed import (FRAME_ACK, FRAME_DATA, FRAME_ESTAB, FRAME_PING, FRAME_PONG,
                                 decode_frames, encode_frame, extract_deltas)
from aerogram.types import Thread


def _varint(n):
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def _field(num, data):
    return _varint(num << 3 | 2) + _varint(len(data)) + data


def pushed_payload(deltas, seq=100):
    """Shape of a real lightspeed push: protobuf { 1: 0600, 2: { 3: { 1: 223,
    2: <graphql json>, ... cursors } } }, base64'd."""
    doc = json.dumps([{"data": {"slide_delta_processor": deltas}}]).encode()
    inner = _varint(1 << 3) + _varint(223) + _field(2, doc) + _field(5, b'{"seq_id":%d}' % seq)
    return base64.b64encode(_field(1, b"\x06\x00") + _field(2, _field(3, inner))).decode()


def test_frame_roundtrip():
    data = encode_frame(FRAME_DATA, struct.pack("<H", 0x8001) + b'{"a":1}')
    assert data[:6] == bytes([13, 0, 0, 9, 0, 0])
    buf = encode_frame(FRAME_ESTAB, b"{}") + data + encode_frame(FRAME_PING) + encode_frame(FRAME_PONG)
    frames = list(decode_frames(buf))
    assert [f[0] for f in frames] == [FRAME_ESTAB, FRAME_DATA, FRAME_PING, FRAME_PONG]
    assert frames[1][2] == struct.pack("<H", 0x8001) + b'{"a":1}'
    assert encode_frame(FRAME_ACK, b"\x01\x00") == bytes([12, 0, 0, 2, 0, 0, 1, 0])


def test_extract_deltas_from_protobuf_wrapper():
    deltas = [{"__typename": "SlideUQPPNewMessage", "uq_seq_id": "101"}]
    assert extract_deltas(pushed_payload(deltas)) == deltas
    # the cursor-ack answer to the sync request carries no JSON
    assert extract_deltas("CgIGABINCgsI3wEQ1o0BGNaNARoA") == []


def new_message_delta(fbid="800", mid="mid.$1", otid="555", sender="42", text="yo"):
    return {"__typename": "SlideUQPPNewMessage", "uq_seq_id": "101", "thread_fbid": fbid,
            "message": {"thread_fbid": fbid, "message_id": mid, "id": mid,
                        "offline_threading_id": otid, "timestamp_ms": "1700000000000",
                        "content_type": "TEXT", "text_body": text,
                        "content": {"__typename": "SlideMessageText", "text_body": text},
                        "sender": {"igid": sender}}}


@pytest.mark.asyncio
async def test_slide_new_message_dispatches_once_with_thread(tmp_path):
    c = Client("t", cookies={"sessionid": "s", "csrftoken": "c", "ds_user_id": "777"},
               workdir=str(tmp_path))
    c._store_thread(Thread(id="111", v2_id="900", fbid="800"))
    got = []

    @c.on_message()
    async def h(client, m):
        got.append(m)

    await c._handle_slide_delta(new_message_delta())
    await c._handle_slide_delta(new_message_delta())  # duplicate push
    await c.dispatcher.wait()
    assert len(got) == 1
    m = got[0]
    assert (m.thread_id, m.thread_fbid, m.text, m.user_id) == ("111", "800", "yo", "42")
    assert m.thread is c._threads["111"] and not m.is_sent_by_viewer


@pytest.mark.asyncio
async def test_slide_message_for_unknown_thread_replies_by_fbid(tmp_path):
    c = Client("t", cookies={"sessionid": "s", "csrftoken": "c", "ds_user_id": "777"},
               workdir=str(tmp_path))

    async def inbox():
        return {"id": "mbox", "threads_by_folder": {"edges": [], "page_info": {}}}

    sent = {}

    async def send_text_message(text, thread_fbid=None, **kw):
        sent.update(text=text, thread_fbid=thread_fbid)
        return {"data": {"xig_direct_text_send_with_slide_messaging_response": {"message_id": "mid.$r"}}}

    c.api.inbox = inbox
    c.api.send_text_message = send_text_message
    got = []

    @c.on_message()
    async def h(client, m):
        got.append(m)

    await c._handle_slide_delta(new_message_delta(fbid="999", mid="mid.$2", otid="556"))
    await c.dispatcher.wait()
    assert got[0].thread_id == "" and got[0].thread_fbid == "999"
    await got[0].reply_text("hi")
    assert sent == {"text": "hi", "thread_fbid": "999"}
