from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from astrbot.api.provider import ProviderRequest
from astrbot.core.astr_main_agent_resources import (
    TOOL_CALL_PROMPT,
    TOOL_CALL_PROMPT_SKILLS_LIKE_MODE,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_group_agent_flow.agent_tools import (  # noqa: E402
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
    GroupAgentFlowPlugin,
    PENDING_CURSOR_EXTRA,
)
from astrbot_plugin_group_agent_flow.store import GroupFlowStore  # noqa: E402

class FakeEvent:
    def __init__(self, extras):
        self.extras = extras
        self.stopped = False
        self.unified_msg_origin = "qq:GroupMessage:1"

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        self.extras[key] = value

    def stop_event(self):
        self.stopped = True


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
    assert "snapshot" not in prompt.lower()


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
async def test_record_schedules_message_while_holding_the_flow_lock(monkeypatch):
    flow_id = "qq:group:1"
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.config = {}
    plugin._locks = {}
    plugin.store = SimpleNamespace(append_record=lambda _flow_id, _record: 7)
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
    event = FakeEvent({})
    event.get_sender_id = lambda: "user-1"
    event.get_self_id = lambda: "bot-1"
    event.is_at_or_wake_command = False

    recorded = await plugin._record(event, schedule=True)

    assert recorded == (flow_id, 7, False)
    assert enqueue_calls == [(flow_id, 7, False, True)]


@pytest.mark.asyncio
async def test_record_treats_astrbot_wake_flag_as_directed(monkeypatch):
    flow_id = "qq:group:1"
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.config = {}
    plugin._locks = {}
    plugin.store = SimpleNamespace(append_record=lambda _flow_id, _record: 7)
    enqueue_calls = []

    class Coordinator:
        def enqueue(self, actual_flow_id, *, seq, received_at, directed):
            enqueue_calls.append((actual_flow_id, seq, directed))

    plugin.coordinator = Coordinator()
    monkeypatch.setattr(
        main_module,
        "extract_group_event",
        lambda _event, **_kwargs: {
            "flow_id": flow_id,
            "text": "wake message",
            "components": [],
            "is_directed_at_bot": False,
        },
    )
    event = FakeEvent({})
    event.get_sender_id = lambda: "user-1"
    event.get_self_id = lambda: "bot-1"
    event.is_at_or_wake_command = True

    recorded = await plugin._record(event, schedule=True)

    assert recorded == (flow_id, 7, True)
    assert enqueue_calls == [(flow_id, 7, True)]


@pytest.mark.asyncio
async def test_observation_request_uses_a_short_tool_call_reminder():
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.config = {}
    plugin.coordinator = GroupRunCoordinator(
        debounce_seconds=0,
        direct_delay_seconds=0,
        min_cycle_interval_seconds=0,
    )
    plugin._locks = {}
    tool_set_events = []
    plugin.tool_runtime = SimpleNamespace(
        build_tool_set=lambda actual_event: tool_set_events.append(actual_event) or "tools"
    )

    async def record(_event, *, schedule):
        assert schedule is True
        plugin.coordinator.enqueue(
            "qq:group:1",
            seq=1,
            received_at=0,
            directed=False,
        )
        return "qq:group:1", 1, False

    async def get_conversation(_event):
        return "conversation"

    plugin._record = record
    plugin._get_conversation = get_conversation
    plugin._is_authorized = lambda _event: True
    event = FakeEvent({})
    event.is_at_or_wake_command = False
    event.platform_meta = SimpleNamespace(support_proactive_message=True)
    event.send = lambda _message: None
    event.should_call_llm = lambda _enabled: None
    event.get_message_str = lambda: "hello"
    event.request_llm = lambda **kwargs: kwargs

    observation = plugin.observe_group_message(event)
    request = await anext(observation)
    await observation.aclose()

    cycle_prompt = getattr(main_module, "OBSERVATION_CYCLE_PROMPT", "")
    assert cycle_prompt == (
        "Review the recent group chat and complete this cycle through the "
        "appropriate tool calls."
    )
    assert "snapshot" not in cycle_prompt.lower()
    assert request["prompt"] == cycle_prompt
    assert request["tool_set"] == "tools"
    assert tool_set_events == [event]


@pytest.mark.asyncio
async def test_stay_silent_persists_outcome_and_advances_cursor(tmp_path):
    flow_id = "qq:group:1"
    store = GroupFlowStore(tmp_path)
    store.append_record(flow_id, {"message_id": "m1", "text": "hello"})
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
            SILENCE_SELECTED_EXTRA: True,
            SNAPSHOT_SEQ_EXTRA: 1,
            PENDING_CURSOR_EXTRA: {
                "conversation_id": "conv",
                "target_seq": 1,
                "history_cursor": 1,
            },
        }
    )

    await plugin._persist_observation_state(event, had_direct_output=False)

    assert store.get_run_outcome(snapshot.run_id)["outcome"] == "silence_selected"
    assert store.get_cursor(flow_id, "conv") == 1
    assert store.get_history_cursor(flow_id, "conv") == 1


