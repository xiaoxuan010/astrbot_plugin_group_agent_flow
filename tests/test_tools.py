import json
from types import SimpleNamespace

import pytest

import agent_tools as agent_tools_module
from agent_tools import SILENCE_SELECTED_EXTRA, ToolRuntime
from aiocqhttp.exceptions import ActionFailed
from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
from astrbot.core.provider.entities import LLMResponse, ProviderRequest, TokenUsage
from astrbot.core.provider.provider import Provider
from mcp.types import CallToolResult, ImageContent, TextContent
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
        self.unified_msg_origin = "napcat:group:1"
        self.message_obj = SimpleNamespace(
            raw_message={},
            group=SimpleNamespace(group_name="测试群"),
        )
        self.sent = []
        self.extras = {
            "_group_agent_run_id": "run-1",
            "_group_agent_flow_id": "napcat:group:1",
            "_group_agent_snapshot_seq": 5,
            "_group_agent_run_generation": 0,
        }
        self.result = None

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        self.extras[key] = value

    def get_result(self):
        return self.result

    def get_platform_id(self):
        return "napcat"

    def get_platform_name(self):
        return "aiocqhttp"

    def get_group_id(self):
        return "1"

    def get_self_id(self):
        return "7"

    async def send(self, chain):
        self.sent.append(chain)


class FakeContext:
    def __init__(self, *, using_provider=None, config=None, providers=None):
        self.using_provider = using_provider
        self.config = config or {}
        self.providers = providers or {}

    def get_using_provider(self, umo=None):
        return self.using_provider

    def get_config(self, umo=None):
        return self.config

    def get_provider_by_id(self, provider_id):
        return self.providers.get(provider_id)


class ConsecutiveActionProvider(Provider):
    def __init__(self):
        super().__init__({}, {})
        self.call_count = 0

    def get_current_key(self):
        return "test"

    def set_key(self, _key):
        return None

    async def get_models(self):
        return ["test"]

    async def text_chat(self, **_kwargs):
        self.call_count += 1
        if self.call_count == 2:
            return LLMResponse(
                role="assistant",
                tools_call_name=["send_message"],
                tools_call_args=[{"content": "再发一次"}],
                tools_call_ids=["call-2"],
                usage=TokenUsage(input_other=10, output=5),
            )
        if self.call_count == 3:
            return LLMResponse(
                role="assistant",
                tools_call_name=["stay_silent"],
                tools_call_args=[{}],
                tools_call_ids=["call-3"],
                usage=TokenUsage(input_other=10, output=5),
            )
        return LLMResponse(
            role="assistant",
            tools_call_name=["send_message"],
            tools_call_args=[{"content": "只发一次"}],
            tools_call_ids=["call-1"],
            usage=TokenUsage(input_other=10, output=5),
        )

    async def text_chat_stream(self, **kwargs):
        yield await self.text_chat(**kwargs)


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
        "get_image_captions",
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
    for tool_name in (
        "reply_message",
        "react_message",
        "get_message",
        "get_image_captions",
    ):
        tool = tool_set.get_tool(tool_name)
        assert "msg=" in tool.parameters["properties"]["message_id"]["description"]


@pytest.mark.parametrize(
    (
        "modalities",
        "exposes_original_images",
        "exposes_image_captions",
    ),
    [
        (["text", "image", "tool_use"], True, False),
        (["text", "tool_use"], False, True),
        ([], False, True),
        (None, False, True),
        ("text,image", False, True),
    ],
)
def test_image_tool_visibility_follows_explicit_provider_capability(
    tmp_path,
    modalities,
    exposes_original_images,
    exposes_image_captions,
):
    provider_config = {}
    if modalities is not None:
        provider_config["modalities"] = modalities
    context = FakeContext(
        using_provider=SimpleNamespace(provider_config=provider_config)
    )
    event = FakeEvent()
    runtime = ToolRuntime(
        GroupFlowStore(tmp_path),
        QQActionGateway(),
        context=context,
    )

    names = runtime.build_tool_set(event).names()

    assert ("get_message_images" in names) is exposes_original_images
    assert ("get_image_captions" in names) is exposes_image_captions


def test_image_tool_visibility_hides_original_when_provider_lookup_fails(tmp_path):
    class FailingContext(FakeContext):
        def get_using_provider(self, umo=None):
            raise ValueError("invalid session provider")

    names = ToolRuntime(
        GroupFlowStore(tmp_path),
        QQActionGateway(),
        context=FailingContext(),
    ).build_tool_set(FakeEvent()).names()

    assert "get_message_images" not in names
    assert "get_image_captions" in names


