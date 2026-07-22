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


def _action_record(message_id: str, text: str) -> dict:
    return {
        "record_kind": "agent_action",
        "message_id": message_id,
        "targetable": False,
        "group_id": "1",
        "sender_id": "bot",
        "self_id": "bot",
        "timestamp": 1710000001,
        "text": text,
        "components": [{"type": "text", "text": text}],
        "action_name": "reply_message",
        "action_status": "succeeded",
        "target_message_id": "m1",
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


def test_prepare_observation_replays_action_fact_across_terminal_cycles(tmp_path):
    flow_id = "qq:group:1"
    store = GroupFlowStore(tmp_path)
    store.append_record(flow_id, _record("m1", "question"))
    store.append_record(
        flow_id,
        _action_record("agent-action:run-1:1", "answered once"),
    )
    store.set_cursor(flow_id, "conv", 2, unified_msg_origin="qq:GroupMessage:1")

    store.append_record(flow_id, _record("m2", "next message"))
    second_cycle = prepare_observation(
        store,
        flow_id=flow_id,
        conversation_id="conv",
        snapshot_seq=3,
        renderer_name="plain_lines",
        max_messages=200,
    )
    store.set_cursor(flow_id, "conv", 3, unified_msg_origin="qq:GroupMessage:1")
    store.append_record(flow_id, _record("m3", "another message"))
    third_cycle = prepare_observation(
        store,
        flow_id=flow_id,
        conversation_id="conv",
        snapshot_seq=4,
        renderer_name="plain_lines",
        max_messages=200,
    )

    assert second_cycle.source_seqs == (2, 3)
    assert third_cycle.source_seqs == (2, 4)
    for prepared in (second_cycle, third_cycle):
        assert "action_id=agent-action:run-1:1" in prepared.contexts[0]["content"]
        assert "target_msg=m1" in prepared.contexts[0]["content"]


def test_action_replay_uses_cycle_limit_and_requires_new_delta(tmp_path):
    flow_id = "qq:group:1"
    store = GroupFlowStore(tmp_path)
    store.append_record(flow_id, _action_record("agent-action:run-1:1", "old"))
    store.append_record(flow_id, _action_record("agent-action:run-2:1", "latest"))
    store.set_cursor(flow_id, "conv", 2, unified_msg_origin="qq:GroupMessage:1")

    no_delta = prepare_observation(
        store,
        flow_id=flow_id,
        conversation_id="conv",
        snapshot_seq=2,
        renderer_name="plain_lines",
        max_messages=1,
    )
    store.append_record(flow_id, _record("m3", "new"))
    with_delta = prepare_observation(
        store,
        flow_id=flow_id,
        conversation_id="conv",
        snapshot_seq=3,
        renderer_name="plain_lines",
        max_messages=1,
    )

    assert no_delta.contexts == ()
    assert no_delta.source_seqs == ()
    assert with_delta.source_seqs == (2, 3)
    assert "agent-action:run-1:1" not in with_delta.contexts[0]["content"]
    assert "action_id=agent-action:run-2:1" in with_delta.contexts[0]["content"]


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
