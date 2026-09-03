"""Il ramo "ragionamento pesante" delle deleghe usa l'Opus corrente.

Prime di default gira su claude-opus-5 (Command Bridge); i sotto-agenti che
chiedono esplicitamente Claude/Opus restavano invece su claude-opus-4-8, una
generazione indietro rispetto al capo. Opus 5.1 non esiste (lineup ufficiale
2026-09: Fable 5.1, Opus 5, Sonnet 5, Haiku 4.5), quindi l'Opus da usare e'
claude-opus-5.
"""

from __future__ import annotations

import pytest

from api import prime_delegation


CURRENT_OPUS = "claude-opus-5"


@pytest.mark.parametrize("task_type", ["opus", "ragiona bene", "reason deeply", "claude"])
def test_explicit_reasoning_request_uses_current_opus(task_type, monkeypatch):
    monkeypatch.delenv("HERMES_SUBAGENT_BRAIN", raising=False)
    model, label = prime_delegation._model_for(task_type, "ricercatore")
    assert (model, label) == (CURRENT_OPUS, "Opus")


def test_claude_brain_default_path_uses_current_opus(monkeypatch):
    monkeypatch.setenv("HERMES_SUBAGENT_BRAIN", "claude")
    model, label = prime_delegation._model_for("analisi di mercato", "ricercatore")
    assert (model, label) == (CURRENT_OPUS, "Opus")


def test_no_stale_opus_generation_left_in_routing():
    import pathlib
    src = (pathlib.Path(prime_delegation.__file__)).read_text(encoding="utf-8")
    assert "claude-opus-4-8" not in src, "prime_delegation.py punta ancora a Opus 4.8"


def test_programmatore_still_routes_to_codex(monkeypatch):
    """Regressione: il dev resta su Codex qualunque sia il task_type."""
    monkeypatch.delenv("HERMES_SUBAGENT_BRAIN", raising=False)
    model, label = prime_delegation._model_for("opus", "programmatore")
    assert (model, label) == (prime_delegation._CODEX_MODEL, "Codex")
