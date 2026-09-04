"""Le domande all'utente (clarify / ask_user) rispettano agent.clarify_timeout,
incluso 0 = attesa illimitata (upstream #7163).

Prima: la chat leggeva solo il vecchio `clarify.timeout` (altrimenti 120 s) e
trattava 0 come non valido; il Command Bridge aveva 600 s fissi nel codice.
config.yaml dell'agente dice `agent.clarify_timeout: 600` e nessuno dei due
lo guardava: una domanda in chat scadeva dopo due minuti.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from api import ask_user_tool, clarify, streaming


# ── resolver ─────────────────────────────────────────────────────────────────

def test_agent_clarify_timeout_is_honoured():
    assert clarify.resolve_clarify_timeout({"agent": {"clarify_timeout": 300}}) == 300


def test_zero_or_negative_means_unlimited():
    assert clarify.resolve_clarify_timeout({"agent": {"clarify_timeout": 0}}) is None
    assert clarify.resolve_clarify_timeout({"agent": {"clarify_timeout": -5}}) is None
    assert clarify.resolve_clarify_timeout({"clarify": {"timeout": 0}}) is None


def test_legacy_key_still_works_but_agent_key_wins():
    assert clarify.resolve_clarify_timeout({"clarify": {"timeout": 45}}) == 45
    assert clarify.resolve_clarify_timeout({"agent": {"clarify_timeout": 300}, "clarify": {"timeout": 45}}) == 300


def test_default_matches_the_agent_default():
    assert clarify.resolve_clarify_timeout({}) == 600
    assert clarify.resolve_clarify_timeout({"agent": {"clarify_timeout": "boh"}}) == 600


def test_streaming_uses_the_shared_resolver(monkeypatch):
    monkeypatch.setattr(streaming, "get_config", lambda: {"agent": {"clarify_timeout": 0}})
    assert streaming._clarify_timeout_seconds() is None
    monkeypatch.setattr(streaming, "get_config", lambda: {"agent": {"clarify_timeout": 90}})
    assert streaming._clarify_timeout_seconds() == 90


# ── payload e metadati: 0 deve sopravvivere ──────────────────────────────────

def test_payload_keeps_zero_timeout():
    assert clarify.normalize_prompt_payload("q?", ["a", "b"], timeout_seconds=0)["timeout_seconds"] == 0
    assert clarify.normalize_prompt_payload("q?", ["a", "b"], timeout_seconds=None)["timeout_seconds"] == clarify.DEFAULT_TIMEOUT_SECONDS


def test_metadata_has_no_expiry_when_unlimited():
    item = clarify._with_timeout_metadata({"timeout_seconds": 0, "requested_at": 1000.0})
    assert item["timeout_seconds"] == 0
    assert item["expires_at"] is None
    limited = clarify._with_timeout_metadata({"timeout_seconds": 30, "requested_at": 1000.0})
    assert limited["expires_at"] == 1030.0


# ── ask_user del Command Bridge ──────────────────────────────────────────────

def _valid_args():
    return {"questions": [{"question": "Quale?", "options": [{"label": "A"}, {"label": "B"}]}]}


def test_ask_user_waits_without_limit_when_configured(monkeypatch):
    monkeypatch.setattr(clarify, "resolve_clarify_timeout", lambda cfg=None: None)
    waits = []

    class _Event:
        def wait(self, timeout=None):
            waits.append(timeout)
            return True

    entry = SimpleNamespace(event=_Event(), result={"answer": "A"}, data={}, clarify_id="c1")
    monkeypatch.setattr(clarify, "submit_pending", lambda sid, payload: (payloads.append(payload) or entry))
    payloads = []
    monkeypatch.setattr(clarify, "format_response_for_agent", lambda data, result: "A", raising=False)

    asyncio.run(ask_user_tool._run_ask_user("hermes-prime", _valid_args()))

    assert waits == [None], "attesa illimitata: event.wait senza timeout"
    assert payloads[0]["timeout_seconds"] == 0


def test_ask_user_uses_configured_limit(monkeypatch):
    monkeypatch.setattr(clarify, "resolve_clarify_timeout", lambda cfg=None: 45)
    waits = []

    class _Event:
        def wait(self, timeout=None):
            waits.append(timeout)
            return True

    entry = SimpleNamespace(event=_Event(), result={"answer": "A"}, data={}, clarify_id="c1")
    monkeypatch.setattr(clarify, "submit_pending", lambda sid, payload: entry)
    monkeypatch.setattr(clarify, "format_response_for_agent", lambda data, result: "A", raising=False)

    asyncio.run(ask_user_tool._run_ask_user("hermes-prime", _valid_args()))
    assert waits == [45]


# ── loop della chat: nessuna scadenza quando il timeout e' None ──────────────

def test_chat_wait_loop_supports_no_deadline_in_source():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "api" / "streaming.py").read_text(encoding="utf-8")
    body = src[src.index("def _clarify_callback_impl("):]
    body = body[:body.index("try:", body.index("entry.event.wait"))]
    assert "deadline = None if timeout is None else" in body
    assert "if deadline is not None" in body
