"""从持久化事件构建一次不可变的群聊观察增量。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from astrbot.core.agent.context.token_counter import EstimateTokenCounter
from astrbot.core.agent.message import Message

try:
    from .context_renderers import ContextRenderer, build_renderer
    from .store import GroupFlowStore
except ImportError:
    from context_renderers import ContextRenderer, build_renderer
    from store import GroupFlowStore


@dataclass(frozen=True, slots=True)
class PreparedObservation:
    """渲染后的观察增量，以及推理成功时需要提交的元数据。"""

    contexts: tuple[dict, ...]
    source_seqs: tuple[int, ...]
    target_cursor: int
    skipped_count: int
    estimated_tokens: int
    audio_urls: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AudioAttachment:
    """一个已通过时长探测、内部保留的语音引用。"""

    seq: int
    url: str
    token_cost: int


@dataclass(frozen=True, slots=True)
class _RenderedBlock:
    records: tuple[dict[str, Any], ...]
    contexts: tuple[dict, ...]
    estimated_tokens: int
    audio_urls: tuple[str, ...]


def _is_model_visible(record: dict[str, Any]) -> bool:
    return bool(str(record.get("text") or "").strip() or record.get("components"))


def _seq(record: dict[str, Any]) -> int:
    try:
        return int(record.get("seq") or 0)
    except (TypeError, ValueError):
        return 0


def _count_contexts(
    contexts: tuple[dict, ...] | list[dict],
    counter: EstimateTokenCounter,
) -> int:
    messages = [Message.model_validate(context) for context in contexts]
    return counter.count_tokens(messages)


def _render_block(
    renderer: ContextRenderer,
    records: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    counter: EstimateTokenCounter,
    audio_attachments: tuple[AudioAttachment, ...] = (),
) -> _RenderedBlock | None:
    if not records:
        return None
    contexts = tuple(renderer.render(list(records)))
    if not contexts:
        return None
    if _seq(records[0]) <= 0 or _seq(records[-1]) < _seq(records[0]):
        return None
    selected_seqs = {_seq(record) for record in records}
    selected_audio = tuple(
        attachment
        for attachment in audio_attachments
        if attachment.seq in selected_seqs
    )
    return _RenderedBlock(
        records=tuple(records),
        contexts=contexts,
        estimated_tokens=_count_contexts(contexts, counter)
        + sum(attachment.token_cost for attachment in selected_audio),
        audio_urls=tuple(attachment.url for attachment in selected_audio),
    )


def _truncate_single_record(
    renderer: ContextRenderer,
    record: dict[str, Any],
    *,
    token_budget: int,
    counter: EstimateTokenCounter,
    audio_attachments: tuple[AudioAttachment, ...] = (),
) -> _RenderedBlock:
    """保留记录元数据和内容尾部，并确定性地压入 token 预算。"""
    original_text = str(record.get("text") or "")
    message_id = str(record.get("message_id") or "")
    marker = f"[内容已截断；使用 get_message(message_id={message_id}) 获取持久化全文] "

    low = 0
    high = len(original_text)
    best: _RenderedBlock | None = None
    while low <= high:
        keep_chars = (low + high) // 2
        projected = deepcopy(record)
        tail = original_text[-keep_chars:] if keep_chars else ""
        truncated_text = f"{marker}{tail}"
        projected["text"] = truncated_text
        if isinstance(projected.get("components"), list) and projected["components"]:
            projected["components"] = [{"type": "text", "text": truncated_text}]
        block = _render_block(
            renderer,
            [projected],
            counter,
            audio_attachments,
        )
        if block is not None and block.estimated_tokens <= token_budget:
            best = block
            low = keep_chars + 1
        else:
            high = keep_chars - 1

    if best is not None:
        return best

    if audio_attachments:
        # 去掉语音附件重试一次：丢弃语音 URL 可能释放足够预算，
        # 以容纳记录元数据与恢复标记。
        return _truncate_single_record(
            renderer,
            record,
            token_budget=token_budget,
            counter=counter,
            audio_attachments=(),
        )
    raise ValueError(
        "max_context_tokens is too small for observation metadata and "
        "the get_message recovery marker"
    )


def _trim_latest_block(
    renderer: ContextRenderer,
    records: list[dict[str, Any]],
    *,
    token_budget: int,
    counter: EstimateTokenCounter,
    audio_attachments: tuple[AudioAttachment, ...] = (),
) -> _RenderedBlock | None:
    candidate = list(records)
    while candidate:
        block = _render_block(renderer, candidate, counter, audio_attachments)
        if block is not None and block.estimated_tokens <= token_budget:
            return block
        if len(candidate) == 1:
            return _truncate_single_record(
                renderer,
                candidate[0],
                token_budget=token_budget,
                counter=counter,
                audio_attachments=audio_attachments,
            )
        candidate.pop(0)
    return None


def prepare_observation(
    store: GroupFlowStore,
    *,
    flow_id: str,
    snapshot_seq: int,
    max_context_tokens: int,
    history_cursor: int | None = None,
    audio_attachments: tuple[AudioAttachment, ...] = (),
) -> PreparedObservation:
    """构建仍在 Buffer 中的 observation 增量。"""
    all_records = store.get_range(flow_id, 1, int(snapshot_seq))
    return prepare_observation_records(
        all_records,
        snapshot_seq=snapshot_seq,
        max_context_tokens=max_context_tokens,
        history_cursor=history_cursor,
        audio_attachments=audio_attachments,
    )


def prepare_observation_records(
    records: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    snapshot_seq: int,
    max_context_tokens: int,
    history_cursor: int | None = None,
    audio_attachments: tuple[AudioAttachment, ...] = (),
) -> PreparedObservation:
    """渲染恰好给定的不可变批次行，不做任何存储读取。"""
    cursor = max(0, int(history_cursor)) if history_cursor is not None else 0
    renderer = build_renderer()
    counter = EstimateTokenCounter()
    hard_limit = max(1, int(max_context_tokens))

    visible_records = [record for record in records if _is_model_visible(record)]
    if history_cursor is not None:
        visible_records = [
            record
            for record in visible_records
            if str(record.get("record_kind") or "group_message") != "agent_action"
        ]
    latest_records = [record for record in visible_records if _seq(record) > cursor]

    if not latest_records:
        return PreparedObservation(
            contexts=(),
            source_seqs=(),
            target_cursor=int(snapshot_seq),
            skipped_count=len(visible_records),
            estimated_tokens=0,
        )

    latest_block = _trim_latest_block(
        renderer,
        latest_records,
        token_budget=hard_limit,
        counter=counter,
        audio_attachments=audio_attachments,
    )
    if latest_block is None:
        return PreparedObservation(
            contexts=(),
            source_seqs=(),
            target_cursor=int(snapshot_seq),
            skipped_count=len(visible_records),
            estimated_tokens=0,
        )

    contexts = latest_block.contexts
    source_seqs = tuple(_seq(record) for record in latest_block.records)
    return PreparedObservation(
        contexts=contexts,
        source_seqs=source_seqs,
        target_cursor=int(snapshot_seq),
        skipped_count=max(0, len(visible_records) - len(source_seqs)),
        estimated_tokens=latest_block.estimated_tokens,
        audio_urls=latest_block.audio_urls,
    )
