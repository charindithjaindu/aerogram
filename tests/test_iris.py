import json

from aerogram.iris import Delta


def make_delta(op, path, value, seq_id=42):
    d = Delta(op=op, path=path, value=value if not isinstance(value, str) else value,
              seq_id=seq_id)
    d.parse_path()
    return d


def test_new_message_delta():
    item = {"item_type": "text", "text": "hi", "user_id": "123"}
    d = make_delta("add", "/direct_v2/threads/3402823668417103012442597/items/3256151230",
                   json.dumps(item))
    assert d.is_new_message
    assert d.thread_id == "3402823668417103012442597"
    assert d.item_id == "3256151230"
    assert d.value_as_dict()["text"] == "hi"


def test_remove_delta():
    d = make_delta("remove", "/direct_v2/threads/123/items/456", None)
    assert d.is_removed_message
    assert not d.is_new_message


def test_item_update_delta():
    d = make_delta("replace", "/direct_v2/threads/123/items/456/seen_state",
                   json.dumps({"has_seen": True}))
    assert d.is_item_update
    assert d.thread_id == "123"
    assert d.item_id == "456"


def test_thread_and_unseen_paths():
    d1 = make_delta("add", "/direct_v2/inbox/threads/999", "{}")
    assert d1.is_thread_update and d1.thread_id == "999"
    d2 = make_delta("replace", "/direct_v2/inbox/unseen_count", "3")
    assert d2.is_unseen_count
    assert d2.value_as_dict() == {"value": 3}


def test_value_as_dict_plain_dict():
    d = make_delta("add", "/direct_v2/threads/1/items/2", {"a": 1})
    assert d.value_as_dict() == {"a": 1}


def test_seq_tracking_fields():
    d = make_delta("add", "/direct_v2/threads/1/items/2", "{}", seq_id=496)
    assert d.seq_id == 496
    assert d.mutation_token is None
