"""供 Agent 工具调用的 QQ 动作边界。"""

from __future__ import annotations

import time
from typing import Any

from astrbot.api.event import MessageChain
from astrbot.api.message_components import At, Plain, Reply

try:
    from .context_renderers import format_group_timestamp
    from .response_policy import tool_send
except ImportError:
    from context_renderers import format_group_timestamp
    from response_policy import tool_send

# Agent 使用 QQ 原生表情名称，协议编号集中在适配层维护。
QQ_REACTION_IDS = {
    "赞": "76",
    "爱心": "66",
    "鼓掌": "99",
    "笑哭": "182",
    "微笑": "14",
    "疑问": "32",
    "惊讶": "0",
    "流泪": "5",
    "生气": "326",
    "玫瑰": "63",
    "捂脸": "264",
}
SUPPORTED_REACTIONS = tuple(QQ_REACTION_IDS)
MESSAGE_SEND_ACTIONS = frozenset({"send_message", "reply_message"})


class QQActionGateway:
    """将已授权的 Agent 动作转换为 AstrBot 和 OneBot 调用。"""

    @staticmethod
    def _message_chain(content: str, mentions: list[str] | None = None) -> MessageChain:
        """构建 AstrBot 消息链，并将提及组件放在正文之前。"""
        chain = [At(qq=str(user_id)) for user_id in mentions or []]
        chain.append(Plain(str(content)))
        return MessageChain(chain=chain)

    async def failure_detail(
        self,
        event: Any,
        *,
        action_name: str,
        exc: Exception,
    ) -> str:
        """用实时群状态补充 QQNT 未解释的消息发送失败。"""
        platform_error = str(exc)
        if (
            action_name not in MESSAGE_SEND_ACTIONS
            or "NodeIKernelMsgService/sendMsg" not in platform_error
        ):
            return platform_error

        bot = getattr(event, "bot", None)
        group_id = str(event.get_group_id() or "")
        self_id = str(event.get_self_id() or "")
        if bot is None or not hasattr(bot, "call_action") or not group_id or not self_id:
            return platform_error

        try:
            member = await bot.call_action(
                "get_group_member_info",
                group_id=int(group_id) if group_id.isdigit() else group_id,
                user_id=int(self_id) if self_id.isdigit() else self_id,
                no_cache=True,
                self_id=int(self_id) if self_id.isdigit() else self_id,
            )
            mute_until = int(member.get("shut_up_timestamp") or 0)
            if mute_until <= int(time.time()):
                return platform_error
            release_time = format_group_timestamp(mute_until, strict=True)
        except Exception:
            return platform_error
        else:
            return f"你已被禁言，无法发送消息；解禁时间：{release_time}"

    async def send_message(
        self,
        event: Any,
        *,
        content: str,
        mentions: list[str] | None = None,
    ) -> dict[str, Any]:
        """通过工具授权的发送入口发送普通群消息。"""
        normalized_content = str(content)
        normalized_mentions = [str(user_id) for user_id in mentions or []]
        await tool_send(
            event,
            self._message_chain(normalized_content, normalized_mentions),
        )
        return {
            "success": True,
            "action": "send_message",
            "content": normalized_content,
            "mentions": normalized_mentions,
        }

    async def reply_message(
        self,
        event: Any,
        *,
        message_id: str,
        content: str,
        mentions: list[str] | None = None,
    ) -> dict[str, Any]:
        """在消息链前添加目标 QQ 消息的引用组件。"""
        normalized_message_id = str(message_id)
        normalized_content = str(content)
        normalized_mentions = [str(user_id) for user_id in mentions or []]
        chain = self._message_chain(normalized_content, normalized_mentions)
        chain.chain.insert(0, Reply(id=normalized_message_id))
        await tool_send(event, chain)
        return {
            "success": True,
            "action": "reply_message",
            "message_id": normalized_message_id,
            "content": normalized_content,
            "mentions": normalized_mentions,
        }

    async def react_message(
        self,
        event: Any,
        *,
        message_id: str,
        reaction: str,
    ) -> dict[str, Any]:
        """将通用表情语义转换成 QQ 编号，再调用 NapCat 扩展。"""
        reaction = str(reaction).strip()
        emoji_id = QQ_REACTION_IDS.get(reaction)
        if emoji_id is None:
            supported = ", ".join(SUPPORTED_REACTIONS)
            raise ValueError(f"unsupported reaction: {reaction}; supported: {supported}")

        bot = getattr(event, "bot", None)
        if bot is None or not hasattr(bot, "call_action"):
            raise RuntimeError("current platform does not support QQ reactions")
        await bot.call_action(
            "set_msg_emoji_like",
            message_id=str(message_id),
            emoji_id=str(emoji_id),
            set=True,
        )
        return {
            "success": True,
            "action": "react_message",
            "message_id": str(message_id),
            "reaction": reaction,
        }

    async def poke_user(self, event: Any, *, user_id: str) -> dict[str, Any]:
        """通过 NapCat 群戳一戳 Action 操作当前群成员。"""
        target_id = str(user_id).strip()
        if not target_id:
            raise ValueError("poke target user_id is required")

        bot = getattr(event, "bot", None)
        group_id = str(event.get_group_id() or "")
        self_id = str(event.get_self_id() or "")
        if bot is None or not hasattr(bot, "call_action") or not group_id:
            raise RuntimeError("current platform does not support QQ group poke")
        await bot.call_action(
            "group_poke",
            group_id=group_id,
            user_id=target_id,
            self_id=int(self_id) if self_id.isdigit() else self_id,
        )
        return {
            "success": True,
            "action": "poke_user",
            "user_id": target_id,
        }
