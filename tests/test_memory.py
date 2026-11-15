import pytest

from agent.db import init_db
from agent.memory import LongTermMemory


@pytest.fixture
def mem(tmp_path):
    db = tmp_path / "agent.db"
    init_db(db)
    return LongTermMemory(db)


def test_remember_and_recall_round_trip(mem):
    mem.remember("u1", "preferred_language", "Python")
    assert mem.recall("u1") == {"preferred_language": "Python"}


def test_update_overwrites_and_keeps_one_row(mem):
    mem.remember("u1", "tone", "formal")
    mem.remember("u1", "tone", "casual")
    assert mem.recall("u1") == {"tone": "casual"}
    assert len(mem.list("u1")) == 1


def test_memories_are_isolated_per_user(mem):
    mem.remember("alice", "city", "Chennai")
    mem.remember("bob", "city", "Pune")
    assert mem.recall("alice") == {"city": "Chennai"}
    assert mem.recall("bob") == {"city": "Pune"}


def test_source_is_recorded_and_visible(mem):
    mem.remember("u1", "tone", "casual", source="user")
    mem.remember("u1", "topic", "llm evals", source="agent")
    by_key = {m.key: m.source for m in mem.list("u1")}
    assert by_key == {"tone": "user", "topic": "agent"}


def test_forget_removes_and_reports(mem):
    mem.remember("u1", "tone", "casual")
    assert mem.forget("u1", "tone") is True
    assert mem.forget("u1", "tone") is False
    assert mem.recall("u1") == {}


@pytest.mark.parametrize("key", ["", "has space", "x" * 65, "UPPER/slash"])
def test_rejects_bad_keys(mem, key):
    with pytest.raises(ValueError):
        mem.remember("u1", key, "value")


def test_rejects_empty_and_oversized_values(mem):
    with pytest.raises(ValueError):
        mem.remember("u1", "k", "   ")
    with pytest.raises(ValueError):
        mem.remember("u1", "k", "x" * 501)


def test_rejects_unknown_source(mem):
    with pytest.raises(ValueError):
        mem.remember("u1", "k", "v", source="guess")
