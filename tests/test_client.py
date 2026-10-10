import json

import pytest

from aerogram.client import Client
from aerogram.types import Message, Thread, User


def make_client(tmp_path, **kw):
    return Client("test", cookies={"sessionid": "s", "csrftoken": "c",
                                   "ds_user_id": "777"},
                  workdir=str(tmp_path), **kw)


def full_thread(tid="111", uid="42", username="alice"):
    return Thread.parse({
        "thread_id": tid, "thread_v2_id": "v2-" + tid, "is_group": False,
        "users": [{"pk": uid, "username": username}],
        "items": [],
    })


def test_store_thread_merges_partial_updates(tmp_path):
    """Iris thread deltas are often partial patches - they must not clobber
    the populated cache entry (users/v2_id/messages)."""
    c = make_client(tmp_path)
    c._store_thread(full_thread())
    partial = Thread.parse({"thread_id": "111", "muted": True})
    c._store_thread(partial)
    merged = c._threads["111"]
    assert merged.muted is True
    assert merged.v2_id == "v2-111"
    assert [u.username for u in merged.users] == ["alice"]
    # and the user index survived
    assert c._user_thread_index["42"] == "111"
    assert c._user_thread_index["alice"] == "111"


def test_store_thread_bounds_cache_and_index(tmp_path):
    c = make_client(tmp_path, max_cached_threads=3)
    for i in range(6):
        c._store_thread(full_thread(tid=str(i), uid=str(i), username=f"u{i}"))
    assert len(c._threads) == 3
    assert set(c._user_thread_index.values()) <= set(c._threads)
    # oldest entries were evicted
    assert "0" not in c._threads and "5" in c._threads


@pytest.mark.asyncio
async def test_new_message_trims_cached_history(tmp_path):
    c = make_client(tmp_path, max_cached_messages=2)
    c._store_thread(full_thread(tid="111"))
    item = json.dumps({"item_type": "text", "text": "hi", "user_id": "42"})
    for i in range(5):
        from aerogram.iris import Delta
        d = Delta(op="add", path=f"/direct_v2/threads/111/items/{i}",
                  value=item, seq_id=100 + i)
        d.parse_path()
        await c._handle_delta(d)
        await c.dispatcher.wait()
    assert len(c._threads["111"].messages) == 2


@pytest.mark.asyncio
async def test_seq_cursor_persisted_periodically(tmp_path):
    c = make_client(tmp_path)
    calls = []
    c._persist_session = lambda: calls.append(c.session.seq_id)
    from aerogram.iris import Delta
    d = Delta(op="replace", path="/direct_v2/inbox/unseen_count", value="3",
              seq_id=500)
    d.parse_path()
    c.session.seq_id = 500
    await c._handle_delta(d)
    assert calls == [500]           # persisted as soon as the cursor moved
    c.session.seq_id = 501
    await c._handle_delta(d)
    assert calls == [500]           # throttled: not again within 30s


def slide_thread(tid="111", key="900", fbid="800", uid="42", username="alice"):
    return {"thread_id": tid, "thread_key": key, "thread_fbid": fbid, "id": fbid,
            "is_group": False, "viewer_id": "777",
            "users": [{"id": uid, "username": username}],
            "slide_messages": {"edges": [{"node": {
                "id": "mid.$abc", "message_id": "mid.$abc", "timestamp_ms": "1700000000000",
                "content_type": "TEXT", "offline_threading_id": "123",
                "content": {"__typename": "SlideMessageText", "text_body": "hey"},
                "sender": {"igid": uid}}}]}}


def mailbox(*threads, has_next=True):
    return {"id": "mbox", "iris_inactive_subscription_uq_seq_id": "18092",
            "snapshot_at_ms": 1791594001146,
            "threads_by_folder": {"edges": [{"node": {"as_ig_direct_thread": t}} for t in threads],
                                  "page_info": {"end_cursor": "c1", "has_next_page": has_next}}}


@pytest.mark.asyncio
async def test_resnapshot_seeds_cursor_and_cache_from_graphql_inbox(tmp_path):
    c = make_client(tmp_path)

    async def inbox():
        return mailbox(slide_thread())

    c.api.inbox = inbox
    await c._resnapshot_cursor()
    assert c.session.seq_id == 18092
    assert c.session.snapshot_at_ms == 1791594001146
    t = c._threads["111"]
    assert (t.v2_id, t.fbid) == ("900", "800")
    assert c._user_thread_index["alice"] == "111"
    assert c._mailbox_id == "mbox"


@pytest.mark.asyncio
async def test_resnapshot_falls_back_to_badge_count(tmp_path):
    from aerogram.errors import InstaDMError
    c = make_client(tmp_path)

    async def inbox():
        raise InstaDMError("graphql down")

    async def badge_count():
        return {"seq_id": "5", "badge_count_at_ms": 10}

    c.api.inbox = inbox
    c.api.badge_count = badge_count
    await c._resnapshot_cursor()
    assert (c.session.seq_id, c.session.snapshot_at_ms) == (5, 10)


