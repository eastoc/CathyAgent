"""SessionStore：基于 SQLite 的会话持久化。

两张表：sessions / messages（不持久化 system 消息）。
所有写操作即时提交，避免崩溃丢数据。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
    content TEXT NOT NULL,
    tool_calls_json TEXT,
    tool_call_id TEXT,
    name TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, id);
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
            "(session_id, role, content, tool_calls_json, tool_call_id, name, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                message.role,
                message.content or "",
                json.dumps(message.tool_calls, ensure_ascii=False) if message.tool_calls else None,
                message.tool_call_id,
                message.name,
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
            "SELECT role, content, tool_calls_json, tool_call_id, name, created_at "
            "FROM messages WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
        out: list[Message] = []
        for r in rows:
            out.append(
                Message(
                    role=r["role"],
                    content=r["content"] or "",
                    tool_calls=json.loads(r["tool_calls_json"]) if r["tool_calls_json"] else None,
                    tool_call_id=r["tool_call_id"],
                    name=r["name"],
                    created_at=r["created_at"],
                )
            )
        return out

    # ---------- 杂项 ---------- #

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SessionStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