@pytest.mark.asyncio
async def test_persist_agent_action_extends_only_an_existing_pending_snapshot(
    tmp_path,
):
    flow_id = "qq:group:1"
    store = GroupFlowStore(tmp_path)
    store.append_record(flow_id, {"message_id": "m1", "text": "first"})
    store.append_record(flow_id, {"message_id": "m2", "text": "during run"})
    coordinator = GroupRunCoordinator(
        debounce_seconds=0,
        direct_delay_seconds=0,
        min_cycle_interval_seconds=0,
    )
    coordinator.enqueue(flow_id, seq=1, received_at=99.0, directed=False)
    snapshot = coordinator.begin_if_due(flow_id, now=99.0)
    coordinator.enqueue(flow_id, seq=2, received_at=100.0, directed=False)
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.store = store
    plugin.coordinator = coordinator
    plugin._locks = {}

    seq = await plugin._persist_agent_action(
        flow_id,
        {"message_id": "agent-action:run-1:1", "text": "replied"},
        run_id=snapshot.run_id,
        generation=snapshot.generation,
    )

    assert seq == 3
    coordinator.finish_if_active(snapshot.run_id, finished_at=100.0)
    assert coordinator.begin_if_due(flow_id, now=100.0).snapshot_seq == 3


@pytest.mark.asyncio
async def test_persist_agent_action_does_not_schedule_without_pending_message(tmp_path):
    flow_id = "qq:group:1"
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.store = GroupFlowStore(tmp_path)
    plugin.coordinator = GroupRunCoordinator(
        debounce_seconds=0,
        direct_delay_seconds=0,
    )
    plugin._locks = {}
    plugin.store.append_record(flow_id, {"message_id": "m1", "text": "first"})
    plugin.coordinator.enqueue(flow_id, seq=1, received_at=0, directed=False)
    snapshot = plugin.coordinator.begin_if_due(flow_id, now=0)

    seq = await plugin._persist_agent_action(
        flow_id,
        {"message_id": "agent-action:run-1:1", "text": "replied"},
        run_id=snapshot.run_id,
        generation=snapshot.generation,
    )

    assert seq == 2
    assert plugin.coordinator.next_due_at(flow_id) is None


@pytest.mark.asyncio
async def test_later_actions_update_the_same_run_outcome(tmp_path):
    flow_id = "qq:group:1"
    store = GroupFlowStore(tmp_path)
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.store = store
    plugin.config = {}
    plugin._locks = {}
    snapshot = _activate_run(plugin, flow_id, 5)
    event = FakeEvent(
        {
            FLOW_ID_EXTRA: flow_id,
            RUN_ID_EXTRA: snapshot.run_id,
            RUN_GENERATION_EXTRA: snapshot.generation,
            SNAPSHOT_SEQ_EXTRA: 5,
        }
    )
    event.set_extra(
        "_group_agent_external_actions",
        [{"action_name": "reply_message", "status": "succeeded", "detail": ""}],
    )

    await plugin._persist_observation_state(event, had_direct_output=False)
    event.set_extra(
        "_group_agent_external_actions",
        [
            {"action_name": "reply_message", "status": "succeeded", "detail": ""},
            {
                "action_name": "react_message",
                "status": "failed",
                "detail": "qq unavailable",
            },
        ],
    )
    await plugin._persist_observation_state(event, had_direct_output=False)

    outcome = store.get_run_outcome(snapshot.run_id)
    assert outcome["outcome"] == "action_partial"
    assert outcome["detail"] == "reply_message,react_message"


@pytest.mark.asyncio
async def test_run_outcome_keeps_safe_fact_persistence_diagnostics(tmp_path):
    flow_id = "qq:group:1"
    store = GroupFlowStore(tmp_path)
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.store = store
    plugin.config = {}
    plugin._locks = {}
    snapshot = _activate_run(plugin, flow_id, 5)
    event = FakeEvent(
        {
            FLOW_ID_EXTRA: flow_id,
            RUN_ID_EXTRA: snapshot.run_id,
            RUN_GENERATION_EXTRA: snapshot.generation,
            SNAPSHOT_SEQ_EXTRA: 5,
            "_group_agent_external_actions": [
                {
                    "action_name": "reply_message",
                    "status": "succeeded",
                    "detail": "fact_persist_failed:OSError",
                }
            ],
        }
    )

    await plugin._persist_observation_state(event, had_direct_output=False)

    outcome = store.get_run_outcome(snapshot.run_id)
    assert outcome["outcome"] == "action_succeeded"
    assert outcome["detail"] == "reply_message(fact_persist_failed:OSError)"


