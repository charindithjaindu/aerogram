# Contributing to Aerogram

Thanks for your interest in improving Aerogram! Since this library talks to a
**reverse-engineered, unofficial API**, contributions need a little extra care.

## Setting up

```bash
git clone https://github.com/charindithjaindu/aerogram.git
cd aerogram
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/
```

## Ground rules

1. **Never commit session data.** Cookies, `*.session.json` files and captured
   traffic contain full account access. They are git-ignored - keep it that
   way. Scrub any IDs/tokens from logs and issue reports.
2. **Test against your own account only.** Integration scripts under
   `scripts/` require `AEROGRAM_TEST_USER` to point at *your own* username so
   test messages never land in a stranger's inbox.
3. **Respect Instagram.** No bulk-mailing helpers, no scraping-at-scale
   features, no anti-rate-limit circumvention. Aerogram is for personal
   automation of conversations you're part of.
4. **Offline unit tests stay offline.** Anything that hits the live API goes
   in `scripts/` (manual), not `tests/`.

## Finding breakage (protocol drift)

Instagram ships constantly. When endpoints change, these are the debug tools:

```bash
python scripts/probe_iris.py          # end-to-end MQTT + iris handshake check
python scripts/capture_ws.py          # CDP capture of the real web client
python scripts/capture_send_ui.py     # capture the web client sending a DM
```

If you fix drift, please open a PR describing exactly which handshake changed -
that knowledge is the most valuable part of this repo.

## Pull requests

- Keep PRs focused; one fix or feature each.
- Add unit tests for parsing/codec changes.
- Update `docs/` when user-facing behaviour changes.
- Run `python -m pytest tests/` before pushing.

## Reporting issues

Include: Python version, the full traceback, and - if it's protocol drift -
the failing endpoint plus what you observed. **Redact all cookies, session
files and user ids.**
