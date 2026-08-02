import pytest

from store import GroupFlowStore, StoreInitializationError
from concurrent.futures import ThreadPoolExecutor


def _record(message_id: str, text: str, *, timestamp: int = 1) -> dict:
    return {
        "record_kind": "group_message",
        "message_id": message_id,
        "sender_id": "alice",
        "sender_name": "Alice",
        "text": text,
        "timestamp": timestamp,
        "components": [{"type": "text", "text": text}],
        "unknown_field": {"nested": True},
    }


def test_append_pending_round_trips_payload_and_keeps_seq_after_ack(tmp_path):
    store = GroupFlowStore(tmp_path)

    first = store.append_pending("qq:group:1", _record("m1", "first"))
    assert first.inserted is True
    assert first.seq == 1

    duplicate = store.append_pending("qq:group:1", _record("m1", "changed"))
    assert duplicate.inserted is False
    assert duplicate.seq == 1

    batch = store.claim_batch("qq:group:1", snapshot_seq=1)
    assert batch is not None
    assert batch.snapshot_seq == 1
    assert batch.records[0]["unknown_field"] == {"nested": True}
    assert store.ack_batch(batch.batch_id) is True
    assert store.get_batch_records(batch.batch_id) == []

    second = store.append_pending("qq:group:1", _record("m2", "second"))
    assert second.seq == 2
    assert store.append_pending("qq:group:1", _record("m1", "redelivery")).seq == 1


def test_claim_freezes_pending_boundary_and_requeue_preserves_order(tmp_path):
    store = GroupFlowStore(tmp_path)
    store.append_pending("qq:group:1", _record("m1", "first"))
    store.append_pending("qq:group:1", _record("m2", "second"))

    batch = store.claim_batch("qq:group:1", snapshot_seq=1)
    assert batch is not None
    assert [item["message_id"] for item in batch.records] == ["m1"]

    store.append_pending("qq:group:1", _record("m3", "arrived-later"))
    stats = store.stats("qq:group:1")
    assert stats["pending"] == 2
    assert stats["inflight"] == 1

    assert store.requeue_batch(batch.batch_id) is True
    retry = store.claim_batch("qq:group:1", snapshot_seq=3)
    assert retry is not None
    assert [item["message_id"] for item in retry.records] == ["m1", "m2", "m3"]


def test_checkpoint_reconciliation_acks_only_new_checkpoint(tmp_path):
    store = GroupFlowStore(tmp_path)
    store.append_pending("qq:group:1", _record("m1", "first"))
    batch = store.claim_batch(
        "qq:group:1",
        snapshot_seq=1,
        checkpoint_id="gaf:batch-1",
        checkpoint_baseline_count=1,
    )
    assert batch is not None

    result = store.reconcile_inflight(
        "qq:group:1",
        {"gaf:batch-1": 1},
    )
    assert result == []
    assert store.stats("qq:group:1")["inflight"] == 1

    result = store.reconcile_inflight(
        "qq:group:1",
        {"gaf:batch-1": 2},
    )
    assert result == [batch.batch_id]
    assert store.stats("qq:group:1")["inflight"] == 0
    assert store.stats("qq:group:1")["records"] == 0


def test_search_records_can_be_bounded_to_one_claimed_batch(tmp_path):
    store = GroupFlowStore(tmp_path)
    store.append_pending("qq:group:1", _record("m1", "current"))
    store.append_pending("qq:group:1", _record("m2", "future"))
    batch = store.claim_batch("qq:group:1", 1)
    assert batch is not None

    results = store.search_records(
        "qq:group:1",
        query="",
        max_seq=1,
        batch_id=batch.batch_id,
    )

    assert [item["message_id"] for item in results] == ["m1"]


def test_buffer_capacity_never_discards_inflight_rows(tmp_path):
    store = GroupFlowStore(tmp_path, max_log_records=1)
    store.append_pending("qq:group:1", _record("m1", "first"))
    batch = store.claim_batch("qq:group:1", snapshot_seq=1)

    try:
        store.append_pending("qq:group:1", _record("m2", "second"))
    except Exception as exc:
        assert type(exc).__name__ == "BufferCapacityError"
    else:
        raise AssertionError("capacity must reject a new message")

    assert batch is not None
    assert store.get_batch_records(batch.batch_id)[0]["message_id"] == "m1"


def test_legacy_jsonl_is_not_created_or_imported(tmp_path):
    legacy = tmp_path / "legacy"
    (legacy / "logs").mkdir(parents=True)
    (legacy / "logs" / "flow.jsonl").write_text(
        '{"message_id":"legacy-1","seq":1}\n',
        encoding="utf-8",
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "logs").mkdir()
    legacy_target = target / "logs" / "flow.jsonl"
    legacy_target.write_text("old\n", encoding="utf-8")

    store = GroupFlowStore(target)
    assert store.db_path.name == "group_agent_flow.db"
    assert store.read_records("qq:group:1") == []
    assert legacy_target.read_text(encoding="utf-8") == "old\n"
    assert not (target / "legacy_migration.json").exists()


def test_concurrent_appenders_keep_unique_monotonic_sequences(tmp_path):
    store = GroupFlowStore(tmp_path)

    def append(index: int) -> int:
        return store.append_pending(
            "qq:group:1",
            _record(f"m{index}", f"message {index}", timestamp=index),
        ).seq

    with ThreadPoolExecutor(max_workers=6) as executor:
        sequences = list(executor.map(append, range(30)))

    assert sorted(sequences) == list(range(1, 31))
    assert store.stats("qq:group:1")["pending"] == 30


def test_corrupt_database_fails_without_touching_legacy_files(tmp_path):
    legacy = tmp_path / "logs"
    legacy.mkdir()
    legacy_file = legacy / "old.jsonl"
    legacy_file.write_text("legacy\n", encoding="utf-8")
    database = tmp_path / "group_agent_flow.db"
    database.write_bytes(b"not sqlite")

    with pytest.raises(StoreInitializationError):
        GroupFlowStore(tmp_path)

    assert legacy_file.read_text(encoding="utf-8") == "legacy\n"


def test_agent_action_is_not_admitted_to_the_buffer(tmp_path):
    store = GroupFlowStore(tmp_path)

    with pytest.raises(ValueError, match="group_message"):
        store.append_pending(
            "qq:group:1",
            {"record_kind": "agent_action", "message_id": "action-1"},
        )

    assert store.read_records("qq:group:1") == []
