"""Always-on entry point: runs the scheduled jobs until the process is stopped.

    python scheduler.py

Docker runs this as PID 1, so SIGTERM is handled and the scheduler shuts down cleanly.
"""

from __future__ import annotations

import logging
import signal
import sys

from agent.config import Settings
from agent.db import init_db
from agent.scheduling import ScheduleStore, build_scheduler
from agent.service import AgentService


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings.from_env()
    init_db(settings.db_path)

    service = AgentService(settings)
    store = ScheduleStore(settings)
    scheduler = build_scheduler(service, store)

    def _stop(signum, _frame):
        logging.getLogger("scheduler").info("received signal %s, shutting down", signum)
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    jobs = scheduler.get_jobs()
    logging.getLogger("scheduler").info(
        "started with %d job(s) in %s; db=%s", len(jobs), settings.timezone, settings.db_path
    )
    try:
        scheduler.start()  # blocks until shutdown
    finally:
        service.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
