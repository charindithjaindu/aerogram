import asyncio

import pytest

from aerogram import filters
from aerogram.dispatcher import MESSAGE, Dispatcher, Handler


class RecordingClient:
    user_id = "777"


def make_msg(text="hi", thread_id="t1", user_id="999"):
    from aerogram.types import Message
    m = Message(thread_id=thread_id, user_id=user_id, text=text, item_type="text")
    m.client = RecordingClient()
    return m


@pytest.mark.asyncio
async def test_dispatch_runs_matching_handlers():
    d = Dispatcher()
    seen = []

    async def h1(client, message):
        seen.append(("h1", message.text))

    async def h2(client, message):
        seen.append(("h2", message.text))

    d.add(Handler(MESSAGE, h1, filters.text))
    d.add(Handler(MESSAGE, h2, filters.Regex(r"^nope")))
    await d.dispatch(MESSAGE, RecordingClient(), make_msg())
    await d.wait()
    assert seen == [("h1", "hi")]


@pytest.mark.asyncio
async def test_group_first_match_wins():
    d = Dispatcher()
    seen = []

    async def a(client, m):
        seen.append("a")

    async def b(client, m):
        seen.append("b")

    d.add(Handler(MESSAGE, a, group=0))
    d.add(Handler(MESSAGE, b, group=1))
    d.add(Handler(MESSAGE, b, group=1, filters=filters.text))
    await d.dispatch(MESSAGE, RecordingClient(), make_msg())
    await d.wait()
    # one handler per group; group 1's first match (b, no filter) wins
    assert seen == ["a", "b"]


@pytest.mark.asyncio
async def test_handler_exception_isolated():
    d = Dispatcher()
    seen = []
    errors = []

    async def boom(client, m):
        raise RuntimeError("nope")

    async def fine(client, m):
        seen.append("fine")

    def on_error(client, update, exc):
        errors.append(str(exc))

    d.set_error_handler(on_error)
    d.add(Handler(MESSAGE, boom, group=0))
    d.add(Handler(MESSAGE, fine, group=1))
    await d.dispatch(MESSAGE, RecordingClient(), make_msg())
    await d.wait()
    assert seen == ["fine"]
    assert errors == ["nope"]


@pytest.mark.asyncio
async def test_handlers_run_concurrently_not_inline():
    """Regression: a handler that awaits (e.g. replying over the realtime
    channel) must not block the dispatch loop - the read loop used to await
    handlers inline, deadlocking any send made from inside a handler."""
    d = Dispatcher()
    release = asyncio.Event()
    order = []

    async def slow(client, m):
        order.append("slow-start")
        await release.wait()          # would deadlock an inline dispatcher
        order.append("slow-done")

    async def after(client, m):
        order.append("after-start")
        release.set()

    d.add(Handler(MESSAGE, slow, group=0))
    d.add(Handler(MESSAGE, after, group=1))
    await d.dispatch(MESSAGE, RecordingClient(), make_msg())
    await asyncio.wait_for(d.wait(), timeout=5)
    assert order == ["slow-start", "after-start", "slow-done"]


@pytest.mark.asyncio
async def test_wait_cancels_stuck_handlers():
    d = Dispatcher()
    stuck = asyncio.Event()

    async def forever(client, m):
        await stuck.wait()

    d.add(Handler(MESSAGE, forever))
    await d.dispatch(MESSAGE, RecordingClient(), make_msg())
    await d.wait(timeout=0.1)  # cancels the stuck task instead of hanging
    assert not d._tasks
