"""Echo bot: sends every incoming DM straight back to its sender.

- text, ❤ likes and links      -> the same text
- photos, videos, voice notes   -> downloaded and re-uploaded
- shared reels and posts        -> the same reel / post shared back as a card
- other share cards             -> their link
"""
import os
import tempfile

from aerogram import Client, filters

app = Client("my_session", cookies_file="session/cookies.txt")

# media_type -> (file name for the re-upload, send as a voice note)
REUPLOAD = {
    "photo": ("echo.jpg", False),
    "video": ("echo.mp4", False),
    "voice_media": ("echo.m4a", True),
    "animated_media": ("echo.gif", False),
}


@app.on_message(filters.private & ~filters.self)
async def echo(client, message):
    media = message.media

    if media and media.media_type in REUPLOAD:
        filename, voice = REUPLOAD[media.media_type]
        with tempfile.TemporaryDirectory() as tmp:
            path = await message.download_media(os.path.join(tmp, filename))
            await client.send_media(path, thread_fbid=message.thread_fbid,
                                    thread_id=message.thread_id or None, voice=voice)
    elif media and media.media_type in ("clip", "media_share") and media.id and message.thread_id:
        # shared reel / post: share the same one back as a real card
        await client.share_media(message.thread_id, media.id,
                                 is_clip=media.media_type == "clip")
    elif media and media.url:  # other share cards (profiles, stories, links)
        await message.reply_text(media.url)
    elif message.text:
        await message.reply_text(message.text)
    else:
        print(f"skipped {message.item_type} message")


@app.on_error
def on_error(client, update, exc):
    print("handler error:", exc)


if __name__ == "__main__":
    print("echo bot running - Ctrl+C to stop")
    app.run()
