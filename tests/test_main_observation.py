from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_group_agent_flow.agent_tools import (  # noqa: E402
    FLOW_ID_EXTRA,
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


@pytest.mark.asyncio
async def test_observation_request_uses_a_short_tool_call_reminder():
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.config = {}
    plugin.coordinator = GroupRunCoordinator(
        debounce_seconds=0,
        direct_delay_seconds=0,
        min_cycle_interval_seconds=0,
    )
    plugin.tool_runtime = SimpleNamespace(build_tool_set=lambda: "tools")

    async def record(_event):
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


@pytest.mark.asyncio
async def test_stay_silent_persists_outcome_and_advances_cursor(tmp_path):
    flow_id = "qq:group:1"
    store = GroupFlowStore(tmp_path)
    store.append_record(flow_id, {"message_id": "m1", "text": "hello"})
    plugin = GroupAgentFlowPlugin.__new__(GroupAgentFlowPlugin)
    plugin.store = store
    plugin.config = {}
    plugin._locks = {}
    event = FakeEvent(
        {
            FLOW_ID_EXTRA: flow_id,
            RUN_ID_EXTRA: "silent-run",
            SILENCE_SELECTED_EXTRA: True,
            SNAPSHOT_SEQ_EXTRA: 1,
            PENDING_CURSOR_EXTRA: {
                "conversation_id": "conv",
                "target_seq": 1,
            },
        }
    )

    await plugin._persist_observation_state(event, had_direct_output=False)

    assert store.get_run_outcome("silent-run")["outcome"] == "silence_selected"
    assert store.get_cursor(flow_id, "conv") == 1


@pytest.mark.asyncio
async def test_inject_snapshot_stops_before_provider_when_cursor_covers_snapshot(
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
    plugin.tool_runtime = SimpleNamespace(build_tool_set=lambda: "tools")
    event = FakeEvent(
        {
            AUTONOMOUS_EXTRA: True,
            FLOW_ID_EXTRA: flow_id,
            RUN_ID_EXTRA: "stale-run",
            SNAPSHOT_SEQ_EXTRA: 1,
        }
    )
    request = SimpleNamespace(
        conversation=SimpleNamespace(cid="conv"),
        contexts=[],
        func_tool=None,
    )

    await plugin.inject_snapshot(event, request)

    assert event.stopped is True
    assert request.contexts == []
    assert request.func_tool is None
    assert store.get_run_outcome("stale-run")["outcome"] == "empty_snapshot_skipped"


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
    plugin.tool_runtime = SimpleNamespace(build_tool_set=lambda: "tools")
    event = FakeEvent(
        {
            AUTONOMOUS_EXTRA: True,
            FLOW_ID_EXTRA: flow_id,
            RUN_ID_EXTRA: "current-run",
            SNAPSHOT_SEQ_EXTRA: 1,
        }
    )
    request = SimpleNamespace(
        conversation=SimpleNamespace(cid="conv"),
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
