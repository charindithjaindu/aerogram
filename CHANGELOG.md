# Changelog

## Unreleased

- **Fixed: `Client.start()` failed with `NotFoundError` on
  `/direct_v2/inbox/`.** Instagram removed the REST inbox from the web API
  (it now returns a 404 page). The iris cursor (`seq_id` /
  `snapshot_at_ms`) is now seeded from `/direct_v2/get_badge_count/`, which
  is still served, and the reconnect gap-heal uses it too. The inbox is
  still tried to warm the thread cache, but a 404 there is no longer fatal.
- New `HttpApi.badge_count()`.
- Known gap: `get_inbox()`, `find_thread_for_user()` and sending by
  username still depend on the removed endpoint. `reply_text()` and
  `send_text(thread_id, ...)` are unaffected.

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
