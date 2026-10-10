"""Instagram realtime: iris message sync over the web MQTT transport.

This replicates exactly what instagram.com does:

1. connect MQTT 3.1 to ``wss://edge-chat.instagram.com/chat?sid=<rand>&cid=<uuid>``
   with the cookie-auth JSON as the MQTT username,
2. SUBSCRIBE ``/ig_message_sync``, ``/ig_send_message_response``, ``/ig_sub_iris_response``,
3. PUBLISH the iris subscribe request to ``/ig_sub_iris`` with the last known
   ``seq_id`` — the broker then replays everything missed since that sequence,
   giving gap-free delivery across reconnects and restarts,
4. DM actions (send text/like/media-share/reaction, typing) are published to
   ``/ig_send_message``; ``/ig_send_message_response`` confirms them.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from .errors import SendError
from .http_api import new_client_context
from .mqtt import MqttClient
from .session import Session

log = logging.getLogger("aerogram.iris")

APP_ID = 936619743392459
GATEWAY = "wss://edge-chat.instagram.com/chat"

TOPIC_MESSAGE_SYNC = "/ig_message_sync"
TOPIC_SEND_RESPONSE = "/ig_send_message_response"
TOPIC_IRIS_SUB = "/ig_sub_iris"
TOPIC_IRIS_SUB_RESPONSE = "/ig_sub_iris_response"

RE_THREAD_ITEM = re.compile(r"^/direct_v2/threads/(\d+)/items/([^/]+)$")
RE_THREAD_ITEM_SUB = re.compile(r"^/direct_v2/threads/(\d+)/items/([^/]+)/")
RE_INBOX_THREAD = re.compile(r"^/direct_v2/inbox/threads/(\d+)$")
RE_UNSEEN_COUNT = re.compile(r"^/direct_v2/inbox/unseen_count$")

DeltaCallback = Callable[["Delta"], Awaitable[None]]


@dataclass
class Delta:
    """One iris patch operation."""

    op: str                      # add | replace | remove | ...
    path: str
    value: dict | list | str | None = None
    mutation_token: str | None = None
    seq_id: int = 0

    # parsed convenience
    thread_id: str = ""
    item_id: str = ""

    def parse_path(self) -> None:
        m = RE_THREAD_ITEM.match(self.path)
        if m:
            self.thread_id, self.item_id = m.group(1), m.group(2)
            return
        m = RE_THREAD_ITEM_SUB.match(self.path)
        if m:
            self.thread_id, self.item_id = m.group(1), m.group(2)
            return
        m = RE_INBOX_THREAD.match(self.path)
        if m:
            self.thread_id = m.group(1)

    @property
    def is_new_message(self) -> bool:
        return self.op in ("add", "replace") and bool(RE_THREAD_ITEM.match(self.path))

    @property
    def is_removed_message(self) -> bool:
        return self.op == "remove" and bool(RE_THREAD_ITEM.match(self.path))

    @property
    def is_item_update(self) -> bool:
        return bool(RE_THREAD_ITEM_SUB.match(self.path))

    @property
    def is_thread_update(self) -> bool:
        return bool(RE_INBOX_THREAD.match(self.path))

    @property
    def is_unseen_count(self) -> bool:
        return bool(RE_UNSEEN_COUNT.match(self.path))

    def value_as_dict(self) -> dict:
        if isinstance(self.value, str):
            try:
                parsed = json.loads(self.value)
                return parsed if isinstance(parsed, dict) else {"value": parsed}
            except json.JSONDecodeError:
                return {"value": self.value}
        return self.value if isinstance(self.value, dict) else {}


@dataclass
class _PendingSend:
    client_context: str
    future: asyncio.Future


class Realtime:
    """Owns the MQTT connection, the iris subscription and the send channel."""

    def __init__(self, session: Session, user_agent: str,
                 on_delta: DeltaCallback,
                 on_connect: Optional[Callable[[bool], Awaitable[None]]] = None,
                 resnapshot: Optional[Callable[[], Awaitable[None]]] = None) -> None:
        """``resnapshot`` re-fetches the inbox (REST) and updates
        ``session.seq_id``/``snapshot_at_ms``; the broker demands it when the
        stored cursor goes stale ("Server force resnapshot")."""
        self._session = session
        self._ua = user_agent
        self._on_delta = on_delta
        self._on_connect_cb = on_connect
        self._resnapshot_cb = resnapshot
        self._mqtt: MqttClient | None = None
        self._pending_sends: dict[str, _PendingSend] = {}
        self._device_id = session.device_id or session.ig_did or str(uuid.uuid4())
        self._iris_backoff = 1.0
        self._iris_resnapshots = 0

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        sid = random.randint(0, 2 ** 53 - 1)
        url = f"{GATEWAY}?sid={sid}&cid={self._device_id}"
        auth = {
            "a": self._ua,
            "aid": APP_ID,
            "asi": {"Accept-Language": "en-US"},
            "chat_on": True,
            "cp": 3,
            "ct": "cookie_auth",
            "d": self._device_id,
            "dc": "",
            "ecp": 10,
            "fg": True,
            "mqtt_sid": "",
            "no_auto_fg": True,
            "pm": [],
            "s": sid,
            "st": [],
            "u": self._session.viewer_uuid() or self._session.ds_user_id,
        }
        self._mqtt = MqttClient(
            url=url,
            ws_headers={"Origin": "https://www.instagram.com", "User-Agent": self._ua,
                        "Cookie": self._session.cookie_header},
            username=json.dumps(auth),
            on_packet=self._on_packet,
            on_reconnect=self._on_mqtt_reconnected,
        )
        # register subscriptions before connecting; MqttClient sends the
        # MQTT SUBSCRIBEs right after CONNACK
        await self._mqtt.subscribe(TOPIC_MESSAGE_SYNC)
        await self._mqtt.subscribe(TOPIC_SEND_RESPONSE)
        await self._mqtt.subscribe(TOPIC_IRIS_SUB_RESPONSE)
        self._mqtt.start()

    async def stop(self) -> None:
        if self._mqtt:
            await self._mqtt.stop()

    @property
    def connected(self) -> bool:
        return bool(self._mqtt and self._mqtt.connected)

    # -- subscription -------------------------------------------------------

    async def _on_mqtt_reconnected(self, is_reconnect: bool) -> None:
        # A fresh CONNACK is a fresh chance for the iris subscription —
        # reset the resnapshot counter for the new connection era.
        self._iris_resnapshots = 0
        # MQTT SUBSCRIBEs for the known topics are sent by MqttClient itself;
        # here we only (re-)establish the iris subscription with the latest
        # sequence id so missed deltas are replayed.
        await self._publish_iris_subscribe()
        if self._on_connect_cb:
            await self._on_connect_cb(is_reconnect)

    async def _publish_iris_subscribe(self) -> None:
        assert self._mqtt is not None
        payload = {
            "seq_id": int(self._session.seq_id or 0),
            "snapshot_app_version": "web",
            "snapshot_at_ms": int(self._session.snapshot_at_ms or 0),
            "subscription_type": "message",
        }
        log.info("publishing iris subscribe: %s", payload)
        await self._mqtt.publish(TOPIC_IRIS_SUB, json.dumps(payload), qos=1)

    async def _on_packet(self, topic: str, payload: bytes, qos: int) -> None:
        try:
            if topic == TOPIC_MESSAGE_SYNC:
                await self._handle_message_sync(payload)
            elif topic == TOPIC_IRIS_SUB_RESPONSE:
                await self._handle_iris_response(payload)
            elif topic == TOPIC_SEND_RESPONSE:
                self._handle_send_response(payload)
            else:
                log.debug("packet on %s: %s", topic, payload[:120])
        except Exception:
            log.exception("failed handling %s", topic)

    async def _handle_message_sync(self, payload: bytes) -> None:
        try:
            events = json.loads(payload)
        except json.JSONDecodeError:
            log.warning("unparseable message_sync payload: %r", payload[:200])
            return
        if not isinstance(events, list):
            return
        for event in events:
            if not isinstance(event, dict) or event.get("event") != "patch":
                continue
            seq_id = event.get("seq_id") or 0
            ops = event.get("data") or []
            if not isinstance(ops, list):
                continue
            for op in ops:
                if not isinstance(op, dict):
                    continue
                delta = Delta(
                    op=str(op.get("op", "")),
                    path=str(op.get("path", "")),
                    value=op.get("value"),
                    mutation_token=event.get("mutation_token"),
                    seq_id=int(seq_id),
                )
                delta.parse_path()
                try:
                    await self._on_delta(delta)
                except Exception:
                    log.exception("delta handler failed for %s", delta.path)
            if seq_id and seq_id > self._session.seq_id:
                self._session.seq_id = int(seq_id)

    async def _handle_iris_response(self, payload: bytes) -> None:
        try:
            resp = json.loads(payload)
        except json.JSONDecodeError:
            return
        if resp.get("succeeded"):
            log.info("iris subscribed (seq_id=%s, latest=%s)",
                     resp.get("seq_id"), resp.get("latest_seq_id"))
            self._iris_backoff = 1.0
            self._iris_resnapshots = 0
            latest = resp.get("seq_id")
            if latest and int(latest) > self._session.seq_id:
                self._session.seq_id = int(latest)
            return
        err = resp.get("error_type")
        log.warning("iris subscribe failed: %s", resp)
        if err == 1:
            # stale cursor: refresh seq_id/snapshot via REST resnapshot, then retry
            self._iris_resnapshots += 1
            if self._iris_resnapshots > 5:
                # Stay connected-but-deaf is the worst outcome (the bot looks
                # alive and silently receives nothing), so tear the transport
                # down and let the reconnect loop try again from scratch.
                log.error("iris resnapshot loop — forcing reconnect after %d attempts",
                          self._iris_resnapshots)
                if self._mqtt:
                    await self._mqtt.force_reconnect()
                return
            await asyncio.sleep(min(2.0 * self._iris_resnapshots, 10))
            if self._resnapshot_cb:
                try:
                    await self._resnapshot_cb()
                except Exception:
                    log.exception("resnapshot fetch failed")
            await self._publish_iris_subscribe()
        elif err == 2:
            await asyncio.sleep(self._iris_backoff + random.uniform(0, 1))
            self._iris_backoff = min(self._iris_backoff * 2, 64.0)
            await self._publish_iris_subscribe()

    def _handle_send_response(self, payload: bytes) -> None:
        try:
            resp = json.loads(payload)
        except json.JSONDecodeError:
            return
        ok = resp.get("status") == "ok"
        cc = ""
        pl = resp.get("payload") or {}
        if isinstance(pl, dict):
            cc = str(pl.get("client_context") or "")
        pending = self._pending_sends.pop(cc, None) if cc else None
        if pending is None and len(self._pending_sends) == 1:
            # response didn't echo client_context, but exactly one send is
            # in flight — unambiguous. With several in flight we can't guess
            # which one this answers; better to let them time out than to
            # hand one caller another's response.
            pending = self._pending_sends.pop(next(iter(self._pending_sends)))
        elif pending is None and self._pending_sends:
            log.warning("send response without client_context while %d sends "
                        "are pending — cannot pair it", len(self._pending_sends))
        if pending and not pending.future.done():
            if ok:
                pending.future.set_result(resp.get("payload"))
            else:
                pending.future.set_exception(
                    SendError(f"send rejected: {resp}"))

    # -- sending ------------------------------------------------------------

    async def wait_connected(self, timeout: float = 10.0) -> bool:
        """Wait for the MQTT connection (start() and reconnects connect in
        the background). Returns whether it is connected."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not self.connected and loop.time() < deadline:
            await asyncio.sleep(0.05)
        return self.connected

    async def _send_item(self, payload: dict, timeout: float = 15.0) -> dict:
        if not await self.wait_connected():
            raise SendError("realtime not connected")
        cc = payload.setdefault("client_context", new_client_context())
        payload.setdefault("device_id", self._device_id)
        pending = _PendingSend(client_context=str(cc),
                               future=asyncio.get_running_loop().create_future())
        self._pending_sends[str(cc)] = pending
        try:
            await self._mqtt.publish("/ig_send_message", json.dumps(payload), qos=1)
            try:
                return await asyncio.wait_for(pending.future, timeout=timeout)
            except asyncio.TimeoutError:
                raise SendError(
                    f"no response on {TOPIC_SEND_RESPONSE} for "
                    f"client_context={cc} within {timeout:.0f}s") from None
        finally:
            self._pending_sends.pop(str(cc), None)

    async def send_text(self, thread_id: str, text: str,
                        replied_to_item_id: str | None = None,
                        replied_to_client_context: str | None = None) -> dict:
        payload: dict = {"action": "send_item", "item_type": "text", "text": text,
                         "thread_id": thread_id,
                         "mutation_token": new_client_context()}
        if replied_to_item_id:
            payload["replied_to_item_id"] = replied_to_item_id
            payload["replied_to_client_context"] = replied_to_client_context or ""
        return await self._send_item(payload)

    async def send_like(self, thread_id: str) -> dict:
        return await self._send_item({"action": "send_item", "item_type": "like",
                                      "thread_id": thread_id,
                                      "mutation_token": new_client_context()})

    async def send_media_share(self, thread_id: str, media_id: str,
                               is_clip: bool = False, text: str = "") -> dict:
        payload = {"action": "send_item",
                   "item_type": "clip_share" if is_clip else "media_share",
                   "media_id": media_id, "thread_id": thread_id}
        if text:
            payload["text"] = text
        return await self._send_item(payload)

    async def send_reaction(self, thread_id: str, item_id: str, emoji: str = "❤️",
                            target_item_type: str = "text", created: bool = True) -> dict:
        return await self._send_item({
            "action": "send_item", "item_type": "reaction", "item_id": item_id,
            "node_type": "item", "reaction_type": "like",
            "reaction_status": "created" if created else "deleted",
            "target_item_type": target_item_type, "thread_id": thread_id,
            "emoji": emoji,
        })

    async def indicate_typing(self, thread_id: str, active: bool = True) -> dict:
        return await self._send_item({"action": "indicate_activity",
                                      "activity_status": "1" if active else "0",
                                      "thread_id": thread_id})
