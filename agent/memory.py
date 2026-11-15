"""Long-term memory: facts that outlive any single run or thread.

Each memory records where it came from. Entries typed by a person are
'user'; entries the model proposed through a tool are 'agent'. Both are
visible and deletable through the CLI, so the model's guesses are never
mixed in silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .db import connect, init_db

_KEY_RE = re.compile(r"^[a-z0-9_.-]{1,64}$")
MAX_VALUE_CHARS = 500


@dataclass(frozen=True)
class Memory:
    key: str
    value: str
    source: str
    updated_at: str


class LongTermMemory:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        init_db(db_path)

    def remember(self, user_id: str, key: str, value: str, source: str = "user") -> None:
        if source not in {"user", "agent"}:
            raise ValueError("source must be 'user' or 'agent'")
        key = key.strip().lower()
        value = value.strip()
        if not _KEY_RE.match(key):
            raise ValueError("memory key must be 1-64 chars of a-z, 0-9, '_', '.', '-'")
        if not value:
            raise ValueError("memory value must not be empty")
        if len(value) > MAX_VALUE_CHARS:
            raise ValueError(f"memory value must be at most {MAX_VALUE_CHARS} characters")
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO memories (user_id, memory_key, memory_value, source, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(user_id, memory_key) DO UPDATE SET
                    memory_value = excluded.memory_value,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (user_id, key, value, source),
            )

    def recall(self, user_id: str) -> dict[str, str]:
        return {m.key: m.value for m in self.list(user_id)}

    def list(self, user_id: str) -> list[Memory]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT memory_key, memory_value, source, updated_at
                FROM memories WHERE user_id = ? ORDER BY memory_key
                """,
                (user_id,),
            ).fetchall()
        return [Memory(key=r[0], value=r[1], source=r[2], updated_at=r[3]) for r in rows]

    def forget(self, user_id: str, key: str) -> bool:
        with connect(self.db_path) as conn:
            cur = conn.execute(
                "DELETE FROM memories WHERE user_id = ? AND memory_key = ?",
                (user_id, key.strip().lower()),
            )
            return cur.rowcount > 0
