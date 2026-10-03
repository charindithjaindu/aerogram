"""Handler registry and update dispatching."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from .filters import Filter

log = logging.getLogger("aerogram.dispatcher")

HandlerFn = Callable[[Any, Any], Awaitable[None]]
ErrorHandlerFn = Callable[[Any, Any, Exception], Any]

MESSAGE = "message"
MESSAGE_EDIT = "message_edit"        # item replaced (e.g. reaction/seen state)
MESSAGE_DELETE = "message_delete"    # unsend
THREAD_UPDATE = "thread_update"      # inbox-level thread change
UNSEEN_COUNT = "unseen_count"
RAW_DELTA = "raw_delta"              # every iris patch op, unfiltered


@dataclass
class Handler:
    update_type: str
    callback: HandlerFn
    filters: Optional[Filter] = None
    group: int = 0


class Dispatcher:
    """Dispatches updates to registered handlers.

    Each matched handler runs as its own asyncio task, so a slow handler
    (or one that performs network I/O — e.g. ``await message.reply_text``)
    never blocks the realtime receive loop. Updates are scheduled in
    arrival order, but handlers may complete out of order once they await.
    """

    def __init__(self) -> None:
        self._handlers: list[Handler] = []
        self._error_handler: Optional[ErrorHandlerFn] = None
        self._sorted_cache: dict[str, list[Handler]] = {}
        self._tasks: set[asyncio.Task] = set()

    def add(self, handler: Handler) -> Handler:
        self._handlers.append(handler)
        self._sorted_cache.clear()
        return handler

    def remove(self, handler: Handler) -> None:
        if handler in self._handlers:
            self._handlers.remove(handler)
            self._sorted_cache.clear()

    def set_error_handler(self, fn: ErrorHandlerFn) -> None:
        self._error_handler = fn

    def handlers_for(self, update_type: str) -> list[Handler]:
        cached = self._sorted_cache.get(update_type)
        if cached is None:
            cached = sorted((h for h in self._handlers if h.update_type == update_type),
                            key=lambda h: h.group)
            self._sorted_cache[update_type] = cached
        return cached

    async def dispatch(self, update_type: str, client: Any, update: Any) -> None:
        """Schedule every matching handler (first match per group wins).

        Returns as soon as the handlers are *scheduled*; use :meth:`wait` to
        await their completion (e.g. on shutdown).
        """
        handled_groups: set[int] = set()
        for h in self.handlers_for(update_type):
            if h.group in handled_groups:
                continue
            if h.filters is not None and not h.filters(update):
                continue
            handled_groups.add(h.group)
            task = asyncio.create_task(
                self._run(h, client, update),
                name=f"aerogram-{update_type}-{h.callback.__name__}")
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def wait(self, timeout: Optional[float] = None) -> None:
        """Wait for in-flight handler tasks; cancel those still running
        once ``timeout`` (if given) expires."""
        if not self._tasks:
            return
        _, pending = await asyncio.wait(list(self._tasks), timeout=timeout)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _run(self, h: Handler, client: Any, update: Any) -> None:
        try:
            await h.callback(client, update)
        except Exception as e:
            log.exception("handler %s failed", h.callback.__name__)
            if self._error_handler:
                try:
                    r = self._error_handler(client, update, e)
                    if asyncio.iscoroutine(r):
                        await r
                except Exception:
                    log.exception("error handler failed")
