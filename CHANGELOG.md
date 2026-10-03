# Changelog

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
