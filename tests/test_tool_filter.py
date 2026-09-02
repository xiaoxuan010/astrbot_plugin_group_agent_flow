from astrbot.core.agent.tool import FunctionTool, ToolSet

from tool_filter import (
    DEFAULT_BLOCKLIST,
    filter_astrbot_tools,
    merge_and_filter,
)


async def _noop(*_args, **_kwargs) -> None:
    """占位 handler：用于在测试中构造工具对象。"""
    return None


def _tool(name: str) -> FunctionTool:
    """构造一个最小可用的工具对象。"""
    return FunctionTool(
        name=name,
        description=f"Tool {name}",
        parameters={"type": "object", "properties": {}},
        handler=_noop,
    )


def test_default_blocklist_is_minimal_and_conflict_only():
    """默认黑名单只含破坏快照/发送边界的两个工具。"""
    assert DEFAULT_BLOCKLIST == frozenset(
        {"send_message_to_user", "get_group_message_history"}
    )


def test_filter_removes_blocked_tools_and_keeps_others():
    tool_set = ToolSet(
        [
            _tool("send_message_to_user"),
            _tool("get_group_message_history"),
            _tool("web_search_tavily"),
            _tool("astr_kb_search"),
        ]
    )

    filter_astrbot_tools(tool_set)

    assert "send_message_to_user" not in tool_set.names()
    assert "get_group_message_history" not in tool_set.names()
    assert "web_search_tavily" in tool_set.names()
    assert "astr_kb_search" in tool_set.names()


def test_filter_respects_custom_blocklist():
    tool_set = ToolSet(
        [_tool("web_search_tavily"), _tool("astr_kb_search"), _tool("future_task")]
    )

    filter_astrbot_tools(tool_set, {"future_task"})

    assert "future_task" not in tool_set.names()
    assert "web_search_tavily" in tool_set.names()
    assert "astr_kb_search" in tool_set.names()


def test_filter_with_empty_blocklist_keeps_all():
    tool_set = ToolSet([_tool("web_search_tavily"), _tool("astr_kb_search")])

    filter_astrbot_tools(tool_set, [])

    assert tool_set.names() == ["web_search_tavily", "astr_kb_search"]


def test_merge_and_filter_combines_astrbot_and_gaf_tools():
    """合并 AstrBot 工具与 GAF 工具，并剔除黑名单命中项。"""
    existing = ToolSet(
        [
            _tool("web_search_tavily"),
            _tool("get_group_message_history"),
        ]
    )
    gaf = ToolSet([_tool("send_message"), _tool("stay_silent")])

    merged = merge_and_filter(existing, gaf)

    assert "web_search_tavily" in merged.names()
    assert "send_message" in merged.names()
    assert "stay_silent" in merged.names()
    assert "get_group_message_history" not in merged.names()


def test_merge_and_filter_handles_none_existing():
    gaf = ToolSet([_tool("send_message")])

    merged = merge_and_filter(None, gaf)

    assert "send_message" in merged.names()


def test_gaf_own_tools_are_never_removed_even_if_in_blocklist():
    """GAF 自身工具不会被黑名单移除；add_tool 同名覆盖保护其保底。"""
    # 即便 AstrBot 侧存在同名工具，GAF 工具也通过 add_tool 覆盖，保持存在。
    existing = ToolSet([_tool("send_message")])
    gaf = ToolSet([_tool("send_message"), _tool("reply_message")])

    merged = merge_and_filter(existing, gaf, {"send_message"})

    assert "send_message" in merged.names()
    assert "reply_message" in merged.names()
