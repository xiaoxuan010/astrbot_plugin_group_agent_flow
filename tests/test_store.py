from store import GroupFlowStore


def _record(message_id: str, sender_id: str, text: str, timestamp: int) -> dict:
    return {
        "record_kind": "group_message",
        "message_id": message_id,
        "sender_id": sender_id,
        "sender_name": sender_id,
        "text": text,
        "timestamp": timestamp,
        "components": [{"type": "text", "text": text}],
    }


def test_search_records_filters_only_active_buffer_rows(tmp_path):
    store = GroupFlowStore(tmp_path)
    flow_id = "napcat:group:1"
    store.append_pending(flow_id, _record("1", "alice", "部署开始", 100))
    store.append_pending(flow_id, _record("2", "bob", "部署完成", 101))
    store.append_pending(flow_id, _record("3", "alice", "晚饭时间", 102))

    results = store.search_records(
        flow_id,
        query="部署",
        sender_id="alice",
        limit=10,
    )

    assert [item["message_id"] for item in results] == ["1"]
    assert store.get_message(flow_id, "2")["text"] == "部署完成"


def test_search_records_applies_snapshot_sequence_before_limit(tmp_path):
    store = GroupFlowStore(tmp_path)
    flow_id = "napcat:group:1"
    for index in range(1, 6):
        store.append_pending(flow_id, _record(f"m{index}", "alice", "关键词", index))

    results = store.search_records(
        flow_id,
        query="关键词",
        limit=2,
        max_seq=3,
    )

    assert [item["message_id"] for item in results] == ["m2", "m3"]


def test_clear_flow_removes_buffer_but_keeps_next_sequence_monotonic(tmp_path):
    flow_id = "qq:group:1"
    store = GroupFlowStore(tmp_path)
    store.append_pending(flow_id, _record("m1", "alice", "first", 1))
    store.clear_flow(flow_id)

    assert store.read_records(flow_id) == []
    assert store.stats(flow_id)["next_seq"] == 2

    result = store.append_pending(flow_id, _record("m2", "alice", "second", 2))
    assert result.seq == 2
