# Security Model, Safety Policies & Limitations

This document outlines the sandboxing guarantees, security boundaries, and known engineering limitations of the Always-On Planning Agent.

---

## Security Model & Sandboxing

### 1. Allowlisted Tools Only
The planner has access **strictly** to the functions declared in `agent/tools.py`.
- There is no shell execution tool (`bash`, `sh`, `cmd`, `powershell`).
- There is no raw network client (`curl`, `requests`) unless explicitly authored as a scoped, allowlisted tool.
- The model cannot invent tools or access arbitrary system libraries.

### 2. Path Traversal Defense
File-based tools validate inputs against a strict regular expression:
```python
_NOTE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\.(md|txt)$")
```
- Path traversal sequences (`../`, `..\`) are rejected immediately.
- Subdirectories (`sub/doc.md`) and hidden dotfiles (`.secret.md`) are disallowed.
- Notes are strictly confined to `data/notes/`.

### 3. Atomic File Writes
`create_note` writes data to a temporary file (`.tmp`) and swaps it into place using `os.replace`:
```python
tmp = path.with_suffix(path.suffix + ".tmp")
tmp.write_text(content, encoding="utf-8")
os.replace(tmp, path)
```
Even if power is lost mid-write, the host filesystem is never left with a partially written note.

### 4. Unattended Cron Policy
Scheduled background jobs (via APScheduler) run with `unattended=True`:
- If a scheduled job's plan contains any step with `requires_approval=True` (e.g., `delete_note`), the agent **fails fast** and rejects the plan immediately.
- It will never hang in the background waiting for a terminal input that nobody is present to answer.

---

## Known Engineering Limitations & Trade-Offs

### 1. Python Thread Cancellation
In standard CPython, there is no safe mechanism to preemptively terminate an active worker thread.
- If a tool exceeds `AGENT_TOOL_TIMEOUT`, the orchestrator marks the step as timed out and continues.
- However, the background thread may continue executing until completion.
- **Why this matters**: Every tool you author must be idempotent so that late completions do not corrupt state.

### 2. Single-Node Concurrency
SQLite with Write-Ahead Logging (`WAL`) allows concurrent readers with one serialized writer.
- [AgentService](file:///b:/My%20Project/always-on-agent/agent/service.py) coordinates writes using a process-level `threading.RLock`.
- Do not run two separate scheduler processes against the same SQLite data directory simultaneously, as they would race to claim daily schedule triggers.
- For multi-node distributed swarms, migrate the state storage layer to PostgreSQL.
