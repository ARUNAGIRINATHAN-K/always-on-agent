import dataclasses
import time


from agent.db import connect
from agent.memory import LongTermMemory
from agent.tools import TOOLS, list_tasks
from tests.conftest import plan_json


def _note(settings, name):
    return (settings.notes_dir / name)


def test_happy_path_executes_each_step(make_service, settings):
    service, _ = make_service([
        plan_json(
            ("create_note", {"filename": "report.md", "content": "# Weekly"}, "Draft the report"),
            ("save_task", {"title": "Review report", "status": "pending"}, "Track the review"),
        )
    ])
    paused = service.run("Prepare a weekly report", "u1")
    assert paused.awaiting_approval
    assert paused.status == "awaiting_approval"

    result = service.resume(paused.thread_id, "approved")
    assert result.status == "completed"
    assert [r["status"] for r in result.results] == ["ok", "ok"]
    assert _note(settings, "report.md").read_text() == "# Weekly"
    assert [t["title"] for t in list_tasks(settings.db_path)] == ["Review report"]


def test_planner_gets_one_correction_round(make_service, settings):
    service, llm = make_service([
        "Sure! Here is my plan: create a note.",           # not JSON
        plan_json(("create_note", {"filename": "a.md", "content": "x"}, "Write it")),
    ])
    paused = service.run("note please", "u1")
    assert paused.awaiting_approval
    result = service.resume(paused.thread_id, "approved")

    assert result.status == "completed"
    assert len(llm.prompts) == 2
    assert "previous reply was rejected" in llm.prompts[1]


def test_unknown_tool_fails_cleanly_without_side_effects(make_service, settings):
    bad = plan_json(("format_disk", {"device": "/dev/sda"}, "Dangerous"))
    service, _ = make_service([bad, bad])
    result = service.run("do something", "u1")

    assert result.status == "failed"
    assert "could not build a valid plan" in result.error
    assert result.results == []
    assert not settings.notes_dir.exists()


def test_too_many_steps_rejected(make_service, settings):
    steps = [("save_task", {"title": f"t{i}"}, "x") for i in range(settings.max_steps + 1)]
    service, _ = make_service([plan_json(*steps), plan_json(*steps)])
    result = service.run("lots", "u1")
    assert result.status == "failed"
    assert "limit is" in result.error


def test_empty_plan_is_reported_as_unachievable(make_service):
    service, _ = make_service(['{"steps": []}', '{"steps": []}'])
    result = service.run("fly to the moon", "u1")
    assert result.status == "failed"
    assert "no steps" in result.error


def test_planner_outage_is_a_failed_run_not_a_crash(make_service):
    service, _ = make_service([ConnectionError("ollama not running")])
    result = service.run("anything", "u1")
    assert result.status == "failed"
    assert "planner error" in result.error


def test_risky_step_pauses_then_approval_runs_it(make_service, settings):
    note = _note(settings, "old.md")
    note.parent.mkdir(parents=True)
    note.write_text("stale")

    service, _ = make_service([
        plan_json(("delete_note", {"filename": "old.md"}, "Remove stale note"))
    ])
    paused = service.run("clean up", "u1")

    assert paused.awaiting_approval
    assert paused.status == "awaiting_approval"
    assert paused.pending_approval["plan"][0]["tool"] == "delete_note"
    assert note.exists(), "nothing may change before approval"

    done = service.resume(paused.thread_id, "approved")
    assert done.status == "completed"
    assert not note.exists()


def test_rejected_step_is_skipped_and_nothing_changes(make_service, settings):
    note = _note(settings, "keep.md")
    note.parent.mkdir(parents=True)
    note.write_text("keep me")

    service, _ = make_service([
        plan_json(("delete_note", {"filename": "keep.md"}, "Remove"))
    ])
    paused = service.run("clean up", "u1")
    done = service.resume(paused.thread_id, "rejected")

    assert done.status == "rejected"
    assert done.results == []
    assert note.read_text() == "keep me"


def test_resume_does_not_repeat_completed_steps(make_service, settings):
    note = _note(settings, "old.md")
    note.parent.mkdir(parents=True)
    note.write_text("stale")

    service, llm = make_service([
        plan_json(
            ("create_note", {"filename": "log.md", "content": "started"}, "Log the start"),
            ("delete_note", {"filename": "old.md"}, "Remove stale note"),
            ("save_task", {"title": "Finish cleanup"}, "Track it"),
        )
    ])
    paused = service.run("cleanup run", "u1")
    assert paused.awaiting_approval
    done = service.resume(paused.thread_id, "approve")
    assert done.status == "completed"

    final = service.resume(paused.thread_id, "approve")  # extra resume on a finished thread is harmless
    step_ids = [r["step_id"] for r in final.results]
    assert step_ids == ["s1", "s2", "s3"]
    assert len(llm.prompts) == 1, "resuming must not re-plan"


