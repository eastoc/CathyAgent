"""SessionStore：持久化会话、消息和后台工具任务。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..contracts.content import deserialize_content_blocks, serialize_content_blocks
from ..contracts.tool import (
    ToolInvocation,
    ToolTaskRecord,
    tool_result_from_dict,
    tool_result_to_dict,
)
from .models import Message, Session, new_session_id

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
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


class SessionStore:
    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._conn.executescript(_SCHEMA)
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

    # ---------- 杂项 ---------- #

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SessionStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
