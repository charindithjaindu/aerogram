# Sessions

## What a session is

Aerogram authenticates with your **browser cookies** (mainly `sessionid`,
`csrftoken`, `ds_user_id`) and keeps realtime state alongside them:

| file                        | contents                                            |
|-----------------------------|-----------------------------------------------------|
| `session/cookies.txt`       | your Netscape-format cookie export (input)          |
| `<name>.session.json`       | cookies + device id + iris `seq_id` cursor (state)  |

The `seq_id` cursor is the reliability core: it records how far the realtime
stream has been consumed. On every reconnect/restart Aerogram re-subscribes
from that cursor and the broker **replays everything missed** — no message
gaps, ever.

## Creating a session

```python
# from a Cookie-Editor Netscape export (recommended)
app = Client("my_session", cookies_file="session/cookies.txt")

# from a dict
app = Client("my_session", cookies={"sessionid": "...", "csrftoken": "...", "ds_user_id": "..."})

# from a portable string (exported earlier via export_session_string)
app = Client("my_session", session_string="...")
```

If you pass no source, `Client` loads `<name>.session.json` if it exists.

## Exporting / moving

```python
s = app.export_session_string()   # base64 blob — store it safely
# on another machine:
app = Client("my_session", session_string=s)
```

The string contains the cookies **and** the realtime cursor — moving it means
the new instance continues gap-free.

## Lifecycle

- `await app.start()` validates cookies, fetches the inbox (bootstrap cursor),
  connects realtime.
- `await app.stop()` disconnects and persists the session file.
- `app.run()` = start + idle + signal-handled clean stop.

## Hygiene

- **Never commit** `*.session.json` or cookie exports. They are git-ignored by
  default; keep it that way.
- Logging out of instagram.com in the exporting browser invalidates the
  `sessionid` — export from a browser session you'll keep, or re-export when
  you rotate sessions.
- Sessions can be invalidated server-side at any time (checkpoint, password
  change, suspicion). Aerogram raises `AuthError` when that happens; export
  fresh cookies to continue. See [troubleshooting.md](troubleshooting.md).
- Instagram shows all *active sessions* under Settings → Security — the
  cookie-based session appears there like any browser login, and you can kill
  it remotely.
