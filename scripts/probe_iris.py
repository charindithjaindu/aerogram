"""End-to-end verification of the reverse-engineered Instagram DM realtime protocol.

Uses the real library stack:
1. Session from cookies file, fresh inbox fetch -> seq_id
2. aerogram Realtime (MQTT web handshake + iris subscribe)
3. send a self-DM over the MQTT send channel (/ig_send_message)
4. expect it back on /ig_message_sync as an iris patch delta -> dispatcher-style callback
"""
import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, ".")
from aerogram.http_api import HttpApi  # noqa: E402
from aerogram.iris import Realtime  # noqa: E402
from aerogram.session import Session  # noqa: E402
from aerogram.types import Thread  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("probe")

COOKIES_FILE = "session/cookies.txt"
# your own (or a consenting) account to test against - never a stranger
TEST_USERNAME = os.environ.get("AEROGRAM_TEST_USER", "")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")


async def main() -> None:
    if not TEST_USERNAME:
        raise SystemExit("set AEROGRAM_TEST_USER=<your own username> to run this probe")
    session = Session.from_cookies_file(COOKIES_FILE)
    session.device_id = session.ig_did
    api = HttpApi(session, UA)

    inbox = await api.inbox()
    session.seq_id = int(inbox.get("seq_id") or 0)
    session.snapshot_at_ms = int(inbox.get("snapshot_at_ms") or 0)
    threads = [Thread.parse(t) for t in inbox.get("inbox", {}).get("threads", [])]
    log.info("inbox ok: seq_id=%s, %d threads", session.seq_id, len(threads))

    me = session.ds_user_id
    # target the "Meta AI" thread: it's an AI chat, so test sends don't bother
    # any human, and the AI replies give us real incoming events to verify.
    target = None
    for t in threads:
        for u in t.users:
            if u.username == TEST_USERNAME:
                target = t
    if target is None:
        log.error("no thread for %r in inbox; cannot run safe end-to-end test", TEST_USERNAME)
        return
    log.info("target thread: %s (%s)", target.id, TEST_USERNAME)

    got_send_response = asyncio.Event()
    got_message = asyncio.Event()
    iris_subscribed = asyncio.Event()
    seen_deltas = []

    async def on_delta(delta):
        seen_deltas.append(delta)
        if delta.is_new_message and delta.thread_id == target.id:
            log.info("NEW MESSAGE on self-thread: %s", json.dumps(delta.value_as_dict())[:400])
            got_message.set()

    async def on_connect(is_reconnect):
        log.info("realtime connected (reconnect=%s)", is_reconnect)

    async def watch_iris(topic, payload, qos):
        if topic == "/ig_sub_iris_response":
            log.info("IRIS RESPONSE: %s", payload.decode()[:200])
            try:
                if json.loads(payload).get("succeeded"):
                    iris_subscribed.set()
            except Exception:
                pass

    rt = Realtime(session, UA, on_delta=on_delta, on_connect=on_connect)
    # hook the iris response by wrapping the mqtt on_packet
    orig_on_packet = rt._on_packet

    async def on_packet(topic, payload, qos):
        await watch_iris(topic, payload, qos)
        await orig_on_packet(topic, payload, qos)

    rt._on_packet = on_packet
    await rt.start()
    try:
        await asyncio.wait_for(iris_subscribed.wait(), timeout=20)
        log.info("iris subscribed - now sending")
    except asyncio.TimeoutError:
        log.error("iris subscribe got no response in 20s - sends will likely fail")

    log.info("=== sending self-DM over MQTT send channel ===")
    marker = f"aerogram e2e probe {asyncio.get_event_loop().time():.0f}"
    resp = await rt.send_text(target.id, marker)
    log.info("send response: %s", json.dumps(resp)[:300] if resp else "(no payload)")
    got_send_response.set()

    await asyncio.wait_for(got_message.wait(), timeout=20)
    log.info("=== SUCCESS: MQTT send + iris receive round-trip complete ===")
    log.info("deltas seen: %d (seq_id now %s)", len(seen_deltas), session.seq_id)
    await rt.stop()


if __name__ == "__main__":
    asyncio.run(main())
