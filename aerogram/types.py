"""Pyrogram-style type models for Instagram DM objects.

Each model keeps ``raw`` (the original JSON) so nothing is ever lost, while
exposing the fields you actually use.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# item_type values seen in the wild
TEXT = "text"
LIKE = "like"
MEDIA = "media"  # shared post/reel
MEDIA_SHARE = "media_share"
REEL_SHARE = "reel_share"
STORY_SHARE = "story_share"
REACTION = "reaction"
PLACEHOLDER = "placeholder"
CLIP = "clip"
FELIX_SHARE = "felix_share"
PROFILE = "profile"
HASHTAG = "hashtag"
LOCATION = "location"
VOICE_MEDIA = "voice_media"
ANIMATED_MEDIA = "animated_media"
XMA_SHARE = "xma_share"  # modern link/media share cards
LINK = "link"

SENDABLE_ITEM_TYPES = {
    TEXT, LIKE, MEDIA_SHARE, REEL_SHARE, STORY_SHARE, PROFILE, HASHTAG, LOCATION,
}

MEDIA_ITEM_TYPES = {"photo", "video", "media", "clip", "xma_share", "media_share",
                    "reel_share", "story_share", "voice_media", "animated_media"}


def _int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


@dataclass
class User:
    id: str
    username: str = ""
    full_name: str = ""
    profile_pic_url: str = ""
    is_verified: bool = False
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def parse(cls, raw: dict) -> "User":
        return cls(
            id=str(raw.get("pk") or raw.get("id") or raw.get("pk_id") or ""),
            username=raw.get("username") or "",
            full_name=raw.get("full_name") or "",
            profile_pic_url=raw.get("profile_pic_url") or "",
            is_verified=bool(raw.get("is_verified")),
            raw=raw,
        )


@dataclass
class Reaction:
    emoji: str = ""
    sender_id: str = ""
    timestamp: Optional[int] = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def parse(cls, raw: dict) -> "Reaction":
        return cls(
            emoji=raw.get("emoji") or "",
            sender_id=str(raw.get("sender_id") or ""),
            timestamp=_int(raw.get("timestamp")),
            raw=raw,
        )


@dataclass
class Media:
    """Media attached to a message (photo, video, voice, shared clip...)."""

    media_type: str = ""          # "photo" | "video" | "voice_media" | ...
    id: str = ""
    url: str = ""
    thumbnail_url: str = ""
    duration_seconds: float = 0.0
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def parse(cls, item: dict) -> Optional["Media"]:
        item_type = item.get("item_type", "")
        if item_type == "voice_media":
            vm = (item.get("voice_media") or {}).get("media") or {}
            return cls(media_type="voice_media", id=str(vm.get("id") or ""),
                       url=vm.get("audio_src") or item.get("audio_src") or "",
                       duration_seconds=float(vm.get("audio_duration") or 0.0), raw=vm)
        if item_type in ("media", "media_share", "clip", "felix_share"):
            m = item.get("media") or {}
            videos = m.get("video_versions") or []
            images = (m.get("image_versions2") or {}).get("candidates") or []
            return cls(
                media_type="video" if videos else "photo",
                id=str(m.get("id") or ""),
                url=(videos[0].get("url") if videos else (images[0].get("url") if images else "")),
                thumbnail_url=(images[0].get("url") if images else ""),
                raw=m,
            )
        if item_type == "photo":
            m = item.get("media") or item
            images = (m.get("image_versions2") or {}).get("candidates") or []
            return cls(media_type="photo", id=str(m.get("id") or ""),
                       url=images[0].get("url") if images else "", raw=m)
        if item_type == "video":
            m = item.get("media") or item
            videos = m.get("video_versions") or []
            images = (m.get("image_versions2") or {}).get("candidates") or []
            return cls(media_type="video", id=str(m.get("id") or ""),
                       url=videos[0].get("url") if videos else "",
                       thumbnail_url=images[0].get("url") if images else "", raw=m)
        if item_type in ("xma_share", "story_share", "reel_share"):
            link = item.get("link_url") or item.get("xma_share") or ""
            m = item.get("media") or {}
            images = (m.get("image_versions2") or {}).get("candidates") or []
            return cls(media_type=item_type, id=str(m.get("id") or ""),
                       url=link if isinstance(link, str) else "",
                       thumbnail_url=images[0].get("url") if images else "", raw=item)
        return None

    @classmethod
    def parse_slide(cls, content: dict) -> Optional["Media"]:
        """Media from a GraphQL ``slide_messages`` node's ``content``."""
        typename = content.get("__typename") or ""
        if "xma" in content:
            xma = content.get("xma") or {}
            return cls(media_type=XMA_SHARE, id=str(xma.get("target_id") or ""),
                       url=xma.get("target_url") or "",
                       thumbnail_url=(xma.get("preview_image") or {}).get("url") or "",
                       raw=content)
        att = content.get("attachment") or (content.get("attachments") or [None])[0] \
            or content.get("animated_media")
        if not isinstance(att, dict):
            return None
        kind = typename.lower()
        if "video" in kind:
            media_type = "video"
        elif "audio" in kind or "voice" in kind:
            media_type = VOICE_MEDIA
        elif "animated" in kind:
            media_type = ANIMATED_MEDIA
        else:
            media_type = "photo"
        return cls(media_type=media_type, id=str(att.get("attachment_fbid") or att.get("id") or ""),
                   url=att.get("attachment_cdn_url") or att.get("url") or "",
                   thumbnail_url=att.get("preview_cdn_url") or "", raw=content)


