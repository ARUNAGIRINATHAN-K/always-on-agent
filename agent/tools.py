"""The tools the agent can execute.

Every tool is:
  * declared with an explicit parameter list, so model-written arguments are
    checked before anything runs (unknown or missing arguments are rejected);
  * confined to the data directory (file names are validated, never paths);
  * idempotent, so a retried step does not duplicate its effect;
  * flagged `requires_approval` when it changes something a person would want
    to review. The graph pauses for a decision before those run.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .db import connect
from .memory import LongTermMemory

VALID_TASK_STATUSES = {"pending", "in_progress", "done"}
_NOTE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\.(md|txt)$")
MAX_CONTENT_CHARS = 20_000


class ToolInputError(ValueError):
    """Bad arguments from the planner. Not retried: the same call would fail again."""


@dataclass(frozen=True)
class ToolContext:
    notes_dir: Path
    db_path: Path
    user_id: str
    thread_id: str


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    params: dict[str, str]
    fn: Callable[..., str]
    requires_approval: bool = False
    max_chars: dict[str, int] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Tool implementations
# --------------------------------------------------------------------------- #

def _note_path(ctx: ToolContext, filename: str) -> Path:
    if not _NOTE_NAME_RE.match(filename):
        raise ToolInputError(
            "filename must be a plain name like 'report.md' (letters, digits, '._-', ending in .md or .txt)"
        )
    ctx.notes_dir.mkdir(parents=True, exist_ok=True)
    return ctx.notes_dir / filename


def create_note(ctx: ToolContext, filename: str, content: str) -> str:
    path = _note_path(ctx, filename)
    # Write to a temp file, then rename: a crash mid-write never leaves a half-written note.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    return f"Wrote note {path.name} ({len(content)} chars)"


def delete_note(ctx: ToolContext, filename: str) -> str:
    path = _note_path(ctx, filename)
    if not path.exists():
        return f"Note {path.name} already absent"
    path.unlink()
    return f"Deleted note {path.name}"


def save_task(ctx: ToolContext, title: str, status: str = "pending") -> str:
    title = title.strip()
    if not 1 <= len(title) <= 200:
        raise ToolInputError("title must be 1-200 characters")
    if status not in VALID_TASK_STATUSES:
        raise ToolInputError(f"status must be one of {sorted(VALID_TASK_STATUSES)}")
    # Same title -> same id, so repeating the step updates the row instead of adding one.
    task_id = hashlib.sha256(title.lower().encode("utf-8")).hexdigest()[:16]
    with connect(ctx.db_path) as conn:
        conn.execute(
            """
            INSERT INTO tasks (id, thread_id, title, status, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                status = excluded.status,
                thread_id = excluded.thread_id,
                updated_at = excluded.updated_at
            """,
            (task_id, ctx.thread_id, title, status),
        )
    return f"Task '{title}' is {status}"


def remember_preference(ctx: ToolContext, key: str, value: str) -> str:
    try:
        LongTermMemory(ctx.db_path).remember(ctx.user_id, key, value, source="agent")
    except ValueError as exc:
        raise ToolInputError(str(exc)) from exc
    return f"Saved preference '{key.strip().lower()}' (source: agent)"


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

TOOLS: dict[str, ToolSpec] = {
    "create_note": ToolSpec(
        name="create_note",
        description="Create or overwrite a markdown/text note in the notes folder.",
        params={"filename": "plain file name ending in .md or .txt", "content": "full text of the note"},
        fn=create_note,
        max_chars={"content": MAX_CONTENT_CHARS},
    ),
    "save_task": ToolSpec(
        name="save_task",
        description="Add a task to the task list, or update its status if a task with that title exists.",
        params={"title": "short task title", "status": "one of: pending, in_progress, done"},
        fn=save_task,
    ),
    "remember_preference": ToolSpec(
        name="remember_preference",
        description="Store a long-term preference about the user. Needs human approval.",
        params={"key": "short snake_case name, e.g. preferred_language", "value": "the preference"},
        fn=remember_preference,
        requires_approval=True,
    ),
    "delete_note": ToolSpec(
        name="delete_note",
        description="Delete a note from the notes folder. Needs human approval.",
        params={"filename": "plain file name of the note to delete"},
        fn=delete_note,
        requires_approval=True,
    ),
}


def describe_tools() -> str:
    lines = []
    for spec in TOOLS.values():
        args = ", ".join(f'{p} ({d})' for p, d in spec.params.items())
        note = " [requires approval]" if spec.requires_approval else ""
        lines.append(f"- {spec.name}({args}): {spec.description}{note}")
    return "\n".join(lines)


def validate_args(name: str, args: Any) -> dict[str, str]:
    if name not in TOOLS:
        raise ToolInputError(f"unknown tool '{name}'")
    if not isinstance(args, dict):
        raise ToolInputError("args must be a JSON object")
    spec = TOOLS[name]
    unknown = set(args) - set(spec.params)
    if unknown:
        raise ToolInputError(f"unknown argument(s) for {name}: {sorted(unknown)}")
    missing = [p for p in spec.params if p not in args and p not in _optional(spec)]
    if missing:
        raise ToolInputError(f"missing argument(s) for {name}: {missing}")
    clean: dict[str, str] = {}
    for key, value in args.items():
        if not isinstance(value, str):
            raise ToolInputError(f"argument '{key}' must be a string")
        limit = spec.max_chars.get(key, 500)
        if len(value) > limit:
            raise ToolInputError(f"argument '{key}' exceeds {limit} characters")
        clean[key] = value
    return clean


def _optional(spec: ToolSpec) -> set[str]:
    # Parameters with a Python default are optional for the planner.
    import inspect

    sig = inspect.signature(spec.fn)
    return {
        name
        for name, p in sig.parameters.items()
        if name != "ctx" and p.default is not inspect.Parameter.empty
    }


def run_tool(name: str, args: Any, ctx: ToolContext) -> str:
    clean = validate_args(name, args)
    return TOOLS[name].fn(ctx, **clean)


def list_tasks(db_path: Path) -> list[dict[str, str]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT title, status, updated_at FROM tasks ORDER BY updated_at DESC, title"
        ).fetchall()
    return [{"title": r[0], "status": r[1], "updated_at": r[2]} for r in rows]
