from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.message import ImageURLPart, Message, TextPart
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.astr_main_agent_resources import (
    TOOL_CALL_PROMPT,
    TOOL_CALL_PROMPT_SKILLS_LIKE_MODE,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_group_agent_flow.agent_tools import (  # noqa: E402
    BATCH_ID_EXTRA,
    BATCH_RECORDS_EXTRA,
    FLOW_ID_EXTRA,
    RUN_GENERATION_EXTRA,
    RUN_ID_EXTRA,
    SILENCE_SELECTED_EXTRA,
    SNAPSHOT_SEQ_EXTRA,
)
from astrbot_plugin_group_agent_flow.coordinator import (  # noqa: E402
    GroupRunCoordinator,
)
import astrbot_plugin_group_agent_flow.main as main_module  # noqa: E402
from astrbot_plugin_group_agent_flow.main import (  # noqa: E402
    AUTONOMOUS_EXTRA,
    CHAT_LEGEND,
    GroupAgentFlowPlugin,
    RUN_CONTEXT_EXTRA,
)
from astrbot_plugin_group_agent_flow.store import (  # noqa: E402
    AppendResult,
    GroupFlowStore,
)


class FakeEvent:
    def __init__(self, extras=None):
        self.extras = extras or {}
        self.stopped = False
        self.unified_msg_origin = "qq:GroupMessage:1"

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        self.extras[key] = value

    def stop_event(self):
        self.stopped = True


async def _noop(*_args, **_kwargs) -> None:
    """占位 handler：用于在测试中构造工具对象。"""
    return None


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


def _activate_run(plugin, flow_id: str, snapshot_seq: int):
    coordinator = GroupRunCoordinator(
        debounce_seconds=0,
        direct_delay_seconds=0,
        min_cycle_interval_seconds=0,
    )
    coordinator.enqueue(
        flow_id,
        seq=snapshot_seq,
        received_at=0,
        directed=False,
    )
    snapshot = coordinator.begin_if_due(flow_id, now=0)
    plugin.coordinator = coordinator
    return snapshot


@pytest.mark.asyncio
async def test_agent_done_compacts_tool_images_before_history_save():
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    event = FakeEvent({AUTONOMOUS_EXTRA: True})
    run_context = SimpleNamespace(
        messages=[
            Message(
                role="tool",
                tool_call_id="call-image",
                content='{"message_id":"m1","caption":"一张部署截图"}',
            ),
            Message(
                role="user",
                content=[
                    TextPart(text="[Image from tool 'get_message_images']"),
                    ImageURLPart(
                        image_url=ImageURLPart.ImageURL(
                            url="data:image/png;base64,dGVzdA=="
                        )
                    ),
                ],
            ),
        ]
    )

    await plugin.remove_direct_output_from_history(
        event,
        run_context,
        SimpleNamespace(completion_text="", result_chain=None),
    )

    assert (
        run_context.messages[0].content
        == '{"message_id":"m1","caption":"一张部署截图"}'
    )
    assert run_context.messages[1].content == [
        TextPart(
            text=(
                "Image has been compacted. Its description is retained "
                "in the preceding tool result."
            )
        )
    ]


@pytest.mark.asyncio
async def test_terminal_observation_compacts_tool_images_before_stay_silent():
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    event = FakeEvent({AUTONOMOUS_EXTRA: True})
    run_context = SimpleNamespace(
        messages=[
            Message(
                role="user",
                content=[
                    ImageURLPart(
                        image_url=ImageURLPart.ImageURL(
                            url="data:image/png;base64,dGVzdA=="
                        )
                    )
                ],
            )
        ]
    )
    event.set_extra(RUN_CONTEXT_EXTRA, run_context)
    persisted = []

    async def persist(_event, *, had_direct_output):
        persisted.append(had_direct_output)

    plugin._persist_observation_state = persist

    await plugin._finalize_terminal_observation(event)

    assert persisted == [False]
    assert run_context.messages[0].content == [
        TextPart(
            text=(
                "Image has been compacted. Its description is retained "
                "in the preceding tool result."
            )
        )
    ]


def test_system_prompt_requires_tools_without_duplicating_tool_names():
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.config = {"prompt": {"custom_system_prompt": "Speak like a cat."}}

    prompt = plugin._system_prompt()

    assert "Use available tools for every group-visible message or action" in prompt
    assert "when choosing silence" in prompt
    assert "ordinary assistant output" in prompt.lower()
    assert prompt.index("Speak like a cat.") < prompt.index(
        "Use available tools for every group-visible message or action"
    )
    for tool_name in (
        "send_message",
        "reply_message",
        "react_message",
        "poke_user",
        "stay_silent",
        "get_message",
        "search_chat_history",
    ):
        assert tool_name not in prompt


def test_system_prompt_appends_legend_only_for_line_messages_renderer():
    """renderer='line_messages' 时附加省略语义图例；xml_delta 时不附加。"""
    xml_plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    xml_plugin.config = {"context": {"renderer": "xml_delta"}}
    assert CHAT_LEGEND not in xml_plugin._system_prompt()

    line_plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    line_plugin.config = {"context": {"renderer": "line_messages"}}
    prompt = line_plugin._system_prompt()
    assert CHAT_LEGEND in prompt
    assert "Use available tools for every group-visible message or action" in prompt


def test_system_prompt_defaults_to_no_legend():
    """未配置 renderer 时（默认 xml_delta）不附加图例。"""
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.config = {}
    assert CHAT_LEGEND not in plugin._system_prompt()


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({}, 20.0),
        ({"scheduling": {"direct_min_cycle_interval_seconds": 7}}, 7.0),
        ({"direct_min_cycle_interval_seconds": 9}, 9.0),
    ],
)
def test_build_coordinator_reads_direct_min_cycle_interval(config, expected):
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.config = config

    coordinator = plugin._build_coordinator()

    assert coordinator.direct_min_cycle_interval_seconds == expected


