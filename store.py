"""群聊上下文流水的本地持久化存储。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any


class GroupFlowStore:
    """无需数据库，按群持久化事件日志和共享运行元数据。"""

    def __init__(self, base_dir: Path, max_log_records: int = 5000) -> None:
        """创建存储目录，并配置每条群流水的日志保留上限。"""
        self.base_dir = base_dir
        self.logs_dir = base_dir / "logs"
        self.state_path = base_dir / "state.json"
        self.migration_path = base_dir / "legacy_migration.json"
        self.max_log_records = max(0, int(max_log_records or 0))
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def _flow_hash(self, flow_id: str) -> str:
        """将平台流水标识映射为文件系统安全的稳定键。"""
        return hashlib.sha256(flow_id.encode("utf-8")).hexdigest()[:32]

    def _log_path(self, flow_id: str) -> Path:
        """返回分配给指定群流水的 JSONL 路径。"""
        return self.logs_dir / f"{self._flow_hash(flow_id)}.jsonl"

    def _cursor_key(self, flow_id: str, conversation_id: str) -> str:
        """将 cursor 和 renderer 分配限定在单个会话内。"""
        return f"{self._flow_hash(flow_id)}:{conversation_id}"

    def _read_json_file(self, path: Path, default: Any) -> Any:
        """读取 JSON；文件缺失或损坏时返回安全默认值。"""
        if not path.is_file():
            return default
        try:
            with path.open("r", encoding="utf-8") as file:
                return json.load(file)
        except (OSError, json.JSONDecodeError):
            return default

    def _write_json_file(self, path: Path, payload: Any) -> None:
        """通过同目录临时文件原子替换 JSON 文件。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp_path, path)

    def read_records(self, flow_id: str) -> list[dict[str, Any]]:
        """读取有效对象记录，并跳过损坏的 JSONL 行。"""
        path = self._log_path(flow_id)
        if not path.is_file():
            return []
        records: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    records.append(item)
        return records

    def write_records(self, flow_id: str, records: list[dict[str, Any]]) -> None:
        """原子重写指定流水保留的 JSONL 记录。"""
        path = self._log_path(flow_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as file:
            for record in records:
                file.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
                file.write("\n")
        os.replace(tmp_path, path)

    def append_record(self, flow_id: str, record: dict[str, Any]) -> int:
        """按消息 ID 去重、分配序号并执行保留上限。"""
        records = self.read_records(flow_id)
        message_id = str(record.get("message_id") or "")
        if message_id:
            for existing in records:
                if str(existing.get("message_id") or "") == message_id:
                    return int(existing.get("seq") or 0)

        last_seq = max((int(item.get("seq") or 0) for item in records), default=0)
        seq = last_seq + 1
        record["seq"] = seq
        records.append(record)

        if self.max_log_records > 0 and len(records) > self.max_log_records:
            records = records[-self.max_log_records :]

        self.write_records(flow_id, records)
        return seq

    def find_seq_by_message_id(self, flow_id: str, message_id: str) -> int | None:
        """查找平台消息 ID 对应的持久化序号。"""
        if not message_id:
            return None
        for record in self.read_records(flow_id):
            if str(record.get("message_id") or "") == message_id:
                return int(record.get("seq") or 0)
        return None

    def get_range(self, flow_id: str, start_seq: int, end_seq: int) -> list[dict[str, Any]]:
        """按持久化顺序返回包含首尾的序号区间。"""
        if end_seq < start_seq:
            return []
        return [
            record
            for record in self.read_records(flow_id)
            if start_seq <= int(record.get("seq") or 0) <= end_seq
        ]

    def get_message(self, flow_id: str, message_id: str) -> dict[str, Any] | None:
        """按平台消息 ID 查找一条持久化消息。"""
        target = str(message_id or "")
        for record in self.read_records(flow_id):
            if str(record.get("message_id") or "") == target:
                return record
        return None

    def search_records(
        self,
        flow_id: str,
        *,
        query: str = "",
        sender_id: str = "",
        since: int | None = None,
        until: int | None = None,
        max_seq: int | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """筛选本地历史，并返回受数量限制的最新匹配结果。"""
        needle = str(query or "").casefold()
        sender = str(sender_id or "")
        matches = []
        for record in self.read_records(flow_id):
            timestamp = int(record.get("timestamp") or 0)
            if max_seq is not None and int(record.get("seq") or 0) > int(max_seq):
                continue
            if needle and needle not in str(record.get("text") or "").casefold():
                continue
            if sender and str(record.get("sender_id") or "") != sender:
                continue
            if since is not None and timestamp < int(since):
                continue
            if until is not None and timestamp > int(until):
                continue
            matches.append(record)
        bounded_limit = max(1, min(int(limit or 20), 100))
        return matches[-bounded_limit:]

    def read_state(self) -> dict[str, Any]:
        """以对象形式读取共享状态文档。"""
        state = self._read_json_file(self.state_path, {})
        return state if isinstance(state, dict) else {}

    def write_state(self, state: dict[str, Any]) -> None:
        """原子持久化共享状态文档。"""
        self._write_json_file(self.state_path, state)

    def record_run_outcome(
        self,
        run_id: str,
        *,
        flow_id: str,
        snapshot_seq: int,
        outcome: str,
        detail: str = "",
    ) -> bool:
        """持久化结果，并允许同一轮次随 tool batch 进展更新。"""
        state = self.read_state()
        outcomes = state.setdefault("run_outcomes", {})
        if not isinstance(outcomes, dict):
            outcomes = {}
            state["run_outcomes"] = outcomes
        recorded_at = int(time.time())
        existing = outcomes.get(run_id)
        if existing is not None:
            if not isinstance(existing, dict):
                return False
            try:
                same_snapshot = int(existing.get("snapshot_seq") or 0) == int(
                    snapshot_seq
                )
            except (TypeError, ValueError):
                same_snapshot = False
            if str(existing.get("flow_id") or "") != str(flow_id) or not same_snapshot:
                return False
            recorded_at = int(existing.get("recorded_at") or recorded_at)
        outcomes[run_id] = {
            "flow_id": flow_id,
            "snapshot_seq": int(snapshot_seq),
            "outcome": str(outcome),
            "detail": str(detail),
            "recorded_at": recorded_at,
        }
        self.write_state(state)
        return True

    def get_run_outcome(self, run_id: str) -> dict[str, Any] | None:
        """返回观察轮次经过分类的最终结果。"""
        outcomes = self.read_state().get("run_outcomes", {})
        if not isinstance(outcomes, dict):
            return None
        outcome = outcomes.get(run_id)
        return outcome if isinstance(outcome, dict) else None

    def import_legacy_data(self, legacy_dir: Path) -> bool:
        """在保留源目录的前提下，一次性复制旧插件日志和状态。"""
        legacy_dir = Path(legacy_dir)
        if self.migration_path.exists() or not legacy_dir.is_dir():
            return False
        legacy_logs = legacy_dir / "logs"
        if legacy_logs.is_dir():
            for source in legacy_logs.glob("*.jsonl"):
                target = self.logs_dir / source.name
                if not target.exists():
                    shutil.copy2(source, target)
        legacy_state = legacy_dir / "state.json"
        if legacy_state.is_file() and not self.state_path.exists():
            shutil.copy2(legacy_state, self.state_path)
        self._write_json_file(
            self.migration_path,
            {"source": str(legacy_dir), "migrated_at": int(time.time())},
        )
        return True

    def get_cursor(self, flow_id: str, conversation_id: str) -> int:
        """返回指定会话最后消费的快照序号。"""
        state = self.read_state()
        cursors = state.get("cursors", {})
        if not isinstance(cursors, dict):
            return 0
        value = cursors.get(self._cursor_key(flow_id, conversation_id), 0)
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def set_cursor(
        self,
        flow_id: str,
        conversation_id: str,
        seq: int,
        *,
        unified_msg_origin: str,
    ) -> None:
        """提交会话 cursor，并保留可读的路由元数据。"""
        state = self.read_state()
        self._apply_cursor_state(
            state,
            flow_id,
            conversation_id,
            seq,
            unified_msg_origin=unified_msg_origin,
        )
        self.write_state(state)

    def _apply_cursor_state(
        self,
        state: dict[str, Any],
        flow_id: str,
        conversation_id: str,
        seq: int,
        *,
        unified_msg_origin: str,
    ) -> None:
        """在现有 state 对象中单调更新 cursor 与路由元数据。"""
        cursors = state.setdefault("cursors", {})
        cursor_meta = state.setdefault("cursor_meta", {})
        if not isinstance(cursors, dict):
            cursors = {}
            state["cursors"] = cursors
        if not isinstance(cursor_meta, dict):
            cursor_meta = {}
            state["cursor_meta"] = cursor_meta

        key = self._cursor_key(flow_id, conversation_id)
        try:
            current = int(cursors.get(key) or 0)
        except (TypeError, ValueError):
            current = 0
        cursors[key] = max(current, 0, int(seq or 0))
        cursor_meta[key] = {
            "flow_id": flow_id,
            "conversation_id": conversation_id,
            "unified_msg_origin": unified_msg_origin,
        }

    def get_or_assign_renderer(
        self, flow_id: str, conversation_id: str, default_renderer: str
    ) -> str:
        """在会话生命周期内固定使用同一个 renderer。"""
        state = self.read_state()
        assignments = state.setdefault("renderer_assignments", {})
        if not isinstance(assignments, dict):
            assignments = {}
            state["renderer_assignments"] = assignments
        key = self._cursor_key(flow_id, conversation_id)
        assigned = str(assignments.get(key) or "")
        if assigned:
            return assigned
        assigned = str(default_renderer)
        assignments[key] = assigned
        self.write_state(state)
        return assigned

    @staticmethod
    def _normalize_observation_window(value: Any) -> dict[str, Any] | None:
        """校验活动窗口结构，并返回与持久化对象解耦的副本。"""
        if not isinstance(value, dict):
            return None
        renderer = str(value.get("renderer") or "")
        raw_blocks = value.get("blocks")
        if not renderer or not isinstance(raw_blocks, list):
            return None

        blocks: list[dict[str, int]] = []
        previous_end = 0
        for raw_block in raw_blocks:
            if not isinstance(raw_block, dict):
                return None
            try:
                start_seq = int(raw_block.get("start_seq") or 0)
                end_seq = int(raw_block.get("end_seq") or 0)
            except (TypeError, ValueError):
                return None
            if start_seq <= 0 or end_seq < start_seq or start_seq <= previous_end:
                return None
            blocks.append({"start_seq": start_seq, "end_seq": end_seq})
            previous_end = end_seq
        return {"renderer": renderer, "blocks": blocks}

    def get_observation_window(
        self, flow_id: str, conversation_id: str
    ) -> dict[str, Any] | None:
        """返回会话当前活动 observation 块窗口。"""
        windows = self.read_state().get("observation_windows", {})
        if not isinstance(windows, dict):
            return None
        return self._normalize_observation_window(
            windows.get(self._cursor_key(flow_id, conversation_id))
        )

    def set_observation_window(
        self,
        flow_id: str,
        conversation_id: str,
        *,
        renderer_name: str,
        blocks: list[dict[str, int]],
    ) -> None:
        """持久化会话活动窗口的稳定块边界。"""
        normalized = self._normalize_observation_window(
            {"renderer": renderer_name, "blocks": blocks}
        )
        if normalized is None:
            raise ValueError("invalid observation window")
        state = self.read_state()
        windows = state.setdefault("observation_windows", {})
        if not isinstance(windows, dict):
            windows = {}
            state["observation_windows"] = windows
        windows[self._cursor_key(flow_id, conversation_id)] = normalized
        self.write_state(state)

    def commit_observation(
        self,
        flow_id: str,
        conversation_id: str,
        seq: int,
        *,
        unified_msg_origin: str,
        renderer_name: str,
        blocks: list[dict[str, int]],
    ) -> None:
        """在一次 state 文件替换中提交 cursor 与活动窗口。"""
        normalized = self._normalize_observation_window(
            {"renderer": renderer_name, "blocks": blocks}
        )
        if normalized is None:
            raise ValueError("invalid observation window")
        state = self.read_state()
        self._apply_cursor_state(
            state,
            flow_id,
            conversation_id,
            seq,
            unified_msg_origin=unified_msg_origin,
        )
        windows = state.setdefault("observation_windows", {})
        if not isinstance(windows, dict):
            windows = {}
            state["observation_windows"] = windows
        windows[self._cursor_key(flow_id, conversation_id)] = normalized
        self.write_state(state)

    def clear_flow(self, flow_id: str) -> None:
        """清除指定流水的日志与全部会话运行状态。"""
        path = self._log_path(flow_id)
        if path.exists():
            path.unlink()

        flow_hash = self._flow_hash(flow_id)
        state = self.read_state()
        for section_name in (
            "cursors",
            "cursor_meta",
            "renderer_assignments",
            "observation_windows",
        ):
            section = state.get(section_name, {})
            if isinstance(section, dict):
                for key in list(section.keys()):
                    if str(key).startswith(f"{flow_hash}:"):
                        section.pop(key, None)
        outcomes = state.get("run_outcomes", {})
        if isinstance(outcomes, dict):
            for run_id, outcome in list(outcomes.items()):
                if (
                    isinstance(outcome, dict)
                    and str(outcome.get("flow_id") or "") == flow_id
                ):
                    outcomes.pop(run_id, None)
        self.write_state(state)

    def stats(self, flow_id: str, conversation_id: str | None = None) -> dict[str, int]:
        """返回记录数、最新序号和 cursor 等轻量统计。"""
        records = self.read_records(flow_id)
        latest_seq = max((int(item.get("seq") or 0) for item in records), default=0)
        cursor = self.get_cursor(flow_id, conversation_id) if conversation_id else 0
        return {
            "records": len(records),
            "latest_seq": latest_seq,
            "cursor": cursor,
        }
