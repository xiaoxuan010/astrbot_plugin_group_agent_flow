"""按黑名单过滤 AstrBot 注入的工具，保留 GAF 自身工具集独立于过滤。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# 默认黑名单：只包含会破坏 GAF 边界模型的工具。
#   - send_message_to_user：绕过快照边界主动发消息/私聊，不受「仅 GAF 工具发送 + 快照内目标」约束。
#   - get_group_message_history：直接读取 AstrBot 持久化全局历史，绕过「search_chat_history 只查冻结 Buffer」模型。
DEFAULT_BLOCKLIST: frozenset[str] = frozenset(
    {
        "send_message_to_user",
        "get_group_message_history",
    }
)


def filter_astrbot_tools(
    tool_set: Any,
    blocklist: Iterable[str] | None = None,
) -> None:
    """就地移除 tool_set 中命中黑名单的工具。

    该函数只作用于 AstrBot 注入的工具；GAF 自身工具集在合并时由 ``ToolSet.add_tool``
    同名覆盖保护，不经过本过滤。

    Args:
        tool_set: AstrBot ``ToolSet`` 实例（具备 ``names()`` / ``remove_tool()``）。
        blocklist: 需要剔除的工具名集合；为 ``None`` 时使用 ``DEFAULT_BLOCKLIST``。
    """
    effective = set(blocklist) if blocklist else set(DEFAULT_BLOCKLIST)
    if not effective:
        return
    names = getattr(tool_set, "names", None)
    remove_tool = getattr(tool_set, "remove_tool", None)
    if names is None or remove_tool is None:
        return
    for name in names():
        if name in effective:
            remove_tool(name)


def merge_and_filter(
    existing_tool_set: Any,
    gaf_tool_set: Any,
    blocklist: Iterable[str] | None = None,
) -> Any:
    """把 GAF 工具集合并进既有 AstrBot 工具集，并按黑名单剔除 AstrBot 冲突工具。

    黑名单**只作用于 AstrBot 注入的部分**（``existing_tool_set``）；GAF 自身工具集
    （``gaf_tool_set``）不经过过滤，通过 ``ToolSet.add_tool`` 同名覆盖机制保底，
    绝不会被黑名单移除。

    Args:
        existing_tool_set: AstrBot 已注入的工具集（可能为 ``None``）。
        gaf_tool_set: GAF 自身工具集。
        blocklist: 需要剔除的 AstrBot 工具名集合；为 ``None`` 时使用
            ``DEFAULT_BLOCKLIST``。

    Returns:
        合并并过滤后的 ``ToolSet``。
    """
    if existing_tool_set is None:
        merged = gaf_tool_set
        return merged
    filter_astrbot_tools(existing_tool_set, blocklist)
    for tool in gaf_tool_set.tools:
        existing_tool_set.add_tool(tool)
    return existing_tool_set
