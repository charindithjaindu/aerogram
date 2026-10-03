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
    def __init__(self) -> None:
        self._handlers: list[Handler] = []
        self._error_handler: Optional[ErrorHandlerFn] = None

    def add(self, handler: Handler) -> Handler:
        self._handlers.append(handler)
        return handler

    def remove(self, handler: Handler) -> None:
        if handler in self._handlers:
            self._handlers.remove(handler)

    def set_error_handler(self, fn: ErrorHandlerFn) -> None:
        self._error_handler = fn

    def handlers_for(self, update_type: str) -> list[Handler]:
        return sorted((h for h in self._handlers if h.update_type == update_type),
                      key=lambda h: h.group)

    async def dispatch(self, update_type: str, client: Any, update: Any) -> None:
        handled_groups: set[int] = set()
        for h in self.handlers_for(update_type):
            if h.group in handled_groups:
                continue
            try:
                if h.filters is None or h.filters(update):
                    handled_groups.add(h.group)
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