@pytest.mark.asyncio
async def test_get_message_images_returns_core_multimodal_content(
    tmp_path,
    monkeypatch,
):
    store = GroupFlowStore(tmp_path)
    store.append_record(
        "napcat:group:1",
        {
            "message_id": "m1",
            "components": [
                {
                    "type": "image",
                    "url": r"D:\\AstrBot\\data\\temp\\expired-a.png",
                    "source_url": "https://example.com/a.png",
                },
                {"type": "text", "text": "两张图"},
                {
                    "type": "image",
                    "url": r"D:\\AstrBot\\data\\temp\\expired-b.jpg",
                    "source_url": "https://example.com/b.jpg",
                },
            ],
        },
    )
    resolved_refs = []

    async def resolve_image(image_ref, *, strict):
        resolved_refs.append((image_ref, strict))
        suffix = "png" if image_ref.endswith(".png") else "jpg"
        return SimpleNamespace(
            base64_data=f"base64-{suffix}",
            mime_type=f"image/{'png' if suffix == 'png' else 'jpeg'}",
        )

    monkeypatch.setattr(
        agent_tools_module,
        "resolve_image_ref_to_base64_data",
        resolve_image,
        raising=False,
    )
    context = FakeContext(
        using_provider=SimpleNamespace(
            provider_config={"modalities": ["text", "image", "tool_use"]}
        )
    )
    event = FakeEvent()
    tool = ToolRuntime(
        store,
        QQActionGateway(),
        context=context,
    ).build_tool_set(event).get_tool("get_message_images")

    result = await tool.handler(event, message_id="m1")

    assert isinstance(result, CallToolResult)
    assert result.content == [
        TextContent(
            type="text",
            text='{"message_id":"m1","image_count":2}',
        ),
        ImageContent(type="image", data="base64-png", mimeType="image/png"),
        ImageContent(type="image", data="base64-jpg", mimeType="image/jpeg"),
    ]
    assert resolved_refs == [
        ("https://example.com/a.png", True),
        ("https://example.com/b.jpg", True),
    ]


@pytest.mark.asyncio
async def test_get_image_captions_uses_dedicated_provider(tmp_path):
    store = GroupFlowStore(tmp_path)
    store.append_record(
        "napcat:group:1",
        {
            "message_id": "m1",
            "components": [
                {
                    "type": "image",
                    "url": r"D:\\AstrBot\\data\\temp\\expired-a.png",
                    "source_url": "https://example.com/a.png",
                },
                {
                    "type": "image",
                    "url": r"D:\\AstrBot\\data\\temp\\expired-b.jpg",
                    "source_url": "https://example.com/b.jpg",
                },
            ],
        },
    )

    class CaptionProvider:
        def __init__(self):
            self.calls = []

        async def text_chat(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(completion_text="两张群聊截图，内容是部署结果。")

    caption_provider = CaptionProvider()
    context = FakeContext(
        using_provider=SimpleNamespace(
            provider_config={"modalities": ["text", "tool_use"]}
        ),
        config={
            "provider_settings": {
                "default_image_caption_provider_id": "caption-provider",
                "image_caption_prompt": "请用一句中文描述图片。",
            }
        },
        providers={"caption-provider": caption_provider},
    )
    event = FakeEvent()
    tool = ToolRuntime(
        store,
        QQActionGateway(),
        context=context,
    ).build_tool_set(event).get_tool("get_image_captions")

    result = json.loads(await tool.handler(event, message_id="m1"))

    assert result == {
        "message_id": "m1",
        "caption": "两张群聊截图，内容是部署结果。",
    }
    assert caption_provider.calls == [
        {
            "prompt": "请用一句中文描述图片。",
            "image_urls": [
                "https://example.com/a.png",
                "https://example.com/b.jpg",
            ],
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "modalities"),
    [
        ("get_message_images", ["text", "image", "tool_use"]),
        ("get_image_captions", ["text", "tool_use"]),
    ],
)
async def test_image_tools_reject_messages_outside_snapshot(
    tmp_path, tool_name, modalities
):
    store = GroupFlowStore(tmp_path)
    store.append_record(
        "napcat:group:1",
        {
            "message_id": "m1",
            "components": [{"type": "text", "text": "快照内"}],
        },
    )
    store.append_record(
        "napcat:group:1",
        {
            "message_id": "m2",
            "components": [
                {"type": "image", "url": "https://example.com/future.png"}
            ],
        },
    )
    context = FakeContext(
        using_provider=SimpleNamespace(
            provider_config={"modalities": modalities}
        )
    )
    event = FakeEvent()
    event.extras["_group_agent_snapshot_seq"] = 1
    tool = ToolRuntime(
        store,
        QQActionGateway(),
        context=context,
    ).build_tool_set(event).get_tool(tool_name)

    result = json.loads(await tool.handler(event, message_id="m2"))

    assert result == {
        "error": "message_not_found_in_snapshot",
        "message_id": "m2",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "modalities"),
    [
        ("get_message_images", ["text", "image", "tool_use"]),
        ("get_image_captions", ["text", "tool_use"]),
    ],
)
async def test_image_tools_reject_messages_without_images(
    tmp_path, tool_name, modalities
):
    store = GroupFlowStore(tmp_path)
    store.append_record(
        "napcat:group:1",
        {
            "message_id": "m1",
            "components": [{"type": "text", "text": "只有文字"}],
        },
    )
    context = FakeContext(
        using_provider=SimpleNamespace(
            provider_config={"modalities": modalities}
        )
    )
    event = FakeEvent()
    tool = ToolRuntime(
        store,
        QQActionGateway(),
        context=context,
    ).build_tool_set(event).get_tool(tool_name)

    result = json.loads(await tool.handler(event, message_id="m1"))

    assert result == {
        "error": "message_contains_no_images",
        "message_id": "m1",
    }


