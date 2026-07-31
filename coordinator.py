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
    generation: int


@dataclass(slots=True)
class _FlowState:
    """单个群流水拥有的可变调度状态。"""

    pending_seq: int = 0
    global_due_at: float | None = None
    direct_due_at: float | None = None
    active_run_id: str | None = None
    last_started_at: float | None = None
    generation: int = 0


class GroupRunCoordinator:
    """按群调度单个推理任务，并保持推理中快照不变。"""

    def __init__(
        self,
        *,
        debounce_seconds: float = 10,
        direct_delay_seconds: float = 1,
        min_cycle_interval_seconds: float = 10,
        direct_min_cycle_interval_seconds: float = 20,
    ) -> None:
        """配置普通/定向消息的聚合延迟和最短启动间隔。"""
        self.debounce_seconds = max(0.0, float(debounce_seconds))
        self.direct_delay_seconds = max(0.0, float(direct_delay_seconds))
        self.min_cycle_interval_seconds = max(0.0, float(min_cycle_interval_seconds))
        self.direct_min_cycle_interval_seconds = max(
            0.0, float(direct_min_cycle_interval_seconds)
        )
        self._flows: dict[str, _FlowState] = {}
        self._runs: dict[str, str] = {}
        self._generations: dict[str, int] = {}

    def _state_for(self, flow_id: str) -> _FlowState:
        state = self._flows.get(flow_id)
        if state is None:
            state = _FlowState(generation=self._generations.get(flow_id, 0))
            self._flows[flow_id] = state
        return state

    def enqueue(
        self,
        flow_id: str,
        *,
        seq: int,
        received_at: float,
        directed: bool,
    ) -> None:
        """将最新消息序号预留给下一次可执行快照。"""
        state = self._state_for(flow_id)
        state.pending_seq = max(state.pending_seq, int(seq))
        delay = self.direct_delay_seconds if directed else self.debounce_seconds
        candidate_due_at = float(received_at) + delay
        if state.global_due_at is None:
            state.global_due_at = candidate_due_at
        elif directed:
            state.global_due_at = min(state.global_due_at, candidate_due_at)
        if directed:
            if state.direct_due_at is None:
                state.direct_due_at = candidate_due_at
            else:
                state.direct_due_at = min(state.direct_due_at, candidate_due_at)

    def _eligible_at(self, state: _FlowState) -> float | None:
        """返回全局和定向启动路径中最早的可执行时间。"""
        candidates: list[float] = []
        if state.global_due_at is not None:
            global_at = state.global_due_at
            if state.last_started_at is not None:
                global_at = max(
                    global_at,
                    state.last_started_at + self.min_cycle_interval_seconds,
                )
            candidates.append(global_at)
        if state.direct_due_at is not None:
            direct_at = state.direct_due_at
            if state.last_started_at is not None:
                direct_at = max(
                    direct_at,
                    state.last_started_at
                    + self.direct_min_cycle_interval_seconds,
                )
            candidates.append(direct_at)
        return min(candidates) if candidates else None

    def include_pending_seq(self, flow_id: str, seq: int) -> bool:
        """只扩展已有待处理快照，不为内部事实创建新调度。"""
        state = self._flows.get(flow_id)
        if state is None or state.pending_seq <= 0:
            return False
        state.pending_seq = max(state.pending_seq, int(seq))
        return True

    def begin_if_due(self, flow_id: str, *, now: float) -> RunSnapshot | None:
        """群流水到期后，原子地占用当前待处理消息。"""
        state = self._flows.get(flow_id)
        if (
            state is None
            or state.active_run_id is not None
            or state.pending_seq <= 0
        ):
            return None

        eligible_at = self._eligible_at(state)
        if eligible_at is None or now < eligible_at:
            return None

        run_id = uuid.uuid4().hex
        snapshot = RunSnapshot(
            run_id=run_id,
            flow_id=flow_id,
            snapshot_seq=state.pending_seq,
            started_at=float(now),
            generation=state.generation,
        )
        # 重置后到达的新消息会保留为下一批待处理消息。
        state.pending_seq = 0
        state.global_due_at = None
        state.direct_due_at = None
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

    def finish_if_active(self, run_id: str, *, finished_at: float) -> bool:
        """释放仍有效的运行；已被 clear 失效的旧运行安全返回。"""
        flow_id = self._runs.pop(run_id, None)
        if flow_id is None:
            return False
        state = self._flows.get(flow_id)
        if state is None or state.active_run_id != run_id:
            return False
        state.active_run_id = None
        return True

    def is_run_current(
        self,
        run_id: str,
        *,
        flow_id: str,
        generation: int,
    ) -> bool:
        """校验运行仍属于当前 flow 世代且保持 active。"""
        state = self._flows.get(flow_id)
        return bool(
            state is not None
            and state.generation == int(generation)
            and state.active_run_id == run_id
            and self._runs.get(run_id) == flow_id
        )

    def clear_flow(self, flow_id: str) -> int:
        """失效指定 flow 的全部内存调度状态并推进世代。"""
        state = self._flows.pop(flow_id, None)
        if state is not None and state.active_run_id is not None:
            self._runs.pop(state.active_run_id, None)
        for run_id, run_flow_id in list(self._runs.items()):
            if run_flow_id == flow_id:
                self._runs.pop(run_id, None)
        generation = self._generations.get(flow_id, 0) + 1
        self._generations[flow_id] = generation
        return generation

    def next_due_at(self, flow_id: str) -> float | None:
        """结合聚合延迟和频率限制，返回下一次唤醒时间。"""
        state = self._flows.get(flow_id)
        if state is None:
            return None
        return self._eligible_at(state)
