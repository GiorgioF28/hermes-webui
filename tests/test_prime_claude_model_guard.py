"""Regressione bug 2026-07-18 (#2): modello Codex nel ramo Claude di Prime.

Con lo store Prime senza modello salvato, il resolver ripiegava sul default del
catalogo WebUI (Codex/GPT "codex 5.5") e lo passava al client CLAUDE -> il CLI
falliva con "There's an issue with the selected model (codex 5.5)". La guardia
_prime_claude_safe_model impedisce a qualunque modello non-Claude di arrivare
al client Claude.
"""
from api import routes


def test_codex_model_falls_back_to_claude():
    state = {"model": "gpt-5.5-codex", "model_provider": "openai-codex"}
    assert routes._prime_claude_safe_model(state) == "claude-opus-5"


def test_codex_display_name_falls_back():
    # Il nome cosi' come appariva nell'errore dal vivo.
    state = {"model": "codex 5.5", "model_provider": ""}
    assert routes._prime_claude_safe_model(state) == "claude-opus-5"


def test_claude_model_passes_through():
    state = {"model": "claude-opus-4-8", "model_provider": "anthropic"}
    assert routes._prime_claude_safe_model(state) == "claude-opus-4-8"


def test_claude_model_without_provider_passes():
    state = {"model": "claude-sonnet-5", "model_provider": ""}
    assert routes._prime_claude_safe_model(state) == "claude-sonnet-5"


def test_empty_state_uses_default():
    assert routes._prime_claude_safe_model({}) == "claude-opus-5"
    assert routes._prime_claude_safe_model(None) == "claude-opus-5"


def test_non_claude_provider_with_claude_model_falls_back():
    # Provider esplicitamente non-Anthropic: non fidarsi anche se il nome inizia
    # con claude (catalogo di terze parti / proxy).
    state = {"model": "claude-opus-4-8", "model_provider": "openai-codex"}
    assert routes._prime_claude_safe_model(state) == "claude-opus-5"


# --- modello mostrato nell'header della UI (_prime_lead_model_id) ------------

def test_lead_model_id_claude_is_always_claude(monkeypatch):
    # Anche se il resolver ripiega su un default Codex, l'id riportato alla UI
    # per il capo Claude deve restare un modello claude*.
    monkeypatch.setattr(
        routes, "_resolve_prime_model_state",
        lambda *a, **k: {"model": "gpt-5.5-codex", "model_provider": "openai-codex"},
    )
    assert routes._prime_lead_model_id("claude").startswith("claude")


def test_lead_model_id_codex_is_codexish():
    got = routes._prime_lead_model_id("codex")
    assert got == "codex" or got.startswith("codex:")
