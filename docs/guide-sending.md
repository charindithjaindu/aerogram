# Sending messages

## By username (recommended)

```python
await app.send_message("some_username", "hello!")
```

- Accepts a username **or** a numeric user id.
- Finds the existing 1:1 thread for that user automatically.
- If no conversation exists yet, starts one (Instagram's
  `recipient_igids` send path).
- Returns the sent [`Message`](api.md#aerogram.Message) (with
  `message_id`).

## By thread id (fast path)

```python
await app.send_text(thread_id, "hello!")
```

Goes over the realtime MQTT send channel — lowest latency, confirmed by
`/ig_send_message_response`. Use this when you already know the `thread_id`
(e.g. from an incoming `message.thread_id`).

## Replying (quoting)

```python
@app.on_message(filters.text & ~filters.self)
async def handler(client, message):
    await app.send_message("me", None)          # nonsense — don't
    await message.reply_text("quoting you!")    # simple
    # or explicitly:
    await app.send_text(message.thread_id, "quoting you!", reply_to=message)
```

`reply_to=` makes Instagram show the quoted-message bubble.

## Likes, shares, reactions, typing

```python
await app.send_like(thread_id)                       # the big ❤️
await app.share_media(thread_id, media_id)           # share a post/reel
await app.send_reaction(thread_id, item_id, "🔥")
await app.unsend_reaction(thread_id, item_id)
await app.indicate_typing(thread_id, active=True)    # show the "typing…" bubble
await app.indicate_typing(thread_id, active=False)
await app.mark_seen(thread_id, item_id)              # read receipt
```

`media_id` for shares is a post/reel id (visible in instagram.com URLs, e.g.
`/p/<shortcode>/` → resolve via the post page; the incoming `message.media.id`
of a shared post also works).

## Photos

```python
await app.send_photo("cats.jpg", to="some_username")
await app.send_photo(image_bytes, thread_id=thread_id, filename="cats.jpg")
```

See [Media](guide-media.md) for how uploads work and current limitations
(videos/voice notes: receiving works, sending is not implemented yet).

## Housekeeping

```python
await app.mute_thread(thread_id)                   # mute forever
await app.mute_thread(thread_id, seconds=8 * 3600)  # mute for 8h
await app.mute_thread(thread_id, mute=False)       # unmute
```

## Error handling

All failures raise typed errors from `aerogram`:

```python
from aerogram import RateLimited, AuthError, NotFoundError

try:
    await app.send_message(user, "hi")
except RateLimited as e:
    print("back off for", e.retry_after, "seconds")
except AuthError:
    print("session dead — export fresh cookies")
except NotFoundError:
    print("no such user/thread")
```

Sends are idempotent-ish: each carries a unique `client_context`
(offline threading id), so Instagram won't double-deliver if you retry a
timed-out send yourself.
