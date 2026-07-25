"""自主群聊推理使用的工具定义与执行策略。"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from astrbot.api import logger
from astrbot.core.agent.tool import FunctionTool, ToolSet

try:
    from .event_codec import build_agent_action_record
    from .qq_gateway import QQActionGateway, SUPPORTED_REACTIONS
    from .response_policy import EXTERNAL_ACTIONS_EXTRA, record_external_action
    from .store import GroupFlowStore
except ImportError:
    from event_codec import build_agent_action_record
    from qq_gateway import QQActionGateway, SUPPORTED_REACTIONS
    from response_policy import EXTERNAL_ACTIONS_EXTRA, record_external_action
    from store import GroupFlowStore

PLUGIN_NAME = "astrbot_plugin_group_agent_flow"
RUN_ID_EXTRA = "_group_agent_run_id"
FLOW_ID_EXTRA = "_group_agent_flow_id"
SNAPSHOT_SEQ_EXTRA = "_group_agent_snapshot_seq"
RUN_GENERATION_EXTRA = "_group_agent_run_generation"
SILENCE_SELECTED_EXTRA = "_group_agent_silence_selected"


def _json(payload: Any) -> str:
    """紧凑序列化工具结果，并保留中文字符。"""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class ToolRuntime:
    """提供受快照限制的读取工具和可连续执行的外部动作工具。"""

    def __init__(
        self,
        store: GroupFlowStore,
        gateway: QQActionGateway,
        terminal_callback: Callable[[Any], Awaitable[None]] | None = None,
        action_persist_callback: Callable[
            ..., Awaitable[int]
        ]
        | None = None,
        action_admission_callback: Callable[
            [str, str, int], Awaitable[bool]
        ]
        | None = None,
    ) -> None:
        """将持久化动作状态与 QQ 副作用网关组合起来。"""
        self.store = store
        self.gateway = gateway
        self.terminal_callback = terminal_callback
        self.action_persist_callback = (
            action_persist_callback or self._append_action_record
        )
        self.action_admission_callback = action_admission_callback

    async def _append_action_record(
        self,
        flow_id: str,
        record: dict[str, Any],
        *,
        run_id: str,
        generation: int,
    ) -> int:
        """为独立 ToolRuntime 构造提供默认的事实写入路径。"""
        return self.store.append_record(flow_id, record)

    @staticmethod
    def _run_metadata(event: Any) -> tuple[str, str, int, int]:
        """读取并校验观察处理器附加的本轮元数据。"""
        run_id = str(event.get_extra(RUN_ID_EXTRA, "") or "")
        flow_id = str(event.get_extra(FLOW_ID_EXTRA, "") or "")
        snapshot_seq = int(event.get_extra(SNAPSHOT_SEQ_EXTRA, 0) or 0)
        generation = int(event.get_extra(RUN_GENERATION_EXTRA, -1))
        if not run_id or not flow_id or snapshot_seq <= 0:
            raise RuntimeError("tool called outside an autonomous group run")
        return run_id, flow_id, snapshot_seq, generation

    def _message_in_snapshot(
        self, flow_id: str, message_id: str, snapshot_seq: int
    ) -> dict[str, Any] | None:
        """仅当消息属于冻结快照时返回其记录。"""
        record = self.store.get_message(flow_id, message_id)
        if record is None or int(record.get("seq") or 0) > snapshot_seq:
            return None
        return record

    def _target_message_in_snapshot(
        self, flow_id: str, message_id: str, snapshot_seq: int
    ) -> dict[str, Any] | None:
        """只返回可映射到平台消息 ID 的快照记录。"""
        record = self._message_in_snapshot(flow_id, message_id, snapshot_seq)
        if record is None or record.get("targetable") is False:
            return None
        return record

    def _user_in_snapshot(self, flow_id: str, user_id: str, snapshot_seq: int) -> bool:
        """确认 QQ 用户作为发送者、提及或戳一戳目标出现在冻结快照中。"""
        target = str(user_id or "")
        if not target:
            return False
        for record in self.store.read_records(flow_id):
            if int(record.get("seq") or 0) > snapshot_seq:
                continue
            if str(record.get("record_kind") or "group_message") == "agent_action":
                continue
            if str(record.get("sender_id") or "") == target:
                return True
            components = record.get("components")
            if not isinstance(components, list):
                continue
            for component in components:
                if not isinstance(component, dict):
                    continue
                component_type = str(component.get("type") or "")
                if (
                    component_type == "at"
                    and str(component.get("user_id") or "") == target
                ):
                    return True
                if (
                    component_type == "poke"
                    and str(component.get("target_id") or "") == target
                ):
                    return True
        return False

    async def _external(
        self,
        event: Any,
        action_name: str,
        operation: Callable[[], Awaitable[dict[str, Any]]],
    ) -> str:
        """执行一次外部动作，并返回供下一次规划使用的紧凑结果。"""
        run_id, flow_id, _, generation = self._run_metadata(event)
        if self.action_admission_callback is not None:
            admitted = await self.action_admission_callback(
                flow_id,
                run_id,
                generation,
            )
            if not admitted:
                record_external_action(
                    event,
                    action_name=action_name,
                    success=False,
                    detail="stale_run",
                )
                return _json(
                    {
                        "success": False,
                        "action": action_name,
                        "error": "stale_run",
                    }
                )
        try:
            action_result = await operation()
        except Exception as exc:
            record_external_action(
                event,
                action_name=action_name,
                success=False,
                detail=str(exc),
            )
            return _json(
                {
                    "success": False,
                    "action": action_name,
                    "error": type(exc).__name__,
                }
            )
        else:
            actions = event.get_extra(EXTERNAL_ACTIONS_EXTRA, [])
            action_index = len(actions) + 1 if isinstance(actions, list) else 1
            detail = ""
            try:
                record = build_agent_action_record(
                    event,
                    run_id=run_id,
                    action_index=action_index,
                    action_name=action_name,
                    action_result=action_result,
                )
            except Exception as exc:
                detail = f"fact_encode_failed:{type(exc).__name__}"
                logger.error(
                    f"[{PLUGIN_NAME}] failed to encode agent action fact "
                    f"flow_id={flow_id} run_id={run_id} "
                    f"action={action_name} error={type(exc).__name__}",
                    exc_info=True,
                )
            else:
                try:
                    await self.action_persist_callback(
                        flow_id,
                        record,
                        run_id=run_id,
                        generation=generation,
                    )
                except Exception as exc:
                    detail = f"fact_persist_failed:{type(exc).__name__}"
                    logger.error(
                        f"[{PLUGIN_NAME}] failed to persist agent action fact "
                        f"flow_id={flow_id} run_id={run_id} "
                        f"action={action_name} error={type(exc).__name__}",
                        exc_info=True,
                    )
            record_external_action(
                event,
                action_name=action_name,
                success=True,
                detail=detail,
            )
            result: dict[str, Any] = {"success": True, "action": action_name}
            if detail:
                result["fact_error"] = detail
            return _json(result)

    def build_tool_set(self) -> ToolSet:
        """为每次请求创建独立工具集，避免共享可变工具对象。"""

        async def send_message(
            event: Any, content: str, mentions: list[str] | None = None
        ) -> str:
            """向本轮对应的群发送一条新消息。"""
            return await self._external(
                event,
                "send_message",
                lambda: self.gateway.send_message(
                    event, content=content, mentions=mentions or []
                ),
            )

        async def reply_message(
            event: Any,
            message_id: str,
            content: str,
            mentions: list[str] | None = None,
        ) -> str:
            """仅引用回复当前快照中可见的消息。"""
            _, flow_id, snapshot_seq, _ = self._run_metadata(event)
            if (
                self._target_message_in_snapshot(flow_id, message_id, snapshot_seq)
                is None
            ):
                return _json(
                    {
                        "success": False,
                        "error": "message_not_found_in_snapshot",
                        "message_id": message_id,
                    }
                )
            return await self._external(
                event,
                "reply_message",
                lambda: self.gateway.reply_message(
                    event,
                    message_id=message_id,
                    content=content,
                    mentions=mentions or [],
                ),
            )

        async def react_message(event: Any, message_id: str, reaction: str) -> str:
            """仅对当前快照中可见的消息添加表情回应。"""
            _, flow_id, snapshot_seq, _ = self._run_metadata(event)
            if (
                self._target_message_in_snapshot(flow_id, message_id, snapshot_seq)
                is None
            ):
                return _json(
                    {
                        "success": False,
                        "error": "message_not_found_in_snapshot",
                        "message_id": message_id,
                    }
                )
            return await self._external(
                event,
                "react_message",
                lambda: self.gateway.react_message(
                    event, message_id=message_id, reaction=reaction
                ),
            )

        async def poke_user(event: Any, user_id: str) -> str:
            """仅戳当前冻结快照中已出现的 QQ 用户。"""
            _, flow_id, snapshot_seq, _ = self._run_metadata(event)
            if not self._user_in_snapshot(flow_id, user_id, snapshot_seq):
                return _json(
                    {
                        "success": False,
                        "error": "user_not_found_in_snapshot",
                        "user_id": user_id,
                    }
                )
            return await self._external(
                event,
                "poke_user",
                lambda: self.gateway.poke_user(event, user_id=user_id),
            )

        async def stay_silent(event: Any) -> None:
            """End this observation cycle without a group-visible action."""
            self._run_metadata(event)
            event.set_extra(SILENCE_SELECTED_EXTRA, True)
            if self.terminal_callback is not None:
                await self.terminal_callback(event)
            return None

        async def get_message(event: Any, message_id: str) -> str:
            """读取一条持久化消息，同时遵守快照边界。"""
            _, flow_id, snapshot_seq, _ = self._run_metadata(event)
            record = self._message_in_snapshot(flow_id, message_id, snapshot_seq)
            return _json(
                record
                or {
                    "error": "message_not_found_in_snapshot",
                    "message_id": message_id,
                }
            )

        async def search_chat_history(
            event: Any,
            query: str = "",
            sender_id: str = "",
            since: int | None = None,
            until: int | None = None,
            limit: int = 20,
        ) -> str:
            """搜索本地群历史，结果上限为本轮快照序号。"""
            _, flow_id, snapshot_seq, _ = self._run_metadata(event)
            messages = self.store.search_records(
                flow_id,
                query=query,
                sender_id=sender_id,
                since=since,
                until=until,
                max_seq=snapshot_seq,
                limit=limit,
            )
            return _json({"messages": messages})

        string_array = {"type": "array", "items": {"type": "string"}}
        return ToolSet(
            [
                FunctionTool(
                    name="send_message",
                    description="Send one message to the current group.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "content": {"type": "string", "maxLength": 500},
                            "mentions": string_array,
                        },
                        "required": ["content"],
                    },
                    handler=send_message,
                ),
                FunctionTool(
                    name="reply_message",
                    description="Reply to a specific observed group message by message ID.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "message_id": {
                                "type": "string",
                                "description": "Exact message ID shown as msg=... in the observed context.",
                            },
                            "content": {"type": "string", "maxLength": 500},
                            "mentions": string_array,
                        },
                        "required": ["message_id", "content"],
                    },
                    handler=reply_message,
                ),
                FunctionTool(
                    name="react_message",
                    description="Add a QQ emoji reaction to a specific observed message.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "message_id": {
                                "type": "string",
                                "description": "Exact message ID shown as msg=... in the observed context.",
                            },
                            "reaction": {
                                "type": "string",
                                "enum": list(SUPPORTED_REACTIONS),
                                "description": "要添加到消息上的 QQ 表情名称。",
                            },
                        },
                        "required": ["message_id", "reaction"],
                    },
                    handler=react_message,
                ),
                FunctionTool(
                    name="poke_user",
                    description="Poke one QQ user observed in the available chat history.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "user_id": {
                                "type": "string",
                                "description": "Exact QQ user ID observed as a sender, mention, or poke target in the available chat history.",
                            }
                        },
                        "required": ["user_id"],
                    },
                    handler=poke_user,
                ),
                FunctionTool(
                    name="stay_silent",
                    description="End the current observation cycle without any group-visible action.",
                    parameters={"type": "object", "properties": {}},
                    handler=stay_silent,
                ),
                FunctionTool(
                    name="get_message",
                    description="Read one locally stored group message by message ID.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "message_id": {
                                "type": "string",
                                "description": "Exact message ID shown as msg=... in the observed context.",
                            }
                        },
                        "required": ["message_id"],
                    },
                    handler=get_message,
                ),
                FunctionTool(
                    name="search_chat_history",
                    description="Search locally stored history for the current group.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "sender_id": {"type": "string"},
                            "since": {"type": ["integer", "null"]},
                            "until": {"type": ["integer", "null"]},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                        },
                    },
                    handler=search_chat_history,
                ),
            ]
        )
