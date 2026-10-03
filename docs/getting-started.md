# Getting started

This guide takes you from zero to a running DM bot in ~5 minutes.

## 1. Install

```bash
pip install git+https://github.com/charindithjaindu/aerogram.git
```

Python 3.11 or newer is required.

## 2. Export your session

Aerogram authenticates with your browser's cookies — no password, no app
login.

1. Log into [instagram.com](https://www.instagram.com) in your desktop browser.
2. Install the [Cookie-Editor](https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm)
   extension.
3. On instagram.com, click the extension → **Export** → **Netscape** format.
4. Save the export as `session/cookies.txt` inside your project folder.

> 🔐 **Danger:** the `sessionid` cookie grants full access to your account.
> Never commit it, paste it into chats, or share exported files. If a session
> leaks, log out of that browser session (Instagram → Settings → Security →
> Log out of that session) or change your password to invalidate it.

## 3. Create the client

```python
from aerogram import Client

app = Client("my_session", cookies_file="session/cookies.txt")
```

- `"my_session"` names the session — state is stored in `my_session.session.json`
  next to your script (cookies, device id and the realtime cursor).
- Alternatives: `cookies={"sessionid": ...}` (dict) or
  `session_string=...` (portable base64 blob from `app.export_session_string()`).

## 4. Handle messages

```python
from aerogram import Client, filters

app = Client("my_session", cookies_file="session/cookies.txt")

@app.on_message(filters.private & ~filters.self)
async def handler(client, message):
    print(f'{message.user_id} says: {message.text}')
    await message.reply_text("got it!")

app.run()
```

`app.run()` blocks, connects realtime, and handles Ctrl+C for a clean
shutdown. Inside your own asyncio app use `await app.start()` / `await app.stop()`
instead.

## 5. Send something

```python
# one-shot script
import asyncio
from aerogram import Client

app = Client("my_session", cookies_file="session/cookies.txt")

async def main():
    await app.start()
    await app.send_message("your_friends_username", "hello from aerogram!")
    await app.stop()

asyncio.run(main())
```

The first message to someone creates the conversation automatically.

## 6. Keep the session alive

- Don't log out of instagram.com in the browser you exported from — that
  invalidates the session.
- If requests suddenly redirect to login, export fresh cookies and re-run.
- A single 302 right after a fresh export is normal (server-side session
  propagation); Aerogram retries through it.

Next: [Receiving messages](guide-receiving.md) ·
[Sending](guide-sending.md) · [Media](guide-media.md)
