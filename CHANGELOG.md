# Changelog

## Unreleased

Instagram removed the web REST DM API (`/direct_v2/inbox/`, `threads/…`,
`seen`/`hide`/`mute`, `get_presence`, `broadcast/…` now return the HTML 404
page). Reads moved to the GraphQL queries the web client itself uses — see
the new *Reads* section of `docs/protocol.md`.

- **Fixed: `Client.start()` failed with `NotFoundError`.** The iris cursor
  and the first inbox page now come from one `PolarisDirectInboxQuery`
  call, which also warms the thread cache; `/direct_v2/get_badge_count/`
  is the fallback and the reconnect gap-heal.
- **Fixed:** `get_inbox()` (paginated), `get_thread_history()` (paginated,
  newest first), `find_thread_for_user()`, `thread_id_for_user()`,
  `send_message()` / `send_photo()` by username, `mute_thread()`.
- **Fixed: `user_by_username()`** — resolves through the profile page
  document + `PolarisProfilePageContentQuery`; the REST profile endpoints
  were 429ing persistently.
- `mark_seen()` uses only the GraphQL mutation (REST fallback is gone) and
  marks up to the newest message when the `mid.$…` id isn't cached.
- New: `Client.get_thread()`, `Client.user_by_id()`,
  `Thread.parse_slide()` / `Message.parse_slide()` / `Media.parse_slide()`,
  `Thread.fbid`; `mute_thread(seconds=…)`.
- `graphql()` now maps 429 → `RateLimited` and login redirects →
  `AuthError`, handles multi-payload (`@defer`) responses, and re-renders
  the token page once when `fb_dtsg` expires. The ~800KB token page is
  therefore cached for 6h instead of 30min.
- `hide_thread()` and `get_presence()` now raise `InstaDMError` (no web
  replacement exists). Removed `HttpApi.upload_photo()` /
  `broadcast_photo()` (dead `rupload`/`configure_photo` path; `send_photo()`
  already used mercury uploads) and the REST `mark_seen`/`hide_thread`/
  `get_presence`/`user_info_by_*` wrappers.
- `X-ASBD-ID` updated to the web client's current `359341`.

## 0.1.1 — reliability & performance fixes

- **Fixed: sending from inside a handler deadlocked realtime.** Handlers now
  run as their own asyncio tasks (Pyrogram-style) instead of inline in the
  MQTT read loop, so `await message.reply_text(...)` inside `on_message`
  returns normally instead of timing out after 15s while stalling all
  incoming events. `Client.stop()` waits for in-flight handlers (10s cap).
- **Fixed: half-open TCP connections could leave the bot silently deaf
  forever.** An RX watchdog closes the connection when nothing arrives for
  several keepalive intervals (the broker only speaks when answering our
  PINGREQs), forcing the normal reconnect path.
- PUBACKs are now sent *before* dispatching application handlers, so a slow
  handler can no longer trigger broker redelivery (duplicate messages).
- Failed connects no longer leak the websocket; iris resnapshot loops force
  a reconnect instead of staying connected-but-deaf.
- The iris `seq_id` cursor is persisted periodically (every 30s of cursor
  movement), not only on graceful `stop()` — a crash replays at most ~30s
  of deltas instead of everything since the last shutdown.
- Memory growth bounded: cached threads (default 500) and per-thread
  message history (default 200) are trimmed; a username/user-id → thread
  index removes the per-send inbox scan (1–3 HTTP fetches per send).
- Partial iris thread patches merge into the cache instead of clobbering
  populated entries (users, `v2_id`, history).
- Send failures now raise the typed `SendError` (was bare `RuntimeError`);
  unpairable send responses (no `client_context`, several sends in flight)
  time out instead of being attributed to the wrong caller.
- HTTP layer: POSTs are no longer retried on transport errors (avoids
  double-sends), `Retry-After` accepts decimals and caps in-request sleeps
  at 60s, page-token fetches are serialized behind a lock, and
  `download()` streams to disk instead of buffering whole media files.
- `Message.thread` is a declared field; `send_text` converts its ms
  timestamp to µs like the other send paths; `websockets>=14` floor
  matches the `additional_headers=` API actually used.
- 41 offline unit tests (was 31).

## 0.1.0 — first public release

- Cookie-file session bootstrap (Netscape/cookies.txt export) with persisted
  iris `seq_id` cursor for gap-free realtime resume.
- Realtime DM receive via the web client's MQTT 3.1-over-WebSocket transport,
  automatic reconnect with backoff, resnapshot handling.
- Sends: text by username or thread id (MQTT fast path + slide mutations),
  likes, media shares, reactions, typing indicators.
- Photo sending (mercury attachment upload → `IGDirectMediaSendMutation`).
- Media receiving with parsed URLs and `download_media()`.
- Mark-thread-as-read via the web Relay mutation.
- Pyrogram-style handler API: `on_message`, `on_message_delete`,
  `on_thread_update`, `on_unseen_count`, `on_raw_delta`; composable filters.
- 31 offline unit tests.
