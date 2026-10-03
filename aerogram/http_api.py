"""Low-level REST wrapper for Instagram's web API (cookie auth).

Only GET endpoints and uploads are needed here for DM work — sends ride the
realtime MQTT channel (exactly what instagram.com's own client does).
Includes the reliability plumbing: retries with backoff, 429/Retry-After
handling, csrf-cookie rotation tracking and typed error mapping.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import re
import uuid
from typing import Any, Optional

import httpx

from .errors import AuthError, ChallengeRequired, InstaDMError, NotFoundError, RateLimited
from .session import Session

log = logging.getLogger("aerogram.http")

BASE = "https://www.instagram.com/api/v1"

DEFAULT_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

MAX_RETRIES = 4


class HttpApi:
    def __init__(self, session: Session, user_agent: str = DEFAULT_UA,
                 timeout: float = 20.0) -> None:
        self.session = session
        self._ua = user_agent
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=False)
        self._page_cache: dict = {}
        self._req_counter = _Counter()

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- core request plumbing ----------------------------------------------

    def _headers(self, post: bool = False, referer: str = "https://www.instagram.com/") -> dict[str, str]:
        h = {
            "Cookie": self.session.cookie_header,
            "User-Agent": self._ua,
            "X-IG-App-ID": "936619743392459",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "*/*",
            "Referer": referer,
            "Origin": "https://www.instagram.com",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        }
        if post:
            h["X-CSRFToken"] = self.session.csrftoken
            h["Content-Type"] = "application/x-www-form-urlencoded"
            h["X-IG-D"] = "www"
            h["X-WebDeviceId"] = self.session.ig_did
        return h

    def _absorb_cookies(self, resp: httpx.Response) -> None:
        for raw in resp.headers.get_list("set-cookie"):
            self.session.set_cookie_from_set_cookie(raw)

    async def _request(self, method: str, url: str, *, params=None, data=None,
                       files=None) -> httpx.Response:
        post = method == "POST"
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = await self._client.request(
                    method, url, params=params, data=data, files=files,
                    headers=self._headers(post=post))
            except httpx.HTTPError as e:
                last_exc = e
                await asyncio.sleep(0.5 * (2 ** attempt) + random.random())
                continue

            self._absorb_cookies(resp)

            if resp.status_code == 429:
                retry_after = resp.headers.get("retry-after")
                seconds = float(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt
                if attempt == MAX_RETRIES - 1:
                    raise RateLimited("Instagram rate limit hit (429)", retry_after=seconds)
                log.warning("429, backing off %.1fs", seconds)
                await asyncio.sleep(seconds)
                continue

            if resp.status_code >= 500:
                last_exc = InstaDMError(f"Instagram server error {resp.status_code}")
                await asyncio.sleep(0.5 * (2 ** attempt) + random.random())
                continue

            if resp.status_code in (301, 302, 303, 307, 308):
                # login redirect => dead/invalid session
                loc = resp.headers.get("location", "")
                if "/accounts/login" in loc or "/accounts/challenge" in loc or "/challenge" in loc:
                    raise AuthError(
                        "Instagram redirected to login/challenge — the session "
                        "cookies are invalid, expired or the account needs a checkpoint. "
                        "Export fresh cookies and rebuild the session.")
                raise InstaDMError(f"Unexpected redirect {resp.status_code} -> {loc}")

            if resp.status_code == 401:
                body = resp.text[:200]
                if "challenge" in body.lower():
                    raise ChallengeRequired(f"Challenge required: {body}")
                raise AuthError(f"Unauthorized (401): {body}")

            if resp.status_code == 403:
                body = resp.text[:300]
                if "challenge" in body.lower():
                    raise ChallengeRequired(f"Challenge required: {body}")
                raise InstaDMError(f"Forbidden (403): {body}")

            if resp.status_code == 404:
                raise NotFoundError(f"Not found: {url}")

            return resp
        raise last_exc or InstaDMError("request failed")

    async def get_json(self, path: str, params: dict | None = None) -> dict:
        resp = await self._request("GET", BASE + path, params=params)
        return self._json(resp)

    async def post_json(self, path: str, data: dict, params: dict | None = None) -> dict:
        resp = await self._request("POST", BASE + path, params=params, data=data)
        return self._json(resp)

    @staticmethod
    def _json(resp: httpx.Response) -> dict:
        try:
            return resp.json()
        except Exception as e:
            raise InstaDMError(
                f"Non-JSON response ({resp.status_code}): {resp.text[:200]!r}") from e

    # -- DM endpoints ---------------------------------------------------------

    async def inbox(self, cursor: str | None = None, limit: int = 20) -> dict:
        params: dict[str, Any] = {"persistentBadging": "true", "use_unified_inbox": "true",
                                  "limit": limit}
        if cursor:
            params["cursor"] = cursor
        return await self.get_json("/direct_v2/inbox/", params)

    async def thread(self, thread_id: str, cursor: str | None = None, limit: int = 30) -> dict:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return await self.get_json(f"/direct_v2/threads/{thread_id}/", params)

    async def get_presence(self) -> dict:
        return await self.get_json("/direct_v2/get_presence/", {})

    async def mark_seen(self, thread_id: str, item_id: str) -> dict:
        data = {"_uuid": self.session.device_id or str(uuid.uuid4()),
                "use_unified_inbox": "true"}
        return await self.post_json(f"/direct_v2/threads/{thread_id}/items/{item_id}/seen/", data)

    async def hide_thread(self, thread_id: str) -> dict:
        data = {"_uuid": self.session.device_id or str(uuid.uuid4())}
        return await self.post_json(f"/direct_v2/threads/{thread_id}/hide/", data)

    async def mute_thread(self, thread_id: str, mute: bool = True) -> dict:
        path = "/direct_v2/threads/{}/mute/" if mute else "/direct_v2/threads/{}/unmute/"
        data = {"_uuid": self.session.device_id or str(uuid.uuid4())}
        return await self.post_json(path.format(thread_id), data)

    async def user_info_by_username(self, username: str) -> dict:
        return await self.get_json("/users/web_profile_info/", {"username": username})

    async def user_info_by_id(self, user_id: str) -> dict:
        return await self.get_json(f"/users/{user_id}/info/")

    # -- uploads --------------------------------------------------------------

    async def upload_photo(self, data: bytes, filename: str = "photo.jpg") -> str:
        """Upload a photo via rupload; returns the ``upload_id`` for broadcasting."""
        upload_id = str(int(asyncio.get_event_loop().time() * 1000)) + str(random.randint(100, 999))
        url = f"https://i.instagram.com/rupload_igphoto/{upload_id}"
        headers = {
            "Cookie": self.session.cookie_header,
            "User-Agent": self._ua,
            "X-IG-App-ID": "936619743392459",
            "X-CSRFToken": self.session.csrftoken,
            "X-Entity-Type": "image/jpeg",
            "X-Entity-Name": filename,
            "X-Entity-Length": str(len(data)),
            "X-Instagram-Rupload-Params": '{"media_type":1,"upload_id":"' + upload_id + '"}',
            "Offset": "0",
            "Content-Type": "application/octet-stream",
        }
        resp = await self._client.post(url, content=data, headers=headers)
        if resp.status_code != 200:
            raise InstaDMError(f"photo upload failed: {resp.status_code} {resp.text[:200]}")
        js = resp.json()
        return str(js.get("upload_id") or upload_id)

    async def broadcast_photo(self, thread_ids: list[str], upload_id: str,
                              caption: str = "") -> dict:
        data = {
            "upload_id": upload_id,
            "caption": caption,
            "thread_ids": _json_list(thread_ids),
            "_uuid": self.session.device_id or str(uuid.uuid4()),
            "allow_full_aspect_ratio": "true",
        }
        return await self.post_json("/direct_v2/threads/broadcast/configure_photo/", data)

    # -- misc -----------------------------------------------------------------

    async def download(self, url: str, path: str | None = None) -> str:
        resp = await self._client.get(url, headers={"User-Agent": self._ua})
        resp.raise_for_status()
        if path is None:
            from urllib.parse import urlparse
            name = urlparse(url).path.split("/")[-1].split("?")[0] or "media.bin"
            path = name
        with open(path, "wb") as f:
            f.write(resp.content)
        return path

    # -- GraphQL (web "Slide" mutations, e.g. mark-thread-read) ----------------

    async def _page_tokens(self) -> dict:
        """Load instagram.com as a browser would and extract the session tokens
        /api/graphql requires (fb_dtsg, lsd, haste session, spin revision...).

        The full browser document-header set is required — without
        ``Sec-Fetch-*``/``sec-ch-ua`` Instagram renders an anonymous shell with
        an empty DTSG token.
        """
        cached = self._page_cache
        if cached and time.time() - cached["t"] < 1800:
            return cached
        h = {
            "Cookie": self.session.cookie_header + "; dpr=2",
            "User-Agent": self._ua,
            "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                       "image/avif,image/webp,*/*;q=0.8"),
            "Accept-Language": "en-US,en;q=0.9",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Sec-Fetch-Dest": "document",
            "sec-ch-ua": '"Chromium";v="141", "Not?A_Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
        }
        resp = await self._client.get("https://www.instagram.com/direct/inbox/", headers=h)
        self._absorb_cookies(resp)
        html = resp.text
        tokens: dict[str, str] = {"t": time.time()}

        def grab(pattern: str) -> str:
            m = re.search(pattern, html)
            return m.group(1) if m else ""

        tokens["fb_dtsg"] = grab(r'"DTSGInitialData",\[\],\{"token":"([^"]+)"')
        tokens["lsd"] = grab(r'"LSD",\[\],\{"token":"([^"]+)"')
        tokens["__hs"] = grab(r'"haste_session":"([^"]+)"')
        tokens["__spin_r"] = grab(r'"__spin_r":([0-9]+)')
        tokens["__spin_b"] = grab(r'"__spin_b":"([^"]+)"') or "trunk"
        tokens["__spin_t"] = grab(r'"__spin_t":([0-9]+)')
        if not tokens["fb_dtsg"]:
            raise AuthError(
                "page render came back without a DTSG token — the session "
                "cookies are likely invalid/expired")
        log.debug("page tokens refreshed (dtsg=%.10s…)", tokens["fb_dtsg"])
        self._page_cache = tokens
        return tokens

    async def graphql(self, doc_id: str, variables: dict,
                      friendly_name: str) -> dict:
        """Execute a persisted Relay mutation/query used by the web client.

        Mirrors the browser's POST to ``/api/graphql``: dtsg/lsd CSRF tokens +
        haste-session params in the form, Sec-Fetch headers required.
        """
        tok = await self._page_tokens()
        data = self._auth_form(tok, self._req_counter.next())
        data["fb_api_caller_class"] = "RelayModern"
        data["fb_api_req_friendly_name"] = friendly_name
        data["variables"] = json.dumps(variables, separators=(",", ":"))
        data["doc_id"] = doc_id
        headers = self._web_headers(tok, friendly_name)
        resp = await self._client.post("https://www.instagram.com/api/graphql",
                                       data=data, headers=headers)
        self._absorb_cookies(resp)
        if resp.status_code != 200:
            raise InstaDMError(
                f"graphql {friendly_name} failed: {resp.status_code} {resp.text[:200]}")
        js = json.loads(self._strip_for_prefix(resp.text))
        if js.get("error"):
            raise InstaDMError(
                f"graphql {friendly_name} error {js.get('error')}: "
                f"{js.get('errorSummary')} ({js.get('errorDescription')})")
        if js.get("errors"):
            err = js["errors"][0]
            raise InstaDMError(
                f"graphql {friendly_name} failed: {err.get('message')}")
        return js

    async def mark_thread_as_read(self, thread_id: str, message_id: str) -> dict:
        """Web-client equivalent of marking a thread read (Relay mutation)."""
        return await self.graphql(
            doc_id="27399783383056109",
            variables={
                "data": {"item_id": "", "message_id": message_id},
                "metadata": {"ig_thread_igid": thread_id},
            },
            friendly_name="useIGDMarkThreadAsReadMutation",
        )

    # -- web slide-message sends (text + media) -------------------------------

    DOC_TEXT_SEND = "26911679871773184"     # IGDirectTextSendMutation
    DOC_MEDIA_SEND = "25766288509716264"    # IGDirectMediaSendMutation

    def _auth_form(self, tok: dict, req: int) -> dict:
        """Common form params for /ajax + /api/graphql web endpoints."""
        return {
            "av": self.session.viewer_uuid() or self.session.ds_user_id,
            "__d": "www",
            "__user": "0",
            "__a": "1",
            "__req": str(req),
            "__hs": tok["__hs"],
            "dpr": "2",
            "__ccg": "MODERATE",
            "__rev": tok["__spin_r"],
            "__comet_req": "7",
            "__spin_r": tok["__spin_r"],
            "__spin_b": tok["__spin_b"],
            "__spin_t": tok["__spin_t"],
            "fb_dtsg": tok["fb_dtsg"],
            "jazoest": "2" + str(sum(ord(c) for c in tok["fb_dtsg"])),
            "lsd": tok["lsd"],
            "server_timestamps": "true",
        }

    def _web_headers(self, tok: dict, friendly_name: str,
                     referer: str = "https://www.instagram.com/direct/inbox/") -> dict:
        h = self._headers(post=True, referer=referer)
        h["X-FB-LSD"] = tok["lsd"]
        h["X-FB-Friendly-Name"] = friendly_name
        h["X-ASBD-ID"] = "129477"
        h["X-IG-Max-Touch-Points"] = "0"
        h.pop("Content-Type", None)  # let httpx set the form type
        return h

    @staticmethod
    def _strip_for_prefix(body: str) -> str:
        return body[len("for (;;);"):] if body.startswith("for (;;);") else body

    async def upload_mercury(self, data: bytes, filename: str = "photo.jpg",
                             mime_type: str = "image/jpeg") -> str:
        """Upload a DM attachment through the web client's mercury upload
        service; returns the ``attachment_fbid`` the slide-message mutations
        expect. (The rupload_igphoto flow does NOT work for web DMs.)
        """
        tok = await self._page_tokens()
        form = self._auth_form(tok, self._req_counter.next())
        form["upload_id"] = str(uuid.uuid4())
        headers = self._web_headers(tok, "FileMercuryUploadService")
        resp = await self._client.post(
            "https://www.instagram.com/ajax/mercury/upload.php",
            data=form, files={"farr": (filename, data, mime_type)}, headers=headers)
        self._absorb_cookies(resp)
        if resp.status_code != 200:
            raise InstaDMError(
                f"mercury upload failed: {resp.status_code} {resp.text[:200]}")
        js = json.loads(self._strip_for_prefix(resp.text))
        if js.get("error"):
            raise InstaDMError(f"mercury upload error {js['error']}: {js.get('errorSummary')}")
        meta = ((js.get("payload") or {}).get("metadata") or {}).get("0") or {}
        fbid = meta.get("fbid") or meta.get("image_id") or meta.get("video_id") \
            or meta.get("audio_id") or meta.get("gif_id") or meta.get("file_id")
        if not fbid:
            raise InstaDMError(f"mercury upload returned no fbid: {js}")
        return str(fbid)

    async def send_text_message(self, text: str, thread_v2_id: str | None = None,
                                recipient_igids: list[str] | None = None,
                                reply_to_message_id: str | None = None) -> dict:
        """Send text via the web client's slide mutation. Either an existing
        thread (``thread_v2_id``) or fresh recipients (``recipient_igids``,
        creates a new thread) must be given."""
        variables = {
            "text": {"sensitive_string_value": text},
            "ig_thread_igid": thread_v2_id,
            "recipient_igids": recipient_igids,
            "offline_threading_id": new_client_context(),
            "send_attribution": "igd_web_chat_tab:in_thread",
            "mentions": [],
            "mentioned_user_ids": [],
            "commands": None,
            "forwarded_from_thread_id": None,
            "is_forwarded_from_own_message": None,
            "replied_to_client_context": None,
            "replied_to_item_id": None,
            "reply_to_message_id": reply_to_message_id,
            "sampled": None,
        }
        return await self.graphql(self.DOC_TEXT_SEND, variables, "IGDirectTextSendMutation")

    async def send_media_message(self, thread_id: str, attachment_fbid: str,
                                 reply_to_message_id: str | None = None) -> dict:
        """Send an uploaded attachment (see ``upload_mercury``) into a thread
        via the web client's slide mutation. Requires the long ``thread_id``."""
        variables = {
            "attachment_fbid": attachment_fbid,
            "reply_to_message_id": reply_to_message_id,
            "forwarded_from_thread_id": None,
            "is_forwarded_from_own_message": None,
            "offline_threading_id": new_client_context(),
            "thread_id": thread_id,
        }
        return await self.graphql(self.DOC_MEDIA_SEND, variables, "IGDirectMediaSendMutation")


class _Counter:
    def __init__(self) -> None:
        self._n = 0

    def next(self) -> int:
        self._n += 1
        return self._n


def _json_list(items: list[str]) -> str:
    import json as _json
    return _json.dumps(items)


def new_client_context() -> str:
    """Facebook-style offline threading id (like MercuryLocalIDs)."""
    import time
    time_now = int(time.time() * 1000)
    rand = random.randint(0, 0x1FFFFF)
    return str((time_now << 21) | rand)


def new_device_id() -> str:
    return str(uuid.uuid4())
