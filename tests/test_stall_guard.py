"""Stall guard: spezza la chiamata identica ripetuta nei worker Claude.

Come gli "runtime stall guards" di hermes-agent upstream: quando un
sotto-agente rifà la stessa identica chiamata a uno strumento (stesso nome,
stessi argomenti) per la N-esima volta, la chiamata viene negata con un
messaggio che gli dice che il risultato lo ha gia' e di cambiare approccio o
consegnare. Usa l'hook PreToolUse dell'SDK, quindi vale per i worker Claude
(Codex e' un processo one-shot senza hook).
"""

from __future__ import annotations

import asyncio

import pytest

from api import stall_guard


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("HERMES_STALL_GUARD_REPEATS", raising=False)


def test_third_identical_call_is_denied():
    guard = stall_guard.StallGuard()
    assert guard.check("Read", {"file_path": "a.py"}) is None
    assert guard.check("Read", {"file_path": "a.py"}) is None
    reason = guard.check("Read", {"file_path": "a.py"})
    assert reason and "identic" in reason.lower()
    assert guard.denied == 1


def test_argument_order_does_not_matter():
    guard = stall_guard.StallGuard()
    guard.check("Bash", {"command": "ls", "timeout": 5})
    guard.check("Bash", {"timeout": 5, "command": "ls"})
    assert guard.check("Bash", {"command": "ls", "timeout": 5}) is not None


def test_different_calls_are_never_denied():
    guard = stall_guard.StallGuard()
    for i in range(20):
        assert guard.check("Read", {"file_path": f"f{i}.py"}) is None
    assert guard.denied == 0


def test_threshold_is_configurable_and_zero_disables(monkeypatch):
    monkeypatch.setenv("HERMES_STALL_GUARD_REPEATS", "5")
    guard = stall_guard.StallGuard()
    for _ in range(4):
        assert guard.check("Grep", {"pattern": "x"}) is None
    assert guard.check("Grep", {"pattern": "x"}) is not None
    monkeypatch.setenv("HERMES_STALL_GUARD_REPEATS", "0")
    off = stall_guard.StallGuard()
    for _ in range(10):
        assert off.check("Grep", {"pattern": "x"}) is None
    assert off.hooks() is None, "disattivato: nessun hook da montare"


def test_hook_callback_returns_sdk_deny_shape():
    guard = stall_guard.StallGuard()
    cb = guard.callback()
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {"file_path": "a.py"}, "tool_use_id": "t1"}
    assert asyncio.run(cb(payload, "t1", {})) == {}
    assert asyncio.run(cb(payload, "t2", {})) == {}
    out = asyncio.run(cb(payload, "t3", {}))
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse"
    assert spec["permissionDecision"] == "deny"
    assert "identic" in spec["permissionDecisionReason"].lower()


def test_hooks_mount_shape_matches_sdk():
    from claude_agent_sdk import HookMatcher

    hooks = stall_guard.StallGuard().hooks()
    assert set(hooks) == {"PreToolUse"}
    assert isinstance(hooks["PreToolUse"][0], HookMatcher)
    assert hooks["PreToolUse"][0].hooks and callable(hooks["PreToolUse"][0].hooks[0])


def test_summary_is_reported():
    guard = stall_guard.StallGuard()
    for _ in range(4):
        guard.check("Read", {"file_path": "a.py"})
    assert guard.summary() == {"denied": 2, "repeated_calls": {"Read": 4}}


def test_worker_mounts_the_guard_and_reports_it():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "api" / "prime_delegation.py").read_text(encoding="utf-8")
    body = src[src.index("async def _run_worker("):]
    body = body[:body.index("def _claude_wrapup_message(")]
    assert "hooks=_stall_guard.hooks()," in body
    assert 'progress["stall_guard"] = _stall_guard.summary()' in body
