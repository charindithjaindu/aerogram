# API reference

Aerogram's public surface is small and Pyrogram-shaped. All network calls are
`async`.

## aerogram.Client

```python
Client(name, *, cookies_file=None, cookies=None, session_string=None,
       user_agent=..., workdir=".", receive_own_messages=True)
```

| param                 | description                                        |
|-----------------------|----------------------------------------------------|
| `name`                | session name; state → `<workdir>/<name>.session.json` |
| `cookies_file`        | Netscape cookies.txt path (Cookie-Editor export)   |
| `cookies`             | or a cookie dict                                   |
| `session_string`      | or a blob from `export_session_string()`           |
| `receive_own_messages`| deliver your own sends to handlers (default True)  |

### Lifecycle

| method                | description                                        |
|-----------------------|----------------------------------------------------|
| `await start()`       | validate, bootstrap cursor, connect realtime       |
| `await stop()`        | disconnect, persist session                        |
| `run()`               | blocking runner with clean Ctrl+C                  |
| `await idle()`        | wait forever (custom loops)                        |
| `export_session_string()` | portable session blob                          |

### Handlers (decorators)

| decorator              | update                                            |
|------------------------|---------------------------------------------------|
| `on_message(filters=None, group=0)` | incoming/new message (`Message`)     |
| `on_message_delete(...)`            | unsent message (`Message`)           |
| `on_thread_update(...)`             | thread change (`Thread`)             |
| `on_unseen_count(...)`              | inbox badge change (dict)            |
| `on_raw_delta(...)`                 | every iris delta (`Delta`)           |
| `on_error`                          | handler exceptions                   |

### Sending

| method                                                   | description              |
|----------------------------------------------------------|--------------------------|
| `await send_message(to, text, reply_to=None)`            | by username/id, creates threads |
| `await send_text(thread_id, text, reply_to=None)`        | MQTT fast path           |
| `await send_photo(photo, to=…, thread_id=…, filename=…)` | upload + send            |
| `await send_like(thread_id)`                             | big ❤️                   |
| `await share_media(thread_id, media_id, is_clip=False)`  | share a post/reel        |
| `await send_reaction(thread_id, item_id, emoji)`         | react to an item         |
| `await unsend_reaction(thread_id, item_id)`              | remove reaction          |
| `await indicate_typing(thread_id, active=True)`          | typing bubble            |

### Reading / housekeeping

| method                                                    | returns                  |
|-----------------------------------------------------------|--------------------------|
| `await get_inbox(cursor=None, limit=20)`                   | `(list[Thread], next_cursor)` |
| `await get_thread_history(thread_id, cursor=None, limit=30)` | `(list[Message], older_cursor)` |
| `await mark_seen(thread_id, item_id)`                      | read receipt             |
| `await hide_thread(thread_id)` / `await mute_thread(thread_id, mute)` | inbox management |
| `await get_presence()`                                     | presence dict            |
| `await user_by_username(username)`                         | `User`                   |
| `await find_thread_for_user(username_or_id)`               | `Thread` or `None`       |
| `await download(url, path=None)`                           | generic file download    |

## aerogram.Message

Fields: `thread_id`, `item_id`, `message_id`, `user_id`, `timestamp_us`,
`item_type`, `text`, `client_context`, `is_sent_by_viewer`, `reactions`,
`media`, `thread` (when cached), `raw`.

Methods: `reply_text`, `mark_seen`, `react`, `download_media`,
`is_media` (property).

## aerogram.Thread

Fields: `id`, `v2_id`, `users` (list[`User`]), `is_group`, `title`, `muted`,
`marked_unread`, `viewer_id`, `messages`, `raw`.
Method: `other_user(viewer_id="")` → the non-viewer participant.

## aerogram.User

Fields: `id`, `username`, `full_name`, `profile_pic_url`, `is_verified`.

## aerogram.Media

Fields: `media_type` (`"photo"`, `"video"`, `"voice_media"`, `"clip"`,
`"xma_share"`, …), `url`, `thumbnail_url`, `id`, `duration_seconds`.

## aerogram.Delta (raw iris update)

Fields: `op`, `path`, `value`, `mutation_token`, `seq_id`, `thread_id`,
`item_id`. Properties: `is_new_message`, `is_removed_message`,
`is_item_update`, `is_thread_update`, `is_unseen_count`, `value_as_dict()`.

## aerogram.filters

`text`, `photo`, `video`, `voice`, `media`, `self`, `me`, `incoming`,
`private`, `group`, `Chat(id)`, `FromUser(uid)`, `Regex(pattern)`,
`create(fn)` — all composable with `&`, `|`, `~`.

## Exceptions

`InstaDMError` (base) → `AuthError`, `ChallengeRequired`, `RateLimited`
(`.retry_after`), `NotFoundError`, `SendError`, `RealtimeError`,
`ProtocolError`.
