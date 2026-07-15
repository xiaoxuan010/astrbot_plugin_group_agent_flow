from observation import prepare_observation
from store import GroupFlowStore


def _record(message_id: str, text: str) -> dict:
    return {
        "message_id": message_id,
        "group_id": "1",
        "sender_id": "2",
        "sender_name": "Alice",
        "timestamp": 1710000000,
        "text": text,
    }


def test_prepare_observation_uses_cursor_through_frozen_snapshot(tmp_path):
    store = GroupFlowStore(tmp_path)
    for index in range(1, 5):
        store.append_record("qq:group:1", _record(str(index), f"m{index}"))
    store.set_cursor("qq:group:1", "conv", 1, unified_msg_origin="qq:GroupMessage:1")

    prepared = prepare_observation(
        store,
        flow_id="qq:group:1",
        conversation_id="conv",
        snapshot_seq=3,
        renderer_name="native_messages",
        max_messages=0,
    )

    assert prepared.source_seqs == (2, 3)
    assert [message["content"].split("] ")[-1] for message in prepared.contexts] == ["m2", "m3"]
    assert prepared.target_cursor == 3
    assert prepared.skipped_count == 0


def test_prepare_observation_limits_oldest_delta_records(tmp_path):
    store = GroupFlowStore(tmp_path)
    for index in range(1, 6):
        store.append_record("qq:group:1", _record(str(index), f"m{index}"))

    prepared = prepare_observation(
        store,
        flow_id="qq:group:1",
        conversation_id="conv",
        snapshot_seq=5,
        renderer_name="plain_lines",
        max_messages=2,
    )

    assert prepared.source_seqs == (4, 5)
    assert prepared.skipped_count == 3


def test_prepare_observation_ignores_transport_events_without_model_content(tmp_path):
    store = GroupFlowStore(tmp_path)
    store.append_record(
        "qq:group:1",
        {
            "message_id": "empty",
            "group_id": "1",
            "sender_id": "2",
            "sender_name": "Alice",
            "timestamp": 1710000000,
            "text": "",
            "components": [],
        },
    )

    prepared = prepare_observation(
        store,
        flow_id="qq:group:1",
        conversation_id="conv",
        snapshot_seq=1,
        renderer_name="legacy_delta",
        max_messages=0,
    )

    assert prepared.contexts == ()
    assert prepared.source_seqs == ()
    assert prepared.target_cursor == 1
