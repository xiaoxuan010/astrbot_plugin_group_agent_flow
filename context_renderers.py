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
    if kind == "voice":
        return "<voice/>"
    if kind == "video":
        return f"<{kind}{_xml_attrs(url=component.get('url'))}/>"
    if kind == "file":
        return (
            f"<file{_xml_attrs(name=component.get('name'), url=component.get('url'))}/>"
        )
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


class _LineState:
    """行式布局在块内继承的状态：当前日期、分钟、上一条发送者。"""

    __slots__ = ("date", "minute", "sender_id")

    def __init__(self) -> None:
        self.date: str | None = None
        self.minute: str | None = None
        self.sender_id: str | None = None


def _line_time_prefix(dt: datetime, state: _LineState) -> str:
    """[YYYY/MM/DD HH:MM] / [HH:MM] / ''（同分钟省略）。"""
    date_part = dt.strftime("%Y/%m/%d")
    time_part = dt.strftime("%H:%M")
    if state.date != date_part:
        state.date, state.minute = date_part, time_part
        return f"[{date_part} {time_part}]\n"
    if state.minute != time_part:
        state.minute = time_part
        return f"[{time_part}]\n"
    return ""


def _line_quote_prefix(component: dict[str, Any]) -> str:
    """引用前缀：> 完整日期 #被引ID: (QQ号)昵称: 预览 /。"""
    qid = _one_line(component.get("message_id"))
    who = _one_line(component.get("sender_name")) or _one_line(
        component.get("sender_id")
    )
    uid = _one_line(component.get("sender_id"))
    text = _one_line(component.get("text"))
    preview = (text[:20] + "…") if len(text) > 20 else text
    ts = component.get("timestamp")
    stamp = ""
    if ts:
        stamp = f"{_datetime(ts).strftime('%Y/%m/%d %H:%M')} "
    idpart = f"#{qid}: " if qid else ""
    upart = f"({uid})" if uid else ""
    return f"> {stamp}{idpart}{upart}{who}: {preview} / "


def _line_render_components(components: list, reply_state: dict) -> str:
    """渲染行式正文组件。reply_state 收集 reply 信息供行前缀使用。"""
    parts = []
    for component in components:
        if not isinstance(component, dict):
            continue
        kind = _one_line(component.get("type"))
        if kind == "text":
            parts.append(_one_line(component.get("text")))
        elif kind == "reply":
            reply_state["prefix"] = _line_quote_prefix(component)
        elif kind == "at":
            name = _one_line(component.get("name"))
            uid = _one_line(component.get("user_id"))
            if uid == "all":
                parts.append("@全体成员")
            else:
                parts.append(f"@{name or uid}")
        elif kind == "image":
            url = _one_line(component.get("url") or "")
            tail = url.rsplit("/", 1)[-1][:12] if url else ""
            parts.append(f"[图片:{tail}]" if tail else "[图片]")
        elif kind == "face":
            parts.append(f"[表情:{_one_line(component.get('id'))}]")
        elif kind == "poke":
            parts.append("[戳一戳]")
        elif kind == "voice":
            parts.append("[语音]")
        elif kind == "video":
            parts.append("[视频]")
        elif kind == "file":
            parts.append(f"[文件:{_one_line(component.get('name'))}]")
        else:
            parts.append(f"[{_one_line(kind) or '未知'}]")
    return " ".join(p for p in parts if p)


class LineMessagesRenderer:
    """行式消息增量块：每条消息一行，紧凑省略式。

    格式规格（研究结论中最优变体）：
      [YYYY/MM/DD HH:MM] (QQ号)昵称: 正文  #消息ID
      - 同分钟省略时间段，跨天恢复完整日期
      - 同人连续发言省略「(QQ号)昵称: 」整段
      - 消息 ID 永不省略，作为 reply/react/get_message 的定位锚点
      - 引用前缀带完整日期 + #被引消息ID，供 get_message 按需取原文
    """

    name = "line_messages"

    def __init__(self) -> None:
        """构造行式渲染器。"""
        self._state = _LineState()

    def _render_record(self, event: dict[str, Any]) -> str:
        dt = _datetime(event.get("timestamp"))
        sender_id = _one_line(event.get("sender_id"))
        sender_name = _one_line(event.get("sender_name")) or sender_id
        message_id = _one_line(event.get("message_id"))

        time_prefix = _line_time_prefix(dt, self._state)

        # 同人连发：省略「(QQ号)昵称: 」整段
        same_as_prev = bool(sender_id) and sender_id == self._state.sender_id
        who = "" if same_as_prev else f"({sender_id}){sender_name}: "

        reply_state: dict = {}
        components = event.get("components")
        body = _line_render_components(
            components
            if isinstance(components, list) and components
            else [{"type": "text", "text": event.get("text")}],
            reply_state,
        )

        line = f"{reply_state.get('prefix', '')}{who}{body}"
        if message_id:
            line += f"  #{message_id}"
        self._state.sender_id = sender_id or self._state.sender_id
        return time_prefix + line

    def render(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """按事件顺序返回单个 user 消息块。"""
        if not events:
            return []
        group_name = _one_line(_first_nonempty(events, "group_name"))
        header = f"【{group_name}】新消息：" if group_name else "新消息："
        self._state = _LineState()
        lines = [self._render_record(e) for e in events]
        return [{"role": "user", "content": header + "\n" + "\n".join(lines)}]


_RENDERERS: dict[str, type[ContextRenderer]] = {
    XmlDeltaRenderer.name: XmlDeltaRenderer,
    LineMessagesRenderer.name: LineMessagesRenderer,
}


def build_renderer(name: str = "xml_delta") -> ContextRenderer:
    """按名称创建渲染器；未知名称回退到 XML 增量块。"""
    renderer_type = _RENDERERS.get(name)
    if renderer_type is None:
        return XmlDeltaRenderer()
    return renderer_type()
