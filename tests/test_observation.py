import pytest
from astrbot.core.agent.context.token_counter import EstimateTokenCounter
from astrbot.core.agent.message import Message

from context_renderers import build_renderer
from observation import prepare_observation
from store import GroupFlowStore


FLOW_ID = "qq:group:1"
CONVERSATION_ID = "conv"


def _record(message_id: str, text: str) -> dict:
    return {
        "record_kind": "group_message",
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


def _prepare(
    store: GroupFlowStore,
    *,
    snapshot_seq: int,
    renderer_name: str = "plain_lines",
    max_context_tokens: int = 8192,
    rotation_retention_ratio: float = 0.5,
):
    return prepare_observation(
        store,
        flow_id=FLOW_ID,
        conversation_id=CONVERSATION_ID,
        snapshot_seq=snapshot_seq,
        renderer_name=renderer_name,
        max_context_tokens=max_context_tokens,
        rotation_retention_ratio=rotation_retention_ratio,
    )


def _commit_window(store: GroupFlowStore, prepared) -> None:
    store.set_observation_window(
        FLOW_ID,
        CONVERSATION_ID,
        renderer_name=prepared.renderer_name,
        blocks=[
            {"start_seq": start_seq, "end_seq": end_seq}
            for start_seq, end_seq in prepared.window_blocks
        ],
    )
    store.set_cursor(
        FLOW_ID,
        CONVERSATION_ID,
        prepared.target_cursor,
        unified_msg_origin="qq:GroupMessage:1",
    )


def _count_contexts(contexts: tuple[dict, ...] | list[dict]) -> int:
    messages = [Message.model_validate(context) for context in contexts]
    return EstimateTokenCounter().count_tokens(messages)


def test_prepare_observation_bootstraps_history_then_appends_latest_block(tmp_path):
    store = GroupFlowStore(tmp_path)
    for index in range(1, 5):
        store.append_record(FLOW_ID, _record(f"m{index}", f"message {index}"))
    store.set_cursor(
        FLOW_ID,
        CONVERSATION_ID,
        3,
        unified_msg_origin="qq:GroupMessage:1",
    )

    prepared = _prepare(
        store,
        snapshot_seq=4,
        renderer_name="native_messages",
    )

    assert prepared.window_blocks == ((1, 3), (4, 4))
    assert prepared.source_seqs == (1, 2, 3, 4)
    assert [message["content"].split("] ")[-1] for message in prepared.contexts] == [
        "message 1",
        "message 2",
        "message 3",
        "message 4",
    ]
    assert prepared.target_cursor == 4


def test_cursor_action_is_not_replayed_when_it_is_outside_active_blocks(tmp_path):
    store = GroupFlowStore(tmp_path)
    store.append_record(FLOW_ID, _record("m1", "kept history"))
    store.append_record(
        FLOW_ID,
        _action_record("agent-action:run-1:1", "old action"),
    )
    store.append_record(FLOW_ID, _record("m3", "latest"))
    store.set_cursor(
        FLOW_ID,
        CONVERSATION_ID,
        2,
        unified_msg_origin="qq:GroupMessage:1",
    )
    store.set_observation_window(
        FLOW_ID,
        CONVERSATION_ID,
        renderer_name="plain_lines",
        blocks=[{"start_seq": 1, "end_seq": 1}],
    )

    prepared = _prepare(store, snapshot_seq=3)

    assert prepared.source_seqs == (1, 3)
    assert "old action" not in prepared.contexts[0]["content"]
    assert "latest" in prepared.contexts[-1]["content"]


@pytest.mark.parametrize(
    "renderer_name",
    ["legacy_delta", "plain_lines", "native_messages"],
)
def test_unrotated_window_reuses_exact_context_prefix(tmp_path, renderer_name):
    store = GroupFlowStore(tmp_path)
    store.append_record(FLOW_ID, _record("m1", "first"))
    first = _prepare(store, snapshot_seq=1, renderer_name=renderer_name)
    _commit_window(store, first)
    store.append_record(FLOW_ID, _record("m2", "second"))

    second = _prepare(store, snapshot_seq=2, renderer_name=renderer_name)

    assert second.contexts[: len(first.contexts)] == first.contexts
    assert second.window_blocks == ((1, 1), (2, 2))
    assert second.estimated_tokens == _count_contexts(second.contexts)


@pytest.mark.parametrize("retention_ratio", [0.1, 0.5, 0.9])
def test_overflow_rotates_complete_oldest_blocks_to_retention_target(
    tmp_path,
    retention_ratio,
):
    store = GroupFlowStore(tmp_path)
    renderer = build_renderer("plain_lines")
    for index in range(1, 5):
        store.append_record(FLOW_ID, _record(f"m{index}", str(index) * 300))
    store.set_cursor(
        FLOW_ID,
        CONVERSATION_ID,
        3,
        unified_msg_origin="qq:GroupMessage:1",
    )
    store.set_observation_window(
        FLOW_ID,
        CONVERSATION_ID,
        renderer_name="plain_lines",
        blocks=[
            {"start_seq": 1, "end_seq": 1},
            {"start_seq": 2, "end_seq": 2},
            {"start_seq": 3, "end_seq": 3},
        ],
    )
    block_tokens = [
        _count_contexts(renderer.render([record]))
        for record in store.read_records(FLOW_ID)
    ]
    hard_limit = sum(block_tokens) - 1
    target = int(hard_limit * retention_ratio)
    expected_start = 1
    remaining = sum(block_tokens)
    while expected_start < 4 and remaining > target:
        remaining -= block_tokens[expected_start - 1]
        expected_start += 1

    prepared = _prepare(
        store,
        snapshot_seq=4,
        max_context_tokens=hard_limit,
        rotation_retention_ratio=retention_ratio,
    )

    assert prepared.window_blocks == tuple(
        (seq, seq) for seq in range(expected_start, 5)
    )
    assert prepared.source_seqs == tuple(range(expected_start, 5))
    assert prepared.estimated_tokens <= max(target, block_tokens[-1])


def test_latest_block_drops_oldest_records_until_under_hard_limit(tmp_path):
    store = GroupFlowStore(tmp_path)
    for index in range(1, 5):
        store.append_record(FLOW_ID, _record(f"m{index}", str(index) * 500))

    prepared = _prepare(
        store,
        snapshot_seq=4,
        max_context_tokens=260,
    )

    assert prepared.estimated_tokens <= 260
    assert prepared.source_seqs[-1] == 4
    assert prepared.source_seqs[0] > 1
    assert prepared.window_blocks == (
        (prepared.source_seqs[0], prepared.source_seqs[-1]),
    )


def test_single_oversized_message_keeps_metadata_tail_and_lookup_marker(tmp_path):
    store = GroupFlowStore(tmp_path)
    original = "BEGIN-" + ("x" * 5000) + "-TAIL"
    store.append_record(FLOW_ID, _record("oversized", original))

    prepared = _prepare(
        store,
        snapshot_seq=1,
        renderer_name="plain_lines",
        max_context_tokens=240,
    )

    assert prepared.source_seqs == (1,)
    assert prepared.window_blocks == ((1, 1),)
    assert prepared.estimated_tokens <= 240
    content = prepared.contexts[0]["content"]
    assert "msg=oversized" in content
    assert "内容已截断" in content
    assert "get_message" in content
    assert content.endswith("-TAIL")
    assert "BEGIN-" not in content
    assert store.get_message(FLOW_ID, "oversized")["text"] == original


def test_impossibly_small_token_budget_fails_instead_of_exceeding_limit(tmp_path):
    store = GroupFlowStore(tmp_path)
    store.append_record(FLOW_ID, _record("oversized", "content"))

    with pytest.raises(ValueError, match="max_context_tokens"):
        _prepare(
            store,
            snapshot_seq=1,
            renderer_name="plain_lines",
            max_context_tokens=1,
        )


def test_prepare_observation_ignores_transport_events_without_model_content(tmp_path):
    store = GroupFlowStore(tmp_path)
    store.append_record(
        FLOW_ID,
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

    prepared = _prepare(
        store,
        snapshot_seq=1,
        renderer_name="legacy_delta",
    )

    assert prepared.contexts == ()
    assert prepared.source_seqs == ()
    assert prepared.window_blocks == ()
    assert prepared.target_cursor == 1
    assert prepared.estimated_tokens == 0
