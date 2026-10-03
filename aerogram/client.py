"""The high-level ``Client`` — the Pyrogram-like entry point.

    from aerogram import Client, filters

    app = Client("my_session", cookies_file="session/cookies.txt")

    @app.on_message(filters.text & ~filters.self)
    async def echo(client, message):
        await message.reply_text(message.text)

    app.run()          # blocking
    # or: await app.start() / app.stop() inside your own asyncio app
"""
from __future__ import annotations

import asyncio
import logging
import signal
import time
import uuid
from typing import Optional, Union

from . import filters as _filters
from .dispatcher import (MESSAGE, MESSAGE_DELETE, RAW_DELTA, THREAD_UPDATE,
                         UNSEEN_COUNT, Dispatcher, Handler, HandlerFn)
from .errors import InstaDMError, NotFoundError
from .filters import Filter
from .http_api import DEFAULT_UA, HttpApi
from .iris import Delta, Realtime
from .session import Session
from .types import Message, Thread, User

log = logging.getLogger("aerogram")


def _ms(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class Client:
    def __init__(
        self,
        name: str,
        *,
        cookies_file: str | None = None,
        cookies: dict[str, str] | None = None,
        session_string: str | None = None,
        user_agent: str = DEFAULT_UA,
        workdir: str = ".",
        receive_own_messages: bool = True,
        max_cached_threads: int = 500,
        max_cached_messages: int = 200,
    ) -> None:
        """``name`` names the session file (``<workdir>/<name>.session.json``).

        The session file persists cookies *and* the iris ``seq_id`` cursor, so
        realtime resumes exactly where it stopped after a restart.
        """
        self.name = name
        self.session_path = f"{workdir.rstrip('/')}/{name}.session.json"
        self.user_agent = user_agent

        if session_string:
            import base64
            raw = base64.urlsafe_b64decode(session_string.encode()).decode()
            import json
            data = json.loads(raw)
            self.session = Session.from_cookies(data["cookies"])
            self.session.device_id = data.get("device_id", "")
            self.session.seq_id = data.get("seq_id", 0)
            self.session.snapshot_at_ms = data.get("snapshot_at_ms", 0)
        elif cookies_file:
            self.session = Session.from_cookies_file(cookies_file)
        elif cookies:
            self.session = Session.from_cookies(cookies)
        else:
            # load existing session file if present
            try:
                self.session = Session.from_file(self.session_path)
            except FileNotFoundError:
                raise InstaDMError(
                    "no session source given — pass cookies_file=, cookies= or "
                    "session_string=, or point at an existing session file")

        self.session.device_id = self.session.device_id or self.session.ig_did or str(uuid.uuid4())
        self.session.user_id = self.session.user_id or self.session.ds_user_id

        self.receive_own_messages = receive_own_messages
        self.max_cached_threads = max_cached_threads
        self.max_cached_messages = max_cached_messages
        self.user_id = self.session.user_id
        self.dispatcher = Dispatcher()
        self.api = HttpApi(self.session, user_agent)
        self._realtime: Optional[Realtime] = None
        self._threads: dict[str, Thread] = {}
        self._user_thread_index: dict[str, str] = {}  # user id/username -> thread id
        self._started = False
        self._last_seq_persist = 0.0
        self._persisted_seq = self.session.seq_id

    # -- handler registration (Pyrogram style) -------------------------------

    def on_message(self, filters: Filter | None = None, group: int = 0):
        return self._decorator(MESSAGE, filters, group)

    def on_message_delete(self, filters: Filter | None = None, group: int = 0):
        return self._decorator(MESSAGE_DELETE, filters, group)

    def on_thread_update(self, filters: Filter | None = None, group: int = 0):
        return self._decorator(THREAD_UPDATE, filters, group)

    def on_unseen_count(self, filters: Filter | None = None, group: int = 0):
        return self._decorator(UNSEEN_COUNT, filters, group)

    def on_raw_delta(self, filters: Filter | None = None, group: int = 0):
        """Receive every iris delta as ``Delta`` objects (escape hatch)."""
        return self._decorator(RAW_DELTA, filters, group)

    def on_error(self, fn):
        self.dispatcher.set_error_handler(fn)
        return fn

    def _decorator(self, update_type: str, filters: Filter | None, group: int):
        def wrapper(fn: HandlerFn) -> HandlerFn:
            self.dispatcher.add(Handler(update_type, fn, filters, group))
            return fn
        return wrapper

    def add_handler(self, fn: HandlerFn, update_type: str = MESSAGE,
                    filters: Filter | None = None, group: int = 0) -> Handler:
        return self.dispatcher.add(Handler(update_type, fn, filters, group))

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        self.session.validate()
        await self._bootstrap_seq_id()
        self._realtime = Realtime(
            self.session, self.user_agent,
            on_delta=self._handle_delta,
            on_connect=self._on_realtime_connect,
            resnapshot=self._resnapshot_cursor,
        )
        await self._realtime.start()
        self._started = True
        log.info("client started (user %s, seq_id %s)", self.user_id, self.session.seq_id)

    async def stop(self) -> None:
        self._started = False
        if self._realtime:
            await self._realtime.stop()
        try:
            await self.dispatcher.wait(timeout=10.0)
        except Exception:
            log.exception("failed waiting for in-flight handlers")
        self._persist_session()
        await self.api.aclose()
        log.info("client stopped")

    async def idle(self) -> None:
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass

    def run(self) -> None:
        async def _run():
            loop = asyncio.get_running_loop()
            stop_signal = asyncio.Event()

            def _sig(*_):
                stop_signal.set()

            for s in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(s, _sig)
                except NotImplementedError:
                    pass
            await self.start()
            idle_task = asyncio.create_task(self.idle())
            stop_task = asyncio.create_task(stop_signal.wait())
            done, _ = await asyncio.wait({idle_task, stop_task},
                                         return_when=asyncio.FIRST_COMPLETED)
            idle_task.cancel()
            await self.stop()

        asyncio.run(_run())

    def _persist_session(self) -> None:
        try:
            self.session.save(self.session_path)
        except Exception:
            log.exception("failed saving session file")

    async def _bootstrap_seq_id(self) -> None:
        """Fetch fresh inbox (and seq_id) so iris starts from the present."""
        if self.session.seq_id:
            log.info("resuming iris from stored seq_id %s", self.session.seq_id)
            return
        await self._resnapshot_cursor()
        self.user_id = self.session.user_id or self.session.ds_user_id

    async def _resnapshot_cursor(self) -> None:
        """Refresh the iris cursor from the REST inbox (also used when the
        broker demands a resnapshot)."""
        inbox = await self.api.inbox()
        self.session.seq_id = int(inbox.get("seq_id") or 0)
        self.session.snapshot_at_ms = int(inbox.get("snapshot_at_ms") or 0)
        for t in inbox.get("inbox", {}).get("threads", []):
            self._store_thread(Thread.parse(t))

    def _store_thread(self, thread: Thread) -> None:
        """Merge a parsed thread into the cache and index its 1:1
        participants for fast username/id → thread lookups.

        Iris thread payloads are often partial patches; they must not
        clobber a fully-populated cached entry (users, v2_id, messages).
        """
        cached = self._threads.pop(thread.id, None)
        if cached is not None:
            if not thread.users:
                thread.users = cached.users
            if not thread.v2_id:
                thread.v2_id = cached.v2_id
            if not thread.messages:
                thread.messages = cached.messages
        self._threads[thread.id] = thread
        if not thread.is_group:
            for u in thread.users:
                if u.id and u.id != self.user_id:
                    self._user_thread_index[u.id] = thread.id
                if u.username:
                    self._user_thread_index[u.username.lower()] = thread.id
        while len(self._threads) > self.max_cached_threads:
            oldest = next(iter(self._threads))
            self._threads.pop(oldest, None)
            stale = [k for k, v in self._user_thread_index.items() if v == oldest]
            for k in stale:
                del self._user_thread_index[k]

    # -- realtime plumbing ----------------------------------------------------

    async def _on_realtime_connect(self, is_reconnect: bool) -> None:
        if is_reconnect and self._started:
            log.info("realtime reconnected — healing any gaps via fresh inbox fetch")
            try:
                inbox = await self.api.inbox()
                self.session.seq_id = max(self.session.seq_id, int(inbox.get("seq_id") or 0))
            except Exception:
                log.warning("gap-heal inbox fetch failed; iris resync will cover it", exc_info=True)

    async def _handle_delta(self, delta: Delta) -> None:
        await self.dispatcher.dispatch(RAW_DELTA, self, delta)
        if delta.is_new_message:
            raw = delta.value_as_dict()
            raw.setdefault("item_id", delta.item_id)
            message = Message.parse(raw, thread_id=delta.thread_id)
            message.client = self
            if not self.receive_own_messages and message.is_sent_by_viewer:
                return
            cached = self._threads.get(delta.thread_id)
            message.thread = cached
            if cached is not None:
                cached.messages.insert(0, message)
                if len(cached.messages) > self.max_cached_messages:
                    del cached.messages[self.max_cached_messages:]
            await self.dispatcher.dispatch(MESSAGE, self, message)
        elif delta.is_removed_message:
            raw = delta.value_as_dict()
            raw.setdefault("item_id", delta.item_id)
            message = Message.parse(raw, thread_id=delta.thread_id)
            message.client = self
            await self.dispatcher.dispatch(MESSAGE_DELETE, self, message)
        elif delta.is_thread_update:
            thread = Thread.parse(delta.value_as_dict())
            thread.id = thread.id or delta.thread_id
            self._store_thread(thread)
            await self.dispatcher.dispatch(THREAD_UPDATE, self, thread)
        elif delta.is_unseen_count:
            value = delta.value_as_dict()
            await self.dispatcher.dispatch(UNSEEN_COUNT, self, value)
        # Persist the iris cursor periodically: only saving on stop() means a
        # crash replays every delta since the last graceful shutdown through
        # the handlers again. 30s of at-most replay is the accepted tradeoff.
        if (self.session.seq_id != self._persisted_seq
                and time.monotonic() - self._last_seq_persist >= 30.0):
            self._persisted_seq = self.session.seq_id
            self._last_seq_persist = time.monotonic()
            self._persist_session()

    # -- DM actions -----------------------------------------------------------

    async def send_like(self, thread_id: str) -> None:
        assert self._realtime is not None, "client not started"
        await self._realtime.send_like(thread_id)

    async def share_media(self, thread_id: str, media_id: str, is_clip: bool = False,
                          caption: str = "") -> None:
        assert self._realtime is not None, "client not started"
        await self._realtime.send_media_share(thread_id, media_id, is_clip, caption)


    async def _resolve_user_id(self, ref: Union[str, int]) -> str:
        """Accept a username or a numeric user id; return the numeric id."""
        ref = str(ref)
        if ref.isdigit():
            return ref
        user = await self.user_by_username(ref)
        if not user.id:
            raise NotFoundError(f"user @{ref} not found")
        return user.id

    async def find_thread_for_user(self, user_ref: Union[str, int],
                                   scan_pages: int = 3) -> Optional[Thread]:
        """Locate the 1:1 thread with this user (by username or id) in the
        inbox, if any. Matching by username avoids the heavily rate-limited
        profile endpoint."""
        ref = str(user_ref)
        cached_id = self._user_thread_index.get(ref) or \
            self._user_thread_index.get(ref.lower())
        if cached_id and cached_id in self._threads:
            return self._threads[cached_id]
        cursor: Optional[str] = None
        for _ in range(scan_pages):
            threads, cursor = await self.get_inbox(cursor=cursor, limit=30)
            for t in threads:
                if t.is_group:
                    continue
                for u in t.users:
                    if u.id == ref or u.username == ref:
                        return t
            if not cursor:
                break
        return None

    async def send_message(self, to: Union[str, int], text: str,
                           reply_to: Optional[Message] = None) -> Message:
        """Send a text message to a **username** (or numeric user id).

        Resolves the user, uses their existing thread when one is in the
        inbox, otherwise starts a new conversation (``recipient_igids`` path).
        """
        thread = await self.find_thread_for_user(to)
        if thread and thread.v2_id:
            resp = await self.api.send_text_message(
                text, thread_v2_id=thread.v2_id,
                reply_to_message_id=reply_to.message_id if reply_to else None)
            thread_id = thread.id
        else:
            uid = await self._resolve_user_id(to)
            resp = await self.api.send_text_message(
                text, recipient_igids=[uid],
                reply_to_message_id=reply_to.message_id if reply_to else None)
            # a brand-new thread was just created server-side — pick it up so
            # the returned Message carries the thread id and future sends to
            # this user hit the cache instead of re-scanning the inbox
            thread = await self.find_thread_for_user(to, scan_pages=1)
            thread_id = thread.id if thread else ""
        raw = resp.get("data", {}).get("xig_direct_text_send_with_slide_messaging_response") or {}
        msg = Message(
            thread_id=thread_id,
            item_id=str(raw.get("message_id") or ""),
            message_id=str(raw.get("message_id") or ""),
            user_id=str(self.user_id or ""),
            timestamp_us=(_ms(raw.get("timestamp_ms")) * 1000) if _ms(raw.get("timestamp_ms")) else None,
            item_type="text",
            text=text,
            is_sent_by_viewer=True,
        )
        msg.client = self
        return msg

    async def send_photo(self, photo: Union[str, bytes],
                         to: Optional[Union[str, int]] = None,
                         thread_id: Optional[str] = None,
                         filename: str = "photo.jpg",
                         caption: str = "") -> Message:
        """Send a photo (bytes or a file path) to an existing thread.

        ``to`` accepts a username or user id; ``thread_id`` (the long
        34028236… form) takes precedence. The thread must already exist —
        start the conversation with :meth:`send_message` first for new users.
        """
        if thread_id is None:
            if to is None:
                raise InstaDMError("send_photo needs either to= or thread_id=")
            thread = await self.find_thread_for_user(to)
            if thread is None:
                raise NotFoundError(
                    f"no existing thread with user {to} — send a text with "
                    "send_message() first, then photos will work")
            thread_id = thread.id
        if isinstance(photo, str):
            with open(photo, "rb") as f:
                data = f.read()
        else:
            data = photo
        fbid = await self.api.upload_mercury(data, filename)
        resp = await self.api.send_media_message(thread_id, fbid)
        raw = resp.get("data", {}).get("xig_direct_media_send_with_slide_messaging_response") or {}
        msg = Message(
            thread_id=thread_id,
            item_id=str(raw.get("message_id") or ""),
            message_id=str(raw.get("message_id") or ""),
            user_id=str(self.user_id or ""),
            timestamp_us=(_ms(raw.get("timestamp_ms")) * 1000) if _ms(raw.get("timestamp_ms")) else None,
            item_type="photo",
            text=caption,
            is_sent_by_viewer=True,
        )
        msg.client = self
        return msg

    async def send_text(self, thread_id: str, text: str,
                        reply_to: Optional[Message] = None) -> Message:
        """Send a text message to a thread via the realtime send channel
        (fastest path when you already know the thread_id)."""
        assert self._realtime is not None, "client not started"
        resp = await self._realtime.send_text(
            thread_id, text,
            replied_to_item_id=reply_to.item_id if reply_to else None,
            replied_to_client_context=reply_to.client_context if reply_to else None,
        )
        raw = resp if isinstance(resp, dict) else {}
        # the send-response timestamp is milliseconds; timestamp_us is µs
        ts_ms = _ms(raw.get("timestamp"))
        msg = Message(
            thread_id=str(raw.get("thread_id") or thread_id),
            item_id=str(raw.get("item_id") or ""),
            message_id=str(raw.get("msg_id") or raw.get("message_id") or ""),
            user_id=str(self.user_id or ""),
            timestamp_us=(ts_ms * 1000) if ts_ms else None,
            item_type="text",
            text=text,
            client_context=str(raw.get("client_context") or ""),
            is_sent_by_viewer=True,
        )
        msg.client = self
        return msg

    async def send_reaction(self, thread_id: str, item_id: str, emoji: str = "❤️",
                            target_item_type: str = "text") -> None:
        assert self._realtime is not None, "client not started"
        await self._realtime.send_reaction(thread_id, item_id, emoji, target_item_type)

    async def unsend_reaction(self, thread_id: str, item_id: str,
                              target_item_type: str = "text") -> None:
        assert self._realtime is not None, "client not started"
        await self._realtime.send_reaction(thread_id, item_id, "", target_item_type,
                                           created=False)

    async def indicate_typing(self, thread_id: str, active: bool = True) -> None:
        assert self._realtime is not None, "client not started"
        await self._realtime.indicate_typing(thread_id, active)

    # -- reads ----------------------------------------------------------------

    async def get_inbox(self, cursor: str | None = None, limit: int = 20) -> tuple[list[Thread], str | None]:
        data = await self.api.inbox(cursor, limit)
        threads = [Thread.parse(t) for t in data.get("inbox", {}).get("threads", [])]
        for t in threads:
            self._store_thread(t)
        next_cursor = data.get("inbox", {}).get("oldest_cursor")
        return threads, next_cursor

    async def get_thread_history(self, thread_id: str, cursor: str | None = None,
                                 limit: int = 30) -> tuple[list[Message], str | None]:
        data = await self.api.thread(thread_id, cursor, limit)
        thread = Thread.parse(data.get("thread", {}))
        self._store_thread(thread)
        next_cursor = thread.raw.get("oldest_cursor")
        return thread.messages, next_cursor

    async def mark_seen(self, thread_id: str, item_id: str) -> None:
        """Mark an item as seen — uses the web client's Relay mutation
        (mark-thread-as-read), falling back to the app-style REST endpoint."""
        # resolve the message_id (mid.$…) the mutation expects
        message_id = ""
        thread = self._threads.get(thread_id)
        for m in (thread.messages if thread else []):
            if m.item_id == item_id and m.message_id:
                message_id = m.message_id
                break
        if message_id:
            try:
                await self.api.mark_thread_as_read(thread_id, message_id)
                return
            except InstaDMError as e:
                log.warning("graphql mark-read failed (%s); falling back to REST", e)
        await self.api.mark_seen(thread_id, item_id)

    async def hide_thread(self, thread_id: str) -> None:
        await self.api.hide_thread(thread_id)

    async def mute_thread(self, thread_id: str, mute: bool = True) -> None:
        await self.api.mute_thread(thread_id, mute)

    async def get_presence(self) -> dict:
        return await self.api.get_presence()

    async def user_by_username(self, username: str) -> User:
        data = await self.api.user_info_by_username(username)
        return User.parse(data.get("data", {}).get("user", {}))

    async def thread_id_for_user(self, user_id: str) -> str:
        """Find an existing 1:1 thread with ``user_id`` from the inbox."""
        cached = self._user_thread_index.get(str(user_id))
        if cached and cached in self._threads:
            return cached
        threads, cursor = await self.get_inbox(limit=50)
        for t in threads:
            if not t.is_group and any(u.id == str(user_id) for u in t.users):
                return t.id
        raise InstaDMError(
            f"no existing thread with user {user_id} found in inbox; send the "
            "first message from the Instagram app, or pass thread_id directly")

    async def download(self, url: str, path: str | None = None) -> str:
        return await self.api.download(url, path)

    def export_session_string(self) -> str:
        import base64
        import json
        raw = json.dumps({
            "cookies": self.session.cookies,
            "device_id": self.session.device_id,
            "seq_id": self.session.seq_id,
            "snapshot_at_ms": self.session.snapshot_at_ms,
        })
        return base64.urlsafe_b64encode(raw.encode()).decode()
