# ✈️ Aerogram

**Automate your Instagram DMs from Python.**

[![CI](https://github.com/charindithjaindu/aerogram/actions/workflows/ci.yml/badge.svg)](https://github.com/charindithjaindu/aerogram/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Read, reply to, and send Instagram direct messages - text, photos, videos,
voice notes, reels and posts - in realtime, using the session you're
already logged into in your browser. No password, no phone emulation.

```python
from aerogram import Client, filters

app = Client("my_session", cookies_file="session/cookies.txt")

@app.on_message(filters.text & ~filters.self)     # text DMs, not my own
async def echo(client, message):
    await message.reply_text(message.text)

app.run()
```

## ✨ What you can do

- **Get every DM the moment it arrives** - text, ❤ likes, links, photos,
  videos, voice notes, GIFs and shared reels/posts, each parsed into a
  `Message` with download links.
- **Reply and send** - text to any username (new conversations included),
  photos, videos and voice notes, and **share reels and posts** as real
  cards.
- **Reactions, typing indicators, mark-as-read, mute.**
- **Read your inbox** - paginated thread list, full message history, user
  lookups by username or id.
- **Download media** - photos, videos and voice notes to disk.
- **Built for bots that stay up** - automatic reconnect without missing
  messages, rate-limit handling, typed errors.

Bots are a few lines of handlers and filters (`filters.photo`,
`filters.private & ~filters.self`, …) - see the [examples](#-examples).

Not supported: message requests (DMs from people who don't follow you)
aren't delivered in realtime until accepted, and end-to-end encrypted chats
aren't reachable.

> ⚠️ **Unofficial.** Aerogram talks to private Instagram endpoints. Automating
> your account violates Instagram's Terms of Service - use it for personal
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
and [`websockets`](https://websockets.readthedocs.io) - nothing heavy.

## 🚀 Getting started

**1. Export your session.** Log into instagram.com in your browser, use the
[Cookie-Editor](https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm)
extension → *Export → Netscape*, and save the file as `session/cookies.txt`.

> 🔐 A `sessionid` cookie **is** full account access. Treat session files like
> passwords - never commit or share them.

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

That's it - the first run fetches your inbox, stores the realtime cursor in
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

# media: photos, videos, voice notes
await app.send_photo("cats.jpg", to="friend")
await app.send_video("clip.mp4", to="friend")
await app.send_voice("note.m4a", to="friend")

# share a reel or post (e.g. one somebody shared with you)
await app.share_media(thread_id, message.media.id, is_clip=True)

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

@app.on_raw_delta                      # every raw realtime update
async def raw(client, delta): ...
```

Incoming messages carry `message.text`, `message.media` (`media_type`:
`photo`, `video`, `voice_media`, `clip` for reels, `media_share` for posts…;
`url`, `id`), `message.user_id`, `message.thread_id`,
`message.is_sent_by_viewer` and `message.raw` (the original payload).

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

*"MQTT or WebSockets?"* - it's not either/or: the connection **is** a
WebSocket; MQTT is the protocol Instagram's servers speak inside it. We mirror
the web client's handshake byte-for-byte. The full reverse-engineering story -
handshake, iris deltas, slide mutations, media uploads - is documented in
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

Issues and PRs welcome - see [CONTRIBUTING.md](CONTRIBUTING.md). Protocol
drift fixes are especially valuable: Instagram ships constantly, and the
debugging workflow in the contributing guide shows how to re-capture the
web client's handshake when something breaks.

## 📜 License & disclaimer

[MIT](LICENSE). This project is not affiliated with, endorsed by, or sponsored
by Meta or Instagram. Use of private APIs may violate Instagram's Terms of
Service; you are responsible for how you use this library. Be kind - don't
build spam.
