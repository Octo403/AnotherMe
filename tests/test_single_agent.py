import sqlite3
import sys

import pytest
from pydantic import ValidationError

from aitown.cli import main
from aitown.config import Settings
from aitown.context import ContextStore
from aitown.graph import AnotherMeAgent
from aitown.models import AnswerPayload, Decision, ReviewPayload


def test_settings_invalid_values_and_secret_representation(monkeypatch):
    with pytest.raises(ValidationError, match="LLM_API_KEY"):
        Settings(mode="live")
    for invalid in ({"mode": "other"}, {"history_turns": 4}, {"temperature": 3},
                    {"max_tokens": 0}, {"source_scope": "all_chats"}):
        with pytest.raises(ValidationError):
            Settings(**invalid)
    settings = Settings(api_key="secret-for-test")
    assert "secret-for-test" not in repr(settings)
    assert "api_key" not in settings.model_dump()
    monkeypatch.setenv("AITOWN_MODE", "live")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_MODEL", "")
    assert Settings.from_env(mode="demo").mode == "demo"


def test_cli_rejects_legacy_commands_and_hides_secrets(monkeypatch, capsys):
    for args in (["aitown", "town"], ["aitown", "ask", "hello", "--resident", "lin"]):
        monkeypatch.setattr(sys, "argv", args)
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 2
    capsys.readouterr()
    monkeypatch.setenv("LLM_API_KEY", "secret-for-test")
    monkeypatch.setenv("HISTORY_TURNS", "4")
    monkeypatch.setattr(sys, "argv", ["aitown", "--mode", "demo", "ask", "hello"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    error = capsys.readouterr().err
    assert "history_turns" in error
    assert "secret-for-test" not in error


def test_legacy_database_migration_preserves_history(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE turns(id INTEGER PRIMARY KEY, session TEXT, "
                     "resident TEXT, question TEXT, answer TEXT, created REAL)")
        conn.execute("INSERT INTO turns VALUES(1, 'old', 'lin', 'q', 'a', strftime('%s','now'))")
    store = ContextStore(path)
    try:
        assert store.load("old", "lin")[0]["content"] == "q"
        assert store.load("old", "anotherme") == []
        store.save("old", "anotherme", "new", "answer")
        assert len(store.load("old", "lin")) == 2
    finally:
        store.close()
    store = ContextStore(path)
    assert store.load("old", "anotherme")[0]["content"] == "new"
    store.close()


def test_six_turns_restart_and_review_corrections(tmp_path):
    settings = Settings(knowledge_dir=tmp_path / "empty", memory_db=tmp_path / "history.sqlite3")
    agent = AnotherMeAgent(settings)
    for i in range(6):
        assert agent.ask(f"{i} + 1", session="six").skills[0].ok
    agent.close()
    agent = AnotherMeAgent(settings)
    assert len(agent.memory.load("six", "anotherme")) == 12

    class Model:
        def structured(self, messages, schema):
            if schema is Decision:
                return Decision(intent="chat", query="hello")
            if schema is AnswerPayload:
                return AnswerPayload(answer="draft")
            assert schema is ReviewPayload
            return ReviewPayload(answer="corrected", corrections=["unsupported fact removed"])

        def close(self):
            pass

    agent.model = Model()
    try:
        reply = agent.ask("hello", session="review")
        assert reply.answer == "corrected"
        assert reply.correction_count == 1
        assert reply.warnings == ["unsupported fact removed"]
        assert reply.original_question == reply.rewritten_question == "hello"
    finally:
        agent.close()