@pytest.mark.asyncio
async def test_clear_invalidates_old_run_state_and_action_writes(tmp_path):
    flow_id = "qq:group:1"
    store = GroupFlowStore(tmp_path)
    store.append_record(flow_id, {"message_id": "m1", "text": "hello"})
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
            SILENCE_SELECTED_EXTRA: True,
            SNAPSHOT_SEQ_EXTRA: 1,
            PENDING_CURSOR_EXTRA: {
                "conversation_id": "conv",
                "target_seq": 1,
                "history_cursor": 1,
            },
        }
    )

    clear_event = FakeEvent({})
    clear_event.get_message_type = lambda: main_module.MessageType.GROUP_MESSAGE
    clear_event.get_platform_id = lambda: "qq"
    clear_event.get_group_id = lambda: "1"
    clear_event.plain_result = lambda content: content

    results = [
        result async for result in plugin.group_agent_clear(clear_event)
    ]

    await plugin._persist_observation_state(event, had_direct_output=False)
    with pytest.raises(RuntimeError, match="stale autonomous group run"):
        await plugin._persist_agent_action(
            flow_id,
            {"message_id": "agent-action:old:1", "text": "old"},
            run_id=snapshot.run_id,
            generation=snapshot.generation,
        )

    assert store.read_records(flow_id) == []
    assert store.get_cursor(flow_id, "conv") == 0
    assert store.get_run_outcome(snapshot.run_id) is None
    assert results == ["Group Agent Flow data cleared for this group."]


@pytest.mark.asyncio
async def test_clear_also_resets_current_core_conversation_history(tmp_path):
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.store = GroupFlowStore(tmp_path)
    plugin.config = {}
    plugin._locks = {}
    plugin.coordinator = GroupRunCoordinator(
        debounce_seconds=0, direct_delay_seconds=0, min_cycle_interval_seconds=0
    )
    updates = []

    class Manager:
        async def get_curr_conversation_id(self, _origin):
            return "conv"

        async def update_conversation(self, origin, cid, *, history, token_usage):
            updates.append((origin, cid, history, token_usage))

    plugin.context = SimpleNamespace(conversation_manager=Manager())
    event = FakeEvent({})
    event.get_message_type = lambda: main_module.MessageType.GROUP_MESSAGE
    event.get_platform_id = lambda: "qq"
    event.get_group_id = lambda: "1"
    event.plain_result = lambda content: content

    results = [result async for result in plugin.group_agent_clear(event)]

    assert updates == [("qq:GroupMessage:1", "conv", [], 0)]
    assert results == ["Group Agent Flow data cleared for this group."]


@pytest.mark.asyncio
async def test_inject_snapshot_bootstraps_when_only_legacy_cursor_covers_snapshot(
    tmp_path,
):
    flow_id = "qq:group:1"
    store = GroupFlowStore(tmp_path)
    store.append_record(
        flow_id,
        {
            "message_id": "m44",
            "group_id": "1",
            "sender_id": "2",
            "sender_name": "Alice",
            "timestamp": 1710000000,
            "text": "already consumed",
        },
    )
    store.set_cursor(
        flow_id,
        "conv",
        1,
        unified_msg_origin="qq:GroupMessage:1",
    )
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.store = store
    plugin.config = {}
    plugin._locks = {}
    tool_set_events = []
    plugin.tool_runtime = SimpleNamespace(
        build_tool_set=lambda actual_event: tool_set_events.append(actual_event) or "tools"
    )
    snapshot = _activate_run(plugin, flow_id, 1)
    event = FakeEvent(
        {
            AUTONOMOUS_EXTRA: True,
            FLOW_ID_EXTRA: flow_id,
            RUN_ID_EXTRA: snapshot.run_id,
            RUN_GENERATION_EXTRA: snapshot.generation,
            SNAPSHOT_SEQ_EXTRA: 1,
        }
    )
    request = SimpleNamespace(
        conversation=SimpleNamespace(cid="conv", token_usage=999),
        contexts=[],
        func_tool=None,
    )

    await plugin.inject_snapshot(event, request)

    assert event.stopped is False
    assert len(request.contexts) == 1
    assert "already consumed" in request.contexts[0]["content"]
    assert request.func_tool == "tools"
    assert tool_set_events == [event]
    assert request.conversation.token_usage == 0
    assert store.get_run_outcome(snapshot.run_id) is None


