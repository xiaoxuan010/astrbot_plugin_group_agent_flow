"""从持久化事件构建一次不可变、缓存友好的群聊观察窗口。"""

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
    """渲染后的窗口，以及推理成功时需要原子提交的元数据。"""

    contexts: tuple[dict, ...]
    source_seqs: tuple[int, ...]
    target_cursor: int
    skipped_count: int
    renderer_name: str
    estimated_tokens: int
    window_blocks: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class _RenderedBlock:
    start_seq: int
    end_seq: int
    records: tuple[dict[str, Any], ...]
    contexts: tuple[dict, ...]
    estimated_tokens: int


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
) -> _RenderedBlock | None:
    if not records:
        return None
    contexts = tuple(renderer.render(list(records)))
    if not contexts:
        return None
    start_seq = _seq(records[0])
    end_seq = _seq(records[-1])
    if start_seq <= 0 or end_seq < start_seq:
        return None
    return _RenderedBlock(
        start_seq=start_seq,
        end_seq=end_seq,
        records=tuple(records),
        contexts=contexts,
        estimated_tokens=_count_contexts(contexts, counter),
    )


def _trim_bootstrap_records(
    renderer: ContextRenderer,
    records: list[dict[str, Any]],
    *,
    token_budget: int,
    counter: EstimateTokenCounter,
) -> _RenderedBlock | None:
    """选择预算内的最近历史后缀，不截断历史单条消息。"""
    if token_budget <= 0:
        return None
    candidate = list(records)
    while candidate:
        block = _render_block(renderer, candidate, counter)
        if block is not None and block.estimated_tokens <= token_budget:
            return block
        candidate.pop(0)
    return None


def _truncate_single_record(
    renderer: ContextRenderer,
    record: dict[str, Any],
    *,
    token_budget: int,
    counter: EstimateTokenCounter,
) -> _RenderedBlock:
    """保留记录元数据和内容尾部，并确定性地压入 token 预算。"""
    original_text = str(record.get("text") or "")
    message_id = str(record.get("message_id") or "")
    marker = (
        f"[内容已截断；使用 get_message(message_id={message_id})"
        " 获取持久化全文] "
    )

    low = 0
    high = len(original_text)
    best: _RenderedBlock | None = None
    while low <= high:
        keep_chars = (low + high) // 2
        projected = deepcopy(record)
        tail = original_text[-keep_chars:] if keep_chars else ""
        projected["text"] = f"{marker}{tail}"
        block = _render_block(renderer, [projected], counter)
        if block is not None and block.estimated_tokens <= token_budget:
            best = block
            low = keep_chars + 1
        else:
            high = keep_chars - 1

    if best is not None:
        return best

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
) -> _RenderedBlock | None:
    candidate = list(records)
    while candidate:
        block = _render_block(renderer, candidate, counter)
        if block is not None and block.estimated_tokens <= token_budget:
            return block
        if len(candidate) == 1:
            return _truncate_single_record(
                renderer,
                candidate[0],
                token_budget=token_budget,
                counter=counter,
            )
        candidate.pop(0)
    return None


def _load_active_blocks(
    store: GroupFlowStore,
    *,
    flow_id: str,
    conversation_id: str,
    cursor: int,
    renderer: ContextRenderer,
    visible_records: list[dict[str, Any]],
    counter: EstimateTokenCounter,
) -> list[_RenderedBlock]:
    window = store.get_observation_window(flow_id, conversation_id)
    if window is None or window.get("renderer") != renderer.name:
        return []

    records_by_seq = {_seq(record): record for record in visible_records}
    blocks: list[_RenderedBlock] = []
    for raw_block in window["blocks"]:
        start_seq = int(raw_block["start_seq"])
        end_seq = int(raw_block["end_seq"])
        if end_seq > cursor:
            continue
        if start_seq not in records_by_seq or end_seq not in records_by_seq:
            # retention 或损坏使该块无法完整重建；此前前缀一并失效。
            blocks = []
            continue
        records = [
            records_by_seq[seq]
            for seq in sorted(records_by_seq)
            if start_seq <= seq <= end_seq
        ]
        block = _render_block(renderer, records, counter)
        if block is None:
            blocks = []
            continue
        blocks.append(block)
    return blocks


def prepare_observation(
    store: GroupFlowStore,
    *,
    flow_id: str,
    conversation_id: str,
    snapshot_seq: int,
    renderer_name: str,
    max_context_tokens: int,
    rotation_retention_ratio: float,
) -> PreparedObservation:
    """构建最近 observation 窗口，并返回成功后应提交的块状态。"""
    cursor = store.get_cursor(flow_id, conversation_id)
    renderer = build_renderer(renderer_name)
    counter = EstimateTokenCounter()
    hard_limit = max(1, int(max_context_tokens))
    retention_ratio = min(0.9, max(0.1, float(rotation_retention_ratio)))

    all_records = store.get_range(flow_id, 1, int(snapshot_seq))
    visible_records = [record for record in all_records if _is_model_visible(record)]
    latest_records = [record for record in visible_records if _seq(record) > cursor]

    if not latest_records:
        return PreparedObservation(
            contexts=(),
            source_seqs=(),
            target_cursor=int(snapshot_seq),
            skipped_count=len(visible_records),
            renderer_name=renderer.name,
            estimated_tokens=0,
            window_blocks=(),
        )

    latest_block = _trim_latest_block(
        renderer,
        latest_records,
        token_budget=hard_limit,
        counter=counter,
    )
    if latest_block is None:
        return PreparedObservation(
            contexts=(),
            source_seqs=(),
            target_cursor=int(snapshot_seq),
            skipped_count=len(visible_records),
            renderer_name=renderer.name,
            estimated_tokens=0,
            window_blocks=(),
        )

    history_blocks = _load_active_blocks(
        store,
        flow_id=flow_id,
        conversation_id=conversation_id,
        cursor=cursor,
        renderer=renderer,
        visible_records=visible_records,
        counter=counter,
    )
    if not history_blocks and cursor > 0:
        history_records = [
            record for record in visible_records if _seq(record) <= cursor
        ]
        bootstrap = _trim_bootstrap_records(
            renderer,
            history_records,
            token_budget=max(0, hard_limit - latest_block.estimated_tokens),
            counter=counter,
        )
        if bootstrap is not None:
            history_blocks = [bootstrap]

    blocks = [*history_blocks, latest_block]
    total_tokens = sum(block.estimated_tokens for block in blocks)
    if total_tokens > hard_limit:
        rotation_target = int(hard_limit * retention_ratio)
        while len(blocks) > 1 and total_tokens > rotation_target:
            total_tokens -= blocks.pop(0).estimated_tokens

    contexts = tuple(
        context
        for block in blocks
        for context in block.contexts
    )
    source_seqs = tuple(
        _seq(record)
        for block in blocks
        for record in block.records
    )
    estimated_tokens = _count_contexts(contexts, counter)
    return PreparedObservation(
        contexts=contexts,
        source_seqs=source_seqs,
        target_cursor=int(snapshot_seq),
        skipped_count=max(0, len(visible_records) - len(source_seqs)),
        renderer_name=renderer.name,
        estimated_tokens=estimated_tokens,
        window_blocks=tuple(
            (block.start_seq, block.end_seq)
            for block in blocks
        ),
    )
