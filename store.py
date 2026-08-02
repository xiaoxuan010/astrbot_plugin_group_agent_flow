"""SQLite-backed pending buffer for autonomous group observations."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
_PENDING = "pending"
_INFLIGHT = "inflight"
_ACKED = "acked"
_REQUEUED = "requeued"


class StoreInitializationError(RuntimeError):
    """The plugin database cannot be initialized or is not supported."""


class BufferCapacityError(RuntimeError):
    """The configured pending buffer limit would be exceeded."""


class ActiveBatchError(RuntimeError):
    """A flow already has an unconfirmed batch."""


@dataclass(frozen=True, slots=True)
class AppendResult:
    """Result of inserting one pending event."""

    seq: int
    inserted: bool


@dataclass(frozen=True, slots=True)
class BatchSnapshot:
    """Immutable rows claimed for one autonomous Agent run."""

    batch_id: str
    flow_id: str
    snapshot_seq: int
    checkpoint_id: str
    checkpoint_baseline_count: int
    records: tuple[dict[str, Any], ...]


class GroupFlowStore:
    """Own the plugin's short-lived pending/inflight SQLite buffer."""

    def __init__(self, base_dir: Path, max_log_records: int = 0) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.base_dir / "group_agent_flow.db"
        self.max_log_records = max(0, int(max_log_records or 0))
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path,
            timeout=5.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.DatabaseError:
            connection.close()
            raise
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        try:
            with self._connection() as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version == 0:
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS flows (
                            flow_id TEXT PRIMARY KEY,
                            next_seq INTEGER NOT NULL DEFAULT 1,
                            updated_at INTEGER NOT NULL
                        );

                        CREATE TABLE IF NOT EXISTS buffer_batches (
                            batch_id TEXT PRIMARY KEY,
                            flow_id TEXT NOT NULL,
                            snapshot_seq INTEGER NOT NULL,
                            checkpoint_id TEXT NOT NULL,
                            checkpoint_baseline_count INTEGER NOT NULL DEFAULT 0,
                            state TEXT NOT NULL,
                            created_at INTEGER NOT NULL,
                            finished_at INTEGER,
                            FOREIGN KEY(flow_id) REFERENCES flows(flow_id)
                        );
                        CREATE INDEX IF NOT EXISTS idx_batches_flow_state
                            ON buffer_batches(flow_id, state);

                        CREATE TABLE IF NOT EXISTS buffer_messages (
                            flow_id TEXT NOT NULL,
                            seq INTEGER NOT NULL,
                            message_id TEXT,
                            status TEXT NOT NULL,
                            batch_id TEXT,
                            payload_json TEXT NOT NULL,
                            timestamp INTEGER NOT NULL DEFAULT 0,
                            sender_id TEXT NOT NULL DEFAULT '',
                            PRIMARY KEY(flow_id, seq),
                            FOREIGN KEY(flow_id) REFERENCES flows(flow_id)
                        );
                        CREATE INDEX IF NOT EXISTS idx_messages_flow_status_seq
                            ON buffer_messages(flow_id, status, seq);
                        CREATE INDEX IF NOT EXISTS idx_messages_flow_batch_seq
                            ON buffer_messages(flow_id, batch_id, seq);

                        CREATE TABLE IF NOT EXISTS message_keys (
                            flow_id TEXT NOT NULL,
                            message_id TEXT NOT NULL,
                            seq INTEGER NOT NULL,
                            first_seen_at INTEGER NOT NULL,
                            PRIMARY KEY(flow_id, message_id),
                            FOREIGN KEY(flow_id) REFERENCES flows(flow_id)
                        );

                        PRAGMA user_version=1;
                        """
                    )
                elif version != SCHEMA_VERSION:
                    raise StoreInitializationError(
                        f"unsupported group flow database schema: {version}"
                    )
                connection.execute(
                    """
                    DELETE FROM buffer_batches
                    WHERE state IN (?, ?) AND finished_at IS NOT NULL AND finished_at < ?
                    """,
                    (_ACKED, _REQUEUED, self._now() - 7 * 24 * 60 * 60),
                )
        except StoreInitializationError:
            raise
        except (OSError, sqlite3.DatabaseError) as exc:
            raise StoreInitializationError(
                f"failed to initialize group flow database: {type(exc).__name__}"
            ) from exc

    @staticmethod
    def _now() -> int:
        return int(time.time())

    @staticmethod
    def _flow_hash(flow_id: str) -> str:
        return hashlib.sha256(str(flow_id).encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _decode_payload(row: sqlite3.Row) -> dict[str, Any]:
        payload = json.loads(str(row["payload_json"]))
        if not isinstance(payload, dict):
            raise StoreInitializationError("buffer payload is not an object")
        result = dict(payload)
        result["seq"] = int(row["seq"])
        return result

    def _ensure_flow(self, connection: sqlite3.Connection, flow_id: str) -> None:
        connection.execute(
            """
            INSERT INTO flows(flow_id, next_seq, updated_at)
            VALUES (?, 1, ?)
            ON CONFLICT(flow_id) DO NOTHING
            """,
            (str(flow_id), self._now()),
        )

    def _active_count(self, connection: sqlite3.Connection, flow_id: str) -> int:
        row = connection.execute(
            "SELECT COUNT(*) FROM buffer_messages WHERE flow_id=?",
            (str(flow_id),),
        ).fetchone()
        return int(row[0])

    def append_pending(self, flow_id: str, record: dict[str, Any]) -> AppendResult:
        """Insert one group message into pending, deduplicating platform IDs."""
        flow_id = str(flow_id)
        if not isinstance(record, dict):
            raise TypeError("buffer record must be a mapping")
        record_kind = str(record.get("record_kind") or "group_message")
        if record_kind != "group_message":
            raise ValueError(
                f"SQLite Buffer accepts only group_message records, got {record_kind}"
            )
        message_id = str(record.get("message_id") or "")
        payload = json.dumps(record, ensure_ascii=False, sort_keys=True)
        timestamp = int(record.get("timestamp") or 0)
        sender_id = str(record.get("sender_id") or "")

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_flow(connection, flow_id)
                if message_id:
                    existing = connection.execute(
                        "SELECT seq FROM message_keys WHERE flow_id=? AND message_id=?",
                        (flow_id, message_id),
                    ).fetchone()
                    if existing is not None:
                        connection.commit()
                        return AppendResult(seq=int(existing[0]), inserted=False)

                if (
                    self.max_log_records > 0
                    and self._active_count(connection, flow_id)
                    >= self.max_log_records
                ):
                    raise BufferCapacityError(
                        f"group flow buffer is full: flow_id={flow_id} "
                        f"limit={self.max_log_records}"
                    )

                row = connection.execute(
                    "SELECT next_seq FROM flows WHERE flow_id=?",
                    (flow_id,),
                ).fetchone()
                seq = int(row[0])
                connection.execute(
                    "UPDATE flows SET next_seq=?, updated_at=? WHERE flow_id=?",
                    (seq + 1, self._now(), flow_id),
                )
                if message_id:
                    connection.execute(
                        """
                        INSERT INTO message_keys(flow_id, message_id, seq, first_seen_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (flow_id, message_id, seq, self._now()),
                    )
                connection.execute(
                    """
                    INSERT INTO buffer_messages(
                        flow_id, seq, message_id, status, batch_id,
                        payload_json, timestamp, sender_id
                    ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?)
                    """,
                    (
                        flow_id,
                        seq,
                        message_id or None,
                        _PENDING,
                        payload,
                        timestamp,
                        sender_id,
                    ),
                )
                connection.commit()
                return AppendResult(seq=seq, inserted=True)
            except Exception:
                connection.rollback()
                raise

    def append_record(self, flow_id: str, record: dict[str, Any]) -> int:
        """Compatibility wrapper for callers still using the old method name."""
        return self.append_pending(flow_id, record).seq

    def claim_batch(
        self,
        flow_id: str,
        snapshot_seq: int,
        *,
        checkpoint_id: str | None = None,
        checkpoint_baseline_count: int = 0,
    ) -> BatchSnapshot | None:
        """Atomically move pending rows up to the requested boundary to inflight."""
        flow_id = str(flow_id)
        requested_seq = int(snapshot_seq)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_flow(connection, flow_id)
                active = connection.execute(
                    """
                    SELECT batch_id FROM buffer_batches
                    WHERE flow_id=? AND state=?
                    LIMIT 1
                    """,
                    (flow_id, _INFLIGHT),
                ).fetchone()
                if active is not None:
                    raise ActiveBatchError(
                        f"flow already has active batch: {active['batch_id']}"
                    )
                rows = connection.execute(
                    """
                    SELECT * FROM buffer_messages
                    WHERE flow_id=? AND status=? AND seq<=?
                    ORDER BY seq
                    """,
                    (flow_id, _PENDING, requested_seq),
                ).fetchall()
                if not rows:
                    connection.commit()
                    return None

                last_seq = int(rows[-1]["seq"])
                batch_id = f"gaf-batch:{self._flow_hash(flow_id)}:{secrets.token_hex(8)}"
                expected_checkpoint = checkpoint_id or f"gaf:{batch_id}"
                baseline = max(0, int(checkpoint_baseline_count or 0))
                now = self._now()
                connection.execute(
                    """
                    INSERT INTO buffer_batches(
                        batch_id, flow_id, snapshot_seq, checkpoint_id,
                        checkpoint_baseline_count, state, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch_id,
                        flow_id,
                        last_seq,
                        expected_checkpoint,
                        baseline,
                        _INFLIGHT,
                        now,
                    ),
                )
                connection.execute(
                    """
                    UPDATE buffer_messages
                    SET status=?, batch_id=?
                    WHERE flow_id=? AND status=? AND seq<=?
                    """,
                    (_INFLIGHT, batch_id, flow_id, _PENDING, last_seq),
                )
                connection.commit()
                records = tuple(self._decode_payload(row) for row in rows)
                return BatchSnapshot(
                    batch_id=batch_id,
                    flow_id=flow_id,
                    snapshot_seq=last_seq,
                    checkpoint_id=expected_checkpoint,
                    checkpoint_baseline_count=baseline,
                    records=records,
                )
            except Exception:
                connection.rollback()
                raise

    def get_batch_records(self, batch_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM buffer_messages
                WHERE batch_id=? AND status=?
                ORDER BY seq
                """,
                (str(batch_id), _INFLIGHT),
            ).fetchall()
        return [self._decode_payload(row) for row in rows]

    def get_active_batch(self, flow_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM buffer_batches
                WHERE flow_id=? AND state=?
                ORDER BY created_at DESC LIMIT 1
                """,
                (str(flow_id), _INFLIGHT),
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def ack_batch(self, batch_id: str) -> bool:
        """Delete a batch's message bodies after Core checkpoint confirmation."""
        batch_id = str(batch_id)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT state FROM buffer_batches WHERE batch_id=?",
                    (batch_id,),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return False
                state = str(row[0])
                if state == _ACKED:
                    connection.commit()
                    return True
                if state != _INFLIGHT:
                    connection.commit()
                    return False
                now = self._now()
                connection.execute(
                    "DELETE FROM buffer_messages WHERE batch_id=? AND status=?",
                    (batch_id, _INFLIGHT),
                )
                connection.execute(
                    "UPDATE buffer_batches SET state=?, finished_at=? WHERE batch_id=?",
                    (_ACKED, now, batch_id),
                )
                connection.commit()
                return True
            except Exception:
                connection.rollback()
                raise

    def requeue_batch(self, batch_id: str) -> bool:
        """Return unconfirmed rows to pending without changing their seq."""
        batch_id = str(batch_id)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT state FROM buffer_batches WHERE batch_id=?",
                    (batch_id,),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return False
                state = str(row[0])
                if state == _REQUEUED:
                    connection.commit()
                    return True
                if state != _INFLIGHT:
                    connection.commit()
                    return False
                now = self._now()
                connection.execute(
                    """
                    UPDATE buffer_messages
                    SET status=?, batch_id=NULL
                    WHERE batch_id=? AND status=?
                    """,
                    (_PENDING, batch_id, _INFLIGHT),
                )
                connection.execute(
                    "UPDATE buffer_batches SET state=?, finished_at=? WHERE batch_id=?",
                    (_REQUEUED, now, batch_id),
                )
                connection.commit()
                return True
            except Exception:
                connection.rollback()
                raise

    def reconcile_inflight(
        self,
        flow_id: str,
        checkpoint_counts: dict[str, int],
        *,
        requeue_unconfirmed: bool = False,
    ) -> list[str]:
        """Ack batches with a newly appended checkpoint, optionally requeue the rest."""
        flow_id = str(flow_id)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT batch_id, checkpoint_id, checkpoint_baseline_count
                FROM buffer_batches
                WHERE flow_id=? AND state=?
                ORDER BY created_at
                """,
                (flow_id, _INFLIGHT),
            ).fetchall()
        acked: list[str] = []
        for row in rows:
            checkpoint_id = str(row["checkpoint_id"])
            observed = int(checkpoint_counts.get(checkpoint_id, 0) or 0)
            baseline = int(row["checkpoint_baseline_count"] or 0)
            if observed > baseline:
                if self.ack_batch(str(row["batch_id"])):
                    acked.append(str(row["batch_id"]))
            elif requeue_unconfirmed:
                self.requeue_batch(str(row["batch_id"]))
        return acked

    def read_records(self, flow_id: str) -> list[dict[str, Any]]:
        """Return only message bodies still owned by the pending buffer."""
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM buffer_messages
                WHERE flow_id=?
                ORDER BY seq
                """,
                (str(flow_id),),
            ).fetchall()
        return [self._decode_payload(row) for row in rows]

    def get_range(
        self,
        flow_id: str,
        start_seq: int,
        end_seq: int,
        *,
        batch_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["flow_id=?", "seq BETWEEN ? AND ?"]
        params: list[Any] = [str(flow_id), int(start_seq), int(end_seq)]
        if batch_id:
            clauses.append("batch_id=?")
            params.append(str(batch_id))
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM buffer_messages WHERE {' AND '.join(clauses)} ORDER BY seq",
                params,
            ).fetchall()
        return [self._decode_payload(row) for row in rows]

    def get_message(
        self,
        flow_id: str,
        message_id: str,
        *,
        snapshot_seq: int | None = None,
        batch_id: str | None = None,
    ) -> dict[str, Any] | None:
        clauses = ["flow_id=?", "message_id=?"]
        params: list[Any] = [str(flow_id), str(message_id)]
        if snapshot_seq is not None:
            clauses.append("seq<=?")
            params.append(int(snapshot_seq))
        if batch_id:
            clauses.append("batch_id=?")
            params.append(str(batch_id))
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT * FROM buffer_messages WHERE {' AND '.join(clauses)} LIMIT 1",
                params,
            ).fetchone()
        return self._decode_payload(row) if row is not None else None

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
        batch_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search only message bodies still in the short-lived buffer."""
        needle = str(query or "").casefold()
        sender = str(sender_id or "")
        source_records = (
            self.get_batch_records(batch_id)
            if batch_id
            else self.read_records(flow_id)
        )
        records = []
        for record in source_records:
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
            records.append(record)
        bounded_limit = max(1, min(int(limit or 20), 100))
        return records[-bounded_limit:]

    def stats(self, flow_id: str, conversation_id: str | None = None) -> dict[str, Any]:
        del conversation_id
        flow_id = str(flow_id)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT
                    SUM(CASE WHEN status=? THEN 1 ELSE 0 END) AS pending,
                    SUM(CASE WHEN status=? THEN 1 ELSE 0 END) AS inflight,
                    COUNT(*) AS records
                FROM buffer_messages WHERE flow_id=?
                """,
                (_PENDING, _INFLIGHT, flow_id),
            ).fetchone()
            flow = connection.execute(
                "SELECT next_seq FROM flows WHERE flow_id=?",
                (flow_id,),
            ).fetchone()
            batch = connection.execute(
                """
                SELECT batch_id FROM buffer_batches
                WHERE flow_id=? AND state=? LIMIT 1
                """,
                (flow_id, _INFLIGHT),
            ).fetchone()
        next_seq = int(flow[0]) if flow is not None else 1
        return {
            "records": int(row["records"] or 0),
            "pending": int(row["pending"] or 0),
            "inflight": int(row["inflight"] or 0),
            "latest_seq": max(0, next_seq - 1),
            "next_seq": next_seq,
            "active_batch": str(batch[0]) if batch is not None else None,
            "cursor": 0,
        }

    def clear_flow(self, flow_id: str) -> None:
        """Clear active buffer state while retaining the monotonic flow waterline."""
        flow_id = str(flow_id)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_flow(connection, flow_id)
                connection.execute(
                    "DELETE FROM buffer_messages WHERE flow_id=?", (flow_id,)
                )
                connection.execute("DELETE FROM message_keys WHERE flow_id=?", (flow_id,))
                connection.execute("DELETE FROM buffer_batches WHERE flow_id=?", (flow_id,))
                connection.execute(
                    "UPDATE flows SET updated_at=? WHERE flow_id=?",
                    (self._now(), flow_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
