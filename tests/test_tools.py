import json
from types import SimpleNamespace

import pytest

from agent_tools import SILENCE_SELECTED_EXTRA, ToolRuntime
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
from qq_gateway import QQActionGateway
from store import GroupFlowStore


class FakeBot:
    def __init__(self):
        self.actions = []

    async def call_action(self, action, **kwargs):
        self.actions.append((action, kwargs))
        return {"action": action, **kwargs}


class FakeEvent:
    def __init__(self):
        self.bot = FakeBot()
        self.message_obj = SimpleNamespace(raw_message={})
        self.sent = []
        self.extras = {
            "_group_agent_run_id": "run-1",
            "_group_agent_flow_id": "napcat:group:1",
            "_group_agent_snapshot_seq": 5,
        }
        self.result = None

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        self.extras[key] = value

    def get_result(self):
        return self.result

    async def send(self, chain):
        self.sent.append(chain)


def test_tool_set_exposes_only_explicit_agent_tools(tmp_path):
    runtime = ToolRuntime(GroupFlowStore(tmp_path), QQActionGateway())

    tool_set = runtime.build_tool_set()

    assert [tool.name for tool in tool_set.tools] == [
        "send_message",
        "reply_message",
        "react_message",
        "poke_user",
        "stay_silent",
        "get_message",
        "search_chat_history",
    ]
    assert tool_set.get_tool("stay_silent").parameters == {
        "type": "object",
        "properties": {},
    }
    poke_tool = tool_set.get_tool("poke_user")
    assert "available chat history" in poke_tool.description
    assert "snapshot" not in poke_tool.description.lower()
    user_id_description = poke_tool.parameters["properties"]["user_id"][
        "description"
    ]
    assert "available chat history" in user_id_description
    assert "snapshot" not in user_id_description.lower()
    react_tool = tool_set.get_tool("react_message")
    assert react_tool.parameters["properties"]["reaction"]["enum"] == [
        "赞",
        "爱心",
        "鼓掌",
        "笑哭",
        "微笑",
        "疑问",
        "惊讶",
        "流泪",
        "生气",
        "玫瑰",
        "捂脸",
    ]
    assert "emoji_id" not in react_tool.parameters["properties"]
    assert react_tool.parameters["required"] == ["message_id", "reaction"]
    poke_tool = tool_set.get_tool("poke_user")
    assert poke_tool.parameters["required"] == ["user_id"]
    assert (
        "observed"
        in poke_tool.parameters["properties"]["user_id"]["description"].lower()
    )
    for tool_name in ("reply_message", "react_message", "get_message"):
        tool = tool_set.get_tool(tool_name)
        assert "msg=" in tool.parameters["properties"]["message_id"]["description"]


@pytest.mark.asyncio
async def test_multiple_external_actions_execute_in_one_run(tmp_path):
    store = GroupFlowStore(tmp_path)
    store.append_record(
        "napcat:group:1",
        {"message_id": "m1", "sender_id": "alice", "text": "给我点赞"},
    )
    runtime = ToolRuntime(store, QQActionGateway())
    tools = runtime.build_tool_set()
    event = FakeEvent()

    reacted = await tools.get_tool("react_message").handler(
        event,
        message_id="m1",
        reaction="赞",
    )
    replied = await tools.get_tool("reply_message").handler(
        event,
        message_id="m1",
        content="点好啦",
    )

    assert reacted is None
    assert replied is None
    assert event.bot.actions[0][0] == "set_msg_emoji_like"
    assert len(event.sent) == 1
    assert [
        action["action_name"]
        for action in event.get_extra("_group_agent_external_actions")
    ] == ["react_message", "reply_message"]


@pytest.mark.asyncio
async def test_external_message_tool_returns_astrbot_terminal_signal(tmp_path):
    finalized = []

    async def finalize(event):
        finalized.append(event)

    runtime = ToolRuntime(
        GroupFlowStore(tmp_path),
        QQActionGateway(),
        terminal_callback=finalize,
    )
    event = FakeEvent()
    tool = runtime.build_tool_set().get_tool("send_message")
    run_context = ContextWrapper(
        context=SimpleNamespace(event=event, context=SimpleNamespace())
    )

    results = [
        result
        async for result in FunctionToolExecutor._execute_local(
            tool,
            run_context,
            content="B",
        )
    ]

    assert results == [None]
    assert len(event.sent) == 1
    assert finalized == [event]
    assert event.get_extra("_group_agent_external_actions") == [
        {"action_name": "send_message", "status": "succeeded", "detail": ""}
    ]


