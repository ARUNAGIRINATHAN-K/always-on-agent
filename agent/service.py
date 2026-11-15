"""High-level API: start a run, pause for approval, resume, and inspect memory/tasks.

One AgentService owns one checkpoint connection and one compiled graph.
Graph invocations are serialized with a lock, so a scheduled job and a CLI
call in the same process cannot interleave writes on the same thread.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .config import Settings
from .db import init_db
from .graph import build_graph
from .memory import LongTermMemory


@dataclass
class RunResult:
    thread_id: str
    status: str
    plan: list[dict[str, Any]] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    pending_approval: dict[str, Any] | None = None

    @property
    def awaiting_approval(self) -> bool:
        return self.pending_approval is not None


def make_llm(settings: Settings):
    from langchain_ollama import ChatOllama

    return ChatOllama(
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
        temperature=0,
        client_kwargs={"timeout": settings.llm_timeout_s},
    )


class AgentService:
    def __init__(self, settings: Settings, llm: Any | None = None):
        self.settings = settings
        init_db(settings.db_path)
        self.memory = LongTermMemory(settings.db_path)
        self._conn = sqlite3.connect(settings.db_path, check_same_thread=False, timeout=10)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._checkpointer = SqliteSaver(self._conn)
        self._graph = build_graph(
            llm=llm if llm is not None else make_llm(settings),
            settings=settings,
            checkpointer=self._checkpointer,
        )
        self._lock = threading.RLock()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------ runs

    def run(
        self,
        goal: str,
        user_id: str = "default",
        *,
        thread_id: str | None = None,
        unattended: bool = False,
    ) -> RunResult:
        goal = goal.strip()
        if not goal:
            raise ValueError("goal must not be empty")
        thread_id = thread_id or f"run-{uuid.uuid4().hex[:12]}"
        payload = {"goal": goal, "user_id": user_id, "thread_id": thread_id}
        config = {"configurable": {"thread_id": thread_id, "unattended": unattended}}
        with self._lock:
            out = self._graph.invoke(payload, config)
        return self._to_result(thread_id, out)

    def resume(self, thread_id: str, decision: str, *, unattended: bool = False) -> RunResult:
        config = {"configurable": {"thread_id": thread_id, "unattended": unattended}}
        with self._lock:
            out = self._graph.invoke(Command(resume=decision), config)
        return self._to_result(thread_id, out)

    def _to_result(self, thread_id: str, out: dict[str, Any]) -> RunResult:
        interrupts = out.get("__interrupt__") or []
        pending = interrupts[0].value if interrupts else None
        return RunResult(
            thread_id=thread_id,
            status="awaiting_approval" if pending else out.get("status", "unknown"),
            plan=out.get("plan", []),
            results=out.get("results", []),
            error=out.get("error"),
            pending_approval=pending,
        )
