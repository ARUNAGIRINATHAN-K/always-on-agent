# Always On Planning Agent

A local, persistent agent built with LangGraph, SQLite, APScheduler and Ollama.
You give it a goal. It recalls what it knows about you, plans steps, pauses for
your approval before executing, runs the approved steps, and saves
its progress so a restart or a crash never repeats finished work.

It runs entirely on your machine. No API keys, no subscriptions.

| Part | File | Role |
|---|---|---|
| Workflow | `agent/graph.py` | LangGraph state machine: recall → plan → approval → execute → finish |
| Tools | `agent/tools.py` | Declared, validated, idempotent actions; approval flags |
| Memory | `agent/memory.py` | Long-term facts curated by the user; you can list, set, and forget them |
| Storage | `agent/db.py` | SQLite schema, WAL mode, short-lived connections |
| Service | `agent/service.py` | `run`, `resume`, and result objects |
| Schedules | `agent/scheduling.py` | Cron jobs stored in SQLite, reloaded every minute, at most one run per day |
| CLI | `agent/cli.py` | Operate the agent from a terminal |
| Always-on process | `scheduler.py` | Long-running entry point; handles SIGTERM |

### What is different from the simple version

The original sketch recorded planned steps but never ran them. This version:

- **Executes real tools** (notes and tasks) through a registry with explicit parameters. Model-written arguments are checked before anything runs.
- **Validates the plan.** Unknown tools, unknown arguments, duplicate note writes, empty content, too many steps, or non-JSON output are rejected, and the planner gets one correction round.
- **Pauses for whole-plan approval** before any step runs. The approval node interrupts cleanly without side effects. If you reject the plan, the run aborts immediately with `status: rejected` and executes zero writes.
- **Resumes without repeating work.** Each step is checkpointed. A resumed run continues at the next unfinished step and does not re-plan.
- **Retries and times out** tool calls. Bad arguments are not retried.
- **Keeps memory honest.** Long-term facts are curated directly by you via the CLI, keeping memory clean from runaway tasks.
- **Runs schedules safely.** A daily job runs once per day, even across restarts. A cleanly failed run is retried by a later trigger that day. A run that crashed mid-way is not repeated automatically. Scheduled runs refuse approval-gated plans instead of waiting for someone who isn't there.

## Quick start (local)

Requires Python 3.11+ (tested on 3.13) and [Ollama](https://ollama.com/download).

```bash
ollama pull llama3.2

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # optional: change model, timezone, limits
python -m agent.cli memory set preferred_language Python
python -m agent.cli run "Prepare a weekly AI research report"
```

If the run pauses for approval, it prints the step and the command to answer it:

```bash
python -m agent.cli resume <thread_id> approve   # or: reject
```

Other commands:

```bash
python -m agent.cli memory list
python -m agent.cli memory forget preferred_language
python -m agent.cli tasks
python -m agent.cli schedule add daily-brief "0 9 * * *" "Prepare today's AI research summary"
python -m agent.cli schedule list
python -m agent.cli schedule remove daily-brief
```

Run the scheduler in the foreground to test it locally:

```bash
python scheduler.py
```

## Always-on (Docker)

```bash
docker compose up -d
docker compose exec ollama ollama pull llama3.2
docker compose logs -f agent
```

- `./data` holds the SQLite database and notes. Back it up; it is the agent's whole state.
- `restart: unless-stopped` restarts the scheduler after crashes and reboots.
- Schedules and memory persist in `./data`. To add a schedule, use the CLI against the same volume:
  `docker compose run --rm agent python -m agent.cli schedule add ...`
- Changes to schedules take effect within a minute, with no restart.

## Safety model

- **Allowlisted tools only.** The planner can name only tools in `agent/tools.py`. There is no shell, no network, and no access outside `data/notes`.
- **Filenames are validated**, not paths. `../x.md`, `sub/x.md` and hidden files are rejected.
- **Whole-plan approval gate before execution.** Attended runs require human confirmation before any write runs. Rejecting immediately stops the run. Scheduled runs refuse approval-gated plans.
- **Plan uniqueness & non-empty content.** Duplicate writes to the same note file in a single plan and blank contents are caught and rejected during validation.
- **Idempotent tools.** Writing the same note twice gives the same file. Saving the same task title updates one row. Retries and resumes are therefore safe.
- **Limits** on plan size, text length, tool time, and retries (see `.env.example`).

## Adding a tool

1. Write the function in `agent/tools.py`: `def my_tool(ctx: ToolContext, arg: str) -> str`. Make it idempotent and validate its inputs.
2. Register it in `TOOLS` with a description and parameters. Set `requires_approval=True` if it changes anything a person would want to review.
3. Add tests in `tests/`. The planner sees the new tool automatically.

Good candidates: fetching a web page from an allowlist of domains, reading a file from a fixed folder, or calling an internal API. Keep each one narrow, and gate anything that sends, deletes, or spends money.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The tests use a scripted stand-in for the model, so they run without Ollama. They cover the approval pause and resume, retries, timeouts, unsafe filenames, memory isolation, schedule guards, and planner output that is malformed or valid.

## Limitations

- **Plan quality depends on the model.** Smaller local models can produce weaker plans. Try `OLLAMA_MODEL` with a larger model if plans look wrong. The validation stops bad plans from running, but it cannot make a poor plan good.
- **Timeouts do not kill threads.** Python cannot stop a running thread, so a timed-out tool may still finish in the background. This is why tools must be idempotent.
- **One scheduler process per data directory.** SQLite handles the scheduler and CLI sharing a host. Do not run two scheduler processes against the same data directory, or they will both run jobs.
- **Single user machine scale.** SQLite and a single process suit one person's agent. For many users or many machines, move to PostgreSQL and a dedicated job queue.
- **The article's pattern is a starting point, not a finished product.** Expect to add your own tools and tune the planner prompt for your model.
