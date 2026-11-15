"""Command-line interface.

    python -m agent.cli run "Prepare a weekly AI research report"
    python -m agent.cli resume <thread_id> approve
    python -m agent.cli memory set preferred_language "Python"
    python -m agent.cli memory list
    python -m agent.cli memory forget preferred_language
    python -m agent.cli tasks
    python -m agent.cli schedule add daily-brief "0 9 * * *" "Prepare today's AI research summary"
    python -m agent.cli schedule list
    python -m agent.cli schedule remove daily-brief
"""

from __future__ import annotations

import argparse
import json
import sys

from .config import Settings
from .db import init_db
from .scheduling import ScheduleStore
from .service import AgentService, RunResult
from .tools import list_tasks


def _print_result(result: RunResult) -> None:
    print(f"thread: {result.thread_id}")
    print(f"status: {result.status}")
    if result.error:
        print(f"error:  {result.error}")
    if result.plan:
        print("\nplan:")
        for step in result.plan:
            print(f"  {step['id']}. [{step['tool']}] {step['description']}")
    if result.results:
        print("\nresults:")
        for r in result.results:
            print(f"  {r['step_id']} {r['status']:<8} {r['output']}")
    if result.pending_approval:
        print("\nwaiting for approval:")
        step = result.pending_approval.get("step")
        if step:
            print(f"  {step['id']} [{step['tool']}] args={json.dumps(step['args'])}")
        else:
            plan_len = len(result.plan)
            print(f"  Approve plan with {plan_len} step{'s' if plan_len != 1 else ''}?")
        print(f"  approve: python -m agent.cli resume {result.thread_id} approve")
        print(f"  reject:  python -m agent.cli resume {result.thread_id} reject")
    elif result.status == "rejected":
        print("\nplan was rejected; no steps were executed")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agent", description="Persistent planning agent")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="plan and execute a goal")
    run.add_argument("goal")
    run.add_argument("--user", default="default")
    run.add_argument("--thread", default=None, help="reuse a thread id (advanced)")

    res = sub.add_parser("resume", help="answer a pending approval")
    res.add_argument("thread_id")
    res.add_argument("decision", choices=["approve", "reject"])

    mem = sub.add_parser("memory", help="manage long-term memory")
    mem.add_argument("--user", default="default")
    msub = mem.add_subparsers(dest="action", required=True)
    msub.add_parser("list")
    ms = msub.add_parser("set")
    ms.add_argument("key")
    ms.add_argument("value")
    mf = msub.add_parser("forget")
    mf.add_argument("key")

    sub.add_parser("tasks", help="show the task list")

    sch = sub.add_parser("schedule", help="manage recurring runs")
    ssub = sch.add_subparsers(dest="action", required=True)
    sa = ssub.add_parser("add")
    sa.add_argument("name")
    sa.add_argument("cron", help='5-field crontab, e.g. "0 9 * * *"')
    sa.add_argument("goal")
    sa.add_argument("--user", default="default")
    ssub.add_parser("list")
    sr = ssub.add_parser("remove")
    sr.add_argument("name")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _dispatch(args)
    except ValueError as exc:  # bad user input: say so, without a traceback
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _dispatch(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    init_db(settings.db_path)

    if args.cmd == "memory":
        from .memory import LongTermMemory

        mem = LongTermMemory(settings.db_path)
        if args.action == "list":
            items = mem.list(args.user)
            if not items:
                print("(no memories)")
            for m in items:
                print(f"{m.key} = {m.value}   [source: {m.source}, updated {m.updated_at}]")
        elif args.action == "set":
            mem.remember(args.user, args.key, args.value, source="user")
            print(f"saved {args.key.lower()}")
        elif args.action == "forget":
            print("forgotten" if mem.forget(args.user, args.key) else "no such memory")
        return 0

    if args.cmd == "tasks":
        rows = list_tasks(settings.db_path)
        if not rows:
            print("(no tasks)")
        for r in rows:
            print(f"[{r['status']:<11}] {r['title']}   (updated {r['updated_at']})")
        return 0

    if args.cmd == "schedule":
        store = ScheduleStore(settings)
        if args.action == "add":
            store.add(args.name, args.cron, args.goal, args.user)
            print(f"scheduled '{args.name}' at '{args.cron}' ({settings.timezone}); "
                  "the running scheduler picks it up within a minute")
        elif args.action == "list":
            items = store.list()
            if not items:
                print("(no schedules)")
            for s in items:
                flag = "" if s.enabled else " (disabled)"
                print(f"{s.name}: '{s.cron}' -> {s.goal}{flag}")
        elif args.action == "remove":
            print("removed" if store.remove(args.name) else "no such schedule")
        return 0

    service = AgentService(settings)
    try:
        if args.cmd == "run":
            result = service.run(args.goal, args.user, thread_id=args.thread)
        else:  # resume
            result = service.resume(args.thread_id, "approved" if args.decision == "approve" else "rejected")
        _print_result(result)
        return 0 if result.status in {"completed", "completed_with_skips", "awaiting_approval"} else 1
    finally:
        service.close()


if __name__ == "__main__":
    sys.exit(main())