@pytest.mark.asyncio
async def test_get_message_images_returns_compact_error_when_resolution_fails(
    tmp_path,
    monkeypatch,
):
    store = GroupFlowStore(tmp_path)
    image_url = "https://secret.example/private.png"
    store.append_record(
        "napcat:group:1",
        {
            "message_id": "m1",
            "components": [{"type": "image", "url": image_url}],
        },
    )

    async def fail_resolution(_image_ref, *, strict):
        assert strict is True
        raise RuntimeError(f"download failed: {image_url}")

    monkeypatch.setattr(
        agent_tools_module,
        "resolve_image_ref_to_base64_data",
        fail_resolution,
    )
    context = FakeContext(
        using_provider=SimpleNamespace(
            provider_config={"modalities": ["text", "image", "tool_use"]}
        )
    )
    event = FakeEvent()
    tool = ToolRuntime(
        store,
        QQActionGateway(),
        context=context,
    ).build_tool_set(event).get_tool("get_message_images")

    result_text = await tool.handler(event, message_id="m1")

    assert json.loads(result_text) == {
        "error": "image_unavailable",
        "message_id": "m1",
    }
    assert image_url not in result_text
    assert "download failed" not in result_text


@pytest.mark.asyncio
async def test_get_message_images_refreshes_expired_url_from_napcat(tmp_path, monkeypatch):
    store = GroupFlowStore(tmp_path)
    store.append_record(
        "napcat:group:1",
        {
            "message_id": "m1",
            "message_seq": "77",
            "components": [
                {
                    "type": "image",
                    "url": r"D:\\AstrBot\\data\\temp\\expired.png",
                    "source_url": "https://cdn.example.com/expired.png",
                }
            ],
        },
    )
    resolved_refs = []

    async def resolve_image(image_ref, *, strict):
        resolved_refs.append((image_ref, strict))
        if image_ref.endswith("expired.png"):
            raise RuntimeError("HTTP status code: 400")
        return SimpleNamespace(base64_data="fresh-base64", mime_type="image/jpeg")

    class RefreshingGateway(QQActionGateway):
        async def refresh_message_images(self, event, *, message_id, message_seq):
            assert message_id == "m1"
            assert message_seq == "77"
            return ["https://cdn.example.com/fresh.jpg"]

    monkeypatch.setattr(
        agent_tools_module,
        "resolve_image_ref_to_base64_data",
        resolve_image,
    )
    context = FakeContext(
        using_provider=SimpleNamespace(
            provider_config={"modalities": ["text", "image", "tool_use"]}
        )
    )
    event = FakeEvent()
    tool = ToolRuntime(
        store,
        RefreshingGateway(),
        context=context,
    ).build_tool_set(event).get_tool("get_message_images")

    result = await tool.handler(event, message_id="m1")

    assert isinstance(result, CallToolResult)
    assert [item.data for item in result.content if isinstance(item, ImageContent)] == [
        "fresh-base64"
    ]
    assert resolved_refs == [
        ("https://cdn.example.com/expired.png", True),
        ("https://cdn.example.com/fresh.jpg", True),
    ]


