# ✈️ Aerogram

**A Pyrogram-style async library for Instagram direct messages.**

[![CI](https://github.com/charindithjaindu/aerogram/actions/workflows/ci.yml/badge.svg)](https://github.com/charindithjaindu/aerogram/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Send, receive, react to and download Instagram DMs from Python — with the same
cookie-based session your browser already has, realtime message delivery over
the same transport instagram.com uses, and an API shaped like
[Pyrogram](https://github.com/pyrogram/pyrogram).

> An *aerogram* is an air-mail letter: a lightweight message sent across the
> world. That's all a DM is.

```python
from aerogram import Client, filters

app = Client("my_session", cookies_file="session/cookies.txt")

@app.on_message(filters.text & ~filters.self)
async def echo(client, message):
    await message.reply_text(message.text)

app.run()
```

## ✨ Features

- **Realtime receive** — messages arrive in milliseconds over the web client's
  lightspeed gateway stream, with automatic reconnect and **gap-free
  resume** (the iris `seq_id` cursor is replayed on every reconnect).
- **Send by username** — `send_message("friend", "hi!")` resolves the thread
  for you and even starts brand-new conversations.
- **Photos, videos, voice notes** — `send_photo` / `send_video` /
  `send_voice` (or `send_media`) upload and send; incoming media arrives
  parsed with download URLs (`download_media()`).
- **Reactions, typing indicators, mark-as-read, likes** — all supported.
- **Pyrogram-style handlers & filters** — `@app.on_message`,
  `filters.text & ~filters.self`, `filters.photo | filters.video`, custom
  filters, handler groups.
- **Reliable by design** — retry with backoff on 5xx, `Retry-After` handling
  for 429s, idempotent message sends, cookie-rotation tracking, typed errors.
- **No app emulation, no password** — authenticate by exporting your browser
  cookies once.

> ⚠️ **Unofficial.** Aerogram talks to private Instagram endpoints. Automating
> your account violates Instagram's Terms of Service — use it for personal
> automation of conversations you're part of, at modest volumes. Accounts can
> be rate-limited or challenge-gated. See [docs/troubleshooting.md](docs/troubleshooting.md).

## 📦 Install

```bash
pip install git+https://github.com/charindithjaindu/aerogram.git
```

or for development:

```bash
git clone https://github.com/charindithjaindu/aerogram.git
cd aerogram
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/
```

Requires Python 3.11+. Dependencies: [`httpx`](https://www.python-httpx.org)
and [`websockets`](https://websockets.readthedocs.io) — nothing heavy.

## 🚀 Getting started

**1. Export your session.** Log into instagram.com in your browser, use the
[Cookie-Editor](https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm)
extension → *Export → Netscape*, and save the file as `session/cookies.txt`.

> 🔐 A `sessionid` cookie **is** full account access. Treat session files like
> passwords — never commit or share them.

**2. Build your bot.**

```python
from aerogram import Client, filters

app = Client("my_session", cookies_file="session/cookies.txt")

@app.on_message(filters.private & ~filters.self)   # DMs, not groups, not me
async def handler(client, message):
    await message.mark_seen()
    await message.reply_text(f'you said: "{message.text}"')

app.run()
```

That's it — the first run fetches your inbox, stores the realtime cursor in
`my_session.session.json`, and your handler fires for every incoming message.

📖 **Full guide:** [docs/getting-started.md](docs/getting-started.md) ·
[Receiving](docs/guide-receiving.md) · [Sending](docs/guide-sending.md) ·
[Media](docs/guide-media.md) · [Filters](docs/guide-filters.md) ·
[Sessions](docs/guide-sessions.md) · [API reference](docs/api.md) ·
[Troubleshooting](docs/troubleshooting.md)

## 📝 A taste of the API

```python
# send by username (creates the conversation if needed)
await app.send_message("friend", "dinner at 8?")

# send a photo (upload + slide mutation)
await app.send_photo("cats.jpg", to="friend")

# react, type, mark read
await app.send_reaction(thread_id, message.item_id, "🔥")
await app.indicate_typing(thread_id, active=True)
await app.mark_seen(thread_id, message.item_id)

# inbox & history
threads, cursor = await app.get_inbox(limit=20)
messages, older = await app.get_thread_history(thread_id, limit=30)

# other update types
@app.on_message_delete
async def unsent(client, message): ...

@app.on_raw_delta                      # every iris patch, unfiltered
async def raw(client, delta): ...
```

Incoming messages are rich objects: `message.text`, `message.media.url`,
`message.user_id`, `message.thread_id`, `message.is_sent_by_viewer`,
`message.reactions`, `message.raw` (the original payload).

## 🧪 Examples

| file | what it does |
|------|--------------|
| [`examples/echo_bot.py`](examples/echo_bot.py) | echoes every DM back: text, photos, videos, voice notes, and re-shares reels/posts |
| [`examples/forward_to_telegram.py`](examples/forward_to_telegram.py) | forwards every DM to a Telegram chat and greets the sender |
| [`examples/send_media.py`](examples/send_media.py) | sends a photo, video, voice note and a shared reel to a user |
| [`examples/send_message.py`](examples/send_message.py) | typing indicator + send into an existing thread |
| [`examples/history.py`](examples/history.py) | dumps the inbox and one thread's history |

## 🏗️ How it works

Aerogram talks to the same infrastructure the instagram.com web app does:

| Layer    | Transport                                                                 |
|----------|---------------------------------------------------------------------------|
| Receive  | DGW stream `gateway.instagram.com/ws/lightspeed` (slide deltas)            |
| Sends    | MQTT 3.1 over WebSocket `/ig_send_message` + Relay mutations on `/api/graphql` |
| Reading  | GraphQL queries on `/api/graphql` (inbox, threads, profiles)               |
| Auth     | Browser cookies                                                            |

*"MQTT or WebSockets?"* — it's not either/or: the connection **is** a
WebSocket; MQTT is the protocol Instagram's servers speak inside it. We mirror
the web client's handshake byte-for-byte. The full reverse-engineering story
— handshake, iris deltas, slide mutations, media uploads — is documented in
[docs/protocol.md](docs/protocol.md).

```
aerogram/
├── client.py      # Client facade: decorators, lifecycle, high-level actions
├── lightspeed.py  # DGW lightspeed stream: incoming slide deltas
├── iris.py        # MQTT iris subscribe + /ig_send_message channel
├── mqtt.py        # minimal MQTT 3.1 (MQIsdp) codec + asyncio WSS client
├── http_api.py    # REST + GraphQL wrapper: retries, backoff, cookie rotation
├── session.py     # cookies.txt / session.json, iris seq_id cursor
├── dispatcher.py  # handler groups
├── filters.py     # composable filters
└── types.py       # Message / Thread / User / Media models
```

## 🤝 Contributing

Issues and PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Protocol
drift fixes are especially valuable: Instagram ships constantly, and the
debugging workflow in the contributing guide shows how to re-capture the
web client's handshake when something breaks.

## 📜 License & disclaimer

[MIT](LICENSE). This project is not affiliated with, endorsed by, or sponsored
by Meta or Instagram. Use of private APIs may violate Instagram's Terms of
Service; you are responsible for how you use this library. Be kind — don't
build spam.