@pytest.mark.asyncio
async def test_record_schedules_inserted_message_while_holding_flow_lock(monkeypatch):
    flow_id = "qq:group:1"
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.config = {}
    plugin._locks = {}
    plugin.store = SimpleNamespace(
        append_pending=lambda _flow_id, _record: AppendResult(seq=7, inserted=True)
    )
    enqueue_calls = []

    class Coordinator:
        def enqueue(self, actual_flow_id, *, seq, received_at, directed):
            enqueue_calls.append(
                (
                    actual_flow_id,
                    seq,
                    directed,
                    plugin._lock_for(actual_flow_id).locked(),
                )
            )

    plugin.coordinator = Coordinator()
    monkeypatch.setattr(
        main_module,
        "extract_group_event",
        lambda _event, **_kwargs: {
            "flow_id": flow_id,
            "text": "hello",
            "components": [],
            "is_directed_at_bot": False,
        },
    )
    event = FakeEvent()
    event.get_sender_id = lambda: "user-1"
    event.get_self_id = lambda: "bot-1"
    event.is_at_or_wake_command = False

    recorded = await plugin._record(event, schedule=True)

    assert recorded == (flow_id, 7, False)
    assert enqueue_calls == [(flow_id, 7, False, True)]


@pytest.mark.asyncio
async def test_contentless_event_never_reaches_store(monkeypatch):
    flow_id = "qq:group:1"
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.config = {}
    plugin._locks = {}
    writes = []
    plugin.store = SimpleNamespace(
        append_pending=lambda *_args, **_kwargs: writes.append(True),
    )
    plugin.coordinator = SimpleNamespace()
    monkeypatch.setattr(
        main_module,
        "extract_group_event",
        lambda *_args, **_kwargs: {
            "flow_id": flow_id,
            "text": "",
            "components": [],
            "is_directed_at_bot": False,
        },
    )
    event = FakeEvent()
    event.get_sender_id = lambda: "user-1"
    event.get_self_id = lambda: "bot-1"
    event.is_at_or_wake_command = False

    assert await plugin._record(event, schedule=True) is None
    assert writes == []


@pytest.mark.asyncio
async def test_plugin_command_is_not_recorded(monkeypatch):
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.config = {}
    record_calls = []
    llm_flags = []

    monkeypatch.setattr(plugin, "_is_authorized", lambda _event: True)
    monkeypatch.setattr(plugin, "_is_plugin_command", lambda _event: True)

    async def unexpected_record(*_args, **_kwargs):
        record_calls.append(True)
        return None

    monkeypatch.setattr(plugin, "_record", unexpected_record)
    event = FakeEvent()
    event.should_call_llm = lambda enabled: llm_flags.append(enabled)

    assert [item async for item in plugin.observe_group_message(event)] == []
    assert record_calls == []
    assert llm_flags == [True]


@pytest.mark.asyncio
async def test_observation_run_does_not_ack_buffer_before_core_checkpoint(tmp_path):
    flow_id = "qq:group:1"
    store = GroupFlowStore(tmp_path)
    store.append_pending(flow_id, _record("m1", "hello"))
    batch = store.claim_batch(flow_id, 1)
    assert batch is not None
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.store = store
    plugin.config = {}
    plugin._locks = {}
    snapshot = _activate_run(plugin, flow_id, 1)
    event = FakeEvent(
        {
            FLOW_ID_EXTRA: flow_id,
            RUN_ID_EXTRA: snapshot.run_id,
            RUN_GENERATION_EXTRA: snapshot.generation,
            SNAPSHOT_SEQ_EXTRA: batch.snapshot_seq,
            BATCH_ID_EXTRA: batch.batch_id,
            BATCH_RECORDS_EXTRA: list(batch.records),
            SILENCE_SELECTED_EXTRA: True,
        }
    )

    await plugin._persist_observation_state(event, had_direct_output=False)

    assert store.stats(flow_id)["inflight"] == 1


