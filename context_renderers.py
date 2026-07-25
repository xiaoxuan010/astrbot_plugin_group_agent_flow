"""将持久化群事件投影为 LLM 消息的可插拔 renderer。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from xml.sax.saxutils import escape, quoteattr
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


def _is_agent_action(event: dict[str, Any]) -> bool:
    """识别插件生成的机器人动作事实。"""
    return _one_line(event.get("record_kind")) == "agent_action"


def _agent_action_line(event: dict[str, Any]) -> str:
    """渲染不可作为 QQ 消息目标的机器人动作事实。"""
    fields = [
        "QQ",
        f"group={_one_line(event.get('group_id'))}",
        "actor=bot",
        f"action={_one_line(event.get('action_name'))}",
        f"status={_one_line(event.get('action_status'))}",
        f"action_id={_one_line(event.get('message_id'))}",
        f"time={_datetime(event.get('timestamp')).isoformat()}",
    ]
    target_message_id = _one_line(
        event.get("target_message_id") or event.get("reply_to")
    )
    if target_message_id:
        fields.append(f"target_msg={target_message_id}")
    target_user_id = _one_line(event.get("target_user_id"))
    if target_user_id:
        fields.append(f"target_user={target_user_id}")
    text = _one_line(event.get("text")) or "[动作]"
    return f"[{' '.join(fields)}] {text}"


def _structured_line(event: dict[str, Any]) -> str:
    """将一条事件渲染为带稳定路由和归属信息的文本行。"""
    if _is_agent_action(event):
        return _agent_action_line(event)
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


def _xml_attrs(**values: Any) -> str:
    """构造省略空值且经过 XML 转义的属性串。"""
    return "".join(
        f" {name}={quoteattr(_one_line(value))}"
        for name, value in values.items()
        if _one_line(value)
    )


def _xml_text(value: Any) -> str:
    """规范化并转义组件的文本内容。"""
    return escape(_one_line(value))


def _xml_timestamp(value: Any) -> str:
    """将存在的组件时间统一投影为带时区的 ISO 时间。"""
    return _datetime(value).isoformat() if _one_line(value) else ""


def _xml_component(component: dict[str, Any]) -> str:
    """将一条持久化消息组件投影为 XML 元素。"""
    kind = _one_line(component.get("type"))
    if kind == "text":
        return f"<text>{_xml_text(component.get('text'))}</text>"
    if kind == "reply":
        attrs = _xml_attrs(
            message_id=component.get("message_id"),
            sender_id=component.get("sender_id"),
            sender_name=component.get("sender_name"),
            timestamp=_xml_timestamp(component.get("timestamp")),
        )
        text = _one_line(component.get("text"))
        if not text:
            return f"<reply{attrs}/>"
        return f"<reply{attrs}><text>{_xml_text(text)}</text></reply>"
    if kind == "at":
        if _one_line(component.get("user_id")) == "all":
            return '<mention all="true"/>'
        return f"<mention{_xml_attrs(user_id=component.get('user_id'), name=component.get('name'))}/>"
    if kind == "image":
        return f"<image{_xml_attrs(url=component.get('url'))}/>"
    if kind == "face":
        return f"<face{_xml_attrs(id=component.get('id'))}/>"
    if kind == "poke":
        return f"<poke{_xml_attrs(target_id=component.get('target_id'))}/>"
    if kind in {"voice", "video"}:
        return f"<{kind}{_xml_attrs(url=component.get('url'))}/>"
    if kind == "file":
        return f"<file{_xml_attrs(name=component.get('name'), url=component.get('url'))}/>"
    return f"<component{_xml_attrs(type=kind or 'unknown')}/>"


def _xml_record(event: dict[str, Any]) -> str:
    """将一条群事件或机器人动作投影为 XML 记录。"""
    timestamp = _xml_timestamp(event.get("timestamp"))
    if _is_agent_action(event):
        attrs = _xml_attrs(
            action_name=event.get("action_name"),
            action_status=event.get("action_status"),
            action_id=event.get("message_id"),
            timestamp=timestamp,
            target_message_id=event.get("target_message_id") or event.get("reply_to"),
            target_user_id=event.get("target_user_id"),
        )
        tag = "action"
    else:
        attrs = _xml_attrs(
            message_id=event.get("message_id"),
            sender_id=event.get("sender_id"),
            sender_name=event.get("sender_name"),
            timestamp=timestamp,
        )
        tag = "message"
    components = event.get("components")
    if isinstance(components, list) and components:
        body = "".join(
            _xml_component(component)
            if isinstance(component, dict)
            else _xml_component({"type": type(component).__name__.lower()})
            for component in components
        )
    else:
        body = f"<text>{_xml_text(event.get('text'))}</text>"
    return f"<{tag}{attrs}>{body}</{tag}>"


def _first_nonempty(events: list[dict[str, Any]], field: str) -> Any:
    """读取 batch 中首个可用的共享元数据字段。"""
    return next(
        (event.get(field) for event in events if _one_line(event.get(field))),
        "",
    )


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
            if _is_agent_action(event):
                lines.append(_agent_action_line(event))
                continue
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


class XmlDeltaRenderer:
    """将事件组件投影为单个带群元数据的 XML 增量块。"""

    name = "xml_delta"

    def render(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not events:
            return []
        attrs = _xml_attrs(
            group_id=_first_nonempty(events, "group_id"),
            group_name=_first_nonempty(events, "group_name"),
        )
        body = "\n".join(_xml_record(event) for event in events)
        return [
            {
                "role": "user",
                "content": f"<group_messages_delta{attrs}>\n{body}\n</group_messages_delta>",
            }
        ]


class NativeMessagesRenderer:
    """将每条群事件映射为逐消息独占的 User 块。"""

    name = "native_messages"

    def render(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """通过独立请求消息保留原始事件边界。"""
        return [{"role": "user", "content": _structured_line(event)} for event in events]


_RENDERERS: dict[str, type[ContextRenderer]] = {
    LegacyDeltaRenderer.name: LegacyDeltaRenderer,
    PlainLinesRenderer.name: PlainLinesRenderer,
    XmlDeltaRenderer.name: XmlDeltaRenderer,
    NativeMessagesRenderer.name: NativeMessagesRenderer,
}


def build_renderer(name: str) -> ContextRenderer:
    """创建指定 renderer，并拒绝未知的持久化名称。"""
    renderer_type = _RENDERERS.get(name)
    if renderer_type is None:
        raise ValueError(f"unknown context renderer: {name}")
    return renderer_type()
