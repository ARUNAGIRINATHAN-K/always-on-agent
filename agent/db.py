"""SQLite helpers shared by memory, tasks, schedules and checkpoints.

Every store opens short-lived connections through `connect()`. That keeps the
scheduler thread and the CLI from sharing a connection object, and WAL mode
plus a busy timeout lets them read and write the same file safely.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    user_id      TEXT NOT NULL,
    memory_key   TEXT NOT NULL,
    memory_value TEXT NOT NULL,
    source       TEXT NOT NULL,           -- 'user' (typed by a person) or 'agent' (proposed by the model)
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, memory_key)
);

CREATE TABLE IF NOT EXISTS tasks (
    id         TEXT PRIMARY KEY,         -- deterministic, so re-running a step upserts instead of duplicating
    thread_id  TEXT NOT NULL,
    title      TEXT NOT NULL,
    status     TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS schedules (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL UNIQUE,
    goal      TEXT NOT NULL,
    user_id   TEXT NOT NULL,
    cron      TEXT NOT NULL,             -- standard 5-field crontab, e.g. "0 9 * * *"
    enabled   INTEGER NOT NULL DEFAULT 1
);

-- One row per schedule per calendar day. A run claims its row before starting.
-- status: running | done | failed. A 'failed' run may be claimed again by a
-- later trigger the same day; 'running' (a crash) and 'done' never are.
CREATE TABLE IF NOT EXISTS schedule_runs (
    schedule_id INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
    run_date    TEXT NOT NULL,
    thread_id   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'running',
    PRIMARY KEY (schedule_id, run_date)
);
"""


def init_db(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
