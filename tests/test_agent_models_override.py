"""Override manuale del modello per sotto-agente (select nel pannello AGENTI)."""

from __future__ import annotations

import pytest

from api import agent_models, prime_delegation


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_models, "STORE_PATH", tmp_path / "agent_models.json")
    monkeypatch.delenv("HERMES_SUBAGENT_BRAIN", raising=False)


def test_store_roundtrip_and_clear():
    assert agent_models.get_overrides() == {}
    agent_models.set_override("research-analyst", "claude-sonnet-5")
    assert agent_models.get_overrides() == {"research-analyst": "claude-sonnet-5"}
    agent_models.set_override("research-analyst", "auto")
    assert agent_models.get_overrides() == {}, "'auto' toglie l'override"


def test_unknown_model_is_rejected():
    with pytest.raises(ValueError):
        agent_models.set_override("research-analyst", "gpt-9-turbo")


def test_override_claude_wins_over_programmatore_codex_rule():
    agent_models.set_override("programmatore-project-engineer", "claude-opus-5")
    model, label = prime_delegation._model_for("codice", "programmatore")
    assert model == "claude-opus-5"
    assert label == "Opus 5"


def test_override_codex_wins_over_explicit_opus_request():
    agent_models.set_override("research-analyst", "codex")
    model, label = prime_delegation._model_for("opus", "ricercatore")
    assert (model, label) == (prime_delegation._CODEX_MODEL, "Codex")


def test_no_override_keeps_legacy_routing():
    model, label = prime_delegation._model_for("codice", "programmatore")
    assert (model, label) == (prime_delegation._CODEX_MODEL, "Codex")


def test_live_agent_slugs_reads_running_tasks(monkeypatch):
    monkeypatch.setattr(prime_delegation, "_BG_TASKS", {
        "d1": {"id": "d1", "agent": "programmatore", "agent_id": "programmatore",
               "task_type": "codice", "status": "in_corso"},
        "d2": {"id": "d2", "agent": "ricercatore", "agent_id": "ricercatore",
               "task_type": "ricerca", "status": "ok", "librarian_status": "in_corso"},
        "d3": {"id": "d3", "agent": "social", "agent_id": "social",
               "task_type": "ricerca", "status": "errore"},
    })
    live = prime_delegation.live_agent_slugs()
    assert set(live) == {"programmatore-project-engineer", "memory-librarian"}
