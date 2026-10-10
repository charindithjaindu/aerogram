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
MAX_RETRY_AFTER = 60.0  # never sleep longer than this inside _request
# Re-rendering the token page costs ~800KB. Tokens outlive this; graphql()
# re-renders early when Instagram reports them expired.
PAGE_TOKEN_TTL = 6 * 3600


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value.strip())
    except ValueError:
        return None


class HttpApi:
    def __init__(self, session: Session, user_agent: str = DEFAULT_UA,
                 timeout: float = 20.0) -> None:
        self.session = session
        self._ua = user_agent
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=False)
        self._page_cache: dict = {}
        self._page_lock = asyncio.Lock()
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
                # A timed-out POST may already have been applied server-side;
                # retrying it can double-send. Only idempotent GETs retry on
                # transport errors.
                if post:
                    raise InstaDMError(
                        f"POST {url} failed ({type(e).__name__}: {e}) — not "
                        "retried to avoid duplicating a possibly-applied "
                        "mutation") from e
                await asyncio.sleep(0.5 * (2 ** attempt) + random.random())
                continue

            self._absorb_cookies(resp)

            if resp.status_code == 429:
                retry_after = _parse_retry_after(resp.headers.get("retry-after"))
                seconds = retry_after if retry_after is not None else float(2 ** attempt)
                if attempt == MAX_RETRIES - 1 or seconds > MAX_RETRY_AFTER:
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
    #
    # Instagram removed the web REST DM routes (/direct_v2/inbox/, threads/…,
    # seen/hide/mute, get_presence) — they now return the HTML 404 page. The
    # web client reads DMs through persisted GraphQL queries instead; the doc
    # ids below were captured from instagram.com's bundles.

    DOC_INBOX = "27909866362025854"              # PolarisDirectInboxQuery
    DOC_INBOX_PAGE = "28774058922187457"         # IGDThreadListOffMsysPaginationQuery
    DOC_THREAD_DETAIL = "29432273173041378"      # IGDThreadDetailQuery
    DOC_MESSAGE_LIST = "29380270148264352"       # IGDMessageListOffMsysQuery
    DOC_MUTE = "26360506043651125"               # IGDInboxInfoMuteToggleOffMsysMutation
    DOC_PROFILE = "28036671149327607"            # PolarisProfilePageContentQuery

    _THREAD_LIST_PV = {
        "__relay_internal__pv__IGDPinnedThreadsRenderEnabledGKrelayprovider": True,
        "__relay_internal__pv__IGDMaxUnreadMessagesCountrelayprovider": 5,
        "__relay_internal__pv__IGDThreadListActionsEnabledGKrelayprovider": True,
    }

    async def inbox(self) -> dict:
        """First inbox page plus the iris cursor.

        Returns the ``get_slide_mailbox_for_iris_subscription`` object
        (``iris_inactive_subscription_uq_seq_id``, ``threads_by_folder``,
        mailbox ``id``) with ``request_start_time_ms`` folded in as the
        snapshot time."""
        js = await self.graphql(self.DOC_INBOX, {
            "device_id_for_iris_subscription": self.session.device_id or self.session.ig_did,
            "__relay_internal__pv__IGDIsProfessionalAccountGKrelayprovider": False,
            **self._THREAD_LIST_PV,
        }, "PolarisDirectInboxQuery")
        mailbox = (js.get("data") or {}).get("get_slide_mailbox_for_iris_subscription") or {}
        meta = (js.get("extensions") or {}).get("server_metadata") or {}
        mailbox["snapshot_at_ms"] = meta.get("request_start_time_ms") or int(time.time() * 1000)
        return mailbox

    async def inbox_page(self, mailbox_id: str, cursor: str, count: int = 15,
                         folder: str = "INBOX") -> dict:
        """Older inbox threads after ``cursor`` (``threads_by_folder``)."""
        js = await self.graphql(self.DOC_INBOX_PAGE, {
            "count": count, "cursor": cursor, "folder": folder, "id": mailbox_id,
            "newer_than_timestamp_ms": None, **self._THREAD_LIST_PV,
        }, "IGDThreadListOffMsysPaginationQuery")
        return ((js.get("data") or {}).get("fetch__SlideMailbox") or {}).get("threads_by_folder") or {}

    async def badge_count(self) -> dict:
        """Unread badge + current iris ``seq_id`` / ``badge_count_at_ms``.

        A cheap REST call (no page-token fetch) that is still served; used as
        the cursor fallback when the GraphQL inbox fails."""
        return await self.get_json("/direct_v2/get_badge_count/", {"no_raven": "1"})

    async def thread(self, thread_key: str, limit: int = 20) -> dict:
        """A thread with its newest ``limit`` messages (``as_ig_direct_thread``).
        ``thread_key`` is the short id (``Thread.v2_id``)."""
        js = await self.graphql(self.DOC_THREAD_DETAIL, {
            "min_uq_seq_id": None, "thread_fbid": thread_key,
            "__relay_internal__pv__IGDEnableOffMsysChatThemesQErelayprovider": False,
            "__relay_internal__pv__IGDInitialMessagePageCountrelayprovider": limit,
        }, "IGDThreadDetailQuery")
        node = (js.get("data") or {}).get("get_slide_thread_nullable") or {}
        if not node.get("as_ig_direct_thread"):
            raise NotFoundError(f"thread {thread_key} not found")
        return node["as_ig_direct_thread"]

    async def thread_messages(self, thread_fbid: str, cursor: str, limit: int = 20) -> dict:
        """Messages older than ``cursor`` (``slide_messages`` connection)."""
        js = await self.graphql(self.DOC_MESSAGE_LIST, {
            "after": cursor, "before": None, "first": limit, "last": None, "id": thread_fbid,
            "newer_than_message_id": None, "older_than_message_id": None,
            "__relay_internal__pv__IGDInitialMessagePageCountrelayprovider": limit,
        }, "IGDMessageListOffMsysQuery")
        node = ((js.get("data") or {}).get("fetch__SlideThread") or {}).get("as_ig_direct_thread") or {}
        return node.get("slide_messages") or {}

    async def mute_thread(self, thread_fbid: str, seconds: int = -1) -> dict:
        """``seconds``: -1 mutes forever, 0 unmutes (the web client also
        offers 28800 = 8h and 86400 = 24h)."""
        return await self.graphql(self.DOC_MUTE, {
            "mute_seconds": seconds, "offline_threading_id": new_client_context(),
            "thread_fbid": thread_fbid,
        }, "IGDInboxInfoMuteToggleOffMsysMutation")

    async def user_id_for_username(self, username: str) -> str:
        """Resolve a username via the profile page document (the web client
        does the same; the REST profile endpoints are aggressively 429'd)."""
        resp = await self._client.get(f"https://www.instagram.com/{username}/",
                                      headers=self._document_headers())
        self._absorb_cookies(resp)
        m = re.search(r'"profile_id":"(\d+)"', resp.text) or \
            re.search(r'"page_id":"profilePage_(\d+)"', resp.text)
        if resp.status_code == 404 or not m:
            raise NotFoundError(f"user @{username} not found")
        return m.group(1)

    async def user_info(self, user_id: str) -> dict:
        """Profile of ``user_id`` (``data.user`` of the profile page query)."""
        js = await self.graphql(self.DOC_PROFILE, {
            "enable_integrity_filters": True, "id": str(user_id),
            "__relay_internal__pv__PolarisCannesGuardianExperienceEnabledrelayprovider": True,
            "__relay_internal__pv__PolarisCASB976ProfileEnabledrelayprovider": False,
            "__relay_internal__pv__PolarisWebSchoolsEnabledrelayprovider": False,
            "__relay_internal__pv__PolarisRepostsConsumptionEnabledrelayprovider": True,
            "__relay_internal__pv__PolarisShortDramaEnabledrelayprovider": True,
        }, "PolarisProfilePageContentQuery")
        user = (js.get("data") or {}).get("user")
        if not user:
            raise NotFoundError(f"user {user_id} not found")
        return user

    # -- misc -----------------------------------------------------------------

    async def download(self, url: str, path: str | None = None) -> str:
        if path is None:
            from urllib.parse import urlparse
            name = urlparse(url).path.split("/")[-1].split("?")[0] or "media.bin"
            path = name
        # stream to disk: DM videos can be large enough that buffering the
        # whole body would spike memory
        # CDN media URLs (videos especially) 302 to the serving host
        async with self._client.stream("GET", url, headers={"User-Agent": self._ua},
                                       follow_redirects=True) as resp:
            resp.raise_for_status()
            with open(path, "wb") as f:
                async for chunk in resp.aiter_bytes(1 << 16):
                    f.write(chunk)
        return path

    # -- GraphQL (web "Slide" mutations, e.g. mark-thread-read) ----------------

    def _document_headers(self) -> dict[str, str]:
        """Browser top-level navigation headers. Without ``Sec-Fetch-*`` /
        ``sec-ch-ua`` Instagram renders an anonymous shell."""
        return {
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

    async def _page_tokens(self) -> dict:
        """Load instagram.com as a browser would and extract the session tokens
        /api/graphql requires (fb_dtsg, lsd, haste session, spin revision...).

        The full browser document-header set is required — without
        ``Sec-Fetch-*``/``sec-ch-ua`` Instagram renders an anonymous shell with
        an empty DTSG token.
        """
        cached = self._page_cache
        if cached and time.time() - cached["t"] < PAGE_TOKEN_TTL:
            return cached
        # serialize page fetches: graphql callers run concurrently (e.g. the
        # upload + send inside send_photo) and each would otherwise render
        # the full inbox HTML page just to extract the same tokens
        async with self._page_lock:
            cached = self._page_cache
            if cached and time.time() - cached["t"] < PAGE_TOKEN_TTL:
                return cached
            resp = await self._client.get("https://www.instagram.com/direct/inbox/",
                                          headers=self._document_headers())
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
        for attempt in range(2):
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
            if resp.status_code == 429:
                raise RateLimited(f"graphql {friendly_name} rate limited (429)",
                                  retry_after=_parse_retry_after(resp.headers.get("retry-after")))
            if resp.status_code in (301, 302, 303, 307, 308):
                raise AuthError(
                    f"graphql {friendly_name} redirected to "
                    f"{resp.headers.get('location', '')} — session cookies are invalid")
            if resp.status_code != 200:
                raise InstaDMError(
                    f"graphql {friendly_name} failed: {resp.status_code} {resp.text[:200]}")
            # @defer/@stream responses are newline-separated JSON payloads;
            # the first one carries the data
            body = self._strip_for_prefix(resp.text).lstrip()
            js = json.loads(body.split("\n", 1)[0]) if "\n" in body else json.loads(body)
            # 1357001/1357004: fb_dtsg/lsd expired — re-render the page once
            if js.get("error") in (1357001, 1357004) and attempt == 0:
                log.info("graphql page tokens expired; refreshing")
                self._page_cache = {}
                continue
            break
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
        h["X-ASBD-ID"] = "359341"
        h["X-IG-Max-Touch-Points"] = "0"
        h.pop("Content-Type", None)  # let httpx set the form type
        return h

    @staticmethod
    def _strip_for_prefix(body: str) -> str:
        return body[len("for (;;);"):] if body.startswith("for (;;);") else body

    async def upload_mercury(self, data: bytes, filename: str = "photo.jpg",
                             mime_type: str = "image/jpeg", voice_clip: bool = False) -> str:
        """Upload a DM attachment (image, video or audio) through the web
        client's mercury upload service; returns the ``attachment_fbid`` the
        media-send mutation expects. ``voice_clip`` marks audio as a voice
        note (as the web voice recorder does).
        """
        tok = await self._page_tokens()
        form = self._auth_form(tok, self._req_counter.next())
        form["upload_id"] = str(uuid.uuid4())
        if voice_clip:
            form["voice_clip"] = "true"
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

    async def send_text_message(self, text: str, thread_fbid: str | None = None,
                                recipient_igids: list[str] | None = None,
                                reply_to_message_id: str | None = None) -> dict:
        """Send text via the web client's slide mutation. Either an existing
        thread (``thread_fbid``, i.e. ``Thread.fbid``) or fresh recipients (``recipient_igids``,
        creates a new thread) must be given."""
        variables = {
            "text": {"sensitive_string_value": text},
            "ig_thread_igid": thread_fbid,
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
        via the web client's slide mutation. ``thread_id`` is the thread's
        ``thread_fbid`` (what the web client passes)."""
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


def new_client_context() -> str:
    """Facebook-style offline threading id (like MercuryLocalIDs)."""
    time_now = int(time.time() * 1000)
    rand = random.randint(0, 0x1FFFFF)
    return str((time_now << 21) | rand)


def new_device_id() -> str:
    return str(uuid.uuid4())
