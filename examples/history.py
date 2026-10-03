"""Dump recent inbox threads and the history of one thread."""
import asyncio

from aerogram import Client

app = Client("my_session", cookies_file="session/cookies.txt")


async def main():
    await app.start()

    threads, cursor = await app.get_inbox(limit=10)
    for t in threads:
        other = t.other_user()
        last = t.messages[0] if t.messages else None
        print(f"[{t.id}] {other.username if other else t.title or 'group'}: "
              f"{(last.text or last.item_type) if last else '(empty)'}")

    if threads:
        print("\n--- history of", threads[0].id, "---")
        messages, _ = await app.get_thread_history(threads[0].id, limit=30)
        for m in reversed(messages):
            who = "me" if m.is_sent_by_viewer else m.user_id
            body = m.text or (m.media.media_type if m.media else m.item_type)
            print(f"{who}: {body}")

    await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
