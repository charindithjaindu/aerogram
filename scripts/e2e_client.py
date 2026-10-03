"""Full-stack e2e: username sends, photo send, media receive + filters + download.

Everything targets a conversation you control (set AEROGRAM_TEST_USER).
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, ".")
from aerogram import Client, Message, filters  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("e2e")

TARGET_USERNAME = os.environ.get("AEROGRAM_TEST_USER", "")

texts: list[Message] = []
photos: list[Message] = []
app = Client("e2e", cookies_file="session/cookies.txt", workdir="/tmp/aerogram-e2e")


@app.on_message(filters.text & filters.self)
async def on_text(client, message):
    log.info("[filter:text] %s %r", message.message_id or message.item_id, message.text)
    texts.append(message)


@app.on_message(filters.photo & filters.self)
async def on_photo(client, message):
    log.info("[filter:photo] %s media=%s url=%s", message.message_id or message.item_id,
             message.media and message.media.media_type,
             (message.media.url[:60] + "…") if message.media and message.media.url else None)
    photos.append(message)


# 1x1 red JPEG
import base64  # noqa: E402
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIy"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIA"
    "AhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQA"
    "AAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3"
    "ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWm"
    "p6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEA"
    "AwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSEx"
    "BhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElK"
    "U1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3"
    "uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD3+iii"
    "gD//2Q==")


async def wait_for(bucket, match, timeout=30):
    for _ in range(int(timeout / 0.5)):
        if any(match(m) for m in bucket):
            return next(m for m in bucket if match(m))
        await asyncio.sleep(0.5)
    raise TimeoutError("expected message never arrived")


async def main() -> None:
    if not TARGET_USERNAME:
        raise SystemExit("set AEROGRAM_TEST_USER=<your own username> to run this e2e")
    await app.start()
    try:
        # -- 1. username-based text send (GraphQL slide mutation) --------------
        marker = f"aerogram username send {asyncio.get_event_loop().time():.0f}"
        sent = await app.send_message(TARGET_USERNAME, marker)
        log.info("send_message ok: msg_id=%s thread=%s", sent.message_id, sent.thread_id)
        assert sent.message_id.startswith("mid.$")
        got = await wait_for(texts, lambda m: m.text == marker)
        log.info("=== username text send -> iris -> filters.text OK ===")

        # -- 2. photo send + receive + download -------------------------------
        sent_photo = await app.send_photo(JPEG, to=TARGET_USERNAME, filename="e2e.jpg")
        log.info("send_photo ok: msg_id=%s", sent_photo.message_id)
        assert sent_photo.message_id.startswith("mid.$")
        got_photo = await wait_for(photos, lambda m: m.message_id == sent_photo.message_id)
        log.info("=== photo send -> iris -> filters.photo OK (media=%s) ===",
                 got_photo.media.media_type if got_photo.media else "?")
        assert got_photo.media and got_photo.media.url, "no media URL parsed"
        path = await got_photo.download_media("/tmp/aerogram-e2e/downloaded.jpg")
        size = os.path.getsize(path)
        assert size > 0, "downloaded file is empty"
        log.info("=== download_media OK (%d bytes, server re-encodes uploads) ===", size)

        # -- 3. reply using the thread id discovered from the inbox -----------
        thread_id = got_photo.thread_id
        await app.send_text(thread_id, "aerogram e2e thread-path send")
        log.info("thread-path send_text OK")

        log.info("=== ALL E2E CHECKS PASSED ===")
    finally:
        await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
