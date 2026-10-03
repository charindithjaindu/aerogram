# Media

## Receiving media

Incoming media messages arrive through the normal `on_message` handler with
`message.media` populated:

```python
from aerogram import filters

@app.on_message(filters.photo & ~filters.self)
async def on_photo(client, message):
    print("photo url:", message.media.url)

@app.on_message(filters.video)
async def on_video(client, message):
    print("video url:", message.media.url)

@app.on_message(filters.voice)
async def on_voice(client, message):
    print("voice note:", message.media.duration_seconds, "seconds")
```

`message.media` is an `aerogram.Media`:

| field              | meaning                                       |
|--------------------|-----------------------------------------------|
| `media_type`       | `"photo"`, `"video"`, `"voice_media"`, `"clip"`, `"xma_share"`, … |
| `url`              | best-quality direct CDN URL (video for videos, audio for voice)   |
| `thumbnail_url`    | poster/thumbnail URL where applicable         |
| `id`               | media id                                      |
| `duration_seconds` | voice notes / videos                          |
| `raw`              | original media payload                        |

Shared posts and reels arrive as `media_share` / `clip` / `xma_share` items —
`media.thumbnail_url` gives you the preview, and `message.raw` the full data.

## Downloading

```python
path = await message.download_media()                    # auto filename
path = await message.download_media("downloads/")        # into a folder
path = await message.download_media("pics/cat.jpg")      # exact path
```

`download_media` streams the CDN URL to disk. Note that Instagram
**re-encodes** uploaded images, so a downloaded photo you sent yourself may
differ byte-wise from the original (dimensions stay).

## Sending photos

```python
await app.send_photo("cats.jpg", to="some_username")
await app.send_photo(image_bytes, thread_id=thread_id, filename="cats.jpg")
```

Under the hood (see [protocol.md](protocol.md) for the whole story):

1. the file is uploaded through the web client's *mercury attachment upload*
   service, returning an `attachment_fbid`;
2. the `IGDirectMediaSendMutation` GraphQL mutation publishes it into the
   thread.

The thread must already exist for photos — call
`send_message(user, "...")` once first for brand-new contacts.

## Limitations

- **Video/voice uploads are not implemented** — receiving and downloading
  them works fine.
- Story replies and group admin actions are not implemented.
- E2EE ("encrypted") DM threads are not accessible through this API surface.
