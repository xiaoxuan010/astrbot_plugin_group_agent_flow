"""将 AstrBot 群事件规范化为稳定的持久化记录。"""

from __future__ import annotations

import time
from typing import Any

from astrbot.api import message_components as Comp


def _value(value: Any) -> str:
    """规范化可选的 AstrBot 组件字段，便于写入 JSON。"""
    return "" if value is None else str(value)


def _component_record(component: Any) -> dict[str, Any]:
    """将支持的 AstrBot 组件转换为稳定的纯数据记录。"""
    if isinstance(component, Comp.Plain):
        return {"type": "text", "text": _value(component.text)}
    if isinstance(component, Comp.Reply):
        return {
            "type": "reply",
            "message_id": _value(component.id),
            "sender_id": _value(component.sender_id),
        }
    if isinstance(component, Comp.At):
        return {
            "type": "at",
            "user_id": _value(component.qq),
            "name": _value(component.name),
        }
    if isinstance(component, Comp.Poke):
        return {
            "type": "poke",
            "target_id": _value(component.target_id()),
        }
    if isinstance(component, Comp.Image):
        return {"type": "image", "url": _value(component.url or component.file)}
    if isinstance(component, Comp.Face):
        return {"type": "face", "id": _value(component.id)}
    if isinstance(component, Comp.Record):
        return {"type": "voice", "url": _value(component.url or component.file)}
    if isinstance(component, Comp.Video):
        return {"type": "video", "url": _value(component.url or component.file)}
    if isinstance(component, Comp.File):
        return {
            "type": "file",
            "name": _value(component.name),
            "url": _value(component.url),
        }
    component_type = getattr(getattr(component, "type", None), "value", None)
    return {"type": _value(component_type or component.__class__.__name__).lower()}


def extract_group_event(event: Any, *, max_text_chars: int = 4000) -> dict[str, Any]:
    """从群事件提取路由、发送者、文本和消息组件。"""
    components = list(event.get_messages())
    self_id = _value(event.get_self_id())
    reply_to = next(
        (_value(component.id) for component in components if isinstance(component, Comp.Reply)),
        "",
    )
    directed = any(
        isinstance(component, Comp.At)
        and _value(component.qq) in {self_id, "all"}
        for component in components
    )
    poke_targets = [
        _value(component.target_id())
        for component in components
        if isinstance(component, Comp.Poke)
    ]
    directed = directed or self_id in poke_targets
    outline = _value(event.get_message_outline()).strip()
    if poke_targets and outline in {"", "[Poke]", "[ComponentType.Poke]"}:
        outline = " ".join(f"[戳一戳 target={target_id}]" for target_id in poke_targets)
    text = outline or _value(event.get_message_str()).strip()
    max_chars = max(0, int(max_text_chars))
    if max_chars:
        text = text[:max_chars]

    message_obj = event.message_obj
    timestamp = int(getattr(message_obj, "timestamp", 0) or time.time())
    message_id = _value(getattr(message_obj, "message_id", ""))
    if not message_id:
        # 回退 ID 在同一适配器事件内保持稳定，可用于消息去重。
        message_id = f"{event.get_sender_id()}:{timestamp}:{outline}"
    group = getattr(message_obj, "group", None)
    group_name = _value(getattr(group, "group_name", "")) if group else ""
    platform_id = _value(event.get_platform_id())
    group_id = _value(event.get_group_id())

    return {
        "schema_version": 2,
        "seq": 0,
        "message_id": message_id,
        "platform_id": platform_id,
        "platform_name": _value(event.get_platform_name()),
        "flow_id": f"{platform_id}:group:{group_id}",
        "group_id": group_id,
        "group_name": group_name,
        "sender_id": _value(event.get_sender_id()),
        "sender_name": _value(event.get_sender_name()),
        "self_id": self_id,
        "timestamp": timestamp,
        "text": text,
        "components": [_component_record(component) for component in components],
        "reply_to": reply_to or None,
        "is_directed_at_bot": directed,
    }
