"""将持久化群事件投影为固定的 XML 增量块。"""

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


def format_group_timestamp(timestamp: Any, *, strict: bool = False) -> str:
    """按群聊上下文约定输出带上海时区的 ISO 时间。"""
    if strict:
        return datetime.fromtimestamp(int(timestamp), tz=SHANGHAI_TZ).isoformat()
    return _datetime(timestamp).isoformat()


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
    return format_group_timestamp(value) if _one_line(value) else ""


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
    """将一条群消息投影为 XML 记录。"""
    timestamp = _xml_timestamp(event.get("timestamp"))
    attrs = _xml_attrs(
        message_id=event.get("message_id"),
        sender_id=event.get("sender_id"),
        sender_name=event.get("sender_name"),
        timestamp=timestamp,
    )
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
    return f"<message{attrs}>{body}</message>"


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


def build_renderer() -> ContextRenderer:
    """创建唯一支持的 XML 增量块 renderer。"""
    return XmlDeltaRenderer()