@pytest.mark.asyncio
async def test_get_image_captions_reports_unconfigured_provider(tmp_path):
    store = GroupFlowStore(tmp_path)
    store.append_record(
        "napcat:group:1",
        {
            "message_id": "m1",
            "components": [
                {"type": "image", "url": "https://example.com/a.png"}
            ],
        },
    )
    context = FakeContext(
        using_provider=SimpleNamespace(
            provider_config={"modalities": ["text", "tool_use"]}
        )
    )
    event = FakeEvent()
    tool = ToolRuntime(
        store,
        QQActionGateway(),
        context=context,
    ).build_tool_set(event).get_tool("get_image_captions")

    result = json.loads(await tool.handler(event, message_id="m1"))

    assert result == {"error": "image_caption_provider_unconfigured"}


@pytest.mark.asyncio
async def test_get_image_captions_returns_compact_error_when_config_lookup_fails(
    tmp_path,
):
    store = GroupFlowStore(tmp_path)
    store.append_record(
        "napcat:group:1",
        {
            "message_id": "m1",
            "components": [
                {"type": "image", "url": "https://example.com/a.png"}
            ],
        },
    )

    class FailingContext(FakeContext):
        def get_config(self, umo=None):
            raise RuntimeError("config contains a secret diagnostic")

    tool = ToolRuntime(
        store,
        QQActionGateway(),
        context=FailingContext(),
    ).build_tool_set(FakeEvent()).get_tool("get_image_captions")

    result_text = await tool.handler(FakeEvent(), message_id="m1")

    assert json.loads(result_text) == {
        "error": "image_caption_failed",
        "message_id": "m1",
    }
    assert "secret diagnostic" not in result_text


@pytest.mark.asyncio
async def test_get_image_captions_returns_compact_error_when_provider_fails(tmp_path):
    store = GroupFlowStore(tmp_path)
    image_url = "https://secret.example/private.png"
    store.append_record(
        "napcat:group:1",
        {
            "message_id": "m1",
            "components": [{"type": "image", "url": image_url}],
        },
    )

    class FailingCaptionProvider:
        async def text_chat(self, **_kwargs):
            raise RuntimeError(f"provider failed for {image_url}")

    context = FakeContext(
        using_provider=SimpleNamespace(
            provider_config={"modalities": ["text", "tool_use"]}
        ),
        config={
            "provider_settings": {
                "default_image_caption_provider_id": "caption-provider",
            }
        },
        providers={"caption-provider": FailingCaptionProvider()},
    )
    event = FakeEvent()
    tool = ToolRuntime(
        store,
        QQActionGateway(),
        context=context,
    ).build_tool_set(event).get_tool("get_image_captions")

    result_text = await tool.handler(event, message_id="m1")

    assert json.loads(result_text) == {
        "error": "image_caption_failed",
        "message_id": "m1",
    }
    assert image_url not in result_text
    assert "provider failed" not in result_text


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

    assert reacted == '{"success":true,"action":"react_message"}'
    assert replied == '{"success":true,"action":"reply_message"}'
    assert event.bot.actions[0][0] == "set_msg_emoji_like"
    assert len(event.sent) == 1
    assert [
        action["action_name"]
        for action in event.get_extra("_group_agent_external_actions")
    ] == ["react_message", "reply_message"]
    action_records = [
        record
        for record in store.read_records("napcat:group:1")
        if record.get("record_kind") == "agent_action"
    ]
    assert [record["action_name"] for record in action_records] == [
        "react_message",
        "reply_message",
    ]
    assert [record["action_index"] for record in action_records] == [1, 2]


@pytest.mark.asyncio
async def test_external_message_tool_returns_structured_result_without_finalizing(tmp_path):
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

    assert len(results) == 1
    assert results[0].content[0].text == '{"success":true,"action":"send_message"}'
    assert len(event.sent) == 1
    assert finalized == []
    assert event.get_extra("_group_agent_external_actions") == [
        {"action_name": "send_message", "status": "succeeded", "detail": ""}
    ]
    records = runtime.store.read_records("napcat:group:1")
    assert len(records) == 1
    assert records[0]["record_kind"] == "agent_action"
    assert records[0]["action_name"] == "send_message"
    assert records[0]["text"] == "B"


