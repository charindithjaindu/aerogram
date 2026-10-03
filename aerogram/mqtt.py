"""Minimal MQTT 3.1 (MQIsdp) client over WebSocket, tuned for Instagram's broker.

Implements exactly the subset Instagram's web client speaks:

* CONNECT with a custom ``username`` payload (the auth JSON), clean session
* SUBSCRIBE / SUBSCRIBE (QoS 0 grants), PUBLISH (QoS 0 and 1) with PUBACK
* PINGREQ/PINGRESP keepalive, DISCONNECT
* asyncio-native, automatic reconnect with exponential backoff + jitter

The framing follows MQTT 3.1.1 wire format; the protocol name is "MQIsdp" with
level 3 (MQTT 3.1), which is what the broker requires.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Awaitable, Callable

import websockets

from .errors import ProtocolError, RealtimeError

log = logging.getLogger("aerogram.mqtt")

CONNECT, CONNACK, PUBLISH, PUBACK = 1, 2, 3, 4
SUBSCRIBE, SUBACK, PINGREQ, PINGRESP, DISCONNECT = 8, 9, 12, 13, 14

CONNACK_RC = {
    0: "accepted",
    1: "unacceptable protocol version",
    2: "identifier rejected",
    3: "server unavailable",
    4: "bad username/password",
    5: "not authorized",
}

KeepaliveCallback = Callable[[], Awaitable[None]]
PacketCallback = Callable[[int, bytes, int], Awaitable[None]]  # (packet_type, body, flags)
ReconnectCallback = Callable[[bool], Awaitable[None]]  # is_reconnect


def encode_varint(n: int) -> bytes:
    out = bytearray()
    while True:
        byte = n % 128
        n //= 128
        if n:
            byte |= 0x80
        out.append(byte)
        if not n:
            return bytes(out)


def _enc_str(s: str) -> bytes:
    b = s.encode()
    if len(b) > 0xFFFF:
        raise ValueError("string too long for MQTT")
    return len(b).to_bytes(2, "big") + b


def build_connect(client_id: str, username: str | None, keepalive: int = 15) -> bytes:
    flags = 0x02  # clean session
    payload = _enc_str(client_id)
    if username is not None:
        flags |= 0x80
        payload += _enc_str(username)
    variable = _enc_str("MQIsdp") + bytes([3, flags]) + keepalive.to_bytes(2, "big")
    body = variable + payload
    return bytes([CONNECT << 4]) + encode_varint(len(body)) + body


def build_publish(topic: str, payload: bytes, qos: int, packet_id: int) -> bytes:
    if qos not in (0, 1):
        raise ValueError("only QoS 0/1 supported")
    body = _enc_str(topic)
    flags = qos << 1
    if qos:
        body += packet_id.to_bytes(2, "big")
    body += payload
    return bytes([(PUBLISH << 4) | flags]) + encode_varint(len(body)) + body


def build_puback(packet_id: int) -> bytes:
    return bytes([PUBACK << 4, 2]) + packet_id.to_bytes(2, "big")


def build_subscribe(topics: list[tuple[str, int]], packet_id: int) -> bytes:
    body = packet_id.to_bytes(2, "big")
    for topic, qos in topics:
        body += _enc_str(topic) + bytes([qos])
    return bytes([(SUBSCRIBE << 4) | 0x02]) + encode_varint(len(body)) + body


def build_pingreq() -> bytes:
    return bytes([PINGREQ << 4, 0])


def build_disconnect() -> bytes:
    return bytes([DISCONNECT << 4, 0])


def parse_packets(data: bytes) -> list[tuple[int, int, bytes]]:
    """Parse one or more concatenated MQTT packets from a websocket message."""
    packets: list[tuple[int, int, bytes]] = []
    i = 0
    while i < len(data):
        first = data[i]
        ptype = first >> 4
        flags = first & 0x0F
        i += 1
        mult, rl = 1, 0
        while True:
            if i >= len(data):
                raise ProtocolError("truncated MQTT packet")
            byte = data[i]
            i += 1
            rl += (byte & 0x7F) * mult
            if not byte & 0x80:
                break
            mult *= 128
        body = data[i : i + rl]
        if len(body) < rl:
            raise ProtocolError("truncated MQTT packet body")
        i += rl
        packets.append((ptype, flags, body))
    return packets


class MqttClient:
    """Async MQTT 3.1 client for ``wss://edge-chat.instagram.com/chat``.

    ``on_packet`` is awaited for every PUBLISH (topic, payload, qos).
    Reconnects automatically; ``on_reconnect(True)`` fires after every
    successful re-establishment so callers can re-subscribe.
    """

    def __init__(
        self,
        url: str,
        ws_headers: dict[str, str],
        username: str | None,
        on_packet: PacketCallback,
        on_reconnect: ReconnectCallback | None = None,
        client_id: str = "mqttwsclient",
        keepalive: int = 15,
        max_backoff: float = 60.0,
    ) -> None:
        self._url = url
        self._headers = ws_headers
        self._username = username
        self._client_id = client_id
        self._keepalive = keepalive
        self._on_packet = on_packet
        self._on_reconnect = on_reconnect
        self._max_backoff = max_backoff

        self._ws: websockets.WebSocketClientProtocol | None = None  # type: ignore[attr-defined]
        self._send_lock = asyncio.Lock()
        self._packet_id = 0
        self._subscriptions: set[str] = set()
        self._closing = False
        self._task: asyncio.Task | None = None
        self._puback_events: dict[int, asyncio.Event] = {}

    # -- public API ---------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._closing

    def start(self) -> asyncio.Task:
        self._closing = False
        self._task = asyncio.create_task(self._run_forever(), name="aerogram-mqtt")
        return self._task

    async def stop(self) -> None:
        self._closing = True
        if self._ws is not None:
            try:
                await self._send(build_disconnect())
            except Exception:
                pass
            await self._ws.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def subscribe(self, topic: str) -> None:
        self._subscriptions.add(topic)
        if self.connected:
            await self._send(build_subscribe([(topic, 0)], self._next_packet_id()))
            log.debug("SUBSCRIBE %s", topic)

    async def publish(self, topic: str, payload: bytes | str, qos: int = 1,
                      await_puback: bool = False) -> None:
        """Publish. The broker generally does not ack QoS-1 publishes on the
        /ig_* topics — application-level response topics are the real ack, so
        blocking on PUBACK is opt-in."""
        if isinstance(payload, str):
            payload = payload.encode()
        pid = self._next_packet_id()
        await self._send(build_publish(topic, payload, qos, pid))
        if qos and await_puback:
            event = asyncio.Event()
            self._puback_events[pid] = event
            try:
                await asyncio.wait_for(event.wait(), timeout=10)
            except asyncio.TimeoutError:
                log.warning("No PUBACK for packet id %s on %s", pid, topic)
            finally:
                self._puback_events.pop(pid, None)

    # -- internals ----------------------------------------------------------

    def _next_packet_id(self) -> int:
        self._packet_id = self._packet_id % 0xFFFF + 1
        return self._packet_id

    async def _send(self, data: bytes) -> None:
        async with self._send_lock:
            ws = self._ws
            if ws is None:
                raise RealtimeError("MQTT not connected")
            await ws.send(data)

    async def _run_forever(self) -> None:
        backoff = 1.0
        first = True
        while not self._closing:
            try:
                await self._connect_once(first)
                first = False
                backoff = 1.0
                await self._read_loop()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if self._closing:
                    return
                log.warning("MQTT connection lost: %s: %s", type(e).__name__, e)
            if self._closing:
                return
            sleep = backoff + random.uniform(0, backoff * 0.3)
            log.info("Reconnecting MQTT in %.1fs", sleep)
            await asyncio.sleep(sleep)
            backoff = min(backoff * 2, self._max_backoff)

    async def _connect_once(self, first: bool) -> None:
        log.info("Connecting to %s", self._url.split("?")[0])
        ws = await websockets.connect(
            self._url,
            additional_headers=self._headers,
            open_timeout=15,
            close_timeout=5,
            max_size=20 * 1024 * 1024,
            ping_interval=None,  # MQTT-level ping instead of WS ping
        )
        self._ws = ws
        await ws.send(build_connect(self._client_id, self._username, self._keepalive))
        raw = await asyncio.wait_for(ws.recv(), timeout=15)
        packets = parse_packets(raw.encode() if isinstance(raw, str) else raw)
        ptype, _flags, body = packets[0]
        if ptype != CONNACK:
            raise ProtocolError(f"expected CONNACK, got packet type {ptype}")
        rc = body[1] if len(body) > 1 else -1
        if rc != 0:
            raise RealtimeError(f"MQTT CONNACK rejected: rc={rc} ({CONNACK_RC.get(rc, '?')})")
        log.info("MQTT connected (CONNACK rc=0)")
        for topic in self._subscriptions:
            await self._send(build_subscribe([(topic, 0)], self._next_packet_id()))
            log.debug("SUBSCRIBE %s", topic)
        if self._on_reconnect:
            await self._on_reconnect(not first)

    async def _read_loop(self) -> None:
        ws = self._ws
        assert ws is not None
        last_ping = time.monotonic()
        ping_task = asyncio.create_task(self._ping_loop(lambda: last_ping))
        try:
            while True:
                raw = await ws.recv()
                if isinstance(raw, str):
                    if raw:
                        log.debug("Ignoring text frame: %r", raw[:100])
                    continue
                for ptype, flags, body in parse_packets(raw):
                    if ptype == PUBLISH:
                        await self._handle_publish(flags, body)
                    elif ptype == PUBACK:
                        pid = int.from_bytes(body[:2], "big")
                        ev = self._puback_events.get(pid)
                        if ev:
                            ev.set()
                    elif ptype == PINGRESP:
                        log.debug("PINGRESP")
                    elif ptype == DISCONNECT:
                        raise RealtimeError("Broker sent DISCONNECT")
                    else:
                        log.debug("Unhandled MQTT packet type %s", ptype)
        finally:
            ping_task.cancel()
            self._ws = None
            try:
                await ws.close()
            except Exception:
                pass

    async def _ping_loop(self, _last_ping) -> None:
        # The web client pings on a fixed cadence matching the keepalive.
        try:
            while True:
                await asyncio.sleep(self._keepalive)
                await self._send(build_pingreq())
        except asyncio.CancelledError:
            pass

    async def _handle_publish(self, flags: int, body: bytes) -> None:
        qos = (flags >> 1) & 0x03
        tlen = int.from_bytes(body[0:2], "big")
        topic = body[2 : 2 + tlen].decode(errors="replace")
        rest = body[2 + tlen :]
        pid = None
        if qos:
            pid = int.from_bytes(rest[:2], "big")
            rest = rest[2:]
        try:
            await self._on_packet(topic, rest, qos)
        except Exception:
            log.exception("on_packet handler failed for %s", topic)
        if pid is not None:
            await self._send(build_puback(pid))
