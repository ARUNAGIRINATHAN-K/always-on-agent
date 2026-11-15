import pytest

from agent.db import init_db
from agent.memory import LongTermMemory
from agent.tools import (
    TOOLS,
    ToolContext,
    ToolInputError,
    delete_note,
    list_tasks,
    run_tool,
    save_task,
    validate_args,
)


@pytest.fixture
def ctx(tmp_path):
    db = tmp_path / "agent.db"
    init_db(db)
    return ToolContext(notes_dir=tmp_path / "notes", db_path=db, user_id="u1", thread_id="t1")


def test_create_note_writes_file_and_is_idempotent(ctx):
    run_tool("create_note", {"filename": "report.md", "content": "hello"}, ctx)
    run_tool("create_note", {"filename": "report.md", "content": "hello"}, ctx)
    assert (ctx.notes_dir / "report.md").read_text() == "hello"
    assert not list(ctx.notes_dir.glob("*.tmp"))


@pytest.mark.parametrize(
    "bad",
    ["../escape.md", "sub/dir.md", ".hidden.md", "script.py", "no_extension", "a" * 120 + ".md"],
)
def test_create_note_rejects_unsafe_names(ctx, bad):
    with pytest.raises(ToolInputError):
        run_tool("create_note", {"filename": bad, "content": "x"}, ctx)
    assert not (ctx.notes_dir.parent / "escape.md").exists()


def test_delete_note_is_flagged_and_idempotent(ctx):
    assert TOOLS["delete_note"].requires_approval
    assert not TOOLS["create_note"].requires_approval
    assert "already absent" in delete_note(ctx, "missing.md")
    run_tool("create_note", {"filename": "a.md", "content": "x"}, ctx)
    assert "Deleted" in run_tool("delete_note", {"filename": "a.md"}, ctx)
    assert not (ctx.notes_dir / "a.md").exists()


def test_save_task_upserts_by_title(ctx):
    save_task(ctx, "Write weekly report", "pending")
    save_task(ctx, "write weekly report", "done")  # same title, different case
    rows = list_tasks(ctx.db_path)
    assert len(rows) == 1
    assert rows[0]["status"] == "done"


def test_save_task_rejects_bad_status_and_title(ctx):
    with pytest.raises(ToolInputError):
        save_task(ctx, "x", "maybe")
    with pytest.raises(ToolInputError):
        save_task(ctx, "   ", "pending")


def test_validate_args_rejects_unknown_missing_and_wrong_types(ctx):
    with pytest.raises(ToolInputError, match="unknown argument"):
        validate_args("create_note", {"filename": "a.md", "content": "x", "extra": "y"})
    with pytest.raises(ToolInputError, match="missing"):
        validate_args("create_note", {"filename": "a.md"})
    with pytest.raises(ToolInputError, match="must be a string"):
        validate_args("create_note", {"filename": "a.md", "content": 42})
    with pytest.raises(ToolInputError, match="unknown tool"):
        validate_args("rm_rf", {})
    with pytest.raises(ToolInputError, match="JSON object"):
        validate_args("create_note", ["not", "a", "dict"])


def test_optional_arguments_may_be_omitted():
    assert validate_args("save_task", {"title": "x"}) == {"title": "x"}


def test_content_limit_enforced():
    with pytest.raises(ToolInputError, match="exceeds"):
        validate_args("create_note", {"filename": "a.md", "content": "x" * 20_001})


def test_remember_preference_is_agent_sourced_and_approval_gated(ctx):
    assert TOOLS["remember_preference"].requires_approval
    run_tool("remember_preference", {"key": "tone", "value": "concise"}, ctx)
    (item,) = LongTermMemory(ctx.db_path).list("u1")
    assert (item.key, item.value, item.source) == ("tone", "concise", "agent")
