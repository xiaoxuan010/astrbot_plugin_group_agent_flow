"""从持久化事件构建一次不可变的群聊观察。"""

from __future__ import annotations

from dataclasses import dataclass

try:
    from .context_renderers import build_renderer
    from .store import GroupFlowStore
except ImportError:
    from context_renderers import build_renderer
    from store import GroupFlowStore


@dataclass(frozen=True, slots=True)
class PreparedObservation:
    """渲染后的增量，以及推理结束时提交 cursor 所需的元数据。"""

    contexts: tuple[dict, ...]
    source_seqs: tuple[int, ...]
    target_cursor: int
    skipped_count: int
    renderer_name: str


def prepare_observation(
    store: GroupFlowStore,
    *,
    flow_id: str,
    conversation_id: str,
    snapshot_seq: int,
    renderer_name: str,
    max_messages: int,
) -> PreparedObservation:
    """渲染尚未消费且不超过固定快照序号的记录。"""
    cursor = store.get_cursor(flow_id, conversation_id)
    records = store.get_range(flow_id, cursor + 1, int(snapshot_seq))
    # 纯传输空事件可以保留用于诊断，但不包含可供模型观察的信息。
    records = [
        record
        for record in records
        if str(record.get("text") or "").strip() or record.get("components")
    ]
    skipped_count = 0
    limit = max(0, int(max_messages or 0))
    if limit and len(records) > limit:
        # 被截去的较早记录仍可通过历史搜索工具读取。
        skipped_count = len(records) - limit
        records = records[-limit:]
    renderer = build_renderer(renderer_name)
    return PreparedObservation(
        contexts=tuple(renderer.render(records)),
        source_seqs=tuple(int(record.get("seq") or 0) for record in records),
        target_cursor=int(snapshot_seq),
        skipped_count=skipped_count,
        renderer_name=renderer.name,
    )
