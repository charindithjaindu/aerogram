# Troubleshooting

## "Instagram redirected to login — the session cookies are invalid…"

The `sessionid` was invalidated server-side (logged out, password change,
checkpoint, or Instagram rotated it). Fix: export fresh cookies
([guide](getting-started.md#2-export-your-session)) and re-create the
session. **A single 302 right after a fresh export is normal** — sessions
take a few seconds to propagate; Aerogram retries through it.

## 429 "Please wait a few minutes…"

You're rate-limited. Aerogram honors `Retry-After` automatically, but heavy
loops (mass DMs, aggressive polling) will get you throttled or
challenge-gated. Slow down — the account pays the price, not the library.

## ChallengeRequired

Instagram flagged the account (unusual activity). Log in via the browser,
complete whatever check it shows, then export fresh cookies. Repeated
automation against a challenged account can lock it.

## Handlers stop receiving after my laptop slept

They don't — Aerogram reconnects and replays the gap. If a reconnect truly
fails you'll see warnings in the log; check network/proxy. Bump verbosity:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## Sends fail with `send rejected`

- Realtime not connected yet — send after `start()` resolves and the
  "iris subscribed" log line appears.
- Recipient thread doesn't accept messages (user blocked you, deactivated
  account, Meta AI special thread) — the backend returns
  error `1545041` ("recipient unavailable"). Not a bug.

## Messages arrive twice

You probably registered the same handler twice, or you're running two
Client instances on one session file. One client per session file.

## `AuthError: page render came back without a DTSG token`

The GraphQL layer needs a logged-in page render to extract CSRF tokens.
This means the cookies died between the check and the render — export fresh
ones.

## The protocol broke (Instagram shipped something)

Symptoms: CONNACK failures, iris subscribe errors, empty message streams.
Re-run the debug tools to compare against the live web client:

```bash
python scripts/probe_iris.py       # end-to-end handshake + send + receive
python scripts/capture_ws.py       # CDP capture of the real web client
```

`capture_ws.py` + Chrome's DevTools show the web client's current handshake;
diff it against `aerogram/iris.py` and `aerogram/mqtt.py`. PRs fixing drift
are very welcome — see [CONTRIBUTING.md](../CONTRIBUTING.md).

## Ethics / account safety

- Automate only conversations you're part of; never bulk-message strangers.
- Prefer reactive bots (reply to incoming) over blasting.
- Every automated action is attributable to your account. Instagram's ToS
  prohibits automation — moderate use, human-like pacing.
