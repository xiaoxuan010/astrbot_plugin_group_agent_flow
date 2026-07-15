"""按群调度固定快照，推理期间不重新判断。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    """分配给一次自主推理的不可变消息上界。"""

    run_id: str
    flow_id: str
    snapshot_seq: int
    started_at: float


@dataclass(slots=True)
class _FlowState:
    """单个群流水拥有的可变调度状态。"""

    pending_seq: int = 0
    due_at: float | None = None
    active_run_id: str | None = None
    last_started_at: float | None = None


class GroupRunCoordinator:
    """按群调度单个推理任务，并保持推理中快照不变。"""

    def __init__(
        self,
        *,
        debounce_seconds: float = 10,
        direct_delay_seconds: float = 1,
        min_cycle_interval_seconds: float = 10,
    ) -> None:
        """配置消息聚合延迟和两次推理启动的最小间隔。"""
        self.debounce_seconds = max(0.0, float(debounce_seconds))
        self.direct_delay_seconds = max(0.0, float(direct_delay_seconds))
        self.min_cycle_interval_seconds = max(0.0, float(min_cycle_interval_seconds))
        self._flows: dict[str, _FlowState] = {}
        self._runs: dict[str, str] = {}

    def enqueue(
        self,
        flow_id: str,
        *,
        seq: int,
        received_at: float,
        directed: bool,
    ) -> None:
        """将最新消息序号预留给下一次可执行快照。"""
        state = self._flows.setdefault(flow_id, _FlowState())
        state.pending_seq = max(state.pending_seq, int(seq))
        delay = self.direct_delay_seconds if directed else self.debounce_seconds
        candidate_due_at = float(received_at) + delay
        if state.due_at is None:
            state.due_at = candidate_due_at
        elif directed:
            # 直接提及机器人时，可以缩短普通消息批次的等待时间。
            state.due_at = min(state.due_at, candidate_due_at)

    def begin_if_due(self, flow_id: str, *, now: float) -> RunSnapshot | None:
        """群流水到期后，原子地占用当前待处理消息。"""
        state = self._flows.get(flow_id)
        if (
            state is None
            or state.active_run_id is not None
            or state.pending_seq <= 0
            or state.due_at is None
        ):
            return None

        eligible_at = state.due_at
        if state.last_started_at is not None:
            eligible_at = max(
                eligible_at,
                state.last_started_at + self.min_cycle_interval_seconds,
            )
        if now < eligible_at:
            return None

        run_id = uuid.uuid4().hex
        snapshot = RunSnapshot(
            run_id=run_id,
            flow_id=flow_id,
            snapshot_seq=state.pending_seq,
            started_at=float(now),
        )
        # 重置后到达的新消息会保留为下一批待处理消息。
        state.pending_seq = 0
        state.due_at = None
        state.active_run_id = run_id
        state.last_started_at = float(now)
        self._runs[run_id] = flow_id
        return snapshot

    def finish(self, run_id: str, *, finished_at: float) -> None:
        """释放已完成任务，使后续待处理快照可以启动。"""
        flow_id = self._runs.pop(run_id, None)
        if flow_id is None:
            raise KeyError(f"unknown run: {run_id}")
        state = self._flows[flow_id]
        state.active_run_id = None

    def next_due_at(self, flow_id: str) -> float | None:
        """结合聚合延迟和频率限制，返回下一次唤醒时间。"""
        state = self._flows.get(flow_id)
        if state is None or state.due_at is None:
            return None
        if state.last_started_at is None:
            return state.due_at
        return max(
            state.due_at,
            state.last_started_at + self.min_cycle_interval_seconds,
        )
