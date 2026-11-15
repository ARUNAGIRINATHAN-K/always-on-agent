import json
from types import SimpleNamespace

import pytest

from agent.config import Settings
from agent.service import AgentService


class ScriptedLLM:
    """Stands in for ChatOllama. Replies are consumed in order; exceptions are raised."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        if not self.replies:
            raise AssertionError("ScriptedLLM ran out of replies")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(content=reply)


def plan_json(*steps):
    """steps: (tool, args, description) tuples -> planner reply text."""
    return json.dumps({
        "steps": [{"tool": t, "args": a, "description": d} for t, a, d in steps]
    })


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path / "data",
        max_steps=5,
        tool_timeout_s=2.0,
        tool_retries=1,
        retry_backoff_s=0.0,
    )


@pytest.fixture
def make_service(settings):
    created = []

    def _make(replies):
        llm = ScriptedLLM(replies)
        service = AgentService(settings, llm=llm)
        created.append(service)
        return service, llm

    yield _make
    for service in created:
        service.close()
