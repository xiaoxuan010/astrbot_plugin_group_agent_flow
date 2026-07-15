"""将持久化群事件投影为 LLM 消息的可插拔 renderer。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _one_line(value: Any) -> str:
    """规范化用户字段，避免单条事件破坏记录边界。"""
    text = "" if value is None else str(value)
    return " ".join(text.replace("\r", " ").replace("\n", " ").split())


def _datetime(timestamp: Any) -> datetime:
    """将持久化 Unix 时间戳转换为群聊使用的时区。"""
    try:
        return datetime.fromtimestamp(int(timestamp or 0), tz=SHANGHAI_TZ)
    except (OSError, OverflowError, TypeError, ValueError):
        return datetime.fromtimestamp(0, tz=SHANGHAI_TZ)


def _structured_line(event: dict[str, Any]) -> str:
    """将一条事件渲染为带稳定路由和归属信息的文本行。"""
    fields = [
        "QQ",
        f"group={_one_line(event.get('group_id'))}",
        f"msg={_one_line(event.get('message_id'))}",
        f"sender={_one_line(event.get('sender_id'))}",
        f"name={_one_line(event.get('sender_name'))}",
        f"time={_datetime(event.get('timestamp')).isoformat()}",
    ]
    reply_to = _one_line(event.get("reply_to"))
    if reply_to:
        fields.append(f"reply_to={reply_to}")
    text = _one_line(event.get("text")) or "[消息]"
    return f"[{' '.join(fields)}] {text}"


class ContextRenderer(Protocol):
    """将持久化事件投影为模型消息的统一接口。"""

    name: str

    def render(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """按事件顺序返回 OpenAI 风格的消息列表。"""
        ...


class LegacyDeltaRenderer:
    """使用单个聚合增量块和 `---` 消息分隔符。"""

    name = "legacy_delta"

    def render(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """将完整增量合并为一条兼容旧格式的 user 消息。"""
        if not events:
            return []
        lines = []
        for event in events:
            sender = _one_line(event.get("sender_name")) or _one_line(
                event.get("sender_id")
            )
            timestamp = _datetime(event.get("timestamp")).strftime("%H:%M:%S")
            message_id = _one_line(event.get("message_id"))
            text = _one_line(event.get("text")) or "[消息]"
            lines.append(f"[{sender}/{timestamp} msg={message_id}]: {text}")
        body = "\n---\n".join(lines)
        return [
            {
                "role": "user",
                "content": f"<group_messages_delta>\n{body}\n</group_messages_delta>",
            }
        ]


class PlainLinesRenderer:
    """将结构化事件行合并为一个换行分隔的聚合增量块。"""

    name = "plain_lines"

    def render(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """使用换行分隔，不添加标签包装。"""
        if not events:
            return []
        return [{"role": "user", "content": "\n".join(map(_structured_line, events))}]


class NativeMessagesRenderer:
    """将每条群事件映射为逐消息独占的 User 块。"""

    name = "native_messages"

    def render(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """通过独立请求消息保留原始事件边界。"""
        return [{"role": "user", "content": _structured_line(event)} for event in events]


_RENDERERS: dict[str, type[ContextRenderer]] = {
    LegacyDeltaRenderer.name: LegacyDeltaRenderer,
    PlainLinesRenderer.name: PlainLinesRenderer,
    NativeMessagesRenderer.name: NativeMessagesRenderer,
}


def build_renderer(name: str) -> ContextRenderer:
    """创建指定 renderer，并拒绝未知的持久化名称。"""
    renderer_type = _RENDERERS.get(name)
    if renderer_type is None:
        raise ValueError(f"unknown context renderer: {name}")
    return renderer_type()
