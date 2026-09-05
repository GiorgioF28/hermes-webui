"""Ripiego Codex -> Claude: ragione vera e modello mostrati nel pannello.

Caso reale (d485, 2026-09-05): Codex CLI e' uscito con exit 1 perche' la quota
era esaurita, ma lo stderr iniziava con un errore cosmetico della cache modelli
(``codex_models_manager::cache: failed to load models cache``). La ragione
registrata (tagliata a 240 caratteri) mostrava solo quello, nascondendo il
messaggio di quota e la data di reset.
"""

from __future__ import annotations

import time

from api import prime_delegation as pd

_STDERR_D485 = (
    "Codex CLI exit 1: 2026-09-05T17:14:59.976678Z ERROR codex_models_manager::cache: "
    "failed to load models cache: missing field `base_instructions` at line 132 column 5\n"
    "OpenAI Codex v0.146.0\n--------\nworkdir: C:\\Users\\giorg\\Documents\\Hermes setup\n"
    "model: gpt-5.6-sol\nprovider: openai\napproval: never\nsandbox: read-only\n"
    "reasoning effort: medium\nreasoning summaries: auto\n--------\n"
    "ERROR: You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), "
    "visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again "
    "at Sep 7th, 2026 11:14 AM.\n"
    "ERROR: You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro)."
)


def test_exhausted_reason_is_the_quota_line_not_the_cache_noise(monkeypatch):
    monkeypatch.setattr(pd, "_CODEX_FALLBACK_STATE", {"until": 0.0, "reason": "", "last_failure": 0.0})
    status = pd._mark_codex_exhausted(_STDERR_D485, now=time.time())
    reason = status["reason"]
    assert "usage limit" in reason.lower()
    assert "Sep 7th, 2026 11:14 AM" in reason, "la data di reset deve restare visibile"
    assert "models cache" not in reason and "codex_models_manager" not in reason


def test_exhausted_reason_without_quota_line_keeps_the_original_text(monkeypatch):
    monkeypatch.setattr(pd, "_CODEX_FALLBACK_STATE", {"until": 0.0, "reason": "", "last_failure": 0.0})
    status = pd._mark_codex_exhausted("Codex CLI exit 1: boom\nsecond line", now=time.time())
    assert status["reason"] == "Codex CLI exit 1: boom second line"


def test_progress_placeholder_names_the_real_fallback_model(monkeypatch):
    monkeypatch.delenv("HERMES_CODEX_FALLBACK_MODEL", raising=False)
    progress: dict = {}
    pd._set_progress_fallback(progress, "quota", "programmatore-project-engineer")
    assert progress["fallback_model"] == pd.codex_fallback_model("programmatore-project-engineer")
    assert progress["fallback_model"] in progress["output"]
    assert "4.6" not in progress["output"]
