"""Test Cantiere 1: tetto duro sessione Prime + cut a fine task.

Verifica:
- session_cap_tokens() legge HERMES_PRIME_SESSION_CAP_TOKENS (default 80k)
- Il compact viene FORZATO quando before_tokens >= cap (ignora cooldown)
- Il compact non scatta se before_tokens < cap (cooldown ancora valido)
- request_compact_after_task / pop_compact_after_task: flag thread-safe
- Il compact forzato post-task (force=True) ignora threshold/cooldown
- after_tokens=0 viene loggato ma NON trattato come errore
- compute_compact_savings calcola delta correttamente
"""
from __future__ import annotations

import asyncio
import json

import pytest

from api import prime_auto_compact


class _ResultMessage:
    def __init__(self, usage):
        self.usage = usage


class _FakeClient:
    def __init__(self, after_usage=None):
        self.queries: list[str] = []
        self._after_usage = after_usage or {}

    async def query(self, message):
        self.queries.append(message)

    async def receive_response(self):
        yield _ResultMessage(self._after_usage)


class _FakeRegistry:
    def __init__(self, after_usage=None):
        self.client = _FakeClient(after_usage)
        self.turns: list[tuple] = []

    def run_turn(self, session_id, drive, timeout=None):
        self.turns.append((session_id, timeout))
        return asyncio.run(drive(self.client))


def _setup(monkeypatch, tmp_path, *, threshold=100, cooldown=2, cap=0):
    monkeypatch.setenv("PRIME_COMPACT_THRESHOLD", str(threshold))
    monkeypatch.setenv("PRIME_COMPACT_COOLDOWN_TURNS", str(cooldown))
    monkeypatch.setenv("HERMES_PRIME_SESSION_CAP_TOKENS", str(cap))
    monkeypatch.setenv("PRIME_AUTO_COMPACT_EVENTS", str(tmp_path / "prime-auto.jsonl"))
    monkeypatch.setenv("PRIME_AUTO_COMPACT_ENABLED", "1")
    prime_auto_compact.reset_state_for_tests(
        threshold_tokens=threshold,
        cooldown_turns=cooldown,
    )
    # Reset il flag post-task per ogni test
    prime_auto_compact._COMPACT_AFTER_TASK_FLAG.clear()


# ── session_cap_tokens ────────────────────────────────────────────────────────

def test_session_cap_default(monkeypatch):
    monkeypatch.delenv("HERMES_PRIME_SESSION_CAP_TOKENS", raising=False)
    assert prime_auto_compact.session_cap_tokens() == prime_auto_compact.DEFAULT_SESSION_CAP_TOKENS


def test_session_cap_from_env(monkeypatch):
    monkeypatch.setenv("HERMES_PRIME_SESSION_CAP_TOKENS", "50000")
    assert prime_auto_compact.session_cap_tokens() == 50_000


def test_session_cap_zero_disables(monkeypatch):
    monkeypatch.setenv("HERMES_PRIME_SESSION_CAP_TOKENS", "0")
    assert prime_auto_compact.session_cap_tokens() == 0


# ── Compact forzato da cap ────────────────────────────────────────────────────

def test_session_cap_forces_compact_ignoring_cooldown(monkeypatch, tmp_path):
    """Cap superato → compact forzato anche con cooldown attivo."""
    _setup(monkeypatch, tmp_path, threshold=1000, cooldown=5, cap=500)
    reg = _FakeRegistry(after_usage={"input_tokens": 10})

    # cooldown = 5, turns_since_compact = 5 (ok per threshold) MA before = 600 > cap=500
    result = prime_auto_compact.maybe_auto_compact_prime(
        reg,
        session_id="hermes-prime",
        usage={"input_tokens": 400, "cache_read_input_tokens": 200},  # = 600 > cap=500
        idle=True,
    )

    assert result["compacted"] is True
    assert result["reason"] == "session_cap_exceeded"
    assert result["forced"] is True
    assert result["before_tokens"] == 600
    assert reg.client.queries == ["/compact"]


def test_session_cap_not_exceeded_no_force(monkeypatch, tmp_path):
    """Before < cap → il compact segue il normale threshold+cooldown."""
    _setup(monkeypatch, tmp_path, threshold=1000, cooldown=5, cap=500)
    reg = _FakeRegistry()

    result = prime_auto_compact.maybe_auto_compact_prime(
        reg,
        session_id="hermes-prime",
        usage={"input_tokens": 300},  # 300 < cap=500, 300 < threshold=1000
        idle=True,
    )

    assert result["compacted"] is False
    assert result["reason"] == "below_threshold"
    assert reg.client.queries == []


def test_session_cap_disabled_when_zero(monkeypatch, tmp_path):
    """Cap=0 → solo la logica threshold/cooldown normale."""
    _setup(monkeypatch, tmp_path, threshold=100, cooldown=0, cap=0)
    reg = _FakeRegistry(after_usage={"input_tokens": 5})

    # before=200 > threshold=100, cooldown=0 → compatta normalmente
    result = prime_auto_compact.maybe_auto_compact_prime(
        reg,
        session_id="hermes-prime",
        usage={"input_tokens": 200},
        idle=True,
    )

    assert result["compacted"] is True
    assert result["reason"] == "threshold_exceeded"  # non "session_cap_exceeded"


# ── request_compact_after_task / pop_compact_after_task ──────────────────────

def test_compact_after_task_flag_lifecycle():
    prime_auto_compact._COMPACT_AFTER_TASK_FLAG.clear()
    assert prime_auto_compact.pop_compact_after_task() is False

    prime_auto_compact.request_compact_after_task()
    assert prime_auto_compact.pop_compact_after_task() is True

    # Il flag è stato consumato: seconda lettura ritorna False
    assert prime_auto_compact.pop_compact_after_task() is False


