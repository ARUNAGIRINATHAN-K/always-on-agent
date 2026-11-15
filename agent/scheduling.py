"""Recurring runs: schedules live in SQLite and are re-read every minute.

Adding or removing a schedule with the CLI takes effect without restarting
the scheduler process. Each schedule runs at most once per calendar day,
even across restarts (see `schedule_runs`).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import Settings
from .db import connect, init_db
from .service import AgentService, RunResult

log = logging.getLogger(__name__)

JOB_PREFIX = "schedule-"
RELOAD_JOB_ID = "__reload_schedules__"


@dataclass(frozen=True)
class Schedule:
    id: int
    name: str
    goal: str
    user_id: str
    cron: str
    enabled: bool


class ScheduleStore:
    def __init__(self, settings: Settings):
        self.db_path = settings.db_path
        self.timezone = settings.timezone
        init_db(self.db_path)

    def add(self, name: str, cron: str, goal: str, user_id: str = "default") -> int:
        name = name.strip()
        if not name.replace("-", "").replace("_", "").isalnum():
            raise ValueError("schedule name may contain only letters, digits, '-' and '_'")
        if not goal.strip():
            raise ValueError("goal must not be empty")
        # Fails early on a bad cron expression instead of at the next tick.
        CronTrigger.from_crontab(cron, timezone=ZoneInfo(self.timezone))
        with connect(self.db_path) as conn:
            try:
                cur = conn.execute(
                    "INSERT INTO schedules (name, goal, user_id, cron, enabled) VALUES (?, ?, ?, ?, 1)",
                    (name, goal.strip(), user_id, cron.strip()),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"a schedule named '{name}' already exists") from exc
            return int(cur.lastrowid)

    def list(self, *, enabled_only: bool = False) -> list[Schedule]:
        sql = "SELECT id, name, goal, user_id, cron, enabled FROM schedules"
        if enabled_only:
            sql += " WHERE enabled = 1"
        with connect(self.db_path) as conn:
            rows = conn.execute(sql + " ORDER BY name").fetchall()
        return [Schedule(r[0], r[1], r[2], r[3], r[4], bool(r[5])) for r in rows]

    def remove(self, name: str) -> bool:
        with connect(self.db_path) as conn:
            cur = conn.execute("DELETE FROM schedules WHERE name = ?", (name,))
            return cur.rowcount > 0

    def claim_run(self, schedule_id: int, run_date: str, thread_id: str) -> bool:
        """True if this caller should run today's job.

        False when today's run is already done or still running (for example,
        it was interrupted by a crash). A run that failed cleanly can be retried.
        """
        with connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO schedule_runs (schedule_id, run_date, thread_id, status) "
                "VALUES (?, ?, ?, 'running')",
                (schedule_id, run_date, thread_id),
            )
            if cur.rowcount == 1:
                return True
            cur = conn.execute(
                "UPDATE schedule_runs SET thread_id = ?, status = 'running' "
                "WHERE schedule_id = ? AND run_date = ? AND status = 'failed'",
                (thread_id, schedule_id, run_date),
            )
            return cur.rowcount == 1

    def finish_run(self, schedule_id: int, run_date: str, status: str) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE schedule_runs SET status = ? WHERE schedule_id = ? AND run_date = ?",
                (status, schedule_id, run_date),
            )


def run_schedule(service: AgentService, store: ScheduleStore, schedule: Schedule) -> RunResult | None:
    today = datetime.now(ZoneInfo(store.timezone)).date().isoformat()
    thread_id = f"{schedule.name}-{today}"
    if not store.claim_run(schedule.id, today, thread_id):
        log.info("schedule %s already ran for %s; skipping", schedule.name, today)
        return None
    log.info("running schedule %s (thread %s)", schedule.name, thread_id)
    try:
        # Unattended: steps that need approval are refused, not left waiting.
        result = service.run(schedule.goal, schedule.user_id, thread_id=thread_id, unattended=True)
    except Exception:
        # Leaves the row 'running', so a crash is not retried automatically today.
        log.exception("schedule %s crashed", schedule.name)
        raise
    ok = result.status in {"completed", "completed_with_skips"}
    store.finish_run(schedule.id, today, "done" if ok else "failed")
    log.info("schedule %s finished with status %s", schedule.name, result.status)
    return result


def sync_jobs(
    scheduler: BlockingScheduler,
    service: AgentService,
    store: ScheduleStore,
) -> None:
    """Make the scheduler's jobs match the schedules table."""
    wanted = {s.name: s for s in store.list(enabled_only=True)}
    for job in scheduler.get_jobs():
        if not job.id.startswith(JOB_PREFIX):
            continue
        name = job.id[len(JOB_PREFIX):]
        if name not in wanted:
            job.remove()
            log.info("removed schedule job %s", name)

    tz = ZoneInfo(store.timezone)
    for name, sched in wanted.items():
        job_id = JOB_PREFIX + name
        existing = scheduler.get_job(job_id)
        if existing is not None and existing.args[2] == sched:
            continue  # unchanged
        scheduler.add_job(
            run_schedule,
            CronTrigger.from_crontab(sched.cron, timezone=tz),
            args=[service, store, sched],
            id=job_id,
            replace_existing=True,
            coalesce=True,       # missed ticks collapse into one run
            max_instances=1,     # never overlap with itself
            misfire_grace_time=3600,
        )
        log.info("scheduled %s with cron '%s'", name, sched.cron)


def build_scheduler(service: AgentService, store: ScheduleStore) -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=ZoneInfo(store.timezone))
    sync_jobs(scheduler, service, store)
    scheduler.add_job(
        sync_jobs,
        "interval",
        seconds=60,
        args=[scheduler, service, store],
        id=RELOAD_JOB_ID,
        replace_existing=True,
        max_instances=1,
    )
    return scheduler