@pytest.mark.asyncio
async def test_enforce_autonomous_tools_builds_tools_for_current_event(monkeypatch):
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    event = FakeEvent({AUTONOMOUS_EXTRA: True})
    request = ProviderRequest(prompt="observe")
    event.set_extra("provider_request", request)
    tool_set_events = []
    plugin.tool_runtime = SimpleNamespace(
        build_tool_set=lambda actual_event: tool_set_events.append(actual_event) or "tools"
    )
    enforced = []
    monkeypatch.setattr(
        main_module,
        "enforce_tool_set",
        lambda actual_request, tool_set: enforced.append((actual_request, tool_set)),
    )

    await plugin.enforce_autonomous_tools(event, None)

    assert tool_set_events == [event]
    assert enforced == [(request, "tools")]


@pytest.mark.asyncio
async def test_inject_snapshot_adds_completed_agent_action_to_provider_context(tmp_path):
    flow_id = "qq:group:1"
    store = GroupFlowStore(tmp_path)
    store.append_record(
        flow_id,
        {
            "record_kind": "agent_action",
            "message_id": "agent-action:run-1:1",
            "targetable": False,
            "group_id": "1",
            "sender_id": "7",
            "self_id": "7",
            "timestamp": 1710000000,
            "text": "已经处理",
            "components": [{"type": "text", "text": "已经处理"}],
            "action_name": "reply_message",
            "action_status": "succeeded",
            "target_message_id": "m1",
        },
    )
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.store = store
    plugin.config = {"context": {"renderer": "plain_lines"}}
    plugin._locks = {}
    plugin.tool_runtime = SimpleNamespace(build_tool_set=lambda _event: "tools")
    snapshot = _activate_run(plugin, flow_id, 1)
    event = FakeEvent(
        {
            AUTONOMOUS_EXTRA: True,
            FLOW_ID_EXTRA: flow_id,
            RUN_ID_EXTRA: snapshot.run_id,
            RUN_GENERATION_EXTRA: snapshot.generation,
            SNAPSHOT_SEQ_EXTRA: 1,
        }
    )
    request = SimpleNamespace(
        conversation=SimpleNamespace(cid="conv", token_usage=999),
        contexts=[
            {"role": "assistant", "content": "old assistant history"},
            {"role": "tool", "content": "old tool result"},
        ],
        func_tool=None,
    )

    await plugin.inject_snapshot(event, request)

    assert len(request.contexts) == 1
    assert "actor=bot action=reply_message status=succeeded" in request.contexts[0][
        "content"
    ]
    assert "target_msg=m1" in request.contexts[0]["content"]
    assert all("old " not in context["content"] for context in request.contexts)
    assert request.conversation.token_usage == 0
    assert request.func_tool == "tools"
    assert store.get_cursor(flow_id, "conv") == 0
    assert store.get_history_cursor(flow_id, "conv") is None

    await plugin._persist_observation_state(event, had_direct_output=False)

    assert store.get_cursor(flow_id, "conv") == 1
    assert store.get_history_cursor(flow_id, "conv") == 1


@pytest.mark.asyncio
async def test_inject_snapshot_uses_the_current_renderer_without_replacing_core_history(tmp_path):
    flow_id = "qq:group:1"
    store = GroupFlowStore(tmp_path)
    store.append_record(
        flow_id,
        {
            "message_id": "m1",
            "group_id": "1",
            "sender_id": "2",
            "sender_name": "Alice",
            "timestamp": 1710000000,
            "text": "earlier message",
        },
    )
    store.get_or_assign_renderer(flow_id, "conv", "legacy_delta")
    store.commit_observation(
        flow_id,
        "conv",
        1,
        unified_msg_origin="qq:GroupMessage:1",
        history_cursor=1,
    )
    store.append_record(
        flow_id,
        {
            "message_id": "m2",
            "group_id": "1",
            "sender_id": "3",
            "sender_name": "Bob",
            "timestamp": 1710000001,
            "text": "new message",
        },
    )
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.store = store
    plugin.config = {"context": {"renderer": "native_messages"}}
    plugin._locks = {}
    plugin.tool_runtime = SimpleNamespace(build_tool_set=lambda _event: "tools")
    snapshot = _activate_run(plugin, flow_id, 2)
    event = FakeEvent(
        {
            AUTONOMOUS_EXTRA: True,
            FLOW_ID_EXTRA: flow_id,
            RUN_ID_EXTRA: snapshot.run_id,
            RUN_GENERATION_EXTRA: snapshot.generation,
            SNAPSHOT_SEQ_EXTRA: 2,
        }
    )
    request = SimpleNamespace(
        conversation=SimpleNamespace(cid="conv", token_usage=999),
        contexts=[
            {"role": "assistant", "content": "previous assistant output"},
            {"role": "tool", "content": "previous tool result"},
        ],
        func_tool=None,
    )

    await plugin.inject_snapshot(event, request)

    assert request.contexts[:2] == [
        {"role": "assistant", "content": "previous assistant output"},
        {"role": "tool", "content": "previous tool result"},
    ]
    assert request.contexts[2]["role"] == "user"
    assert "msg=m2" in request.contexts[2]["content"]
    assert "new message" in request.contexts[2]["content"]
    assert "group_messages_delta" not in request.contexts[2]["content"]
    assert request.conversation.token_usage == (
        999 + event.get_extra(PENDING_CURSOR_EXTRA)["estimated_tokens"]
    )
    assert store.get_or_assign_renderer(flow_id, "conv", "native_messages") == "native_messages"