@pytest.mark.asyncio
async def test_clear_removes_sqlite_buffer_and_core_conversation(tmp_path):
    flow_id = "qq:group:1"
    store = GroupFlowStore(tmp_path)
    store.append_pending(flow_id, _record("m1", "hello"))
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.store = store
    plugin.config = {}
    plugin._locks = {}
    plugin.coordinator = GroupRunCoordinator()
    updated = []

    async def update_conversation(*args, **kwargs):
        updated.append((args, kwargs))

    async def get_curr_conversation_id(*_args):
        return None

    plugin.context = SimpleNamespace(
        conversation_manager=SimpleNamespace(
            get_curr_conversation_id=get_curr_conversation_id,
            update_conversation=update_conversation,
        )
    )
    event = FakeEvent()
    event.get_message_type = lambda: main_module.MessageType.GROUP_MESSAGE
    event.get_platform_id = lambda: "qq"
    event.get_group_id = lambda: "1"
    event.plain_result = lambda content: content

    results = [result async for result in plugin.group_agent_clear(event)]

    assert results == ["Group Agent Flow data cleared for this group."]
    assert store.read_records(flow_id) == []
    assert not updated


@pytest.mark.asyncio
async def test_enforce_autonomous_tools_merges_and_filters_astrbot_tools():
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.config = {
        "context": {"astrbot_tool_blocklist": ["get_group_message_history"]}
    }
    gaf_tool = FunctionTool(
        name="send_message",
        description="Send one message to the current group.",
        parameters={"type": "object", "properties": {}},
        handler=_noop,
    )
    plugin.tool_runtime = SimpleNamespace(
        build_tool_set=lambda _event: ToolSet([gaf_tool])
    )
    event = FakeEvent({AUTONOMOUS_EXTRA: True})
    request = ProviderRequest(prompt="review", contexts=[], func_tool=ToolSet())
    request.func_tool.add_tool(
        FunctionTool(
            name="get_group_message_history",
            description="Read persisted group history",
            parameters={"type": "object", "properties": {}},
            handler=_noop,
        )
    )
    event.set_extra("provider_request", request)

    await plugin.enforce_autonomous_tools(event, None)

    names = request.func_tool.names()
    assert "send_message" in names
    assert "get_group_message_history" not in names


@pytest.mark.asyncio
async def test_final_prompt_hook_moves_protocol_after_later_astrbot_hooks():
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.config = {}
    plugin._locks = {}
    event = FakeEvent({AUTONOMOUS_EXTRA: True})
    request = ProviderRequest(
        prompt="review",
        contexts=[],
        system_prompt=main_module.AGENT_PROTOCOL_PROMPT,
    )
    request.system_prompt += "\n\n# Persona\nFollow tools."

    await plugin.finalize_agent_protocol(event, request)

    assert request.system_prompt.endswith(main_module.AGENT_PROTOCOL_PROMPT)
    assert request.system_prompt.count(main_module.AGENT_PROTOCOL_PROMPT) == 1


@pytest.mark.asyncio
async def test_finalize_agent_protocol_removes_core_tool_prompts_for_autonomous_request():
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    event = FakeEvent({AUTONOMOUS_EXTRA: True})
    request = ProviderRequest(
        prompt="review",
        contexts=[],
        system_prompt=(
            "persona\n" + TOOL_CALL_PROMPT + "\n" + TOOL_CALL_PROMPT_SKILLS_LIKE_MODE
        ),
    )

    await plugin.finalize_agent_protocol(event, request)

    assert TOOL_CALL_PROMPT not in request.system_prompt
    assert TOOL_CALL_PROMPT_SKILLS_LIKE_MODE not in request.system_prompt
    assert request.system_prompt.endswith(main_module.AGENT_PROTOCOL_PROMPT)


@pytest.mark.asyncio
async def test_finalize_agent_protocol_leaves_non_autonomous_request_unchanged():
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    event = FakeEvent()
    request = ProviderRequest(
        prompt="review",
        contexts=[],
        system_prompt=TOOL_CALL_PROMPT,
    )
    before = request.system_prompt

    await plugin.finalize_agent_protocol(event, request)

    assert request.system_prompt == before