def test_compact_after_task_force_ignores_cooldown(monkeypatch, tmp_path):
    """force=True compatta anche con cooldown attivo e threshold non raggiunta."""
    _setup(monkeypatch, tmp_path, threshold=10_000, cooldown=99, cap=0)
    reg = _FakeRegistry(after_usage={"input_tokens": 5})

    result = prime_auto_compact.maybe_auto_compact_prime(
        reg,
        session_id="hermes-prime",
        usage={"input_tokens": 500},  # sotto threshold=10000
        idle=True,
        force=True,
    )

    assert result["compacted"] is True
    assert result["reason"] == "task_done_cut"
    assert result["forced"] is True
    assert reg.client.queries == ["/compact"]


def test_compact_after_task_force_zero_tokens_noop(monkeypatch, tmp_path):
    """force=True ma before_tokens=0 → noop (non sappiamo se c'è contesto)."""
    _setup(monkeypatch, tmp_path, threshold=10_000, cooldown=0, cap=0)
    reg = _FakeRegistry()

    result = prime_auto_compact.maybe_auto_compact_prime(
        reg,
        session_id="hermes-prime",
        usage={},  # before_tokens = 0
        idle=True,
        force=True,
    )

    # force=True con before=0 → no compact (non c'è contesto da compattare)
    assert result["compacted"] is False
    assert reg.client.queries == []


# ── after_tokens=0 ────────────────────────────────────────────────────────────

def test_after_tokens_zero_is_logged_not_error(monkeypatch, tmp_path, caplog):
    """after_tokens=0 dopo compact è atteso; status=ok e warning nel log."""
    import logging
    _setup(monkeypatch, tmp_path, threshold=100, cooldown=0, cap=0)
    # FakeRegistry restituisce usage vuoto → after_tokens=0
    reg = _FakeRegistry(after_usage={})

    with caplog.at_level(logging.INFO, logger="api.prime_auto_compact"):
        result = prime_auto_compact.maybe_auto_compact_prime(
            reg,
            session_id="hermes-prime",
            usage={"input_tokens": 200},  # > threshold=100
            idle=True,
        )

    assert result["compacted"] is True
    assert result["after_tokens"] == 0
    assert result["after_tokens_unknown"] is True
    assert result["status"] == "ok"
    assert "after_tokens=0" in caplog.text


def test_compact_event_includes_after_tokens_unknown_field(monkeypatch, tmp_path):
    """L'evento scritto su disco include il campo after_tokens_unknown."""
    _setup(monkeypatch, tmp_path, threshold=100, cooldown=0, cap=0)
    reg = _FakeRegistry(after_usage={})

    prime_auto_compact.maybe_auto_compact_prime(
        reg,
        session_id="hermes-prime",
        usage={"input_tokens": 200},
        idle=True,
    )

    events = prime_auto_compact.read_prime_auto_compact_events(days=1)
    assert len(events) >= 1
    last = events[-1]
    assert "after_tokens_unknown" in last
    assert last["after_tokens_unknown"] is True


# ── compute_compact_savings ───────────────────────────────────────────────────

def test_compute_compact_savings_known_after():
    events = [
        {"status": "ok", "before_tokens": 1000, "after_tokens": 300, "after_tokens_unknown": False},
        {"status": "ok", "before_tokens": 800, "after_tokens": 200, "after_tokens_unknown": False},
    ]
    s = prime_auto_compact.compute_compact_savings(events)
    assert s["total_compacts"] == 2
    assert s["total_saved_tokens"] == (700 + 600)
    assert s["events_after_unknown"] == 0


def test_compute_compact_savings_unknown_after():
    events = [
        {"status": "ok", "before_tokens": 1000, "after_tokens": 0, "after_tokens_unknown": True},
    ]
    s = prime_auto_compact.compute_compact_savings(events)
    assert s["total_compacts"] == 1
    assert s["total_saved_tokens"] == 0  # sconosciuto, non contiamo
    assert s["events_after_unknown"] == 1


def test_compute_compact_savings_skips_errors():
    events = [
        {"status": "error", "before_tokens": 1000, "after_tokens": 0, "after_tokens_unknown": True},
    ]
    s = prime_auto_compact.compute_compact_savings(events)
    assert s["total_compacts"] == 0
    assert s["total_saved_tokens"] == 0


def test_compute_compact_savings_empty():
    s = prime_auto_compact.compute_compact_savings([])
    assert s["total_compacts"] == 0
    assert s["total_saved_tokens"] == 0


# ── PrimeAutoCompactDecision.forced field ────────────────────────────────────

def test_decision_forced_false_for_normal_compact(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, threshold=100, cooldown=0, cap=0)
    state = prime_auto_compact.PrimeAutoCompactState(threshold_tokens=100, cooldown_turns=0)
    state.turns_since_compact = 0
    decision = state.consider({"input_tokens": 200}, idle=True)
    assert decision.should_compact is True
    assert decision.forced is False
    assert decision.reason == "threshold_exceeded"


def test_decision_forced_true_for_cap(monkeypatch):
    state = prime_auto_compact.PrimeAutoCompactState(threshold_tokens=1000, cooldown_turns=99)
    # cap=500, before=600 → forced
    decision = state.consider({"input_tokens": 600}, idle=True, cap_tokens=500)
    assert decision.should_compact is True
    assert decision.forced is True
    assert decision.reason == "session_cap_exceeded"


def test_decision_forced_true_for_task_done():
    state = prime_auto_compact.PrimeAutoCompactState(threshold_tokens=10_000, cooldown_turns=99)
    decision = state.consider({"input_tokens": 500}, idle=True, force=True)
    assert decision.should_compact is True
    assert decision.forced is True
    assert decision.reason == "task_done_cut"
