import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_group_agent_flow.agent_tools import (  # noqa: E402
    BATCH_ID_EXTRA,
    BATCH_RECORDS_EXTRA,
    FLOW_ID_EXTRA,
    RUN_GENERATION_EXTRA,
    RUN_ID_EXTRA,
    SNAPSHOT_SEQ_EXTRA,
)
from astrbot_plugin_group_agent_flow.main import (  # noqa: E402
    AUTONOMOUS_EXTRA,
    GroupAgentFlowPlugin,
)
from astrbot_plugin_group_agent_flow.observation import AudioAttachment  # noqa: E402
from astrbot_plugin_group_agent_flow.coordinator import GroupRunCoordinator  # noqa: E402
from astrbot_plugin_group_agent_flow.store import GroupFlowStore  # noqa: E402


class _Event:
    def __init__(self, extras=None):
        self.extras = extras or {}
        self.unified_msg_origin = "qq:GroupMessage:1"
        self.stopped = False

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        self.extras[key] = value

    def stop_event(self):
        self.stopped = True


def _record(message_id: str, text: str) -> dict:
    return {
        "record_kind": "group_message",
        "message_id": message_id,
        "group_id": "1",
        "sender_id": "2",
        "sender_name": "Alice",
        "timestamp": 1710000000,
        "text": text,
        "components": [{"type": "text", "text": text}],
    }


@pytest.mark.asyncio
async def test_inject_snapshot_only_projects_claimed_rows(tmp_path):
    store = GroupFlowStore(tmp_path)
    store.append_pending("qq:group:1", _record("m1", "first"))
    store.append_pending("qq:group:1", _record("m2", "second"))
    batch = store.claim_batch("qq:group:1", 2)
    assert batch is not None

    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.store = store
    plugin.config = {"context": {"max_context_tokens": 8192}}
    plugin._locks = {}
    plugin.coordinator = SimpleNamespace(
        is_run_current=lambda *_args, **_kwargs: True,
    )
    plugin.tool_runtime = SimpleNamespace(build_tool_set=lambda _event: "tools")
    event = _Event(
        {
            AUTONOMOUS_EXTRA: True,
            FLOW_ID_EXTRA: "qq:group:1",
            RUN_ID_EXTRA: "run-1",
            RUN_GENERATION_EXTRA: 0,
            SNAPSHOT_SEQ_EXTRA: batch.snapshot_seq,
            BATCH_ID_EXTRA: batch.batch_id,
            BATCH_RECORDS_EXTRA: list(batch.records),
        }
    )
    request = SimpleNamespace(
        conversation=SimpleNamespace(cid="conv", token_usage=10),
        contexts=[{"role": "assistant", "content": "core history"}],
        func_tool=None,
    )

    await plugin.inject_snapshot(event, request)

    assert len(request.contexts) == 2
    assert "first" in request.contexts[-1]["content"]
    assert "second" in request.contexts[-1]["content"]
    assert request.contexts[-1]["content"].startswith(
        '<group_messages_delta group_id="1">'
    )
    assert batch.batch_id not in json.dumps(request.contexts)
    assert request.func_tool == "tools"


@pytest.mark.asyncio
async def test_inject_snapshot_attaches_only_budget_selected_audio(tmp_path):
    store = GroupFlowStore(tmp_path)
    voice_record = {
        **_record("voice-1", "voice"),
        "components": [{"type": "voice", "url": "https://private.example/a.amr"}],
    }
    store.append_pending("qq:group:1", voice_record)
    batch = store.claim_batch("qq:group:1", 1)
    assert batch is not None

    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.store = store
    plugin.config = {"context": {"max_context_tokens": 8192}}
    plugin._locks = {}
    plugin.coordinator = SimpleNamespace(is_run_current=lambda *_args, **_kwargs: True)
    plugin.context = SimpleNamespace(
        get_using_provider=lambda _umo: SimpleNamespace(
            provider_config={"modalities": ["text", "audio"]}
        )
    )
    plugin.tool_runtime = SimpleNamespace(build_tool_set=lambda _event: "tools")

    async def attachments(records):
        assert records == list(batch.records)
        return (
            AudioAttachment(seq=1, url="https://private.example/a.amr", token_cost=1),
        )

    plugin._resolve_voice_attachments = attachments
    event = _Event(
        {
            AUTONOMOUS_EXTRA: True,
            FLOW_ID_EXTRA: "qq:group:1",
            RUN_ID_EXTRA: "run-1",
            RUN_GENERATION_EXTRA: 0,
            SNAPSHOT_SEQ_EXTRA: batch.snapshot_seq,
            BATCH_ID_EXTRA: batch.batch_id,
            BATCH_RECORDS_EXTRA: list(batch.records),
        }
    )
    request = SimpleNamespace(
        conversation=SimpleNamespace(cid="conv", token_usage=0),
        contexts=[],
        func_tool=None,
        audio_urls=[],
    )

    await plugin.inject_snapshot(event, request)

    assert request.audio_urls == ["https://private.example/a.amr"]
    assert "private.example" not in request.contexts[-1]["content"]


