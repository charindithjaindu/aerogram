import json

import pytest

from aerogram.client import Client
from aerogram.types import Thread, User


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
    """Iris thread deltas are often partial patches — they must not clobber
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


@pytest.mark.asyncio
async def test_resnapshot_uses_badge_count_when_inbox_404s(tmp_path):
    """The web REST inbox now 404s; the iris cursor must still be seeded."""
    from aerogram.errors import NotFoundError
    c = make_client(tmp_path)

    async def badge_count():
        return {"seq_id": "18092", "badge_count_at_ms": 1791594001146}

    async def inbox(*a, **kw):
        raise NotFoundError("Not found: /direct_v2/inbox/")

    c.api.badge_count = badge_count
    c.api.inbox = inbox
    await c._resnapshot_cursor()
    assert c.session.seq_id == 18092
    assert c.session.snapshot_at_ms == 1791594001146


@pytest.mark.asyncio
async def test_resnapshot_still_warms_cache_when_inbox_works(tmp_path):
    c = make_client(tmp_path)

    async def badge_count():
        return {"seq_id": 5, "badge_count_at_ms": 10}

    async def inbox(*a, **kw):
        return {"inbox": {"threads": [{"thread_id": "111", "users": [
            {"pk": "42", "username": "alice"}]}]}}

    c.api.badge_count = badge_count
    c.api.inbox = inbox
    await c._resnapshot_cursor()
    assert c.session.seq_id == 5
    assert c._user_thread_index["alice"] == "111"
