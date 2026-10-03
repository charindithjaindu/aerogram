from dataclasses import dataclass, field
from typing import Optional

from aerogram import filters
from aerogram.types import Media, Message


def make_msg(**kw) -> Message:
    defaults = dict(thread_id="t1", item_id="i1", user_id="999", item_type="text",
                    text="hello world", client=None)
    defaults.update(kw)
    return Message(**defaults)


@dataclass
class FakeClient:
    user_id: str = "777"


def test_text_and_composition():
    m = make_msg()
    assert filters.text(m)
    assert (filters.text & ~filters.self)(m) is True
    assert (filters.text & filters.self)(m) is False


def test_self_filter_uses_client_backref():
    me = make_msg(user_id="777")
    me.client = FakeClient("777")
    other = make_msg(user_id="999")
    other.client = FakeClient("777")
    assert filters.self(me)
    assert not filters.self(other)
    assert filters.incoming(other)
    assert not filters.incoming(me)


def test_media_filters():
    photo_msg = make_msg(item_type="photo", text="")
    photo_msg.media = Media(media_type="photo", id="m1", url="http://x")
    video_msg = make_msg(item_type="video", text="")
    video_msg.media = Media(media_type="video", id="m2", url="http://y")
    assert filters.photo(photo_msg) and not filters.photo(video_msg)
    assert filters.video(video_msg) and not filters.video(photo_msg)
    assert filters.media(photo_msg) and filters.media(video_msg)
    assert not filters.text(photo_msg)


def test_chat_and_from_user():
    m = make_msg(thread_id="thA", user_id="42")
    assert filters.Chat("thA")(m)
    assert not filters.Chat("thB")(m)
    assert filters.FromUser(42)(m)
    assert not filters.FromUser("43")(m)
    assert (filters.Chat("thA") & filters.FromUser("42"))(m)
    assert (filters.Chat("thA") | filters.Chat("thB"))(m)
    assert (~filters.Chat("thB"))(m)


def test_regex():
    m = make_msg(text="/start 42")
    assert filters.Regex(r"^/start") (m)
    assert not filters.Regex(r"^/stop")(m)
    assert filters.Regex(r"START", flags=0)(make_msg(text="/START"))


def test_create_custom():
    is_long = filters.create(lambda m: len(m.text or "") > 20)
    assert is_long(make_msg(text="x" * 30))
    assert not is_long(make_msg(text="short"))
