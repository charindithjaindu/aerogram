import json
import os

import pytest

from aerogram.errors import AuthError
from aerogram.session import Session
from aerogram.types import Message, Thread, User

NETSCAPE = """# Netscape HTTP Cookie File
#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t1802205589\tps_n\t1
.instagram.com\tTRUE\t/\tTRUE\t1798812583\tds_user_id\t99900011122
.instagram.com\tTRUE\t/\tTRUE\t1825596583\tcsrftoken\tABCdef123
#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t1822572583\tsessionid\t99900011122%3Asecret%3A18%3AXYZ
#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t1791122993\trur\tCCO%2C17841471011833178%2C1792246193%3A0abc
"""


def test_netscape_parsing_includes_httponly(tmp_path):
    p = tmp_path / "cookies.txt"
    p.write_text(NETSCAPE)
    s = Session.from_cookies_file(str(p))
    # the earlier debugging bug: #HttpOnly lines MUST be included
    assert s.sessionid == "99900011122%3Asecret%3A18%3AXYZ"
    assert s.ds_user_id == "99900011122"
    assert s.csrftoken == "ABCdef123"
    assert s.cookies["ps_n"] == "1"


def test_viewer_uuid_from_rur(tmp_path):
    p = tmp_path / "cookies.txt"
    p.write_text(NETSCAPE)
    s = Session.from_cookies_file(str(p))
    assert s.viewer_uuid() == "17841471011833178"


def test_validate_missing_cookies():
    s = Session(cookies={"sessionid": "x"})
    with pytest.raises(AuthError):
        s.validate()


def test_cookie_rotation_tracking():
    s = Session(cookies={"csrftoken": "old", "sessionid": "keep"})
    s.set_cookie_from_set_cookie("csrftoken=NEW123; Path=/; Domain=.instagram.com; Secure")
    assert s.csrftoken == "NEW123"
    assert s.sessionid == "keep"
    # logout clears the session
    s.set_cookie_from_set_cookie('sessionid=""; expires=Thu, 01-Jan-1970')
    assert s.sessionid == ""


def test_save_and_load_roundtrip(tmp_path):
    p = tmp_path / "my.session.json"
    s = Session(cookies={"sessionid": "abc"}, device_id="dev-1", seq_id=496,
                snapshot_at_ms=1234, user_id="99900011122")
    s.save(str(p))
    s2 = Session.from_file(str(p))
    assert s2.seq_id == 496
    assert s2.device_id == "dev-1"
    assert s2.user_id == "99900011122"
    assert s2.cookie_header == "sessionid=abc"


def test_message_parse_from_real_shape():
    raw = {
        "item_id": "32561512301934496954155318261055488",
        "message_id": "mid.$cAD8_x",
        "user_id": "99900011122",
        "timestamp": 1765163118858543,
        "item_type": "text",
        "client_context": "7403630721611315543",
        "is_sent_by_viewer": True,
        "text": "hello there",
    }
    m = Message.parse(raw, thread_id="th")
    assert m.item_id == raw["item_id"]
    assert m.text == "hello there"
    assert m.is_sent_by_viewer
    assert m.client_context == "7403630721611315543"
    assert m.timestamp_us == 1765163118858543


def test_message_parse_reactions_and_media():
    raw = {
        "item_id": "1", "user_id": "2", "item_type": "media",
        "media": {
            "id": "12345_67890",
            "video_versions": [{"url": "http://video"}],
            "image_versions2": {"candidates": [{"url": "http://thumb"}]},
        },
        "reactions": {"likes": [{"emoji": "❤️", "sender_id": "9"}]},
    }
    m = Message.parse(raw, thread_id="th")
    assert m.media and m.media.media_type == "video" and m.media.url == "http://video"
    assert m.reactions and m.reactions[0].emoji == "❤️"


def test_thread_parse_other_user():
    raw = {
        "thread_id": "340282366841710301244259525149423678654",
        "thread_v2_id": "1350131846591678",
        "viewer_id": "99900011122",
        "is_group": False,
        "muted": False,
        "users": [
            {"pk": "99900011122", "username": "me_account", "full_name": "Me"},
            {"pk": "30559676892", "username": "other_account", "full_name": "Other"},
        ],
        "items": [],
    }
    t = Thread.parse(raw)
    assert t.id == "340282366841710301244259525149423678654"
    assert len(t.users) == 2
    assert t.other_user().username == "other_account"
