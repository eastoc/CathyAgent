"""SessionStore：持久化会话、消息和后台工具任务。"""

from __future__ import annotations

import asyncio
import functools
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from ..contracts.agent import AgentEvent
from ..contracts.content import deserialize_content_blocks, serialize_content_blocks
from ..contracts.run import RunRecord
from ..contracts.tool import (
    ToolInvocation,
    ToolTaskRecord,
    tool_result_from_dict,
    tool_result_to_dict,
)
from .models import Message, Session, new_session_id

_SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    root_run_id TEXT NOT NULL,
    parent_run_id TEXT,
    session_id TEXT NOT NULL,
    episode_id TEXT,
    status TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    model_metadata_json TEXT NOT NULL DEFAULT '{}',
    context_json TEXT NOT NULL DEFAULT '{}',
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (parent_run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_runs_root ON runs (root_run_id, started_at);
CREATE INDEX IF NOT EXISTS idx_runs_session ON runs (session_id, started_at);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    timestamp REAL NOT NULL,
    payload_json TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (run_id) REFERENCES runs(run_id),
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    UNIQUE (run_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_events_run ON events (run_id, sequence);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    sha256 TEXT,
    mime_type TEXT,
    size_bytes INTEGER,
    uri TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS event_artifacts (
    event_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'attachment',
    PRIMARY KEY (event_id, artifact_id, role),
    FOREIGN KEY (event_id) REFERENCES events(event_id),
    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    tool_calls_json TEXT,
    tool_call_id TEXT,
    name TEXT,
    reasoning TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, id);

CREATE TABLE IF NOT EXISTS tool_tasks (
    task_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    provider TEXT,
    provider_call_id TEXT,
    provider_response_id TEXT,
    latest_response_id TEXT,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT,
    error_message TEXT,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    updated_at REAL NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_tool_tasks_run ON tool_tasks (run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tool_tasks_status ON tool_tasks (status, updated_at);
CREATE INDEX IF NOT EXISTS idx_tool_tasks_provider_call
    ON tool_tasks (provider, provider_call_id);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_RUN_TERMINAL_EVENTS = {
    "run_completed": "completed",
    "run_pending": "pending",
    "run_failed": "failed",
    "run_cancelled": "cancelled",
}


def _collect_artifact_records(value: Any) -> dict[str, dict[str, Any]]:
    """从事件 payload 中抽取完整附件描述或裸 artifact_id。"""

    found: dict[str, dict[str, Any]] = {}

    def visit(item: Any, *, role: str = "attachment") -> None:
        if isinstance(item, dict):
            artifact_id = item.get("artifact_id")
            if isinstance(artifact_id, str) and artifact_id:
                record = found.setdefault(artifact_id, {"role": role})
                for key in ("sha256", "mime_type", "size_bytes", "uri"):
                    if item.get(key) is not None:
                        record[key] = item[key]
                metadata = {
                    key: item[key]
                    for key in ("filename", "width", "height")
                    if item.get(key) is not None
                }
                if metadata:
                    record["metadata"] = metadata
            for key, nested in item.items():
                nested_role = "tool_output" if key == "artifacts" else role
                visit(nested, role=nested_role)
            return
        if isinstance(item, (list, tuple)):
            for nested in item:
                visit(nested, role=role)
            return
        if role == "tool_output" and isinstance(item, str) and item:
            found.setdefault(item, {"role": role})

    visit(value)
    return found


class SessionStore:
    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        current_version = int(
            self._conn.execute("PRAGMA user_version;").fetchone()[0]
        )
        if current_version > _SCHEMA_VERSION:
            self._conn.close()
            raise RuntimeError(
                f"数据库版本 {current_version} 高于当前支持版本 {_SCHEMA_VERSION}"
            )
        self._conn.executescript(_SCHEMA)
        self._conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION};")
        self._conn.commit()

    # ---------- Session CRUD ---------- #

    def create(self, session_id: str | None = None, metadata: dict | None = None) -> Session:
        sid = (session_id or new_session_id()).strip()
        now = _now_iso()
        meta = json.dumps(metadata or {}, ensure_ascii=False)
        try:
            self._conn.execute(
                "INSERT INTO sessions (id, created_at, updated_at, metadata_json) VALUES (?, ?, ?, ?)",
                (sid, now, now, meta),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"会话已存在: {sid}") from exc
        return Session(id=sid, created_at=now, updated_at=now, metadata=metadata or {}, messages=[])

    def load(self, session_id: str) -> Session | None:
        row = self._conn.execute(
            "SELECT id, created_at, updated_at, metadata_json FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        messages = self._load_messages(session_id)
        return Session(
            id=row["id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=json.loads(row["metadata_json"] or "{}"),
            messages=messages,
        )

    def get_or_create(self, session_id: str | None) -> Session:
        if session_id:
            existing = self.load(session_id)
            if existing is not None:
                return existing
            return self.create(session_id=session_id)
        return self.create()

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, created_at, updated_at, "
            "(SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS msg_count "
            "FROM sessions s ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------- Message CRUD ---------- #

    def append_message(self, session_id: str, message: Message) -> None:
        now = message.created_at or _now_iso()
        self._conn.execute(
            "INSERT INTO messages "
            "(session_id, role, content_json, metadata_json, tool_calls_json, "
            "tool_call_id, name, reasoning, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                message.role,
                json.dumps(
                    serialize_content_blocks(message.content),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                json.dumps(message.metadata, ensure_ascii=False, sort_keys=True),
                json.dumps(message.tool_calls, ensure_ascii=False) if message.tool_calls else None,
                message.tool_call_id,
                message.name,
                message.reasoning,
                now,
            ),
        )
        self._conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (now, session_id),
        )
        self._conn.commit()

    def _load_messages(self, session_id: str) -> list[Message]:
        rows = self._conn.execute(
            "SELECT role, content_json, metadata_json, tool_calls_json, "
            "tool_call_id, name, reasoning, created_at "
            "FROM messages WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
        out: list[Message] = []
        for r in rows:
            raw_content = json.loads(r["content_json"])
            if not isinstance(raw_content, list):
                raise ValueError("messages.content_json 必须是 JSON 数组")
            raw_metadata = json.loads(r["metadata_json"] or "{}")
            if not isinstance(raw_metadata, dict):
                raise ValueError("messages.metadata_json 必须是 JSON 对象")
            out.append(
                Message(
                    role=r["role"],
                    content=deserialize_content_blocks(raw_content),
                    metadata=raw_metadata,
                    tool_calls=json.loads(r["tool_calls_json"]) if r["tool_calls_json"] else None,
                    tool_call_id=r["tool_call_id"],
                    name=r["name"],
                    reasoning=r["reasoning"],
                    created_at=r["created_at"],
                )
            )
        return out

    # ---------- Run Journal ---------- #

    def create_run(self, run: RunRecord) -> None:
        try:
            self._conn.execute(
                "INSERT INTO runs "
                "(run_id, root_run_id, parent_run_id, session_id, episode_id, "
                "status, started_at, finished_at, model_metadata_json, context_json, "
                "schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.run_id,
                    run.root_run_id,
                    run.parent_run_id,
                    run.session_id,
                    run.episode_id,
                    run.status,
                    run.started_at,
                    run.finished_at,
                    json.dumps(run.model_metadata, ensure_ascii=False, sort_keys=True),
                    json.dumps(run.context, ensure_ascii=False, sort_keys=True),
                    run.schema_version,
                ),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            self._conn.rollback()
            raise ValueError(f"运行无法创建或已存在: {run.run_id}") from exc

    def append_event(self, event: AgentEvent) -> None:
        run_row = self._conn.execute(
            "SELECT session_id FROM runs WHERE run_id = ?",
            (event.run_id,),
        ).fetchone()
        if run_row is None:
            raise KeyError(f"运行不存在: {event.run_id}")
        if run_row["session_id"] != event.session_id:
            raise ValueError("事件 session_id 与运行不一致")
        if event.sequence <= 0:
            raise ValueError("事件 sequence 必须是正整数")

        payload = dict(event.payload)
        try:
            payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("AgentEvent.payload 必须可 JSON 序列化") from exc

        try:
            self._conn.execute(
                "INSERT INTO events "
                "(event_id, run_id, session_id, sequence, event_type, timestamp, "
                "payload_json, schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    event.run_id,
                    event.session_id,
                    event.sequence,
                    event.type,
                    event.timestamp,
                    payload_json,
                    event.schema_version,
                ),
            )
            for artifact_id, artifact in _collect_artifact_records(payload).items():
                sha256 = artifact.get("sha256")
                if sha256 is None and artifact_id.startswith("sha256:"):
                    sha256 = artifact_id[len("sha256:") :]
                self._conn.execute(
                    "INSERT INTO artifacts "
                    "(artifact_id, sha256, mime_type, size_bytes, uri, metadata_json, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(artifact_id) DO UPDATE SET "
                    "sha256 = COALESCE(excluded.sha256, artifacts.sha256), "
                    "mime_type = COALESCE(excluded.mime_type, artifacts.mime_type), "
                    "size_bytes = COALESCE(excluded.size_bytes, artifacts.size_bytes), "
                    "uri = COALESCE(excluded.uri, artifacts.uri), "
                    "metadata_json = CASE WHEN excluded.metadata_json = '{}' "
                    "THEN artifacts.metadata_json ELSE excluded.metadata_json END",
                    (
                        artifact_id,
                        sha256,
                        artifact.get("mime_type"),
                        artifact.get("size_bytes"),
                        artifact.get("uri"),
                        json.dumps(
                            artifact.get("metadata") or {},
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        event.timestamp,
                    ),
                )
                self._conn.execute(
                    "INSERT OR IGNORE INTO event_artifacts "
                    "(event_id, artifact_id, role) VALUES (?, ?, ?)",
                    (event.event_id, artifact_id, artifact.get("role") or "attachment"),
                )

            terminal_status = _RUN_TERMINAL_EVENTS.get(event.type)
            if terminal_status is not None:
                self._conn.execute(
                    "UPDATE runs SET status = ?, finished_at = ? WHERE run_id = ?",
                    (terminal_status, event.timestamp, event.run_id),
                )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            self._conn.rollback()
            raise ValueError(
                f"事件无法写入或顺序重复: {event.run_id}/{event.sequence}"
            ) from exc

    def load_run(self, run_id: str) -> RunRecord | None:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return self._run_from_row(row) if row is not None else None

    def list_runs(
        self,
        *,
        root_run_id: str | None = None,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[RunRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if root_run_id is not None:
            clauses.append("root_run_id = ?")
            params.append(root_run_id)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM runs{where} ORDER BY started_at ASC LIMIT ?",
            (*params, max(1, int(limit))),
        ).fetchall()
        return [self._run_from_row(row) for row in rows]

    def list_events(self, run_id: str) -> list[AgentEvent]:
        rows = self._conn.execute(
            "SELECT * FROM events WHERE run_id = ? ORDER BY sequence ASC",
            (run_id,),
        ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def list_root_run_ids(self, limit: int = 100) -> list[str]:
        rows = self._conn.execute(
            "SELECT root_run_id, MIN(started_at) AS first_started "
            "FROM runs GROUP BY root_run_id ORDER BY first_started ASC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
        return [str(row["root_run_id"]) for row in rows]

    def load_run_bundle(self, root_run_id: str) -> dict[str, Any] | None:
        runs = self.list_runs(root_run_id=root_run_id, limit=10000)
        if not runs:
            return None
        run_ids = [run.run_id for run in runs]
        placeholders = ", ".join("?" for _ in run_ids)
        event_rows = self._conn.execute(
            f"SELECT * FROM events WHERE run_id IN ({placeholders}) "
            "ORDER BY timestamp ASC, run_id ASC, sequence ASC",
            tuple(run_ids),
        ).fetchall()
        task_rows = self._conn.execute(
            f"SELECT * FROM tool_tasks WHERE run_id IN ({placeholders}) "
            "ORDER BY created_at ASC",
            tuple(run_ids),
        ).fetchall()
        artifact_rows = self._conn.execute(
            f"SELECT DISTINCT a.* FROM artifacts a "
            "JOIN event_artifacts ea ON ea.artifact_id = a.artifact_id "
            "JOIN events e ON e.event_id = ea.event_id "
            f"WHERE e.run_id IN ({placeholders}) ORDER BY a.created_at ASC",
            tuple(run_ids),
        ).fetchall()
        return {
            "schema": "cathy.raw-run.v1",
            "root_run_id": root_run_id,
            "runs": [self._run_to_dict(run) for run in runs],
            "events": [self._event_to_dict(self._event_from_row(row)) for row in event_rows],
            "tool_tasks": [
                self._tool_task_to_dict(self._tool_task_from_row(row))
                for row in task_rows
            ],
            "artifacts": [self._artifact_row_to_dict(row) for row in artifact_rows],
        }

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            run_id=row["run_id"],
            root_run_id=row["root_run_id"],
            parent_run_id=row["parent_run_id"],
            session_id=row["session_id"],
            episode_id=row["episode_id"],
            status=row["status"],
            started_at=float(row["started_at"]),
            finished_at=(
                float(row["finished_at"])
                if row["finished_at"] is not None
                else None
            ),
            model_metadata=json.loads(row["model_metadata_json"] or "{}"),
            context=json.loads(row["context_json"] or "{}"),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> AgentEvent:
        return AgentEvent(
            event_id=row["event_id"],
            type=row["event_type"],
            run_id=row["run_id"],
            session_id=row["session_id"],
            sequence=int(row["sequence"]),
            timestamp=float(row["timestamp"]),
            payload=json.loads(row["payload_json"] or "{}"),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    def _run_to_dict(run: RunRecord) -> dict[str, Any]:
        return {
            "run_id": run.run_id,
            "root_run_id": run.root_run_id,
            "parent_run_id": run.parent_run_id,
            "session_id": run.session_id,
            "episode_id": run.episode_id,
            "status": run.status,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "model_metadata": dict(run.model_metadata),
            "context": dict(run.context),
            "schema_version": run.schema_version,
        }

    @staticmethod
    def _event_to_dict(event: AgentEvent) -> dict[str, Any]:
        return {
            "event_id": event.event_id,
            "run_id": event.run_id,
            "session_id": event.session_id,
            "sequence": event.sequence,
            "type": event.type,
            "timestamp": event.timestamp,
            "payload": dict(event.payload),
            "schema_version": event.schema_version,
        }

    @staticmethod
    def _artifact_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "artifact_id": row["artifact_id"],
            "sha256": row["sha256"],
            "mime_type": row["mime_type"],
            "size_bytes": row["size_bytes"],
            "uri": row["uri"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "created_at": float(row["created_at"]),
        }

    # ---------- Background Tool Task CRUD ---------- #

    def create_tool_task(self, task: ToolTaskRecord) -> None:
        try:
            self._conn.execute(
                "INSERT INTO tool_tasks "
                "(task_id, run_id, session_id, provider, provider_call_id, "
                "provider_response_id, latest_response_id, tool_name, "
                "arguments_json, status, result_json, error_message, "
                "created_at, started_at, finished_at, updated_at, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                self._tool_task_values(task),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"工具任务无法创建或已存在: {task.task_id}") from exc

    def update_tool_task(self, task: ToolTaskRecord) -> None:
        values = self._tool_task_values(task)
        cursor = self._conn.execute(
            "UPDATE tool_tasks SET "
            "run_id = ?, session_id = ?, provider = ?, provider_call_id = ?, "
            "provider_response_id = ?, latest_response_id = ?, tool_name = ?, "
            "arguments_json = ?, status = ?, result_json = ?, error_message = ?, "
            "created_at = ?, started_at = ?, finished_at = ?, updated_at = ?, "
            "metadata_json = ? WHERE task_id = ?",
            (*values[1:], values[0]),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"工具任务不存在: {task.task_id}")
        self._conn.commit()

    def load_tool_task(self, task_id: str) -> ToolTaskRecord | None:
        row = self._conn.execute(
            "SELECT * FROM tool_tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return self._tool_task_from_row(row) if row is not None else None

    def list_tool_tasks(
        self,
        *,
        run_id: str | None = None,
        session_id: str | None = None,
        statuses: tuple[str, ...] | None = None,
        limit: int = 100,
    ) -> list[ToolTaskRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend(statuses)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM tool_tasks{where} "
            "ORDER BY created_at ASC LIMIT ?",
            (*params, max(1, int(limit))),
        ).fetchall()
        return [self._tool_task_from_row(row) for row in rows]

    @staticmethod
    def _tool_task_values(task: ToolTaskRecord) -> tuple[Any, ...]:
        return (
            task.task_id,
            task.run_id,
            task.session_id,
            task.provider,
            task.provider_call_id,
            task.provider_response_id,
            task.latest_response_id,
            task.invocation.name,
            json.dumps(task.invocation.arguments, ensure_ascii=False, sort_keys=True),
            task.status,
            (
                json.dumps(
                    tool_result_to_dict(task.result),
                    ensure_ascii=False,
                    sort_keys=True,
                )
                if task.result is not None
                else None
            ),
            task.error_message,
            task.created_at,
            task.started_at,
            task.finished_at,
            task.updated_at,
            json.dumps(task.metadata, ensure_ascii=False, sort_keys=True),
        )

    @staticmethod
    def _tool_task_from_row(row: sqlite3.Row) -> ToolTaskRecord:
        raw_result = json.loads(row["result_json"]) if row["result_json"] else None
        return ToolTaskRecord(
            task_id=row["task_id"],
            run_id=row["run_id"],
            session_id=row["session_id"],
            invocation=ToolInvocation(
                call_id=row["provider_call_id"] or row["task_id"],
                name=row["tool_name"],
                arguments=json.loads(row["arguments_json"] or "{}"),
            ),
            status=row["status"],
            provider=row["provider"],
            provider_call_id=row["provider_call_id"],
            provider_response_id=row["provider_response_id"],
            latest_response_id=row["latest_response_id"],
            result=(tool_result_from_dict(raw_result) if raw_result is not None else None),
            error_message=row["error_message"],
            created_at=float(row["created_at"]),
            started_at=(
                float(row["started_at"])
                if row["started_at"] is not None
                else None
            ),
            finished_at=(
                float(row["finished_at"])
                if row["finished_at"] is not None
                else None
            ),
            updated_at=float(row["updated_at"]),
            metadata=json.loads(row["metadata_json"] or "{}"),
        )

    @staticmethod
    def _tool_task_to_dict(task: ToolTaskRecord) -> dict[str, Any]:
        return {
            "task_id": task.task_id,
            "run_id": task.run_id,
            "session_id": task.session_id,
            "provider": task.provider,
            "provider_call_id": task.provider_call_id,
            "provider_response_id": task.provider_response_id,
            "latest_response_id": task.latest_response_id,
            "invocation": {
                "call_id": task.invocation.call_id,
                "name": task.invocation.name,
                "arguments": dict(task.invocation.arguments),
            },
            "status": task.status,
            "result": (
                tool_result_to_dict(task.result) if task.result is not None else None
            ),
            "error_message": task.error_message,
            "created_at": task.created_at,
            "started_at": task.started_at,
            "finished_at": task.finished_at,
            "updated_at": task.updated_at,
            "metadata": dict(task.metadata),
        }

    # ---------- 杂项 ---------- #

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SessionStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


_T = TypeVar("_T")


class AsyncSessionStore:
    """单线程所有权的异步 SessionStore。

    SQLite 连接在专属 worker 线程中创建、使用和关闭；所有操作提交到同一个
    executor，因此不会阻塞 Agent 事件循环，也不会跨线程使用 sqlite3 连接。
    """

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="cathy-session-store",
        )
        self._store: SessionStore | None = None
        self._closed = False

    @classmethod
    async def open(cls, db_path: Path | str) -> "AsyncSessionStore":
        instance = cls(db_path)
        await instance._initialize()
        return instance

    async def _initialize(self) -> None:
        if self._store is not None:
            return
        loop = asyncio.get_running_loop()
        self._store = await loop.run_in_executor(
            self._executor,
            SessionStore,
            self._db_path,
        )

    async def _call(self, method_name: str, *args: Any, **kwargs: Any) -> _T:
        if self._closed:
            raise RuntimeError("AsyncSessionStore 已关闭")
        await self._initialize()
        assert self._store is not None
        method = getattr(self._store, method_name)
        call = functools.partial(method, *args, **kwargs)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, call)

    async def acreate(
        self,
        session_id: str | None = None,
        metadata: dict | None = None,
    ) -> Session:
        return await self._call("create", session_id, metadata)

    async def aload(self, session_id: str) -> Session | None:
        return await self._call("load", session_id)

    async def aget_or_create(self, session_id: str | None) -> Session:
        return await self._call("get_or_create", session_id)

    async def alist_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        return await self._call("list_sessions", limit)

    async def aappend_message(self, session_id: str, message: Message) -> None:
        await self._call("append_message", session_id, message)

    async def acreate_run(self, run: RunRecord) -> None:
        await self._call("create_run", run)

    async def aappend_event(self, event: AgentEvent) -> None:
        await self._call("append_event", event)

    async def aload_run(self, run_id: str) -> RunRecord | None:
        return await self._call("load_run", run_id)

    async def alist_runs(
        self,
        *,
        root_run_id: str | None = None,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[RunRecord]:
        return await self._call(
            "list_runs",
            root_run_id=root_run_id,
            session_id=session_id,
            limit=limit,
        )

    async def alist_events(self, run_id: str) -> list[AgentEvent]:
        return await self._call("list_events", run_id)

    async def alist_root_run_ids(self, limit: int = 100) -> list[str]:
        return await self._call("list_root_run_ids", limit)

    async def aload_run_bundle(self, root_run_id: str) -> dict[str, Any] | None:
        return await self._call("load_run_bundle", root_run_id)

    async def acreate_tool_task(self, task: ToolTaskRecord) -> None:
        await self._call("create_tool_task", task)

    async def aupdate_tool_task(self, task: ToolTaskRecord) -> None:
        await self._call("update_tool_task", task)

    async def aload_tool_task(self, task_id: str) -> ToolTaskRecord | None:
        return await self._call("load_tool_task", task_id)

    async def alist_tool_tasks(
        self,
        *,
        run_id: str | None = None,
        session_id: str | None = None,
        statuses: tuple[str, ...] | None = None,
        limit: int = 100,
    ) -> list[ToolTaskRecord]:
        return await self._call(
            "list_tool_tasks",
            run_id=run_id,
            session_id=session_id,
            statuses=statuses,
            limit=limit,
        )

    async def aclose(self) -> None:
        if self._closed:
            return
        if self._store is not None:
            await self._call("close")
            self._store = None
        self._closed = True
        self._executor.shutdown(wait=True)

    async def __aenter__(self) -> "AsyncSessionStore":
        await self._initialize()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()