@pytest.mark.asyncio
async def test_external_action_allows_follow_up_provider_call_until_stay_silent(tmp_path):
    store = GroupFlowStore(tmp_path)
    runtime = ToolRuntime(store, QQActionGateway())
    event = FakeEvent()
    provider = ConsecutiveActionProvider()
    request = ProviderRequest(
        prompt="respond",
        func_tool=runtime.build_tool_set(),
        contexts=[],
    )
    runner = ToolLoopAgentRunner()
    await runner.reset(
        provider=provider,
        request=request,
        run_context=ContextWrapper(
            context=SimpleNamespace(event=event, context=SimpleNamespace())
        ),
        tool_executor=FunctionToolExecutor(),
        agent_hooks=BaseAgentRunHooks(),
        streaming=False,
    )

    async for _ in runner.step_until_done(3):
        pass

    assert runner.done() is True
    assert provider.call_count == 3
    assert len(event.sent) == 2
    assert [record["action_name"] for record in store.read_records("napcat:group:1")] == [
        "send_message",
        "send_message",
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
async def test_failed_external_action_returns_structured_error_and_records_failure(tmp_path):
    store = GroupFlowStore(tmp_path)
    runtime = ToolRuntime(store, QQActionGateway())
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

    assert result == (
        '{"success":false,"action":"send_message","error":"RuntimeError",'
        '"detail":"qq unavailable"}'
    )
    assert event.get_extra("_group_agent_external_actions") == [
        {
            "action_name": "send_message",
            "status": "failed",
            "detail": "qq unavailable",
        }
    ]
    assert store.read_records("napcat:group:1") == []


@pytest.mark.asyncio
async def test_action_failed_result_returns_verified_bot_mute_to_model(tmp_path):
    runtime = ToolRuntime(GroupFlowStore(tmp_path), QQActionGateway())
    event = FakeEvent()
    mute_until = 4_000_000_000
    platform_result = {
        "status": "failed",
        "retcode": 1200,
        "data": None,
        "message": (
            "EventChecker Failed: NodeIKernelMsgService/sendMsg "
            'EventRet: {"result": 120, "errMsg": ""}'
        ),
        "wording": (
            "EventChecker Failed: NodeIKernelMsgService/sendMsg "
            'EventRet: {"result": 120, "errMsg": ""}'
        ),
        "echo": {"seq": 129},
        "stream": "normal-action",
    }
    failure = ActionFailed(platform_result)

    async def fail_send(_chain):
        raise failure

    async def get_member(action, **kwargs):
        event.bot.actions.append((action, kwargs))
        return {"shut_up_timestamp": mute_until}

    event.send = fail_send
    event.bot.call_action = get_member

    result = json.loads(
        await runtime.build_tool_set()
        .get_tool("send_message")
        .handler(event, content="A")
    )

    assert result == {
        "success": False,
        "action": "send_message",
        "error": "ActionFailed",
        "detail": "你已被禁言，无法发送消息；解禁时间：2096-10-02T15:06:40+08:00",
    }
    assert event.bot.actions == [
        (
            "get_group_member_info",
            {
                "group_id": 1,
                "user_id": 7,
                "no_cache": True,
                "self_id": 7,
            },
        )
    ]
    assert event.get_extra("_group_agent_external_actions") == [
        {
            "action_name": "send_message",
            "status": "failed",
            "detail": (
                "你已被禁言，无法发送消息；"
                "解禁时间：2096-10-02T15:06:40+08:00"
            ),
        }
    ]


@pytest.mark.asyncio
async def test_mute_status_query_failure_preserves_platform_diagnostic(tmp_path):
    runtime = ToolRuntime(GroupFlowStore(tmp_path), QQActionGateway())
    event = FakeEvent()
    failure = ActionFailed(
        {
            "status": "failed",
            "retcode": 1200,
            "data": None,
            "message": "NodeIKernelMsgService/sendMsg result=120",
            "wording": "NodeIKernelMsgService/sendMsg result=120",
        }
    )

    async def fail_send(_chain):
        raise failure

    async def fail_status_query(action, **kwargs):
        event.bot.actions.append((action, kwargs))
        raise RuntimeError("status unavailable")

    event.send = fail_send
    event.bot.call_action = fail_status_query

    result = json.loads(
        await runtime.build_tool_set()
        .get_tool("send_message")
        .handler(event, content="A")
    )

    assert result["error"] == "ActionFailed"
    assert result["detail"] == str(failure)
    assert event.get_extra("_group_agent_external_actions")[0]["detail"] == str(
        failure
    )


@pytest.mark.asyncio
async def test_invalid_mute_timestamp_preserves_platform_diagnostic(tmp_path):
    runtime = ToolRuntime(GroupFlowStore(tmp_path), QQActionGateway())
    event = FakeEvent()
    failure = ActionFailed(
        {
            "status": "failed",
            "retcode": 1200,
            "data": None,
            "message": "NodeIKernelMsgService/sendMsg result=120",
            "wording": "NodeIKernelMsgService/sendMsg result=120",
        }
    )

    async def fail_send(_chain):
        raise failure

    async def get_invalid_member(_action, **_kwargs):
        return {"shut_up_timestamp": 10**100}

    event.send = fail_send
    event.bot.call_action = get_invalid_member

    result = json.loads(
        await runtime.build_tool_set()
        .get_tool("send_message")
        .handler(event, content="A")
    )

    assert result["detail"] == str(failure)
    assert event.get_extra("_group_agent_external_actions")[0]["detail"] == str(
        failure
    )


@pytest.mark.asyncio
async def test_external_action_failure_omits_empty_detail(tmp_path):
    runtime = ToolRuntime(GroupFlowStore(tmp_path), QQActionGateway())
    event = FakeEvent()

    async def fail_send(_chain):
        raise RuntimeError

    event.send = fail_send

    result = (
        await runtime.build_tool_set()
        .get_tool("send_message")
        .handler(event, content="A")
    )

    assert result == '{"success":false,"action":"send_message","error":"RuntimeError"}'
    assert event.get_extra("_group_agent_external_actions") == [
        {
            "action_name": "send_message",
            "status": "failed",
            "detail": "",
        }
    ]


@pytest.mark.asyncio
async def test_action_encoding_failure_returns_successful_result_after_send(
    tmp_path,
    monkeypatch,
):
    finalized = []

    async def finalize(event):
        finalized.append(event)

    def fail_encode(*_args, **_kwargs):
        raise ValueError("invalid receipt")

    monkeypatch.setattr(
        agent_tools_module,
        "build_agent_action_record",
        fail_encode,
    )
    store = GroupFlowStore(tmp_path)
    runtime = ToolRuntime(
        store,
        QQActionGateway(),
        terminal_callback=finalize,
    )
    event = FakeEvent()

    result = await runtime.build_tool_set().get_tool("send_message").handler(
        event,
        content="A",
    )

    assert result == ('{"success":true,"action":"send_message",'
                      '"fact_error":"fact_encode_failed:ValueError"}')
    assert len(event.sent) == 1
    assert finalized == []
    assert store.read_records("napcat:group:1") == []
    assert event.get_extra("_group_agent_external_actions") == [
        {
            "action_name": "send_message",
            "status": "succeeded",
            "detail": "fact_encode_failed:ValueError",
        }
    ]


@pytest.mark.asyncio
async def test_action_persistence_failure_returns_successful_result_after_send(tmp_path):
    finalized = []

    async def fail_persist(
        _flow_id,
        _record,
        *,
        run_id,
        generation,
    ):
        assert run_id == "run-1"
        assert generation == 0
        raise OSError("disk full")

    async def finalize(event):
        finalized.append(event)

    runtime = ToolRuntime(
        GroupFlowStore(tmp_path),
        QQActionGateway(),
        terminal_callback=finalize,
        action_persist_callback=fail_persist,
    )
    event = FakeEvent()

    result = await runtime.build_tool_set().get_tool("send_message").handler(
        event,
        content="A",
    )

    assert result == ('{"success":true,"action":"send_message",'
                      '"fact_error":"fact_persist_failed:OSError"}')
    assert len(event.sent) == 1
    assert finalized == []
    assert event.get_extra("_group_agent_external_actions") == [
        {
            "action_name": "send_message",
            "status": "succeeded",
            "detail": "fact_persist_failed:OSError",
        }
    ]


@pytest.mark.asyncio
async def test_stale_run_is_rejected_before_external_gateway_call(tmp_path):
    admissions = []
    persisted = []

    async def reject(flow_id, run_id, generation):
        admissions.append((flow_id, run_id, generation))
        return False

    async def persist(flow_id, record, *, run_id, generation):
        persisted.append((flow_id, record, run_id, generation))
        return 1

    runtime = ToolRuntime(
        GroupFlowStore(tmp_path),
        QQActionGateway(),
        action_admission_callback=reject,
        action_persist_callback=persist,
    )
    event = FakeEvent()

    result = await runtime.build_tool_set().get_tool("send_message").handler(
        event,
        content="stale",
    )

    assert result == '{"success":false,"action":"send_message","error":"stale_run"}'
    assert admissions == [("napcat:group:1", "run-1", 0)]
    assert event.sent == []
    assert persisted == []
    assert event.get_extra("_group_agent_external_actions") == [
        {
            "action_name": "send_message",
            "status": "failed",
            "detail": "stale_run",
        }
    ]


@pytest.mark.asyncio
async def test_admitted_gateway_action_can_finish_while_stale_fact_is_rejected(
    tmp_path,
):
    admitted = []
    persisted = []

    async def admit(flow_id, run_id, generation):
        admitted.append((flow_id, run_id, generation))
        return True

    async def reject_stale_fact(flow_id, record, *, run_id, generation):
        persisted.append((flow_id, record, run_id, generation))
        raise RuntimeError("stale autonomous group run")

    runtime = ToolRuntime(
        GroupFlowStore(tmp_path),
        QQActionGateway(),
        action_admission_callback=admit,
        action_persist_callback=reject_stale_fact,
    )
    event = FakeEvent()

    result = await runtime.build_tool_set().get_tool("send_message").handler(
        event,
        content="already admitted",
    )

    assert result == (
        '{"success":true,"action":"send_message",'
        '"fact_error":"fact_persist_failed:RuntimeError"}'
    )
    assert admitted == [("napcat:group:1", "run-1", 0)]
    assert len(event.sent) == 1
    assert len(persisted) == 1
    assert event.get_extra("_group_agent_external_actions") == [
        {
            "action_name": "send_message",
            "status": "succeeded",
            "detail": "fact_persist_failed:RuntimeError",
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
async def test_action_facts_are_readable_but_cannot_be_qq_targets(tmp_path):
    store = GroupFlowStore(tmp_path)
    action_id = "agent-action:run-1:1"
    store.append_record(
        "napcat:group:1",
        {
            "record_kind": "agent_action",
            "message_id": action_id,
            "targetable": False,
            "sender_id": "7",
            "text": "已经回复",
            "components": [{"type": "text", "text": "已经回复"}],
        },
    )
    event = FakeEvent()
    event.extras["_group_agent_snapshot_seq"] = 1
    tools = ToolRuntime(store, QQActionGateway()).build_tool_set()

    observed = json.loads(
        await tools.get_tool("get_message").handler(event, message_id=action_id)
    )
    search = json.loads(
        await tools.get_tool("search_chat_history").handler(event, query="已经回复")
    )
    reply = json.loads(
        await tools.get_tool("reply_message").handler(
            event,
            message_id=action_id,
            content="again",
        )
    )
    reaction = json.loads(
        await tools.get_tool("react_message").handler(
            event,
            message_id=action_id,
            reaction="赞",
        )
    )
    poke = json.loads(
        await tools.get_tool("poke_user").handler(event, user_id="7")
    )

    assert observed["message_id"] == action_id
    assert [item["message_id"] for item in search["messages"]] == [action_id]
    assert reply["error"] == "message_not_found_in_snapshot"
    assert reaction["error"] == "message_not_found_in_snapshot"
    assert poke["error"] == "user_not_found_in_snapshot"
    assert event.sent == []
    assert event.bot.actions == []


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
        assert await tools.get_tool("poke_user").handler(
            event, user_id=user_id
        ) == '{"success":true,"action":"poke_user"}'
    rejected = json.loads(
        await tools.get_tool("poke_user").handler(event, user_id="future")
    )

    assert event.sent == []
    assert event.bot.actions == [
        (
            "group_poke",
            {
                "group_id": "1",
                "user_id": user_id,
                "self_id": 7,
            },
        )
        for user_id in ("alice", "bob", "carol")
    ]
    assert rejected == {
        "success": False,
        "error": "user_not_found_in_snapshot",
        "user_id": "future",
    }
