"""Pyrogram-style composable message filters.

    filters.text & ~filters.self
    filters.photo | filters.video
    filters.chat("340282...") & filters.regex(r"^hi")
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Callable, Optional, Union

from . import types

if TYPE_CHECKING:  # pragma: no cover
    from .types import Message


class Filter:
    def __call__(self, message: "Message") -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def __and__(self, other: "Filter") -> "Filter":
        return _And(self, other)

    def __or__(self, other: "Filter") -> "Filter":
        return _Or(self, other)

    def __invert__(self) -> "Filter":
        return _Invert(self)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<filter {type(self).__name__}>"


class _And(Filter):
    def __init__(self, a: Filter, b: Filter) -> None:
        self.a, self.b = a, b

    def __call__(self, m) -> bool:
        return self.a(m) and self.b(m)

    def __repr__(self) -> str:
        return f"({self.a!r} & {self.b!r})"


class _Or(Filter):
    def __init__(self, a: Filter, b: Filter) -> None:
        self.a, self.b = a, b

    def __call__(self, m) -> bool:
        return self.a(m) or self.b(m)

    def __repr__(self) -> str:
        return f"({self.a!r} | {self.b!r})"


class _Invert(Filter):
    def __init__(self, a: Filter) -> None:
        self.a = a

    def __call__(self, m) -> bool:
        return not self.a(m)

    def __repr__(self) -> str:
        return f"~{self.a!r}"


class _Custom(Filter):
    def __init__(self, fn: Callable[[object], bool], name: str = "custom") -> None:
        self.fn = fn
        self.name = name

    def __call__(self, m) -> bool:
        return bool(self.fn(m))

    def __repr__(self) -> str:
        return f"<filter {self.name}>"


def create(fn: Callable[[object], bool], name: str = "custom") -> Filter:
    """Build a custom filter from a predicate."""
    return _Custom(fn, name)


# -- builtins ----------------------------------------------------------------

class _Text(Filter):
    def __call__(self, m) -> bool:
        return bool(m.text)


class _Photo(Filter):
    def __call__(self, m) -> bool:
        return m.media is not None and m.media.media_type == "photo"


class _Video(Filter):
    def __call__(self, m) -> bool:
        return m.media is not None and m.media.media_type == "video"


class _Voice(Filter):
    def __call__(self, m) -> bool:
        return m.media is not None and m.media.media_type == "voice_media"


class _Media(Filter):
    def __call__(self, m) -> bool:
        return m.is_media


class _Self(Filter):
    """Messages sent by the logged-in account itself."""

    def __call__(self, m) -> bool:
        me = getattr(m.client, "user_id", None)
        return bool(me) and str(m.user_id) == str(me)


class _Private(Filter):
    """1:1 threads (not group chats)."""

    def __call__(self, m) -> bool:
        thread = getattr(m, "thread", None)
        if thread is not None:
            return not thread.is_group
        return True


class _Group(Filter):
    def __call__(self, m) -> bool:
        thread = getattr(m, "thread", None)
        if thread is not None:
            return thread.is_group
        return False


class Chat(Filter):
    """Messages in a specific thread."""

    def __init__(self, thread_id: str) -> None:
        self.thread_id = str(thread_id)

    def __call__(self, m) -> bool:
        return str(m.thread_id) == self.thread_id


class FromUser(Filter):
    """Messages from a specific user id."""

    def __init__(self, user_id: Union[str, int]) -> None:
        self.user_id = str(user_id)

    def __call__(self, m) -> bool:
        return str(m.user_id) == self.user_id


class Regex(Filter):
    """Message text (or caption) matching a regular expression."""

    def __init__(self, pattern: str, flags: int = 0) -> None:
        self.re = re.compile(pattern, flags)

    def __call__(self, m) -> bool:
        text = m.text or ""
        return self.re.search(text) is not None


text = _Text()
photo = _Photo()
video = _Video()
voice = _Voice()
media = _Media()
self = _Self()
private = _Private()
group = _Group()
me = self  # alias
incoming = ~self


class _FiltersNamespace:
    """Pyrogram-style ``filters`` namespace: ``filters.text``, ``filters.chat(id)``..."""

    Chat = Chat
    FromUser = FromUser
    Regex = Regex
    create = staticmethod(create)


filters = _FiltersNamespace()
for _name in ("text", "photo", "video", "voice", "media", "self", "private",
              "group", "me", "incoming"):
    setattr(filters, _name, globals()[_name])
