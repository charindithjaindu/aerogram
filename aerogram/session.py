"""Session state: cookies, device ids, and the iris sync cursor.

A session can be bootstrapped from:
  * a Netscape cookies.txt export (Cookie-Editor "Export > Netscape"),
  * an explicit cookie dict,
  * or a previously-saved instaDM session file (which also carries the iris
    ``seq_id`` so realtime can resume without gaps after a restart).
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from .errors import AuthError

IG_DOMAIN = ".instagram.com"


def parse_netscape_file(path: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        cookies[parts[5]] = parts[6]
    return cookies


@dataclass
class Session:
    cookies: dict[str, str] = field(default_factory=dict)
    device_id: str = ""          # uuid used for MQTT cid / d param
    seq_id: int = 0              # last iris sequence id seen (for gap-free resume)
    snapshot_at_ms: int = 0
    user_id: str = ""            # ds_user_id (numeric IG id)
    saved_at: float = 0.0

    # -- constructors -------------------------------------------------------

    @classmethod
    def from_cookies_file(cls, path: str) -> "Session":
        return cls(cookies=parse_netscape_file(path))

    @classmethod
    def from_cookies(cls, cookies: dict[str, str]) -> "Session":
        return cls(cookies=dict(cookies))

    @classmethod
    def from_file(cls, path: str) -> "Session":
        data = json.load(open(path, encoding="utf-8"))
        return cls(
            cookies=data.get("cookies", {}),
            device_id=data.get("device_id", ""),
            seq_id=data.get("seq_id", 0),
            snapshot_at_ms=data.get("snapshot_at_ms", 0),
            user_id=data.get("user_id", ""),
            saved_at=data.get("saved_at", 0.0),
        )

    # -- persistence --------------------------------------------------------

    def save(self, path: str) -> None:
        self.saved_at = time.time()
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({
                "cookies": self.cookies,
                "device_id": self.device_id,
                "seq_id": self.seq_id,
                "snapshot_at_ms": self.snapshot_at_ms,
                "user_id": self.user_id,
                "saved_at": self.saved_at,
            }, f, indent=1)
        os.replace(tmp, path)

    # -- accessors ----------------------------------------------------------

    @property
    def sessionid(self) -> str:
        return self.cookies.get("sessionid", "")

    @property
    def ds_user_id(self) -> str:
        return self.cookies.get("ds_user_id", "")

    @property
    def csrftoken(self) -> str:
        return self.cookies.get("csrftoken", "")

    @property
    def ig_did(self) -> str:
        return self.cookies.get("ig_did", "")

    @property
    def cookie_header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    def set_cookie_from_set_cookie(self, value: str) -> None:
        """Track server-side cookie rotation (e.g. csrftoken/sessionid)."""
        first = value.split(";", 1)[0]
        if "=" not in first:
            return
        name, _, val = first.partition("=")
        if val in ('""', ""):
            self.cookies.pop(name.strip(), None)
        else:
            self.cookies[name.strip()] = val

    def viewer_uuid(self) -> str:
        """The FB-style viewer id the web client uses for MQTT ``u``.

        Derived from the ``rur`` cookie (``CCO,<uuid>,...``), which is exactly
        where the web client gets it.
        """
        rur = urllib.parse.unquote(self.cookies.get("rur", ""))
        parts = rur.split(",")
        if len(parts) >= 2 and parts[1].isdigit():
            return parts[1]
        return ""

    def validate(self) -> None:
        missing = [c for c in ("sessionid", "csrftoken", "ds_user_id") if not self.cookies.get(c)]
        if missing:
            raise AuthError(f"session is missing required cookies: {', '.join(missing)}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "cookies": self.cookies,
            "device_id": self.device_id,
            "seq_id": self.seq_id,
            "snapshot_at_ms": self.snapshot_at_ms,
            "user_id": self.user_id,
            "saved_at": self.saved_at,
        }
