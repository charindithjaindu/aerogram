"""Echo bot: replies to every incoming text/photo DM (except your own messages)."""
from aerogram import Client, filters

app = Client("my_session", cookies_file="session/cookies.txt")


@app.on_message(filters.text & ~filters.self)
async def echo_text(client, message):
    await message.mark_seen()
    await message.reply_text(message.text)


@app.on_message(filters.photo & ~filters.self)
async def echo_photo(client, message):
    await message.mark_seen()
    path = await message.download_media("downloads/")
    await message.reply_text("nice pic!")


@app.on_error
def on_error(client, update, exc):
    print("handler error:", exc)


if __name__ == "__main__":
    print("echo bot running — Ctrl+C to stop")
    app.run()
