"""Forward every incoming DM to a Telegram chat and greet the sender.

    export TELEGRAM_BOT_TOKEN=123456:ABC...   # from @BotFather
    export TELEGRAM_CHAT_ID=123456789         # where to forward
    python examples/forward_to_telegram.py
"""
import os

import httpx

from aerogram import Client, filters

TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

app = Client("my_session", cookies_file="session/cookies.txt")
tg = httpx.AsyncClient(timeout=15)


async def send_to_telegram(text: str) -> None:
    r = await tg.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      json={"chat_id": TG_CHAT_ID, "text": text})
    if r.status_code != 200:
        print(f"Telegram error {r.status_code}: {r.text}")


async def sender_name(client, message) -> str:
    try:
        thread = message.thread or await client.get_thread(message.thread_id)
        user = next((u for u in thread.users if u.id == message.user_id and u.username), None)
        user = user or await client.user_by_id(message.user_id)
        return f"@{user.username}"
    except Exception:
        return f"user {message.user_id}"


@app.on_message(filters.private & ~filters.self)
async def forward(client, message):
    media = message.media
    if message.text:
        body = message.text
    elif media and media.url:
        body = f"[{media.media_type}] {media.url}"
    else:
        body = f"[{message.item_type}]"

    # forward first: the reply can fail (e.g. restricted threads)
    await send_to_telegram(f"📩 Instagram DM from {await sender_name(client, message)}:\n\n{body}")
    try:
        await message.reply_text("hi")
    except Exception as e:
        print("reply failed:", e)


if __name__ == "__main__":
    print("forwarding DMs to Telegram - Ctrl+C to stop")
    app.run()
