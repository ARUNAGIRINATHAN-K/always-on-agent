import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from zoneinfo import ZoneInfo

from agent.scheduling import JOB_PREFIX, ScheduleStore, run_schedule, sync_jobs
from tests.conftest import plan_json


@pytest.fixture
def store(settings):
    return ScheduleStore(settings)


def test_add_list_remove(store):
    store.add("daily-brief", "0 9 * * *", "Prepare today's summary")
    (s,) = store.list()
    assert (s.name, s.cron, s.enabled) == ("daily-brief", "0 9 * * *", True)
    assert store.remove("daily-brief") is True
    assert store.remove("daily-brief") is False
    assert store.list() == []


def test_rejects_bad_cron_duplicate_name_and_empty_goal(store):
    with pytest.raises(Exception):
        store.add("bad", "every tuesday", "x")
    store.add("once", "0 9 * * *", "x")
    with pytest.raises(ValueError, match="already exists"):
        store.add("once", "0 10 * * *", "y")
    with pytest.raises(ValueError):
        store.add("empty", "0 9 * * *", "   ")
    with pytest.raises(ValueError):
        store.add("has space", "0 9 * * *", "x")


def test_claim_run_is_once_per_schedule_per_day(store):
    store.add("daily", "0 9 * * *", "x")
    (s,) = store.list()
    assert store.claim_run(s.id, "2026-11-14", "t1") is True
    assert store.claim_run(s.id, "2026-11-14", "t2") is False  # same day: refused
    assert store.claim_run(s.id, "2026-11-15", "t3") is True   # next day: allowed


def test_run_schedule_skips_second_trigger_on_same_day(make_service, store, settings):
    store.add("daily", "0 9 * * *", "Save a task", user_id="u1")
    (s,) = store.list()
    service, llm = make_service([plan_json(("save_task", {"title": "daily item"}, "x"))])

    first = run_schedule(service, store, s)
    second = run_schedule(service, store, s)

    assert first is not None and first.status == "completed"
    assert second is None
    assert len(llm.prompts) == 1, "the duplicate trigger must not call the planner"


def test_scheduled_run_refuses_risky_steps_unattended(make_service, store, settings):
    store.add("cleanup", "0 6 * * *", "Remove old notes", user_id="u1")
    (s,) = store.list()
    service, _ = make_service([plan_json(("delete_note", {"filename": "old.md"}, "remove"))])
    result = run_schedule(service, store, s)
    assert result is not None
    assert not result.awaiting_approval
    assert result.results[0]["status"] == "skipped"


def test_sync_jobs_adds_updates_and_removes(store, make_service, settings):
    service, _ = make_service([])
    # Paused and started, like the live process: replace_existing only applies once running.
    scheduler = BackgroundScheduler(timezone=ZoneInfo(settings.timezone))
    scheduler.start(paused=True)
    store.add("morning", "0 9 * * *", "first goal")
    sync_jobs(scheduler, service, store)
    job = scheduler.get_job(JOB_PREFIX + "morning")
    assert job is not None

    # Changing the goal replaces the job, so the scheduler picks up edits.
    store.remove("morning")
    store.add("morning", "0 9 * * *", "second goal")
    sync_jobs(scheduler, service, store)
    assert scheduler.get_job(JOB_PREFIX + "morning").args[2].goal == "second goal"

    # Removing the schedule removes the job.
    store.remove("morning")
    sync_jobs(scheduler, service, store)
    assert scheduler.get_job(JOB_PREFIX + "morning") is None
    scheduler.shutdown(wait=False)


def test_failed_run_is_retried_by_a_later_trigger_same_day(make_service, store, settings):
    store.add("hourly", "0 * * * *", "Save a task", user_id="u1")
    (s,) = store.list()
    service, _ = make_service([
        ConnectionError("ollama down"),                                  # first trigger: planner unreachable
        plan_json(("save_task", {"title": "recovered"}, "x")),           # second trigger: succeeds
        plan_json(("save_task", {"title": "extra"}, "x")),               # third trigger: must be skipped
    ])
    first = run_schedule(service, store, s)
    second = run_schedule(service, store, s)
    third = run_schedule(service, store, s)

    assert first.status == "failed"
    assert second.status == "completed"
    assert third is None, "a completed run is never repeated on the same day"


def test_crashed_run_is_not_retried_automatically(store, settings):
    store.add("crashy", "0 9 * * *", "x")
    (s,) = store.list()
    assert store.claim_run(s.id, "2026-11-14", "t1") is True
    # Simulate a process that died mid-run: the row stays 'running'.
    assert store.claim_run(s.id, "2026-11-14", "t2") is False
