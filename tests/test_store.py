import json

from store import GroupFlowStore


def _record(message_id: str, sender_id: str, text: str, timestamp: int) -> dict:
    return {
        "message_id": message_id,
        "sender_id": sender_id,
        "sender_name": sender_id,
        "text": text,
        "timestamp": timestamp,
    }


def test_search_records_filters_text_sender_and_returns_chronological_results(tmp_path):
    store = GroupFlowStore(tmp_path)
    flow_id = "napcat:group:1"
    store.append_record(flow_id, _record("1", "alice", "部署开始", 100))
    store.append_record(flow_id, _record("2", "bob", "部署完成", 101))
    store.append_record(flow_id, _record("3", "alice", "晚饭时间", 102))

    results = store.search_records(
        flow_id,
        query="部署",
        sender_id="alice",
        limit=10,
    )

    assert [item["message_id"] for item in results] == ["1"]
    assert store.get_message(flow_id, "2")["text"] == "部署完成"


def test_search_records_applies_limit_to_most_recent_matches(tmp_path):
    store = GroupFlowStore(tmp_path)
    flow_id = "napcat:group:1"
    for index in range(5):
        store.append_record(
            flow_id,
            _record(str(index), "alice", f"共同关键词 {index}", 100 + index),
        )

    results = store.search_records(flow_id, query="共同关键词", limit=2)

    assert [item["message_id"] for item in results] == ["3", "4"]


def test_search_records_applies_snapshot_sequence_before_limit(tmp_path):
    store = GroupFlowStore(tmp_path)
    flow_id = "napcat:group:1"
    for index in range(1, 6):
        store.append_record(
            flow_id,
            _record(f"m{index}", "alice", "共同关键词", index),
        )

    results = store.search_records(
        flow_id,
        query="共同关键词",
        limit=2,
        max_seq=3,
    )

    assert [item["message_id"] for item in results] == ["m2", "m3"]


def test_run_outcome_is_persistent_and_first_write_wins(tmp_path):
    store = GroupFlowStore(tmp_path)

    assert store.record_run_outcome(
        "run-1",
        flow_id="napcat:group:1",
        snapshot_seq=20,
        outcome="no_action",
    ) is True
    assert store.record_run_outcome(
        "run-1",
        flow_id="napcat:group:1",
        snapshot_seq=20,
        outcome="direct_output_suppressed",
    ) is False

    reloaded = GroupFlowStore(tmp_path)
    assert reloaded.get_run_outcome("run-1")["outcome"] == "no_action"


def test_import_legacy_data_copies_logs_and_state_once(tmp_path):
    legacy = tmp_path / "legacy"
    target = tmp_path / "target"
    (legacy / "logs").mkdir(parents=True)
    (legacy / "logs" / "flow.jsonl").write_text(
        json.dumps({"message_id": "1", "seq": 1}) + "\n",
        encoding="utf-8",
    )
    (legacy / "state.json").write_text('{"cursors": {"x": 1}}', encoding="utf-8")
    store = GroupFlowStore(target)

    assert store.import_legacy_data(legacy) is True
    assert (target / "logs" / "flow.jsonl").is_file()
    assert store.read_state()["cursors"] == {"x": 1}
    assert store.import_legacy_data(legacy) is False


def test_renderer_assignment_is_stable_for_a_conversation(tmp_path):
    store = GroupFlowStore(tmp_path)

    assert store.get_or_assign_renderer("qq:group:1", "conv-1", "legacy_delta") == "legacy_delta"
    assert store.get_or_assign_renderer("qq:group:1", "conv-1", "native_messages") == "legacy_delta"
    assert store.get_or_assign_renderer("qq:group:1", "conv-2", "native_messages") == "native_messages"


def test_cursor_never_moves_backwards_when_a_stale_run_finishes(tmp_path):
    store = GroupFlowStore(tmp_path)
    flow_id = "qq:group:1"

    store.set_cursor(flow_id, "conv", 44, unified_msg_origin="qq:GroupMessage:1")
    store.set_cursor(flow_id, "conv", 43, unified_msg_origin="qq:GroupMessage:1")

    assert store.get_cursor(flow_id, "conv") == 44