@dataclass
class Message:
    """One DM item inside a thread."""

    client: Any = field(default=None, repr=False)  # backref, set by the library
    thread: Optional["Thread"] = field(default=None, repr=False)  # cached thread, when known
    thread_id: str = ""
    thread_fbid: str = ""  # set for GraphQL/lightspeed messages
    item_id: str = ""
    message_id: str = ""
    user_id: str = ""
    timestamp_us: Optional[int] = None
    item_type: str = ""
    text: str = ""
    client_context: str = ""
    is_sent_by_viewer: bool = False
    is_shh_mode: bool = False
    reactions: list[Reaction] = field(default_factory=list)
    media: Optional[Media] = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def parse(cls, raw: dict, thread_id: str = "") -> "Message":
        item_type = raw.get("item_type", "")
        reactions: list[Reaction] = []
        for kind in ("reactions", "preview_reactions"):
            node = raw.get(kind) or {}
            for r in node.get("likes") or []:
                reactions.append(Reaction.parse(r))
        msg = cls(
            thread_id=thread_id,
            item_id=str(raw.get("item_id") or raw.get("item_id_v2") or ""),
            message_id=raw.get("message_id") or "",
            user_id=str(raw.get("user_id") or ""),
            timestamp_us=_int(raw.get("timestamp")),
            item_type=item_type,
            text=raw.get("text") or "",
            client_context=str(raw.get("client_context") or raw.get("otid") or ""),
            is_sent_by_viewer=bool(raw.get("is_sent_by_viewer")),
            is_shh_mode=bool(raw.get("is_shh_mode")),
            reactions=reactions,
            media=Media.parse(raw),
            raw=raw,
        )
        return msg

    @classmethod
    def parse_slide(cls, node: dict, thread_id: str = "", viewer_id: str = "") -> "Message":
        """Parse a GraphQL ``slide_messages`` edge node (the web client's
        current message shape)."""
        content = node.get("content") or {}
        sender = node.get("sender") or {}
        user_id = str(sender.get("igid") or (sender.get("user_dict") or {}).get("id") or "")
        media = Media.parse_slide(content)
        text = node.get("text_body") or content.get("text_body") or content.get("xma_text_body") or ""
        ts_ms = _int(node.get("timestamp_ms"))
        mid = str(node.get("message_id") or node.get("id") or "")
        if media is not None:
            item_type = media.media_type
        elif content.get("__typename") == "SlideMessageAdminText":
            item_type = PLACEHOLDER
        else:
            item_type = TEXT
        return cls(
            thread_id=thread_id,
            thread_fbid=str(node.get("thread_fbid") or ""),
            item_id=mid,
            message_id=mid,
            user_id=user_id,
            timestamp_us=ts_ms * 1000 if ts_ms else None,
            item_type=item_type,
            text=text,
            client_context=str(node.get("offline_threading_id") or ""),
            is_sent_by_viewer=bool(viewer_id) and user_id == str(viewer_id),
            media=media,
            raw=node,
        )

    # -- conveniences (need a live client backref) ---------------------------

    @property
    def is_media(self) -> bool:
        return self.media is not None

    async def reply_text(self, text: str) -> "Message":
        if self.thread_id:
            return await self.client.send_text(self.thread_id, text)
        # thread not resolvable to its long id (not in the recent inbox):
        # the GraphQL send only needs thread_fbid
        return await self.client.send_text_to_fbid(self.thread_fbid, text)

    async def mark_seen(self) -> None:
        await self.client.mark_seen(self.thread_id, self.item_id)

    async def react(self, emoji: str = "❤️") -> None:
        await self.client.send_reaction(self.thread_id, self.item_id, emoji)

    async def download_media(self, path: str | None = None) -> str:
        if not self.media or not self.media.url:
            raise ValueError("message has no downloadable media")
        return await self.client.download(self.media.url, path)