@pytest.mark.asyncio
async def test_get_inbox_paginates_with_mailbox_id(tmp_path):
    c = make_client(tmp_path)
    calls = []

    async def inbox():
        return mailbox(slide_thread())

    async def inbox_page(mailbox_id, cursor, count):
        calls.append((mailbox_id, cursor, count))
        return {"edges": [{"node": {"as_ig_direct_thread": slide_thread(tid="222", uid="43", username="bob")}}],
                "page_info": {"end_cursor": "c2", "has_next_page": False}}

    c.api.inbox = inbox
    c.api.inbox_page = inbox_page
    threads, cursor = await c.get_inbox()
    assert [t.id for t in threads] == ["111"] and cursor == "c1"
    threads, cursor = await c.get_inbox(cursor)
    assert [t.id for t in threads] == ["222"] and cursor is None
    assert calls == [("mbox", "c1", 15)]
    assert (await c.find_thread_for_user("@Bob")).id == "222"


def test_message_parse_slide():
    t = Thread.parse_slide(slide_thread())
    m = t.messages[0]
    assert (m.thread_id, m.message_id, m.user_id, m.text) == ("111", "mid.$abc", "42", "hey")
    assert m.timestamp_us == 1700000000000 * 1000
    assert not m.is_sent_by_viewer and m.media is None
    img = Message.parse_slide({"id": "mid.$x", "sender": {"igid": "777"}, "content": {
        "__typename": "SlideMessageImageContent",
        "attachments": [{"attachment_fbid": "5", "attachment_cdn_url": "https://cdn/x.jpg",
                         "preview_cdn_url": "https://cdn/p.jpg"}]}}, viewer_id="777")
    assert img.is_sent_by_viewer
    assert (img.media.media_type, img.media.url, img.item_type) == ("photo", "https://cdn/x.jpg", "photo")


@pytest.mark.asyncio
async def test_send_message_to_existing_thread_uses_thread_fbid(tmp_path):
    """The web composer sends ig_thread_igid = thread_fbid, not thread_key."""
    c = make_client(tmp_path)
    c._store_thread(Thread.parse_slide(slide_thread(key="900", fbid="800")))
    sent = {}

    async def send_text_message(text, thread_fbid=None, recipient_igids=None,
                                reply_to_message_id=None):
        sent.update(thread_fbid=thread_fbid, recipient_igids=recipient_igids)
        return {"data": {"xig_direct_text_send_with_slide_messaging_response": {
            "message_id": "mid.$new"}}}

    c.api.send_text_message = send_text_message
    msg = await c.send_message("alice", "hi")
    assert sent == {"thread_fbid": "800", "recipient_igids": None}
    assert (msg.thread_id, msg.message_id) == ("111", "mid.$new")


def test_media_parse_slide_video_and_voice():
    from aerogram.types import Media
    v = Media.parse_slide({"__typename": "SlideMessageVideosContent", "videos": [
        {"attachment_fbid": "1", "attachment_cdn_url": "https://cdn/v.mp4", "preview_cdn_url": "https://cdn/p.jpg"}]})
    assert (v.media_type, v.url, v.thumbnail_url) == ("video", "https://cdn/v.mp4", "https://cdn/p.jpg")
    a = Media.parse_slide({"__typename": "SlideMessageAudiosContent", "audio_attachments": [
        {"attachment_fbid": "2", "attachment_cdn_url": "https://cdn/a.mp4", "playable_duration_ms": 1396}]})
    assert (a.media_type, a.url, a.duration_seconds) == ("voice_media", "https://cdn/a.mp4", 1.396)


def test_media_parse_slide_shared_reel_and_post():
    from aerogram.types import Media

    def xma(url, decoration=None):
        return {"__typename": "SlideMessageXMAContent", "xma": {
            "target_id": "123", "target_url": url, "preview_image": {"url": "https://cdn/p.jpg"},
            "xmaPreviewImage": {"preview_image_decoration_type": decoration}}}

    reel = Media.parse_slide(xma("https://www.instagram.com/reel/AbC/?id=123_9", "REEL"))
    post = Media.parse_slide(xma("https://www.instagram.com/p/XyZ/"))
    other = Media.parse_slide(xma("https://www.instagram.com/someone/"))
    assert (reel.media_type, reel.id) == ("clip", "123")
    assert (post.media_type, post.id, post.thumbnail_url) == ("media_share", "123", "https://cdn/p.jpg")
    assert other.media_type == "xma_share"


@pytest.mark.asyncio
async def test_download_into_folder_keeps_cdn_name(tmp_path):
    import httpx
    c = make_client(tmp_path)
    c.api._client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=b"img")))
    folder = str(tmp_path / "downloads") + "/"
    path = await c.download("https://cdn.example/v/pic.jpg?x=1", folder)
    assert path == str(tmp_path / "downloads" / "pic.jpg")
    assert open(path, "rb").read() == b"img"
