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
            "sender_name": _value(component.sender_nickname),
            "timestamp": _value(component.time),
            "text": _value(component.message_str),
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


def _mention_records(mentions: Any) -> tuple[list[str], list[dict[str, Any]]]:
    """规范化动作回执中的提及列表。"""
    if not isinstance(mentions, list):
        mentions = []
    user_ids = [_value(user_id) for user_id in mentions if _value(user_id)]
    return user_ids, [
        {"type": "at", "user_id": user_id, "name": ""}
        for user_id in user_ids
    ]


def build_agent_action_record(
    event: Any,
    *,
    run_id: str,
    action_index: int,
    action_name: str,
    action_result: dict[str, Any],
) -> dict[str, Any]:
    """将成功 QQ 动作回执规范化为群聊事实记录。"""
    result_action = _value(action_result.get("action"))
    if action_result.get("success") is not True or result_action != action_name:
        raise ValueError(
            f"action result mismatch: expected {action_name}, got {result_action}"
        )

    reply_to: str | None = None
    target_message_id: str | None = None
    target_user_id: str | None = None
    components: list[dict[str, Any]]
    if action_name in {"send_message", "reply_message"}:
        content = _value(action_result.get("content"))
        user_ids, mention_components = _mention_records(
            action_result.get("mentions")
        )
        text_parts = [*(f"[At:{user_id}]" for user_id in user_ids), content]
        text = " ".join(part for part in text_parts if part)
        components = [*mention_components, {"type": "text", "text": content}]
        if action_name == "reply_message":
            reply_to = _value(action_result.get("message_id"))
            target_message_id = reply_to
            components.insert(
                0,
                {"type": "reply", "message_id": reply_to, "sender_id": ""},
            )
    elif action_name == "react_message":
        message_id = _value(action_result.get("message_id"))
        target_message_id = message_id
        reaction = _value(action_result.get("reaction"))
        text = f"[消息表情 target={message_id} reaction={reaction}]"
        components = [
            {
                "type": "reaction",
                "message_id": message_id,
                "reaction": reaction,
            }
        ]
    elif action_name == "poke_user":
        user_id = _value(action_result.get("user_id"))
        target_user_id = user_id
        text = f"[戳一戳 target={user_id}]"
        components = [{"type": "poke", "target_id": user_id}]
    else:
        raise ValueError(f"unsupported agent action: {action_name}")

    message_obj = event.message_obj
    group = getattr(message_obj, "group", None)
    platform_id = _value(event.get_platform_id())
    group_id = _value(event.get_group_id())
    self_id = _value(event.get_self_id())
    return {
        "schema_version": 2,
        "record_kind": "agent_action",
        "seq": 0,
        "message_id": f"agent-action:{run_id}:{int(action_index)}",
        "targetable": False,
        "platform_id": platform_id,
        "platform_name": _value(event.get_platform_name()),
        "flow_id": f"{platform_id}:group:{group_id}",
        "group_id": group_id,
        "group_name": _value(getattr(group, "group_name", "")) if group else "",
        "sender_id": self_id,
        "sender_name": self_id,
        "self_id": self_id,
        "timestamp": int(time.time()),
        "text": text,
        "components": components,
        "reply_to": reply_to,
        "target_message_id": target_message_id,
        "target_user_id": target_user_id,
        "is_directed_at_bot": False,
        "action_name": action_name,
        "action_status": "succeeded",
        "run_id": _value(run_id),
        "action_index": int(action_index),
    }


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
        "record_kind": "group_message",
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