@dataclass
class Thread:
    """A DM conversation."""

    id: str = ""
    v2_id: str = ""        # thread_key: thread detail query, /direct/t/<key>/ URLs
    fbid: str = ""         # thread_fbid: text-send ig_thread_igid, mute, message-list pagination
    users: list[User] = field(default_factory=list)
    is_group: bool = False
    title: str = ""
    muted: bool = False
    marked_unread: bool = False
    viewer_id: str = ""
    last_activity_at: Optional[int] = None
    messages: list[Message] = field(default_factory=list)
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def parse(cls, raw: dict) -> "Thread":
        t = cls(
            id=str(raw.get("thread_id") or ""),
            v2_id=str(raw.get("thread_v2_id") or ""),
            users=[User.parse(u) for u in (raw.get("users") or [])],
            is_group=bool(raw.get("is_group")),
            title=raw.get("thread_title") or "",
            muted=bool(raw.get("muted")),
            marked_unread=bool(raw.get("marked_as_unread")),
            viewer_id=str(raw.get("viewer_id") or ""),
            last_activity_at=_int(raw.get("last_activity_at")),
            raw=raw,
        )
        t.messages = [Message.parse(i, thread_id=t.id)
                      for i in (raw.get("items") or [])]
        return t

    @classmethod
    def parse_slide(cls, raw: dict) -> "Thread":
        """Parse a GraphQL ``as_ig_direct_thread`` object."""
        viewer_id = str(raw.get("viewer_id") or "")
        t = cls(
            id=str(raw.get("thread_id") or ""),
            v2_id=str(raw.get("thread_key") or ""),
            fbid=str(raw.get("thread_fbid") or raw.get("id") or ""),
            users=[User.parse(u) for u in (raw.get("users") or [])],
            is_group=bool(raw.get("is_group")),
            title=raw.get("thread_title") or "",
            muted=bool(raw.get("is_muted")),
            marked_unread=bool(raw.get("marked_as_unread")),
            viewer_id=viewer_id,
            last_activity_at=_int(raw.get("last_activity_timestamp_ms")),
            raw=raw,
        )
        edges = (raw.get("slide_messages") or {}).get("edges") or []
        t.messages = [Message.parse_slide(e["node"], thread_id=t.id, viewer_id=viewer_id)
                      for e in edges if e.get("node")]
        return t

    def other_user(self, viewer_id: str = "") -> Optional[User]:
        """The non-viewer participant (for 1:1 threads)."""
        vid = viewer_id or self.viewer_id
        for u in self.users:
            if u.id != vid:
                return u
        return self.users[0] if self.users else None
