"""Send a photo, a video, a voice note and a shared reel to a user.

    python examples/send_media.py some_username
"""
import asyncio
import sys

from aerogram import Client

app = Client("my_session", cookies_file="session/cookies.txt")


async def main(username: str):
    await app.start()
    try:
        # media needs an existing conversation; this starts one if needed
        await app.send_message(username, "incoming media 👇")

        await app.send_photo("cat.jpg", to=username)
        await app.send_video("clip.mp4", to=username)
        await app.send_voice("note.m4a", to=username)      # AAC audio -> voice note

        # share a post or reel by its media id (e.g. message.media.id of a
        # shared card you received)
        thread = await app.find_thread_for_user(username)
        await app.share_media(thread.id, "3999162250127733606", is_clip=True)
    finally:
        await app.stop()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
