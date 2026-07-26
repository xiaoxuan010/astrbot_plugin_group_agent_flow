"""在平台发送边界和会话历史中落实仅工具动作策略。"""

from __future__ import annotations

import copy
from typing import Any

ORIGINAL_SEND_EXTRA = "_group_agent_original_send"
EXTERNAL_ACTIONS_EXTRA = "_group_agent_external_actions"


def record_external_action(
    event: Any,
    *,
    action_name: str,
    success: bool,
    detail: str = "",
) -> None:
    """记录本轮可见动作结果，供观察结束时分类。"""
    actions = event.get_extra(EXTERNAL_ACTIONS_EXTRA, [])
    if not isinstance(actions, list):
        actions = []
    event.set_extra(
        EXTERNAL_ACTIONS_EXTRA,
        [
            *actions,
            {
                "action_name": action_name,
                "status": "succeeded" if success else "failed",
                "detail": detail,
            },
        ],
    )


def classify_run_outcome(
    external_actions: list[dict[str, Any]],
    *,
    silence_selected: bool,
    had_direct_output: bool,
) -> str:
    """Classify how an autonomous observation cycle terminated."""
    if external_actions:
        statuses = {str(action.get("status") or "") for action in external_actions}
        if statuses == {"succeeded"}:
            return "action_succeeded"
        if statuses == {"failed"}:
            return "action_failed"
        if "succeeded" in statuses and "failed" in statuses:
            return "action_partial"
        return "action_incomplete"
    if silence_selected:
        return "silence_selected"
    return "direct_output_suppressed" if had_direct_output else "no_action"


def install_send_guard(event: Any) -> None:
    """替换事件的直接发送入口，同时保留仅供工具使用的原始入口。"""
    if event.get_extra(ORIGINAL_SEND_EXTRA) is not None:
        return
    event.set_extra(ORIGINAL_SEND_EXTRA, event.send)

    async def guarded_send(message: Any) -> None:
        """丢弃普通 content、模型错误和内部状态的核心发送。"""
        return None

    event.send = guarded_send


async def tool_send(event: Any, message: Any) -> None:
    """通过安装拦截器前保存的原始事件方法发送消息。"""
    get_extra = getattr(event, "get_extra", None)
    sender = get_extra(ORIGINAL_SEND_EXTRA) if get_extra else None
    if sender is None:
        sender = event.send
    await sender(message)


def suppress_builtin_active_reply(event: Any) -> None:
    """让 AstrBot 的概率回复链路将该事件视为已经处理。"""
    event.is_at_or_wake_command = True


def enforce_tool_set(request: Any, tool_set: Any) -> None:
    """替换 AstrBot 在请求装饰阶段可能追加的全局工具。"""
    request.func_tool = tool_set


def isolate_platform_metadata(event: Any) -> None:
    """在事件私有的元数据副本上禁用 AstrBot 额外主动发送工具。"""
    metadata = copy.copy(event.platform_meta)
    metadata.support_proactive_message = False
    event.platform_meta = metadata
    event.platform = metadata


def suppress_direct_output(response: Any, *, run_context: Any = None) -> bool:
    """从发送结果与会话历史中移除本轮普通 assistant 输出。"""
    if response is None:
        return False
    had_direct_output = bool(getattr(response, "completion_text", ""))
    response.completion_text = ""
    response.result_chain = None
    messages = getattr(run_context, "messages", None)
    if (
        had_direct_output
        and messages
        and getattr(response, "role", "assistant") == "assistant"
    ):
        last_message = messages[-1]
        if getattr(last_message, "role", None) == "assistant":
            messages.pop()
    return had_direct_output