@pytest.mark.asyncio
async def test_stay_silent_marks_cycle_and_returns_terminal_signal(tmp_path):
    finalized = []

    async def finalize(event):
        finalized.append(event.get_extra(SILENCE_SELECTED_EXTRA))

    store = GroupFlowStore(tmp_path)
    store.append_record(
        "napcat:group:1",
        {"message_id": "m1", "sender_id": "alice", "text": "hello"},
    )
    runtime = ToolRuntime(
        store,
        QQActionGateway(),
        terminal_callback=finalize,
    )
    event = FakeEvent()
    tools = runtime.build_tool_set()
    observed = json.loads(
        await tools.get_tool("get_message").handler(event, message_id="m1")
    )
    tool = tools.get_tool("stay_silent")
    run_context = ContextWrapper(
        context=SimpleNamespace(event=event, context=SimpleNamespace())
    )

    results = [
        result
        async for result in FunctionToolExecutor._execute_local(tool, run_context)
    ]

    assert observed["message_id"] == "m1"
    assert results == [None]
    assert finalized == [True]
    assert event.sent == []
    assert event.bot.actions == []
    assert event.get_extra(SILENCE_SELECTED_EXTRA) is True
    assert event.get_extra("_group_agent_external_actions", []) == []


@pytest.mark.asyncio
async def test_failed_external_action_is_terminal_and_records_failure(tmp_path):
    runtime = ToolRuntime(GroupFlowStore(tmp_path), QQActionGateway())
    event = FakeEvent()

    async def fail_send(_chain):
        raise RuntimeError("qq unavailable")

    event.send = fail_send
    result = (
        await runtime.build_tool_set()
        .get_tool("send_message")
        .handler(
            event,
            content="A",
        )
    )

    assert result is None
    assert event.get_extra("_group_agent_external_actions") == [
        {
            "action_name": "send_message",
            "status": "failed",
            "detail": "qq unavailable",
        }
    ]


@pytest.mark.asyncio
async def test_history_tools_return_records_without_claiming_terminal_slot(tmp_path):
    store = GroupFlowStore(tmp_path)
    store.append_record(
        "napcat:group:1",
        {
            "message_id": "m1",
            "sender_id": "alice",
            "sender_name": "Alice",
            "text": "部署完成",
            "timestamp": 100,
        },
    )
    runtime = ToolRuntime(store, QQActionGateway())
    tools = runtime.build_tool_set()
    event = FakeEvent()

    message = json.loads(
        await tools.get_tool("get_message").handler(event, message_id="m1")
    )
    search = json.loads(
        await tools.get_tool("search_chat_history").handler(
            event,
            query="部署",
            sender_id="",
            since=None,
            until=None,
            limit=10,
        )
    )

    assert message["message_id"] == "m1"
    assert [item["message_id"] for item in search["messages"]] == ["m1"]
    assert event.get_extra("_group_agent_external_actions", []) == []


@pytest.mark.asyncio
async def test_tools_cannot_read_or_target_messages_after_frozen_snapshot(tmp_path):
    store = GroupFlowStore(tmp_path)
    store.append_record("napcat:group:1", {"message_id": "old", "text": "old"})
    store.append_record("napcat:group:1", {"message_id": "future", "text": "future"})
    event = FakeEvent()
    event.extras["_group_agent_snapshot_seq"] = 1
    runtime = ToolRuntime(store, QQActionGateway())
    tools = runtime.build_tool_set()

    get_result = json.loads(
        await tools.get_tool("get_message").handler(event, message_id="future")
    )
    search_result = json.loads(
        await tools.get_tool("search_chat_history").handler(event, query="")
    )
    reply_result = json.loads(
        await tools.get_tool("reply_message").handler(
            event,
            message_id="future",
            content="late",
        )
    )

    assert get_result["error"] == "message_not_found_in_snapshot"
    assert [message["message_id"] for message in search_result["messages"]] == ["old"]
    assert reply_result["error"] == "message_not_found_in_snapshot"
    assert event.sent == []


@pytest.mark.asyncio
async def test_poke_user_only_targets_users_observed_in_frozen_snapshot(tmp_path):
    store = GroupFlowStore(tmp_path)
    store.append_record(
        "napcat:group:1",
        {
            "message_id": "m1",
            "sender_id": "alice",
            "text": "hello",
            "components": [
                {"type": "at", "user_id": "bob"},
                {"type": "poke", "target_id": "carol"},
            ],
        },
    )
    store.append_record(
        "napcat:group:1",
        {"message_id": "m2", "sender_id": "future", "text": "later"},
    )
    event = FakeEvent()
    event.extras["_group_agent_snapshot_seq"] = 1
    tools = ToolRuntime(store, QQActionGateway()).build_tool_set()

    for user_id in ("alice", "bob", "carol"):
        assert await tools.get_tool("poke_user").handler(event, user_id=user_id) is None
    rejected = json.loads(
        await tools.get_tool("poke_user").handler(event, user_id="future")
    )

    assert [chain.chain[0].target_id() for chain in event.sent] == [
        "alice",
        "bob",
        "carol",
    ]
    assert rejected == {
        "success": False,
        "error": "user_not_found_in_snapshot",
        "user_id": "future",
    }
