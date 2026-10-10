"""Realtime *receive* channel: the web client's DGW "lightspeed" stream.

Instagram's web client gets incoming DMs here, not over the edge-chat MQTT
iris subscription (that socket takes ``/ig_send_message`` sends; its
pushes, when it sends any, are partial). Messages arrive on ``wss://gateway.instagram.com/ws/
lightspeed`` as *slide deltas* - the same JSON node shapes the GraphQL
queries return - wrapped in a tiny binary framing ("DGW") and protobuf.

DGW frame: ``type:u8 | stream:u16le | length:u24le | payload``, except the
one-byte ``Ping`` (9) / ``Pong`` (10) / ``Empty`` (2) frames. Data frames
(13) start with ``ack:u16le`` (bit 15 = "please ack", answered by an Ack
frame (12) carrying the 15-bit id). A stream opens with an EstabStream
frame (15) whose payload is the JSON header object (``{}`` here).

On the lightspeed stream the client sends one sync request carrying the
iris cursor (``seq_id``) and the server answers with a cursor ack, then
pushes ``slide_delta_processor`` payloads as deltas happen. Each delta has
``uq_seq_id``; resuming with the last one is gap-free.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import struct
import time
import uuid
from typing import Any, Awaitable, Callable, Iterator, Optional

import websockets

from .session import Session

log = logging.getLogger("aerogram.lightspeed")

APP_ID = "936619743392459"
URL = "wss://gateway.instagram.com/ws/lightspeed"
DELTA_DOC_ID = "28859056920377149"  # IGDSlideDeltaProcessorQuery
DATABASE = 223

FRAME_EMPTY, FRAME_PING, FRAME_PONG = 2, 9, 10
FRAME_ACK, FRAME_DATA, FRAME_END, FRAME_ESTAB = 12, 13, 14, 15
_ONE_BYTE_FRAMES = {FRAME_EMPTY, FRAME_PING, FRAME_PONG}

PING_INTERVAL = 10.0
RX_TIMEOUT = 45.0  # no frame (not even a pong) for this long => reconnect

DeltaCallback = Callable[[dict], Awaitable[None]]


# -- codec ---------------------------------------------------------------------

def encode_frame(ftype: int, payload: bytes = b"", stream: int = 0) -> bytes:
    if ftype in _ONE_BYTE_FRAMES:
        return bytes([ftype])
    return bytes([ftype]) + struct.pack("<H", stream) + len(payload).to_bytes(3, "little") + payload


def decode_frames(buf: bytes) -> Iterator[tuple[int, int, bytes]]:
    """Yield ``(type, stream, payload)`` for each frame in a WS message."""
    i = 0
    while i < len(buf):
        ftype = buf[i]
        if ftype in _ONE_BYTE_FRAMES:
            yield ftype, 0, b""
            i += 1
            continue
        if i + 6 > len(buf):
            raise ValueError("truncated DGW frame header")
        stream = struct.unpack_from("<H", buf, i + 1)[0]
        n = int.from_bytes(buf[i + 3:i + 6], "little")
        yield ftype, stream, buf[i + 6:i + 6 + n]
        i += 6 + n


def _varint(b: bytes, i: int) -> tuple[int, int]:
    n = shift = 0
    while True:
        c = b[i]
        i += 1
        n |= (c & 0x7F) << shift
        shift += 7
        if c < 0x80:
            return n, i


def protobuf_blobs(b: bytes) -> Iterator[bytes]:
    """Every length-delimited field in a protobuf message, depth-first.
    The lightspeed payloads are small wrappers around a JSON string, so a
    schema-less walk is enough to find it."""
    i = 0
    while i < len(b):
        key, i = _varint(b, i)
        wire = key & 7
        if wire == 0:
            _, i = _varint(b, i)
        elif wire == 1:
            i += 8
        elif wire == 5:
            i += 4
        elif wire == 2:
            n, i = _varint(b, i)
            chunk = b[i:i + n]
            i += n
            yield chunk
            try:
                yield from protobuf_blobs(chunk)
            except (IndexError, ValueError):
                pass  # a string, not a nested message
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")


def extract_deltas(payload_b64: str) -> list[dict]:
    """The ``slide_delta_processor`` entries inside a pushed payload."""
    deltas: list[dict] = []
    for blob in protobuf_blobs(base64.b64decode(payload_b64)):
        if not blob.startswith((b"[{", b'{"')):
            continue
        try:
            doc = json.loads(blob)
        except ValueError:
            continue
        for part in doc if isinstance(doc, list) else [doc]:
            deltas.extend(((part.get("data") or {}).get("slide_delta_processor")) or [])
        break
    return deltas


# -- client --------------------------------------------------------------------

class Lightspeed:
    """Maintains the lightspeed stream and hands every slide delta to
    ``on_delta``. Tracks ``session.seq_id`` so reconnects resume gap-free."""

    def __init__(self, session: Session, user_agent: str, on_delta: DeltaCallback,
                 max_backoff: float = 60.0) -> None:
        self._session = session
        self._ua = user_agent
        self._on_delta = on_delta
        self._max_backoff = max_backoff
        self._task: Optional[asyncio.Task] = None
        self._ws: Any = None
        self._request_id = 0
        self._ack_id = 0
        self.connected = False

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_forever(), name="aerogram-lightspeed")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self.connected = False

    async def _run_forever(self) -> None:
        backoff = 1.0
        while True:
            started = time.monotonic()
            try:
                await self._connect_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("lightspeed connection lost: %s: %s", type(e).__name__, e)
            self.connected = False
            if time.monotonic() - started > 60:
                backoff = 1.0  # it was a healthy connection; reconnect quickly
            sleep = backoff + random.uniform(0, backoff * 0.3)
            log.info("reconnecting lightspeed in %.1fs", sleep)
            await asyncio.sleep(sleep)
            backoff = min(backoff * 2, self._max_backoff)

    def _url(self, device_id: str) -> str:
        return (f"{URL}?x-dgw-appid={APP_ID}&x-dgw-appversion=0&x-dgw-authtype=6:0"
                f"&x-dgw-version=5&x-dgw-uuid={self._session.viewer_uuid()}"
                f"&x-dgw-tier=prod&x-dgw-deviceid={device_id}")

    def _data(self, body: dict) -> bytes:
        ack = 0x8000 | (self._ack_id & 0x7FFF)
        self._ack_id += 1
        return encode_frame(FRAME_DATA, struct.pack("<H", ack) + _compact(body))

    def _sync_request(self, device_id: str) -> dict:
        self._request_id += 1
        sync = {
            "database": DATABASE, "epoch_id": 0, "failure_count": 0,
            "last_applied_cursor": _compact({"seq_id": int(self._session.seq_id or 0)}).decode(),
            "sync_params": _compact({
                "user_agent": "WMI Web",
                "snapshot_at_ms": int(self._session.snapshot_at_ms or 0),
                "prevalidated_graphql_doc_id": DELTA_DOC_ID,
            }).decode(),
            "version": -3,
        }
        return {"app_id": APP_ID, "device_id": device_id, "payload": _compact(sync).decode(),
                "request_id": self._request_id, "type": 2}

    async def _connect_once(self) -> None:
        device_id = str(uuid.uuid4())  # the web client uses a fresh one per socket
        headers = {"Origin": "https://www.instagram.com", "User-Agent": self._ua,
                   "Cookie": self._session.cookie_header}
        async with websockets.connect(self._url(device_id), additional_headers=headers,
                                      max_size=None, ping_interval=None) as ws:
            self._ws = ws
            self._ack_id = 0
            await ws.send(encode_frame(FRAME_ESTAB, b"{}") + self._data(self._sync_request(device_id)))
            pinger = asyncio.create_task(self._ping_loop(ws))
            try:
                while True:
                    msg = await asyncio.wait_for(ws.recv(), RX_TIMEOUT)
                    if isinstance(msg, str):
                        msg = msg.encode()
                    for ftype, stream, payload in decode_frames(msg):
                        await self._on_frame(ws, ftype, stream, payload)
            finally:
                pinger.cancel()
                self._ws = None

    async def _ping_loop(self, ws) -> None:
        while True:
            await asyncio.sleep(PING_INTERVAL)
            await ws.send(encode_frame(FRAME_PING))

    async def _on_frame(self, ws, ftype: int, stream: int, payload: bytes) -> None:
        if ftype == FRAME_ESTAB:
            status = json.loads(payload or b"{}").get("code")
            if status != 200:
                raise ConnectionError(f"lightspeed stream rejected: {payload[:200]!r}")
            self.connected = True
            log.info("lightspeed connected (seq_id %s)", self._session.seq_id)
        elif ftype == FRAME_DATA:
            ack = struct.unpack_from("<H", payload)[0]
            if ack & 0x8000:
                await ws.send(encode_frame(FRAME_ACK, struct.pack("<H", ack & 0x7FFF), stream))
            body = json.loads(payload[2:])
            if isinstance(body.get("payload"), str):
                for delta in extract_deltas(body["payload"]):
                    seq = _int(delta.get("uq_seq_id"))
                    if seq and seq > int(self._session.seq_id or 0):
                        self._session.seq_id = seq
                    try:
                        await self._on_delta(delta)
                    except Exception:
                        log.exception("lightspeed delta handler failed")
        elif ftype == FRAME_END:
            raise ConnectionError("lightspeed stream ended by server")
        # ACK / PONG / EMPTY need no action


def _compact(obj: Any) -> bytes:
    return json.dumps(obj, separators=(",", ":")).encode()


def _int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
