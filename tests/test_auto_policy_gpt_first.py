"""Politica "Auto" dei sotto-agenti: GPT (Codex) per tutti finche' ha crediti,
poi Claude scelto per agente.

Prima l'escape hatch (task_type opus/ragiona/reason/claude) mandava su Claude
Opus anche con Codex disponibile, e il fallback a quota esaurita era un solo
modello per tutti (Sonnet 4.6). Ora: Codex sempre; a quota esaurita ogni
agente cade sul Claude che gli conviene (Librarian economico, Programmatore
il piu' forte nel codice, Social/Ricercatore Sonnet).
"""

from __future__ import annotations

import pytest

from api import agent_models, prime_delegation as pd


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_models, "STORE_PATH", tmp_path / "agent_models.json")
    monkeypatch.delenv("HERMES_SUBAGENT_BRAIN", raising=False)
    monkeypatch.delenv("HERMES_CODEX_FALLBACK_MODEL", raising=False)


@pytest.mark.parametrize("task_type", ["opus", "ragiona bene", "reason deeply", "claude", "ricerca", "codice"])
@pytest.mark.parametrize("agent", ["ricercatore", "programmatore", "social", ""])
def test_auto_is_codex_for_everyone(task_type, agent):
    assert pd._model_for(task_type, agent) == (pd._CODEX_MODEL, "Codex")


def test_claude_fallback_is_chosen_per_agent():
    assert pd.codex_fallback_model("librarian") == "claude-haiku-4-5"
    assert pd.codex_fallback_model("memory-librarian") == "claude-haiku-4-5"
    assert pd.codex_fallback_model("programmatore") == "claude-opus-5"
    assert pd.codex_fallback_model("social") == "claude-sonnet-5"
    assert pd.codex_fallback_model("ricercatore") == "claude-sonnet-5"


def test_unknown_agent_falls_back_to_sonnet():
    assert pd.codex_fallback_model("qualcosa-di-nuovo") == "claude-sonnet-5"
    assert pd.codex_fallback_model("") == "claude-sonnet-5"
    assert pd.codex_fallback_model(None) == "claude-sonnet-5"


def test_env_override_still_wins_for_everyone(monkeypatch):
    monkeypatch.setenv("HERMES_CODEX_FALLBACK_MODEL", "claude-opus-5")
    assert pd.codex_fallback_model("librarian") == "claude-opus-5"


def test_subagent_brain_claude_uses_per_agent_model(monkeypatch):
    monkeypatch.setenv("HERMES_SUBAGENT_BRAIN", "claude")
    assert pd._model_for("codice", "programmatore") == ("claude-opus-5", "Opus 5")
    assert pd._model_for("ricerca", "ricercatore") == ("claude-sonnet-5", "Sonnet 5")


def test_manual_override_still_wins():
    agent_models.set_override("research-analyst", "claude-fable-5-1")
    assert pd._model_for("ricerca", "ricercatore") == ("claude-fable-5-1", "Fable 5.1")


def test_librarian_pass_runs_on_the_cheap_model():
    assert pd._LIBRARIAN_MODEL == "claude-haiku-4-5"
