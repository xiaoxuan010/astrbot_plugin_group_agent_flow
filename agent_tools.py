"""自主群聊推理使用的工具定义与执行策略。"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from astrbot.api import logger
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.utils.media_utils import resolve_image_ref_to_base64_data
from mcp.types import CallToolResult, ImageContent, TextContent

try:
    from .qq_gateway import QQActionGateway, SUPPORTED_REACTIONS
    from .response_policy import record_external_action
    from .store import GroupFlowStore
except ImportError:
    from qq_gateway import QQActionGateway, SUPPORTED_REACTIONS
    from response_policy import record_external_action
    from store import GroupFlowStore

PLUGIN_NAME = "astrbot_plugin_group_agent_flow"
RUN_ID_EXTRA = "_group_agent_run_id"
FLOW_ID_EXTRA = "_group_agent_flow_id"
SNAPSHOT_SEQ_EXTRA = "_group_agent_snapshot_seq"
RUN_GENERATION_EXTRA = "_group_agent_run_generation"
SILENCE_SELECTED_EXTRA = "_group_agent_silence_selected"
BATCH_ID_EXTRA = "_group_agent_batch_id"
BATCH_RECORDS_EXTRA = "_group_agent_batch_records"
CHECKPOINT_ID_EXTRA = "_group_agent_checkpoint_id"


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
        context: Any | None = None,
    ) -> None:
        """组合 SQLite snapshot reads with QQ side effects.

        ``action_persist_callback`` remains an ignored compatibility argument;
        Core conversation owns successful action/tool history.
        """
        self.store = store
        self.gateway = gateway
        self.context = context
        self.terminal_callback = terminal_callback
        del action_persist_callback
        self.action_admission_callback = action_admission_callback

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
        self,
        flow_id: str,
        message_id: str,
        snapshot_seq: int,
        batch_id: str = "",
    ) -> dict[str, Any] | None:
        """仅当消息属于冻结快照时返回其记录。"""
        return self.store.get_message(
            flow_id,
            message_id,
            snapshot_seq=snapshot_seq,
            batch_id=batch_id or None,
        )

    def _target_message_in_snapshot(
        self,
        flow_id: str,
        message_id: str,
        snapshot_seq: int,
        batch_id: str = "",
    ) -> dict[str, Any] | None:
        """只返回可映射到平台消息 ID 的快照记录。"""
        record = self._message_in_snapshot(
            flow_id,
            message_id,
            snapshot_seq,
            batch_id,
        )
        if record is None or record.get("targetable") is False:
            return None
        return record

    def _user_in_snapshot(
        self,
        flow_id: str,
        user_id: str,
        snapshot_seq: int,
        batch_id: str = "",
    ) -> bool:
        """确认 QQ 用户作为发送者、提及或戳一戳目标出现在冻结快照中。"""
        target = str(user_id or "")
        if not target:
            return False
        for record in self.store.get_range(
            flow_id,
            1,
            snapshot_seq,
            batch_id=batch_id or None,
        ):
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

    def _current_provider_supports_images(self, event: Any | None) -> bool:
        """仅接受当前会话 Provider 显式声明的图片能力。"""
        if self.context is None or event is None:
            return False
        try:
            provider = self.context.get_using_provider(event.unified_msg_origin)
        except Exception as exc:
            logger.warning(
                f"[{PLUGIN_NAME}] failed to inspect current provider capability "
                f"error={type(exc).__name__}"
            )
            return False
        if provider is None:
            return False
        provider_config = getattr(provider, "provider_config", None)
        if not isinstance(provider_config, dict):
            return False
        modalities = provider_config.get("modalities")
        return isinstance(modalities, list) and "image" in modalities

    @staticmethod
    def _message_image_refs(record: dict[str, Any]) -> list[str]:
        """按组件顺序提取一条持久化消息中的有效图片引用。"""
        components = record.get("components")
        if not isinstance(components, list):
            return []
        refs = []
        for component in components:
            if not isinstance(component, dict) or component.get("type") != "image":
                continue
            image_ref = str(component.get("source_url") or component.get("url") or "")
            if image_ref:
                refs.append(image_ref)
        return refs

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
            await operation()
        except Exception as exc:
            detail = await self.gateway.failure_detail(
                event,
                action_name=action_name,
                exc=exc,
            )
            record_external_action(
                event,
                action_name=action_name,
                success=False,
                detail=detail,
            )
            result = {
                "success": False,
                "action": action_name,
                "error": type(exc).__name__,
            }
            if detail:
                result["detail"] = detail
            return _json(result)
        else:
            record_external_action(
                event,
                action_name=action_name,
                success=True,
                detail="",
            )
            return _json({"success": True, "action": action_name})

    def build_tool_set(self, event: Any | None = None) -> ToolSet:
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
            batch_id = str(event.get_extra(BATCH_ID_EXTRA, "") or "")
            if (
                self._target_message_in_snapshot(
                    flow_id,
                    message_id,
                    snapshot_seq,
                    batch_id,
                )
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
            batch_id = str(event.get_extra(BATCH_ID_EXTRA, "") or "")
            if (
                self._target_message_in_snapshot(
                    flow_id,
                    message_id,
                    snapshot_seq,
                    batch_id,
                )
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
            batch_id = str(event.get_extra(BATCH_ID_EXTRA, "") or "")
            if not self._user_in_snapshot(flow_id, user_id, snapshot_seq, batch_id):
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
            batch_id = str(event.get_extra(BATCH_ID_EXTRA, "") or "")
            record = self._message_in_snapshot(
                flow_id,
                message_id,
                snapshot_seq,
                batch_id,
            )
            return _json(
                record
                or {
                    "error": "message_not_found_in_snapshot",
                    "message_id": message_id,
                }
            )

        async def get_message_images(
            event: Any, message_id: str
        ) -> str | CallToolResult:
            """读取一条快照消息中的原图。"""
            _, flow_id, snapshot_seq, _ = self._run_metadata(event)
            batch_id = str(event.get_extra(BATCH_ID_EXTRA, "") or "")
            record = self._message_in_snapshot(
                flow_id,
                message_id,
                snapshot_seq,
                batch_id,
            )
            if record is None:
                return _json(
                    {
                        "error": "message_not_found_in_snapshot",
                        "message_id": message_id,
                    }
                )
            image_refs = self._message_image_refs(record)
            if not image_refs:
                return _json(
                    {
                        "error": "message_contains_no_images",
                        "message_id": message_id,
                    }
                )

            async def build_image_result(image_refs: list[str]) -> CallToolResult:
                content: list[TextContent | ImageContent] = [
                    TextContent(
                        type="text",
                        text=_json(
                            {
                                "message_id": message_id,
                                "image_count": len(image_refs),
                            }
                        ),
                    )
                ]
                for image_ref in image_refs:
                    resolved = await resolve_image_ref_to_base64_data(
                        image_ref,
                        strict=True,
                    )
                    if resolved is None:
                        raise ValueError("image reference could not be resolved")
                    content.append(
                        ImageContent(
                            type="image",
                            data=resolved.base64_data,
                            mimeType=resolved.mime_type,
                        )
                    )
                return CallToolResult(content=content)
            try:
                return await build_image_result(image_refs)
            except Exception as initial_exc:
                failure = initial_exc
                try:
                    refreshed_refs = await self.gateway.refresh_message_images(
                        event,
                        message_id=message_id,
                        message_seq=str(record.get("message_seq") or ""),
                    )
                    if refreshed_refs:
                        return await build_image_result(refreshed_refs)
                except Exception as exc:
                    failure = exc
                    logger.warning(
                        f"[{PLUGIN_NAME}] failed to refresh observed image "
                        f"message_id={message_id} error={type(exc).__name__}"
                    )
            logger.warning(
                f"[{PLUGIN_NAME}] failed to resolve observed image "
                f"message_id={message_id} error={type(failure).__name__}"
            )
            return _json(
                {
                    "error": "image_unavailable",
                    "message_id": message_id,
                }
            )

        async def get_image_captions(event: Any, message_id: str) -> str:
            """读取一条快照消息中的图片转述。"""
            _, flow_id, snapshot_seq, _ = self._run_metadata(event)
            batch_id = str(event.get_extra(BATCH_ID_EXTRA, "") or "")
            record = self._message_in_snapshot(
                flow_id,
                message_id,
                snapshot_seq,
                batch_id,
            )
            if record is None:
                return _json(
                    {
                        "error": "message_not_found_in_snapshot",
                        "message_id": message_id,
                    }
                )
            image_refs = self._message_image_refs(record)
            if not image_refs:
                return _json(
                    {
                        "error": "message_contains_no_images",
                        "message_id": message_id,
                    }
                )
            if self.context is None:
                return _json({"error": "image_caption_provider_unconfigured"})
            provider_id = ""
            try:
                config = self.context.get_config(umo=event.unified_msg_origin)
                provider_settings = (
                    config.get("provider_settings", {})
                    if isinstance(config, dict)
                    else {}
                )
                if not isinstance(provider_settings, dict):
                    provider_settings = {}
                provider_id = str(
                    provider_settings.get("default_image_caption_provider_id") or ""
                )
                if not provider_id:
                    return _json({"error": "image_caption_provider_unconfigured"})
                prompt = str(
                    provider_settings.get("image_caption_prompt")
                    or "Please describe the image using Chinese."
                )
                provider = self.context.get_provider_by_id(provider_id)
                if provider is None:
                    raise ValueError("image caption provider unavailable")
                response = await provider.text_chat(
                    prompt=prompt,
                    image_urls=image_refs,
                )
                caption = str(getattr(response, "completion_text", "") or "").strip()
                if not caption:
                    raise ValueError("image caption provider returned empty content")
                return _json(
                    {
                        "message_id": message_id,
                        "caption": caption,
                    }
                )
            except Exception as exc:
                logger.warning(
                    f"[{PLUGIN_NAME}] image caption request failed "
                    f"message_id={message_id} provider_id={provider_id} "
                    f"error={type(exc).__name__}"
                )
                return _json(
                    {
                        "error": "image_caption_failed",
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
            """搜索当前冻结 Buffer batch；已确认的 QQ 历史由独立工具提供。"""
            _, flow_id, snapshot_seq, _ = self._run_metadata(event)
            batch_id = str(event.get_extra(BATCH_ID_EXTRA, "") or "")
            messages = self.store.search_records(
                flow_id,
                query=query,
                sender_id=sender_id,
                since=since,
                until=until,
                max_seq=snapshot_seq,
                limit=limit,
                batch_id=batch_id or None,
            )
            return _json({"messages": messages})

        string_array = {"type": "array", "items": {"type": "string"}}
        tools = [
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
                    description="Poke one QQ user observed in the current frozen message set.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "user_id": {
                                "type": "string",
                                "description": "Exact QQ user ID observed as a sender, mention, or poke target in the current frozen message set.",
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
                    description=(
                        "Search messages in the current frozen Buffer snapshot; "
                        "historical QQ search is provided separately."
                    ),
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
        if self._current_provider_supports_images(event):
            tools.insert(
                -1,
                FunctionTool(
                    name="get_message_images",
                    description="View images from one observed group message.",
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
                    handler=get_message_images,
                ),
            )
        else:
            tools.insert(
                -1,
                FunctionTool(
                    name="get_image_captions",
                    description="Describe images from one observed group message.",
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
                    handler=get_image_captions,
                ),
            )
        return ToolSet(tools)
