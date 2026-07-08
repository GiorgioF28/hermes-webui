"""Test Fase 2 Punto 5: preset lean Prime (<8k token) — drop claude_code preset.

Verifica:
- lean_preset_enabled: abilitato per default, disabilitato con HERMES_PRIME_USE_LEAN_PRESET=0
- build_lean_system_prompt: ritorna str (lean) o dict (compat)
- estimate_tokens: stima corretta
- token budget: il prompt lean standalone < 2000 token (≈ <8000 token con append tipico)
"""
from __future__ import annotations

import os

import pytest

from api.prime_lean_preset import (
    build_lean_system_prompt,
    estimate_tokens,
    lean_preset_enabled,
)


# ── lean_preset_enabled ───────────────────────────────────────────────────────

def test_lean_preset_enabled_default(monkeypatch):
    """HERMES_PRIME_USE_LEAN_PRESET non settato → True."""
    monkeypatch.delenv("HERMES_PRIME_USE_LEAN_PRESET", raising=False)
    assert lean_preset_enabled() is True


def test_lean_preset_disabled_by_env(monkeypatch):
    """HERMES_PRIME_USE_LEAN_PRESET=0 → False."""
    monkeypatch.setenv("HERMES_PRIME_USE_LEAN_PRESET", "0")
    assert lean_preset_enabled() is False


def test_lean_preset_disabled_by_false(monkeypatch):
    monkeypatch.setenv("HERMES_PRIME_USE_LEAN_PRESET", "false")
    assert lean_preset_enabled() is False


def test_lean_preset_disabled_by_off(monkeypatch):
    monkeypatch.setenv("HERMES_PRIME_USE_LEAN_PRESET", "off")
    assert lean_preset_enabled() is False


def test_lean_preset_enabled_by_1(monkeypatch):
    monkeypatch.setenv("HERMES_PRIME_USE_LEAN_PRESET", "1")
    assert lean_preset_enabled() is True


# ── build_lean_system_prompt ──────────────────────────────────────────────────

def test_build_lean_returns_string_when_enabled(monkeypatch):
    """Con lean abilitato, build_lean_system_prompt ritorna una stringa."""
    monkeypatch.setenv("HERMES_PRIME_USE_LEAN_PRESET", "1")
    result = build_lean_system_prompt(["extra content"])
    assert isinstance(result, str), "lean deve ritornare str, non dict"


def test_build_lean_returns_dict_when_disabled(monkeypatch):
    """Con lean disabilitato, build_lean_system_prompt ritorna dict con preset=claude_code."""
    monkeypatch.setenv("HERMES_PRIME_USE_LEAN_PRESET", "0")
    result = build_lean_system_prompt(["extra content"])
    assert isinstance(result, dict), "compat deve ritornare dict"
    assert result["preset"] == "claude_code"
    assert result["type"] == "preset"
    assert "extra content" in result["append"]


def test_build_lean_includes_append_parts(monkeypatch):
    """Le append_parts vengono incluse nel prompt lean."""
    monkeypatch.setenv("HERMES_PRIME_USE_LEAN_PRESET", "1")
    result = build_lean_system_prompt(["parte extra test"])
    assert isinstance(result, str)
    assert "parte extra test" in result


def test_build_lean_empty_parts(monkeypatch):
    """build_lean_system_prompt con lista vuota non crasha."""
    monkeypatch.setenv("HERMES_PRIME_USE_LEAN_PRESET", "1")
    result = build_lean_system_prompt([])
    assert isinstance(result, str)


def test_build_lean_filters_empty_parts(monkeypatch):
    """build_lean_system_prompt filtra le parti vuote."""
    monkeypatch.setenv("HERMES_PRIME_USE_LEAN_PRESET", "0")
    result = build_lean_system_prompt(["", "contenuto", ""])
    assert result["append"] == "contenuto"


# ── estimate_tokens ────────────────────────────────────────────────────────────

def test_estimate_tokens():
    """Stima di base: 1000 chars → 250 token."""
    assert estimate_tokens("x" * 1000) == 250


def test_estimate_tokens_zero_returns_one():
    """estimate_tokens di stringa vuota → 1 (minimo)."""
    assert estimate_tokens("") == 1


def test_estimate_tokens_short():
    """Stringa corta: 4 chars → 1 token."""
    assert estimate_tokens("abcd") == 1


# ── token budget ───────────────────────────────────────────────────────────────

def test_lean_prompt_under_8k_tokens(monkeypatch):
    """Il prompt lean standalone (senza append) deve essere < 2000 token.

    Soglia 2000 = 8000 chars / 4 = 2000 token.
    Il lean da solo (≈500 token) lascia ampio margine per brief + memoria + footer.
    """
    monkeypatch.setenv("HERMES_PRIME_USE_LEAN_PRESET", "1")
    result = build_lean_system_prompt([])
    assert isinstance(result, str)
    tokens = estimate_tokens(result)
    assert tokens < 2000, (
        f"Il prompt lean da solo è {tokens} token (chars={len(result)}), "
        f"dovrebbe essere < 2000"
    )