@pytest.mark.asyncio
async def test_final_prompt_hook_moves_protocol_after_later_astrbot_hooks(tmp_path):
    flow_id = "qq:group:1"
    store = GroupFlowStore(tmp_path)
    store.append_record(
        flow_id,
        {
            "message_id": "m45",
            "group_id": "1",
            "sender_id": "2",
            "sender_name": "Alice",
            "timestamp": 1710000001,
            "text": "new message",
        },
    )
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.store = store
    plugin.config = {}
    plugin._locks = {}
    plugin.tool_runtime = SimpleNamespace(build_tool_set=lambda _event: "tools")
    snapshot = _activate_run(plugin, flow_id, 1)
    event = FakeEvent(
        {
            AUTONOMOUS_EXTRA: True,
            FLOW_ID_EXTRA: flow_id,
            RUN_ID_EXTRA: snapshot.run_id,
            RUN_GENERATION_EXTRA: snapshot.generation,
            SNAPSHOT_SEQ_EXTRA: 1,
        }
    )
    request = SimpleNamespace(
        conversation=SimpleNamespace(cid="conv", token_usage=0),
        contexts=[],
        func_tool=None,
        system_prompt=main_module.AGENT_PROTOCOL_PROMPT,
    )

    await plugin.inject_snapshot(event, request)
    request.system_prompt += (
        "\n\n# Persona Instructions\nAct like Paimon.\n\n"
        "# Tool Instructions\nCall tools when useful."
    )

    await plugin.finalize_agent_protocol(event, request)

    assert request.system_prompt.endswith(main_module.AGENT_PROTOCOL_PROMPT)
    assert request.system_prompt.count(main_module.AGENT_PROTOCOL_PROMPT) == 1
    assert "# Persona Instructions\nAct like Paimon." in request.system_prompt
    assert "# Tool Instructions\nCall tools when useful." in request.system_prompt
    assert request.func_tool == "tools"


@pytest.mark.asyncio
async def test_finalize_agent_protocol_removes_core_tool_prompts_for_autonomous_request():
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    event = FakeEvent({AUTONOMOUS_EXTRA: True})
    request = SimpleNamespace(
        system_prompt=(
            f"# Persona Instructions\nAct like Paimon.\n\n{TOOL_CALL_PROMPT}\n\n"
            f"{TOOL_CALL_PROMPT_SKILLS_LIKE_MODE}\n\n"
            f"{main_module.AGENT_PROTOCOL_PROMPT}"
        )
    )

    await plugin.finalize_agent_protocol(event, request)

    assert TOOL_CALL_PROMPT not in request.system_prompt
    assert TOOL_CALL_PROMPT_SKILLS_LIKE_MODE not in request.system_prompt
    assert request.system_prompt.endswith(main_module.AGENT_PROTOCOL_PROMPT)
    assert request.system_prompt.count(main_module.AGENT_PROTOCOL_PROMPT) == 1
    assert "# Persona Instructions\nAct like Paimon." in request.system_prompt


@pytest.mark.asyncio
async def test_finalize_agent_protocol_leaves_non_autonomous_request_unchanged():
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    event = FakeEvent({AUTONOMOUS_EXTRA: False})
    original_prompt = f"# Tool Instructions\n{TOOL_CALL_PROMPT}"
    request = SimpleNamespace(system_prompt=original_prompt)

    await plugin.finalize_agent_protocol(event, request)

    assert request.system_prompt == original_prompt
