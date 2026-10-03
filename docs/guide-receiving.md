# Receiving messages

Aerogram connects to Instagram's realtime transport on `start()` and feeds
every incoming update through your handlers.

## The message handler

```python
from aerogram import Client, filters

app = Client("my_session", cookies_file="session/cookies.txt")

@app.on_message(filters.private & ~filters.self)
async def handler(client, message):
    print(message.user_id, "→", message.text or message.item_type)

app.run()
```

Each `message` is an [`aerogram.Message`](api.md#aerogram.Message):

| field                | meaning                                             |
|----------------------|-----------------------------------------------------|
| `text`               | text content (empty for media-only messages)        |
| `item_type`          | `"text"`, `"photo"`, `"video"`, `"voice_media"`, …  |
| `media`              | parsed [`Media`](api.md#aerogram.Media) or `None`   |
| `thread_id`          | conversation id — pass it back when sending         |
| `user_id`            | sender's numeric id                                 |
| `message_id` / `item_id` | server message identifiers                      |
| `is_sent_by_viewer`  | `True` if you sent it                               |
| `reactions`          | list of `Reaction`                                  |
| `timestamp_us`       | message time (microseconds)                         |
| `raw`                | the original JSON payload                           |

## Convenience actions on a message

```python
await message.reply_text("hi!")        # quote-free reply into the thread
await message.mark_seen()              # mark that message read
await message.react("❤️")              # reaction
path = await message.download_media()  # media messages only
```

## Own messages

By default Aerogram also delivers messages **you** send (useful for echo
bots and self-notes). To ignore them:

```python
app = Client("my_session", receive_own_messages=False, ...)
```

or use the `~filters.self` filter to still see them elsewhere but not act.

## Other update types

```python
@app.on_message_delete              # someone unsent a message
async def deleted(client, message): ...

@app.on_thread_update               # thread-level inbox change
async def thread_change(client, thread): ...

@app.on_unseen_count                # the inbox badge number changed
async def badge(client, data): ...

@app.on_raw_delta                   # every iris patch operation
async def raw(client, delta):
    print(delta.op, delta.path)
```

`on_raw_delta` is the escape hatch: iris operations arrive as
`delta.op` (`add`/`replace`/`remove`/…), `delta.path`
(`/direct_v2/threads/<tid>/items/<iid>`) and `delta.value`.

## Reliability

- The connection auto-reconnects with exponential backoff + jitter.
- On every reconnect Aerogram re-subscribes and re-syncs iris from the last
  persisted `seq_id`, so **no messages are lost** while you were offline.
- The cursor lives in `<name>.session.json`; deleting that file makes the
  next start resnapshot from the present (old messages are skipped).
- Handlers run isolated: an exception in one handler never breaks the
  connection. Register `@app.on_error` to observe failures.
