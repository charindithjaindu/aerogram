# The protocol (reverse-engineering notes)

This page documents **how Aerogram talks to Instagram** - the full,
reproducible wire-level story. It exists so that (a) contributors can fix
protocol drift without starting from zero, and (b) future maintainers can
verify behaviour against the live web client.

All tokens/ids below are placeholders, obviously.

## How it was mapped

1. Fetch instagram.com HTML and JS bundles; grep for the DM transport
   (`edge-chat.instagram.com`, `mqtt`, `sub_iris`, `PolarisDirectMQTT` …).
2. Drive headless Chrome with CDP, inject cookies, open
   `/direct/inbox/`, and record **every WebSocket frame** the real client
   sends (`scripts/capture_ws.py`).
3. Decode the captured CONNECT byte-for-byte, replicate it in Python
   (`scripts/probe_iris.py`), iterate until the broker accepts and messages
   flow.

## Layer 0 - receiving: the lightspeed DGW stream

This is instagram.com's receive channel and aerogram's source of truth
for incoming messages. The edge-chat MQTT iris subscription (Layer 1) is
not reliable on its own: for a while in 2026 it pushed nothing to web
sessions, and as of October 2026 it pushes again but with partial items
(see [Two receive channels](#two-receive-channels)). instagram.com receives on

```
wss://gateway.instagram.com/ws/lightspeed?x-dgw-appid=936619743392459
  &x-dgw-appversion=0&x-dgw-authtype=6:0&x-dgw-version=5
  &x-dgw-uuid=<viewer uuid from rur cookie>&x-dgw-tier=prod
  &x-dgw-deviceid=<fresh uuid4 per socket>
```

with the session cookies, `Origin: https://www.instagram.com`.

**DGW framing** (binary WS messages, several frames may share one):
`type:u8 | stream:u16le | length:u24le | payload`; Ping (9), Pong (10)
and Empty (2) are a single byte. Types: EstabStream 15, Data 13, Ack 12,
EndOfData 14. A Data payload starts with `ack:u16le` - bit 15 set means
"ack me", answered by an Ack frame whose payload is the 15-bit id.

**Session:** send EstabStream with payload `{}` plus one Data frame:

```json
{"app_id": "936619743392459", "device_id": "<same uuid>", "request_id": 1, "type": 2,
 "payload": "{\"database\":223,\"epoch_id\":0,\"failure_count\":0,
   \"last_applied_cursor\":\"{\\\"seq_id\\\":<iris seq_id>}\",
   \"sync_params\":\"{\\\"user_agent\\\":\\\"WMI Web\\\",\\\"snapshot_at_ms\\\":<ms>,
     \\\"prevalidated_graphql_doc_id\\\":\\\"28859056920377149\\\"}\",\"version\":-3}"}
```

The server answers EstabStream `{"code":200}`, acks the cursor, then
pushes Data frames `{"request_id": null, "payload": "<base64 protobuf>"}`.
The protobuf wraps a JSON string
`[{"data":{"slide_delta_processor":[…]}}]` (IGDSlideDeltaProcessorQuery);
each delta has a `__typename` (`SlideUQPPNewMessage`, …), `uq_seq_id` and
`thread_fbid`, and new-message deltas carry the same `slide_messages` node
shape as the GraphQL reads. The `seq_id`/`snapshot_at_ms` come from
`PolarisDirectInboxQuery` (`iris_inactive_subscription_uq_seq_id`).

**Message requests** (threads in `system_folder: PENDING`) get no deltas
at all until accepted; replying to the thread accepts it.

### Two receive channels

When iris pushes, every new message arrives on both channels. Measured on
two accounts in October 2026 (12 messages, time from the sender's publish):

| channel | arrival | item shape |
|---|---|---|
| MQTT iris `/ig_message_sync` | 640-1030 ms | old REST item (`item_type`, `text`, `clip`, ...) |
| lightspeed | 740-1150 ms (60-130 ms later) | slide node, same as the GraphQL reads |

Both carry the same `message_id` (`mid.$…`) and offline threading id
(`client_context` on iris, `offline_threading_id` on lightspeed), and both
cursors are the same sequence (`seq_id` on iris equals `uq_seq_id` on
lightspeed), so either one can resume the other.

The iris copy is incomplete for anything but text: a shared post arrives as
`{"item_type": "media_share"}` with **no media at all**, a shared reel nests
the media under `clip.clip`, and a voice note has no audio URL yet. Aerogram
therefore dispatches:

- **text** from whichever channel delivers first (identical on both);
- **media and share cards** from lightspeed; the iris copy is held for
  `Client.MEDIA_FALLBACK_DELAY` (3 s) and only dispatched if the lightspeed
  copy never arrives (or immediately when lightspeed is down). Held copies
  are flushed on `stop()`, because the cursor has already moved past them.

Duplicates are dropped by `message_id` / threading id.

## Layer 1 - MQTT 3.1 over WebSocket (sends; legacy receive)

The web client connects to:

```
wss://edge-chat.instagram.com/chat?sid=<random 53-bit int>&cid=<uuid4>
```

- `sid` is client-generated: `random(0, 2^53)`
- `cid` is a per-connection device UUID (also used in the auth JSON)

The WebSocket upgrade carries the browser **cookies** (auth!), `Origin:
https://www.instagram.com`, and a normal browser User-Agent. No
`Sec-WebSocket-Protocol`.

### CONNECT

MQTT **3.1** (`MQIsdp`, protocol level 3 - not 3.1.1):

```
fixed header: 0x10 <varint length>
variable header: "MQIsdp" | level=0x03 | flags=0x82 (clean + username) | keepalive=15
payload: clientId="mqttwsclient", username=<auth JSON>, no password
```

The MQTT **username is a JSON blob** - this is the auth:

```json
{
  "a": "<User-Agent>",
  "aid": 936619743392459,
  "asi": {"Accept-Language": "en-US"},
  "chat_on": true,
  "cp": 3,
  "ct": "cookie_auth",
  "d": "<device uuid == cid>",
  "dc": "",
  "ecp": 10,
  "fg": true,
  "mqtt_sid": "",
  "no_auto_fg": true,
  "pm": [],
  "s": <same numeric sid as the URL>,
  "st": [],
  "u": "<viewer fb-id, e.g. 17841471011833178>"
}
```

Notes:

- `u` is the FB-style viewer id - derivable from the `rur` cookie
  (`rur=CCO,<viewer>,<...>:<hmac>`), not the `ds_user_id`.
- auth is bound to the **cookies** in the WS upgrade; CONNACK `rc=0` means
  accepted, `rc=4/5` means the cookies were rejected.

### After CONNACK

1. `SUBSCRIBE /ig_message_sync`, `/ig_send_message_response`,
   `/ig_sub_iris_response` (QoS 0)
2. `PUBLISH /ig_sub_iris` (QoS 1):

```json
{
  "seq_id": <last seen iris sequence id, 0 for a fresh session>,
  "snapshot_app_version": "web",
  "snapshot_at_ms": <ms timestamp>,
  "subscription_type": "message"
}
```

3. The broker answers on `/ig_sub_iris_response`:

```json
{"succeeded": true, "seq_id": 496, "latest_seq_id": 496, ...}
```

Failure modes: `error_type: 1` = "Server force resnapshot" (stale cursor →
refetch the REST inbox for a fresh `seq_id` and re-subscribe),
`error_type: 2` = transient (retry with backoff, 1s→64s).

**The `seq_id` mechanism is what makes delivery gap-free:** the broker
replays every iris event since your last acknowledged sequence id - across
reconnects, restarts, even process crashes (Aerogram persists it in the
session file).

### Iris events: `/ig_message_sync`

Payloads are a JSON **array** of patches:

```json
[{
  "event": "patch",
  "seq_id": 497,
  "mutation_token": null,
  "data": [
    {"op": "add",
     "path": "/direct_v2/threads/<tid>/items/<item_id>",
     "value": "{...full item JSON, string-encoded...}"}
  ]
}]
```

Aerogram tracks the highest `seq_id` and turns each op into a `Delta`:

| path pattern                                        | meaning              |
|-----------------------------------------------------|----------------------|
| `/direct_v2/threads/<tid>/items/<id>` (`op=add`)     | new message          |
| `…/items/<id>` (`op=remove`)                         | unsend               |
| `…/items/<id>/…`                                     | item update (reactions, seen state…) |
| `/direct_v2/inbox/threads/<tid>`                     | thread add/replace   |
| `/direct_v2/inbox/unseen_count`                      | badge counter        |

## Layer 2 - sends

Two channels, mirroring the web client. Measured from a pushed message to
the reply arriving on the other account (October 2026, warm connections):

| path | call returns | reply arrives |
|---|---|---|
| GraphQL `IGDirectTextSendMutation` | ~0.7 s | ~0.62 s |
| MQTT `/ig_send_message` | ~1.0 s | ~0.85 s |

The MQTT ack comes 100-300 ms *after* the recipient already has the
message. `Message.reply_text` uses the GraphQL path whenever the
`thread_fbid` is known, and MQTT otherwise.

### MQTT: `/ig_send_message`

```json
{"action": "send_item", "item_type": "text", "text": "hi",
 "thread_id": "<long id>", "client_context": "<offline threading id>",
 "device_id": "<ig_did>", "mutation_token": "<otid>"}
```

Confirmed by a JSON ack on `/ig_send_message_response`
(`{"status": "ok", ...}` or `item_ack` with `status_code`).
The same channel (per the web client) supports `item_type` values
`like`, `media_share`/`clip_share` (with `media_id`), `reaction`
(`reaction_status` created/deleted + `emoji`), and the
`indicate_activity` action for typing.

Note: the broker does **not** PUBACK QoS-1 publishes on `/ig_*` topics - the
application-level response topic is the ack.

### GraphQL slide mutations: `/api/graphql`

Used by the web client for username-initiated sends, media, and mark-read.
Auth is trickier than REST:

- the POST needs a `fb_dtsg` token, which only appears in a **logged-in page
  render** of instagram.com (fetching the HTML with plain headers yields an
  anonymous shell with an empty DTSG - you must send full browser document
  headers: `Sec-Fetch-*`, `sec-ch-ua`, `Upgrade-Insecure-Requests`, and the
  `dpr` cookie);
- `jazoest` = `"2" + sum(ord(c) for c in fb_dtsg)`;
- `lsd` (from the same page) goes in the form **and** the `X-FB-LSD` header;
- the request **must** carry `Sec-Fetch-Site: same-origin`,
  `Sec-Fetch-Mode: cors`, `Sec-Fetch-Dest: empty` - otherwise the server
  answers error `1357004` ("close and re-open your browser window");
- error `1357054` = mutation input problem; `1357004` = auth problem.

Text send (`IGDirectTextSendMutation`, doc `26911679871773184`) - variables
are **top-level** (the query wraps them into `data` itself):

```json
{"text": {"sensitive_string_value": "hi"},
 "ig_thread_igid": "<thread_fbid>",        // existing thread
 "recipient_igids": ["<uid>"],              // …or a NEW conversation (mutually exclusive)
 "offline_threading_id": "<otid>",
 "send_attribution": "igd_web_chat_tab:in_thread",
 "mentions": [], "mentioned_user_ids": [],
 "commands": null, "sampled": null,
 "replied_to_item_id": null, "reply_to_message_id": null,
 "replied_to_client_context": null,
 "forwarded_from_thread_id": null, "is_forwarded_from_own_message": null}
```

Gotcha: `ig_thread_igid` is the thread's **`thread_fbid`** (not `thread_key` -
the web composer passes `thread.thread_fbid`), while the
media mutation wants the long `thread_id`.

Media send (`IGDirectMediaSendMutation`, doc `25766288509716264`):

1. upload the file: `POST /ajax/mercury/upload.php` (multipart, file field
   **`farr`**, plus the standard auth form params) → response
   `payload.metadata["0"].fbid` = `attachment_fbid`;
2. mutate with `{"attachment_fbid": ..., "offline_threading_id": ...,
   "thread_id": "<long id>", "reply_to_message_id": null,
   "forwarded_from_thread_id": null, "is_forwarded_from_own_message": null}`.

Mark-thread-as-read (`useIGDMarkThreadAsReadMutation`, doc
`27399783383056109`): `{"data": {"item_id": "", "message_id": "mid.$…"},
"metadata": {"ig_thread_igid": "<long thread_id>"}}` - unlike the send
mutation, the web client passes the long `thread_id` here.

### Reads (GraphQL queries)

The web client no longer reads DMs over REST; everything goes through
persisted queries on `/api/graphql` (same form/tokens as the mutations).
Relay provider variables (`__relay_internal__pv__…`) must be sent too -
see `HttpApi` for the exact sets.

| query (doc id) | variables | result |
|---|---|---|
| `PolarisDirectInboxQuery` (`27909866362025854`) | `device_id_for_iris_subscription` | `data.get_slide_mailbox_for_iris_subscription`: first 15 threads, mailbox `id`, and `iris_inactive_subscription_uq_seq_id` (the iris `seq_id`) |
| `IGDThreadListOffMsysPaginationQuery` (`28774058922187457`) | `id` (mailbox id), `cursor`, `count`, `folder: "INBOX"` (required) | `data.fetch__SlideMailbox.threads_by_folder` |
| `IGDThreadDetailQuery` (`29432273173041378`) | `thread_fbid` = the thread's **`thread_key`** | `data.get_slide_thread_nullable.as_ig_direct_thread` with newest messages |
| `IGDMessageListOffMsysQuery` (`29380270148264352`) | `id` = `thread_fbid`, `after` = `slide_messages.page_info.end_cursor`, `first` | `data.fetch__SlideThread.as_ig_direct_thread.slide_messages` (older) |
| `PolarisProfilePageContentQuery` (`28036671149327607`) | `id` (user pk) | `data.user` |
| `IGDInboxInfoMuteToggleOffMsysMutation` (`26360506043651125`) | `thread_fbid`, `mute_seconds` (-1 forever, 0 unmute), `offline_threading_id` | mute state |

A thread carries three ids: `thread_id` (long, `34028236…` - iris paths,
MQTT sends, media send, mark-read), `thread_key` (thread detail query,
`/direct/t/<key>/` URLs) and `thread_fbid` (`ig_thread_igid` in text sends,
mute, message-list pagination). Key and fbid are often different. Messages are
`slide_messages` nodes: `message_id` `mid.$…`, `sender.igid` = user pk,
`text_body`, `timestamp_ms`, `content.__typename` for media.

Username → user id has no cheap query; the web client resolves it from the
profile page document (`"profile_id":"<pk>"`), which aerogram mirrors.

### Connections and retries

instagram.com closes an idle HTTP connection after 60-120 s, and a cold
TCP+TLS handshake added 0.4-1 s to the next request. Aerogram keeps pooled
connections for 50 s (`KEEPALIVE_EXPIRY`, safely under the server's limit
so a closed socket is never reused) and, after 40 s without traffic, makes
a tiny `GET /robots.txt` to keep one warm (`HttpApi.keep_warm`). The page
tokens are fetched at `start()` instead of on the first send. HTTP/2 was
measured and is no faster here.

Mutations (sends, uploads) are retried **only** when the connection could
not be opened, since nothing reached the server. Any later failure (a read
timeout, a dropped connection) is raised, because the server may already
have applied the send and a retry could post it twice.

## Layer 3 - REST

Only `GET /api/v1/direct_v2/get_badge_count/` (iris `seq_id` fallback) is
still used. The web REST DM routes - `direct_v2/inbox`,
`direct_v2/threads/<tid>`, `…/seen/`, `…/hide/`, `…/mute/`,
`direct_v2/get_presence`, `direct_v2/threads/broadcast/…` - now return the
HTML 404 page, and `users/web_profile_info` / `users/<id>/info` are heavily
429'd. Hide and presence have no web replacement.

## Keeping this honest

Every claim above was verified against the live web client and a real
account; the debug scripts in `scripts/` re-verify each layer. When
something breaks, re-capture and diff - and please PR the fix.
