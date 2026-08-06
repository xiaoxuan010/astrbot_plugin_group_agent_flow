import pytest
from astrbot.core.agent.context.token_counter import EstimateTokenCounter
from astrbot.core.agent.message import Message

from observation import _truncate_single_record, prepare_observation
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


def _prepare(
    store: GroupFlowStore,
    *,
    snapshot_seq: int,
    max_context_tokens: int = 8192,
    history_cursor: int | None = None,
):
    return prepare_observation(
        store,
        flow_id=FLOW_ID,
        snapshot_seq=snapshot_seq,
        max_context_tokens=max_context_tokens,
        history_cursor=history_cursor,
    )


def _count_contexts(contexts: tuple[dict, ...] | list[dict]) -> int:
    messages = [Message.model_validate(context) for context in contexts]
    return EstimateTokenCounter().count_tokens(messages)


def test_observation_rebuilds_from_current_buffer_rows(tmp_path):
    store = GroupFlowStore(tmp_path)
    for index in range(1, 5):
        store.append_pending(FLOW_ID, _record(f"m{index}", f"message {index}"))

    prepared = _prepare(store, snapshot_seq=4)

    assert prepared.source_seqs == (1, 2, 3, 4)
    assert len(prepared.contexts) == 1
    assert all(
        f">message {index}</text>" in prepared.contexts[0]["content"]
        for index in range(1, 5)
    )
    assert prepared.target_cursor == 4


def test_existing_history_cursor_selects_only_the_new_suffix(tmp_path):
    store = GroupFlowStore(tmp_path)
    store.append_pending(FLOW_ID, _record("m1", "kept history"))
    store.append_pending(FLOW_ID, _record("m2", "latest"))
    prepared = _prepare(store, snapshot_seq=2, history_cursor=1)

    assert prepared.source_seqs == (2,)
    assert "kept history" not in prepared.contexts[0]["content"]
    assert "latest" in prepared.contexts[0]["content"]


def test_existing_history_cursor_renders_only_the_new_suffix(tmp_path):
    store = GroupFlowStore(tmp_path)
    store.append_pending(FLOW_ID, _record("m1", "first"))
    store.append_pending(FLOW_ID, _record("m2", "second"))

    prepared = _prepare(
        store,
        snapshot_seq=2,
        history_cursor=1,
    )

    assert prepared.source_seqs == (2,)
    assert all("first" not in context["content"] for context in prepared.contexts)
    assert any("second" in context["content"] for context in prepared.contexts)
    assert prepared.estimated_tokens == _count_contexts(prepared.contexts)


def test_latest_block_drops_oldest_records_until_under_hard_limit(tmp_path):
    store = GroupFlowStore(tmp_path)
    for index in range(1, 5):
        store.append_pending(FLOW_ID, _record(f"m{index}", str(index) * 500))

    prepared = _prepare(
        store,
        snapshot_seq=4,
        max_context_tokens=260,
    )

    assert prepared.estimated_tokens <= 260
    assert prepared.source_seqs[-1] == 4
    assert prepared.source_seqs[0] > 1


def test_single_oversized_message_keeps_metadata_tail_and_lookup_marker(tmp_path):
    store = GroupFlowStore(tmp_path)
    original = "BEGIN-" + ("x" * 5000) + "-TAIL"
    store.append_pending(FLOW_ID, _record("oversized", original))

    prepared = _prepare(
        store,
        snapshot_seq=1,
        max_context_tokens=240,
    )

    assert prepared.source_seqs == (1,)
    assert prepared.estimated_tokens <= 240
    content = prepared.contexts[0]["content"]
    assert 'message_id="oversized"' in content
    assert "内容已截断" in content
    assert "get_message" in content
    assert "-TAIL</text>" in content
    assert "BEGIN-" not in content
    assert store.get_message(FLOW_ID, "oversized")["text"] == original


def test_component_backed_truncation_replaces_model_visible_components():
    class ComponentRenderer:
        def render(self, events):
            return [
                {
                    "role": "user",
                    "content": events[0]["components"][0]["text"],
                }
            ]

    class CharacterCounter:
        def count_tokens(self, messages):
            return sum(len(str(message.content)) for message in messages)

    original = "BEGIN-" + ("x" * 500) + "-TAIL"
    block = _truncate_single_record(
        ComponentRenderer(),
        {
            "seq": 1,
            "message_id": "oversized",
            "text": original,
            "components": [{"type": "text", "text": original}],
        },
        token_budget=100,
        counter=CharacterCounter(),
    )

    assert block.estimated_tokens <= 100
    assert "内容已截断" in block.contexts[0]["content"]
    assert block.contexts[0]["content"].endswith("-TAIL")
    assert "BEGIN-" not in block.contexts[0]["content"]


def test_impossibly_small_token_budget_fails_instead_of_exceeding_limit(tmp_path):
    store = GroupFlowStore(tmp_path)
    store.append_pending(FLOW_ID, _record("oversized", "content"))

    with pytest.raises(ValueError, match="max_context_tokens"):
        _prepare(
            store,
            snapshot_seq=1,
            max_context_tokens=1,
        )


def test_prepare_observation_ignores_transport_events_without_model_content(tmp_path):
    store = GroupFlowStore(tmp_path)
    store.append_pending(
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
    )

    assert prepared.contexts == ()
    assert prepared.source_seqs == ()
    assert prepared.target_cursor == 1
    assert prepared.estimated_tokens == 0
