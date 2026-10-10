# The protocol (reverse-engineering notes)

This page documents **how Aerogram talks to Instagram** — the full,
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

## Layer 1 — realtime: MQTT 3.1 over WebSocket

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

MQTT **3.1** (`MQIsdp`, protocol level 3 — not 3.1.1):

```
fixed header: 0x10 <varint length>
variable header: "MQIsdp" | level=0x03 | flags=0x82 (clean + username) | keepalive=15
payload: clientId="mqttwsclient", username=<auth JSON>, no password
```

The MQTT **username is a JSON blob** — this is the auth:

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

- `u` is the FB-style viewer id — derivable from the `rur` cookie
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
replays every iris event since your last acknowledged sequence id — across
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

## Layer 2 — sends

Two channels, mirroring the web client:

### MQTT fast path: `/ig_send_message`

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

Note: the broker does **not** PUBACK QoS-1 publishes on `/ig_*` topics — the
application-level response topic is the ack.

### GraphQL slide mutations: `/api/graphql`

Used by the web client for username-initiated sends, media, and mark-read.
Auth is trickier than REST:

- the POST needs a `fb_dtsg` token, which only appears in a **logged-in page
  render** of instagram.com (fetching the HTML with plain headers yields an
  anonymous shell with an empty DTSG — you must send full browser document
  headers: `Sec-Fetch-*`, `sec-ch-ua`, `Upgrade-Insecure-Requests`, and the
  `dpr` cookie);
- `jazoest` = `"2" + sum(ord(c) for c in fb_dtsg)`;
- `lsd` (from the same page) goes in the form **and** the `X-FB-LSD` header;
- the request **must** carry `Sec-Fetch-Site: same-origin`,
  `Sec-Fetch-Mode: cors`, `Sec-Fetch-Dest: empty` — otherwise the server
  answers error `1357004` ("close and re-open your browser window");
- error `1357054` = mutation input problem; `1357004` = auth problem.

Text send (`IGDirectTextSendMutation`, doc `26911679871773184`) — variables
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

Gotcha: `ig_thread_igid` is the thread's **`thread_fbid`** (not `thread_key`
— the web composer passes `thread.thread_fbid`), while the
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
"metadata": {"ig_thread_igid": "<long thread_id>"}}` — unlike the send
mutation, the web client passes the long `thread_id` here.

### Reads (GraphQL queries)

The web client no longer reads DMs over REST; everything goes through
persisted queries on `/api/graphql` (same form/tokens as the mutations).
Relay provider variables (`__relay_internal__pv__…`) must be sent too —
see `HttpApi` for the exact sets.

| query (doc id) | variables | result |
|---|---|---|
| `PolarisDirectInboxQuery` (`27909866362025854`) | `device_id_for_iris_subscription` | `data.get_slide_mailbox_for_iris_subscription`: first 15 threads, mailbox `id`, and `iris_inactive_subscription_uq_seq_id` (the iris `seq_id`) |
| `IGDThreadListOffMsysPaginationQuery` (`28774058922187457`) | `id` (mailbox id), `cursor`, `count`, `folder: "INBOX"` (required) | `data.fetch__SlideMailbox.threads_by_folder` |
| `IGDThreadDetailQuery` (`29432273173041378`) | `thread_fbid` = the thread's **`thread_key`** | `data.get_slide_thread_nullable.as_ig_direct_thread` with newest messages |
| `IGDMessageListOffMsysQuery` (`29380270148264352`) | `id` = `thread_fbid`, `after` = `slide_messages.page_info.end_cursor`, `first` | `data.fetch__SlideThread.as_ig_direct_thread.slide_messages` (older) |
| `PolarisProfilePageContentQuery` (`28036671149327607`) | `id` (user pk) | `data.user` |
| `IGDInboxInfoMuteToggleOffMsysMutation` (`26360506043651125`) | `thread_fbid`, `mute_seconds` (-1 forever, 0 unmute), `offline_threading_id` | mute state |

A thread carries three ids: `thread_id` (long, `34028236…` — iris paths,
MQTT sends, media send, mark-read), `thread_key` (thread detail query,
`/direct/t/<key>/` URLs) and `thread_fbid` (`ig_thread_igid` in text sends,
mute, message-list pagination). Key and fbid are often different. Messages are
`slide_messages` nodes: `message_id` `mid.$…`, `sender.igid` = user pk,
`text_body`, `timestamp_ms`, `content.__typename` for media.

Username → user id has no cheap query; the web client resolves it from the
profile page document (`"profile_id":"<pk>"`), which aerogram mirrors.

## Layer 3 — REST

Only `GET /api/v1/direct_v2/get_badge_count/` (iris `seq_id` fallback) is
still used. The web REST DM routes — `direct_v2/inbox`,
`direct_v2/threads/<tid>`, `…/seen/`, `…/hide/`, `…/mute/`,
`direct_v2/get_presence`, `direct_v2/threads/broadcast/…` — now return the
HTML 404 page, and `users/web_profile_info` / `users/<id>/info` are heavily
429'd. Hide and presence have no web replacement.

## Keeping this honest

Every claim above was verified against the live web client and a real
account; the debug scripts in `scripts/` re-verify each layer. When
something breaks, re-capture and diff — and please PR the fix.
