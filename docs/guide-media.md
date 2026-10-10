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

Shared posts and reels arrive as share cards: `media.media_type` is
`"clip"` for a reel, `"media_share"` for a post and `"xma_share"` for other
cards (profiles, stories, links). `media.id` is the post/reel media id,
`media.url` its link and `media.thumbnail_url` the preview image.

Share one (back) as a real card — the bare media id works for both:

```python
if message.media and message.media.media_type in ("clip", "media_share"):
    await client.share_media(message.thread_id, message.media.id,
                             is_clip=message.media.media_type == "clip")
```

## Downloading

```python
path = await message.download_media()                    # auto filename
path = await message.download_media("downloads/")        # into a folder
path = await message.download_media("pics/cat.jpg")      # exact path
```

`download_media` streams the CDN URL to disk. Note that Instagram
**re-encodes** uploaded images, so a downloaded photo you sent yourself may
differ byte-wise from the original (dimensions stay).

## Sending photos, videos and voice notes

```python
await app.send_photo("cats.jpg", to="some_username")
await app.send_photo(image_bytes, thread_id=thread_id, filename="cats.jpg")
await app.send_video("clip.mp4", to="some_username")
await app.send_voice("note.m4a", thread_id=thread_id)        # AAC/mp4 audio
await app.send_media(path, thread_fbid=message.thread_fbid)  # generic
```

The mime type is guessed from the filename; `send_voice` marks the upload
as a voice clip so it shows up as a playable voice note.

Realtime pushes can arrive before a voice note's CDN url exists;
`download_media()` re-reads the message from the thread history (a few
retries) when the url is missing.

Under the hood (see [protocol.md](protocol.md) for the whole story):

1. the file is uploaded through the web client's *mercury attachment upload*
   service, returning an `attachment_fbid`;
2. the `IGDirectMediaSendMutation` GraphQL mutation publishes it into the
   thread.

The thread must already exist for photos — call
`send_message(user, "...")` once first for brand-new contacts.

## Limitations

- GIF/sticker *sending* (`IGDirectAnimatedMediaSendMutation`) and media
  shares/forwards are not implemented; received GIFs can be re-uploaded as
  files.
- Story replies and group admin actions are not implemented.
- E2EE ("encrypted") DM threads are not accessible through this API surface.
