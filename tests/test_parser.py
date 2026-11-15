import pytest

from agent.graph import PlanError, parse_plan

GOOD = '{"steps": [{"tool": "create_note", "args": {"filename": "a.md", "content": "x"}, "description": "d"}]}'


@pytest.mark.parametrize(
    "reply",
    [
        GOOD,
        f"```json\n{GOOD}\n```",
        f"Here is the plan:\n```\n{GOOD}\n```\nLet me know if you want changes.",
        f"Sure. {GOOD} Thanks!",
    ],
)
def test_accepts_common_model_output_shapes(reply):
    (step,) = parse_plan(reply, max_steps=5)
    assert step["id"] == "s1"
    assert step["tool"] == "create_note"
    assert step["args"] == {"filename": "a.md", "content": "x"}


@pytest.mark.parametrize(
    "reply, message",
    [
        ("no json here at all", "no JSON object"),
        ('{"steps": [}', "not valid JSON"),
        ('["a", "list"]', "JSON object"),
        ('{"plan": []}', "no steps"),
        ('{"steps": [{"tool": "create_note", "args": {"filename": "a.md"}}]}', "missing"),
        ('{"steps": [{"tool": "shell", "args": {"cmd": "rm -rf /"}}]}', "unknown tool"),
        ('{"steps": ["create_note"]}', "not an object"),
        ('{"steps": [{"tool": "create_note", "args": {"filename": "a.md", "content": ""}}]}', "cannot be empty"),
        ('{"steps": [{"tool": "create_note", "args": {"filename": "a.md", "content": "   "}}]}', "cannot be empty"),
        ('{"steps": [{"tool": "create_note", "args": {"filename": "a.md", "content": "x"}}, {"tool": "create_note", "args": {"filename": "a.md", "content": "y"}}]}', "multiple times"),
    ],
)
def test_rejects_bad_plans_with_a_clear_reason(reply, message):
    with pytest.raises(PlanError, match=message):
        parse_plan(reply, max_steps=5)


def test_accepts_distinct_filenames():
    reply = '{"steps": [{"tool": "create_note", "args": {"filename": "a.md", "content": "x"}}, {"tool": "create_note", "args": {"filename": "b.md", "content": "y"}}]}'
    steps = parse_plan(reply, max_steps=5)
    assert len(steps) == 2
    assert [s["args"]["filename"] for s in steps] == ["a.md", "b.md"]
