"""Send a message to an existing thread, with typing indicator and reactions."""
import asyncio

from aerogram import Client

app = Client("my_session", cookies_file="session/cookies.txt")


async def main():
    async with asyncio.timeout(60):
        await app.start()

        # find a 1:1 thread by username via the inbox
        threads, _ = await app.get_inbox(limit=20)
        for t in threads:
            other = t.other_user()
            print(f"thread {t.id}  with {other.username if other else '?'}")

        # pick the first thread and interact
        thread = threads[0]
        await app.indicate_typing(thread.id, active=True)
        msg = await app.send_text(thread.id, "hello from instaDM!")
        print("sent:", msg.item_id or msg.text)
        await app.indicate_typing(thread.id, active=False)

        await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