@pytest.mark.asyncio
async def test_observe_claims_batch_and_sets_private_checkpoint(monkeypatch, tmp_path):
    flow_id = "qq:group:1"
    store = GroupFlowStore(tmp_path)
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.store = store
    plugin.config = {"agent_settings": {"authorized_group_ids": ["1"]}}
    plugin._locks = {}
    plugin.coordinator = GroupRunCoordinator(
        debounce_seconds=0,
        direct_delay_seconds=0,
        min_cycle_interval_seconds=0,
        direct_min_cycle_interval_seconds=0,
    )
    plugin.tool_runtime = SimpleNamespace(build_tool_set=lambda _event: "tools")
    plugin._get_conversation = lambda _event: _conversation()
    plugin._is_authorized = lambda _event: True
    monkeypatch.setattr(
        "astrbot_plugin_group_agent_flow.main.extract_group_event",
        lambda *_args, **_kwargs: {
            **_record("m1", "hello"),
            "flow_id": flow_id,
            "is_directed_at_bot": False,
        },
    )
    monkeypatch.setattr(
        "astrbot_plugin_group_agent_flow.main.suppress_builtin_active_reply",
        lambda _event: None,
    )
    monkeypatch.setattr(
        "astrbot_plugin_group_agent_flow.main.isolate_platform_metadata",
        lambda _event: None,
    )
    monkeypatch.setattr(
        "astrbot_plugin_group_agent_flow.main.install_send_guard",
        lambda _event: None,
    )
    event = _Event()
    event.get_message_str = lambda: "hello"
    event.get_sender_id = lambda: "2"
    event.get_self_id = lambda: "bot"
    event.is_at_or_wake_command = False
    event.should_call_llm = lambda _enabled: None
    event.request_llm = lambda **kwargs: kwargs

    observation = plugin.observe_group_message(event)
    request = await anext(observation)
    await observation.aclose()

    assert request["prompt"]
    assert event.get_extra("llm_checkpoint_id", "").startswith("gaf:")
    assert event.get_extra(BATCH_ID_EXTRA)
    assert event.get_extra(BATCH_ID_EXTRA) not in repr(request)
    assert store.stats(flow_id)["inflight"] == 1


async def _conversation():
    return SimpleNamespace(history="[]", cid="conv", token_usage=0)


def test_reconcile_acks_checkpoint_and_requeues_missing_checkpoint(tmp_path):
    store = GroupFlowStore(tmp_path)
    store.append_pending("qq:group:1", _record("m1", "first"))
    first = store.claim_batch("qq:group:1", 1)
    assert first is not None
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.store = store

    conversation = SimpleNamespace(
        history=json.dumps(
            [{"role": "_checkpoint", "content": {"id": first.checkpoint_id}}]
        )
    )
    plugin._reconcile_inflight("qq:group:1", conversation)
    assert store.stats("qq:group:1")["inflight"] == 0

    store.append_pending("qq:group:1", _record("m2", "second"))
    second = store.claim_batch("qq:group:1", 2)
    assert second is not None
    plugin._reconcile_inflight(
        "qq:group:1",
        SimpleNamespace(history="[]"),
    )
    assert store.stats("qq:group:1")["pending"] == 1
    assert store.stats("qq:group:1")["inflight"] == 0


@pytest.mark.asyncio
async def test_empty_event_is_not_written(monkeypatch):
    flow_id = "qq:group:1"
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.config = {}
    plugin._locks = {}
    writes = []
    plugin.store = SimpleNamespace(
        append_pending=lambda *_args, **_kwargs: writes.append(True),
    )
    monkeypatch.setattr(
        "astrbot_plugin_group_agent_flow.main.extract_group_event",
        lambda *_args, **_kwargs: {
            "flow_id": flow_id,
            "text": "",
            "components": [],
            "is_directed_at_bot": False,
        },
    )
    event = _Event()
    event.get_sender_id = lambda: "user-1"
    event.get_self_id = lambda: "bot-1"
    event.is_at_or_wake_command = False

    assert await plugin._record(event, schedule=True) is None
    assert writes == []