def test_unattended_run_refuses_risky_steps_without_waiting(make_service, settings):
    note = _note(settings, "old.md")
    note.parent.mkdir(parents=True)
    note.write_text("stale")

    service, _ = make_service([
        plan_json(
            ("create_note", {"filename": "daily.md", "content": "summary"}, "Write summary"),
            ("delete_note", {"filename": "old.md"}, "Remove stale note"),
        )
    ])
    result = service.run("daily job", "u1", unattended=True)

    assert not result.awaiting_approval
    assert result.status == "rejected"
    assert "refused approval-gated plan" in result.error
    assert result.results == []
    assert note.exists()


def test_agent_proposed_memory_needs_approval_and_is_marked_agent(make_service, settings):
    service, _ = make_service([
        plan_json(("remember_preference", {"key": "tone", "value": "concise"}, "Remember tone")),
        plan_json(("remember_preference", {"key": "tone", "value": "concise"}, "Remember tone")),
    ])
    result = service.run("learn my tone", "u1")
    assert result.status == "failed"
    assert "unknown tool" in result.error
    assert LongTermMemory(settings.db_path).recall("u1") == {}


def test_saved_memory_is_passed_to_the_planner(make_service, settings):
    LongTermMemory(settings.db_path).remember("u1", "preferred_language", "Python")
    service, llm = make_service([
        plan_json(("save_task", {"title": "Draft Python tutorial outline"}, "Outline"))
    ])
    service.run("plan my learning", "u1")
    assert "preferred_language: Python" in llm.prompts[0]


def test_other_users_memory_is_not_leaked_to_the_planner(make_service, settings):
    LongTermMemory(settings.db_path).remember("bob", "secret", "bob-only")
    service, llm = make_service([
        plan_json(("save_task", {"title": "x"}, "x"))
    ])
    service.run("hi", "alice")
    assert "bob-only" not in llm.prompts[0]


def test_tool_failure_is_retried_then_reported(make_service, settings, monkeypatch):
    calls = {"n": 0}

    def flaky(ctx, filename, content):
        calls["n"] += 1
        raise RuntimeError("disk on fire")

    spec = dataclasses.replace(TOOLS["create_note"], fn=flaky)
    monkeypatch.setitem(TOOLS, "create_note", spec)

    service, _ = make_service([
        plan_json(
            ("create_note", {"filename": "a.md", "content": "x"}, "fails"),
            ("save_task", {"title": "should not run"}, "after failure"),
        )
    ])
    paused = service.run("write", "u1")
    assert paused.awaiting_approval
    result = service.resume(paused.thread_id, "approved")

    assert calls["n"] == settings.tool_retries + 1
    assert result.status == "failed"
    assert "disk on fire" in result.error
    assert len(result.results) == 1, "steps after a failure must not run"
    assert list_tasks(settings.db_path) == []


def test_tool_timeout_is_reported(make_service, settings, monkeypatch):
    def slow(ctx, filename, content):
        time.sleep(1.0)
        return "late"

    spec = dataclasses.replace(TOOLS["create_note"], fn=slow)
    monkeypatch.setitem(TOOLS, "create_note", spec)
    settings_fast = dataclasses.replace(settings, tool_timeout_s=0.1, tool_retries=0)

    from agent.service import AgentService
    from tests.conftest import ScriptedLLM

    llm = ScriptedLLM([plan_json(("create_note", {"filename": "a.md", "content": "x"}, "slow"))])
    service = AgentService(settings_fast, llm=llm)
    try:
        paused = service.run("slow write", "u1")
        assert paused.awaiting_approval
        result = service.resume(paused.thread_id, "approved")
    finally:
        service.close()
    assert result.status == "failed"
    assert "timed out" in result.error


def test_runs_are_checkpointed_per_thread(make_service, settings):
    service, _ = make_service([
        plan_json(("save_task", {"title": "one"}, "one")),
    ])
    result = service.run("first", "u1", thread_id="fixed-thread")
    assert result.thread_id == "fixed-thread"
    with connect(settings.db_path) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?", ("fixed-thread",)
        ).fetchone()[0]
    assert n > 0


def test_rejecting_plan_with_multiple_steps_executes_nothing(make_service, settings):
    service, _ = make_service([
        plan_json(
            ("create_note", {"filename": "report.md", "content": "draft"}, "Step 1"),
            ("save_task", {"title": "Task 1"}, "Step 2"),
            ("create_note", {"filename": "report_final.md", "content": "final"}, "Step 3"),
        )
    ])
    paused = service.run("run goal", "u1")
    assert paused.awaiting_approval
    assert len(paused.plan) == 3

    rejected = service.resume(paused.thread_id, "reject")
    assert rejected.status == "rejected"
    assert rejected.results == []
    assert not _note(settings, "report.md").exists()
    assert not _note(settings, "report_final.md").exists()
    assert list_tasks(settings.db_path) == []
