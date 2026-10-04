"""Provider activity must outlive a worker budget without hiding real failures."""
import collections
import json
import threading

import pytest

from api import bridge_errors, codex_prime as cp, codex_profiles, prime_delegation, routes


def run_timed(monkeypatch, frames, *, pending=None, cancel=None):
    clock = [0.0]
    messages = collections.deque()

    class Events:
        def put(self, line):
            messages.append(line)

        def get(self, timeout=None):
            line = messages.popleft()
            if line is not None:
                clock[0] += json.loads(line).get("advance_seconds", 0.1)
            return line

    class ImmediateThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    class Proc:
        stdout = iter(json.dumps(frame) + "\n" for frame in frames)
        stderr = iter(())
        stdin = type("Input", (), {"write": lambda self, text: None, "close": lambda self: None})()

        def wait(self, timeout=None):
            return 0

    class Runtime:
        url = "http://127.0.0.1:1/mcp"
        revoked = False

        def register(self, *args):
            return "test-token"

        def revoke(self, token):
            self.revoked = True

    runtime = Runtime()
    stopped = []
    monkeypatch.setattr(cp, "get_runtime", lambda: runtime)
    monkeypatch.setattr(cp, "build_tools", lambda *args: [])
    monkeypatch.setattr(cp.queue, "Queue", Events)
    monkeypatch.setattr(cp.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(cp.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(cp.subprocess, "Popen", lambda *args, **kwargs: Proc())
    monkeypatch.setattr(cp, "stop_process", stopped.append)
    monkeypatch.setattr(prime_delegation, "_resolve_codex_executable", lambda: "codex")
    monkeypatch.setattr(prime_delegation, "_CODEX_TIMEOUT", 1000)
    monkeypatch.setattr(codex_profiles, "cli_args", lambda **kwargs: [])
    monkeypatch.setattr(routes, "get_clarify_pending_count", lambda sid: pending(clock[0]) if pending else 0)
    streamed = []
    try:
        result = cp.run_prime("prompt", ".", session_id="hermes-prime",
                              cancel=cancel or threading.Event(), on_token=streamed.append)
        return result, streamed
    finally:
        assert runtime.revoked
        assert len(stopped) == 1


def activity(seconds=600):
    return {"type": "item.updated", "advance_seconds": seconds,
            "item": {"type": "command_execution"}}


FINAL = {"type": "item.completed", "item": {"type": "agent_message", "phase": "final_answer", "text": "Done"}}
DONE = {"type": "turn.completed", "usage": {"output_tokens": 1}}


def test_active_prime_turn_completes_after_old_absolute_timeout(monkeypatch):
    monkeypatch.delenv("HERMES_CODEX_PRIME_HARD_CAP", raising=False)
    result, streamed = run_timed(monkeypatch, [activity(), activity(), activity(), FINAL, DONE])
    assert result["reply"] == "Done"
    assert streamed == ["Done"]
    assert result["usage"]["output_tokens"] == 1


def test_silence_still_times_out_and_is_not_transport_cut(monkeypatch):
    monkeypatch.delenv("HERMES_CODEX_PRIME_HARD_CAP", raising=False)
    with pytest.raises(cp.PrimeIdleTimeout) as error:
        run_timed(monkeypatch, [{"type": "test.silence", "advance_seconds": 1001}])
    assert bridge_errors.classify_branch(error.value) == bridge_errors.PRIME_IDLE_TIMEOUT


def test_absolute_cap_stops_continuous_activity(monkeypatch):
    monkeypatch.setenv("HERMES_CODEX_PRIME_HARD_CAP", "1100")
    with pytest.raises(cp.PrimeHardCapError) as error:
        run_timed(monkeypatch, [activity(), activity(), FINAL, DONE])
    assert bridge_errors.classify_branch(error.value) == bridge_errors.PRIME_HARDCAP


def test_human_wait_does_not_consume_idle_or_absolute_budget(monkeypatch):
    monkeypatch.setenv("HERMES_CODEX_PRIME_HARD_CAP", "1100")
    frames = [{"type": "test.silence", "advance_seconds": 400} for _ in range(6)]
    result, _ = run_timed(monkeypatch, [*frames, activity(0.1), FINAL, DONE], pending=lambda now: now <= 2400)
    assert result["reply"] == "Done"


@pytest.mark.parametrize("value", ["invalid", "0", "-1", "nan", "inf"])
def test_invalid_cap_uses_bounded_default(monkeypatch, value):
    monkeypatch.setenv("HERMES_CODEX_PRIME_HARD_CAP", value)
    result, _ = run_timed(monkeypatch, [activity(), activity(), FINAL, DONE])
    assert result["reply"] == "Done"


def test_explicit_cancel_cleans_up_without_timeout(monkeypatch):
    cancel = threading.Event()
    cancel.set()
    result, streamed = run_timed(monkeypatch, [], cancel=cancel)
    assert result["cancelled"] is True
    assert streamed == []
