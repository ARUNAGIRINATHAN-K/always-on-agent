# Always On Planning Agent
<h3>
  Stop building toy AI agents that crash and repeat steps.
</h3>

<img src="docs/img/top.png" width="100%">

<br>

<p>
  <strong>Always-On Agent</strong> is a local-first, crash-resilient autonomous agent. It recalls what it knows about you, generates a plan, <strong>pauses for your approval before anything touches disk</strong>, executes approved steps with retries, and <strong>saves progress to SQLite so a restart never repeats finished work.</strong>
</p>

<p>
  <em>Zero cloud reliance. Zero API keys. Zero unreviewed writes.</em>
</p>

---

## Quick Start (Under 60 Seconds)

### Option A: Local Python

**Prerequisites:** Python 3.11+ and [Ollama](https://ollama.com/download) installed.

```bash
ollama pull llama3.2
git clone https://github.com/ARUNAGIRINATHAN-K/always-on-agent.git && cd always-on-agent
python -m venv .venv && source .venv/bin/activate  # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
# Give it a goal!
python -m agent.cli run "Prepare a weekly AI research report"
```
### Option B: One-Command Docker Compose

Start Ollama and the Always-On Agent daemon together:

```bash
docker compose up -d
docker compose exec ollama ollama pull llama3.2
docker compose logs -f agent
```

---

## Experience It in the Terminal

### 1. The Agent Plans & Pauses Cleanly
When you run a goal, the agent plans and halts safely before executing side effects:

```text
$ python -m agent.cli run "Prepare a weekly AI research report"

thread: run-6a781db6b822
status: awaiting_approval

plan:
  s1. [create_note] Write the complete research report note.
  s2. [save_task] Add a task to review the draft.

waiting for approval:
  Approve plan with 2 steps?
  approve: python -m agent.cli resume run-6a781db6b822 approve
  reject:  python -m agent.cli resume run-6a781db6b822 reject
```

### 2. If You Reject &rarr; Instant Safe Abort
```bash
$ python -m agent.cli resume run-6a781db6b822 reject

thread: run-6a781db6b822
status: rejected

plan was rejected; no steps were executed
```

### 3. If You Approve &rarr; Sequential Execution & Checkpointing
```bash
$ python -m agent.cli resume run-6a781db6b822 approve

thread: run-6a781db6b822
status: completed

results:
  s1 ok       Wrote note weekly_ai_report.md (1842 chars)
  s2 ok       Task 'Review weekly AI report' is pending
```

---

## Why This Architecture Wins

| Problem | Toy Script Loop | Always-On Planning Agent |
|---|---|---|
| **Machine Crash / Power Loss** | Context lost; starts from 0; repeats past tool calls. | **Resumes from exact unfinished step** via SQLite checkpoints. |
| **Unintended Side-Effects** | Model writes files without human confirmation. | **Whole-plan upfront approval**. Rejection executes 0 writes. |
| **Memory Drift** | Model dumps temporary goals into user memory. | **User-curated facts**. Memory is read-only context to the model. |
| **Unattended Scheduled Runs** | Hangs indefinitely on terminal prompts. | **Safe policy**. Cron jobs refuse approval-gated actions. |
| **Transient Failures** | Fails on momentary timeout or disk lag. | **Exponential backoff & idempotent execution**. |

---

## Deep Technical Documentation

Instead of dumping everything into one page, technical deep-dives are organized in [`docs/`](docs/):

| Guide | Description |
|---|---|
| [**Architecture & State Machine**](docs/architecture.md) | LangGraph nodes, SQLite WAL mode, and step-by-step crash recovery mechanics. |
| [**Tool Authoring Guide**](docs/tools.md) | How to write custom, idempotent, type-safe tools with parameter checking. |
| [**Configuration & Model Guide**](docs/configuration.md) | Complete `.env` reference, model hardware tiers (`llama3.2`, `qwen2.5`, `llama3.1`), and timeouts. |
| [**Security & Safety Model**](docs/safety.md) | Path traversal immunization, atomic note writes, unattended cron safety, and limitations. |

---

## Testing

The test suite runs with mocked model fixtures—no GPU or Ollama instance required:

```bash
pip install -r requirements-dev.txt
pytest
```

---

## License

Distributed under the **Apache License 2.0**. See [LICENSE](LICENSE) for details.
