import time

import pytest

from aitown.config import Settings
from aitown.context import ContextStore
from aitown.graph import FALLBACK, AnotherMeAgent
from aitown.models import SkillCall
from aitown.skills import default_registry


@pytest.fixture
def agent(tmp_path):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "library.md").write_text(
        "图书馆活动室最多容纳20人，需要提前一天预约。", encoding="utf-8")
    instance = AnotherMeAgent(Settings(knowledge_dir=knowledge, memory_db=tmp_path / "memory.sqlite3"))
    yield instance
    instance.close()


def test_rag_citations_and_no_hit(agent):
    reply = agent.ask("图书馆活动室容纳多少人？")
    assert reply.trace == ["context", "intent", "rag", "reflect"]
    assert reply.sources[0].source == "library.md"
    assert "20人" in reply.answer
    unknown = agent.ask("ZXQ987654")
    assert unknown.sources == []
    assert FALLBACK in unknown.answer


def test_skill_failure_does_not_break_next_turn(agent):
    failed = agent.ask("计算 15 / 0")
    assert not failed.skills[0].ok
    success = agent.ask("计算 15 * 20 + 10")
    assert success.skills[0].data["value"] == 310
    assert not success.warnings
    assert success.trace == ["context", "intent", "skill", "reflect"]


def test_registry_disable_and_no_code_execution():
    registry = default_registry()
    bad = registry.execute(SkillCall(name="calculator", arguments={"expression": "__import__('os')"}))
    assert not bad.ok
    registry.enable("calculator", False)
    assert not registry.execute(SkillCall(name="calculator", arguments={"expression": "1+2"})).ok
    registry.enable("calculator")
    assert registry.execute(SkillCall(name="calculator", arguments={"expression": "(1+2)*3"})).data["value"] == 9


def test_event_skill(agent):
    result = agent.ask("整理事件：图书馆预约；咖啡店准备饮品")
    assert result.skills[0].data["count"] == 2


def test_memory_isolation_retention_restart_and_expiry(tmp_path):
    path = tmp_path / "memory.sqlite3"
    store = ContextStore(path, turns=5)
    for i in range(8):
        store.save("s", "lin", f"question-{i}", f"answer-{i}")
    assert len(store.load("s", "lin")) == 10
    assert store.load("s", "lin")[0]["content"] == "question-3"
    assert store.load("s", "zhou") == []
    assert store.load("other", "lin") == []
    store.close()
    store = ContextStore(path, turns=5)
    assert len(store.load("s", "lin")) == 10
    with store.conn:
        store.conn.execute("UPDATE turns SET created=?", (time.time() - store.ttl - 1,))
    assert store.load("s", "lin") == []
    store.close()


def test_followup_and_transient_reset(agent):
    agent.ask("图书馆活动室")
    response = agent.ask("它需要预约吗？")
    assert response.sources
    assert "预约" in response.answer
    agent.ask("15 * 2")
    greeting = agent.ask("你好")
    assert greeting.skills == []
    assert greeting.sources == []


def test_knowledge_refresh_and_empty_corpus(agent):
    path = agent.settings.knowledge_dir / "library.md"
    path.write_text("图书馆活动室暂时关闭，停止预约。", encoding="utf-8")
    agent.knowledge.refresh()
    assert "停止预约" in agent.ask("图书馆活动室").answer
    path.unlink()
    agent.knowledge.refresh()
    assert agent.knowledge.search("图书馆") == []


def test_single_agent_sessions_and_reply_schema(agent):
    from aitown.models import Reply

    first = agent.ask("15 * 2", session="first")
    second = agent.ask("9 + 1", session="second")
    assert first.agent == second.agent == "AnotherMe"
    assert Reply.model_validate_json(first.model_dump_json()) == first
    assert first.original_question == first.rewritten_question == "15 * 2"
    assert first.tool_calls[0].name == "calculator"
    assert first.style_examples == first.profile_evidence == []
    assert len(agent.memory.load("first", "anotherme")) == 2
    assert agent.memory.load("second", "anotherme")[0]["content"] == "9 + 1"
    assert agent.memory.load("first", "lin") == []


def test_model_error_is_visible_and_evidence_still_available(agent):
    class UnavailableModel:
        def structured(self, *args):
            raise RuntimeError("provider unavailable")

    agent.model = UnavailableModel()
    reply = agent.ask("图书馆活动室")
    assert len(reply.warnings) == 2
    assert "离线演示" in reply.answer
    assert reply.sources
    agent.model = None
