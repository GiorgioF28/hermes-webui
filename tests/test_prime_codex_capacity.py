"""Capacity recovery uses one turn owner and never repeats observed work."""
import json
import threading

import pytest

from api import bridge_errors as be, codex_prime as cp, codex_profiles, prime_delegation, routes
from tests.test_prime_transport_continuity import bridge  # noqa: F401


CAPACITY = {"type": "turn.failed", "error": {"message": "Selected model is at capacity. Please try a different model."}}
FINAL = [
    {"type": "item.completed", "item": {"type": "agent_message", "phase": "final_answer", "text": "Recap verificato."}},
    {"type": "turn.completed", "usage": {"input_tokens": 2, "output_tokens": 3}},
]


class Proc:
    def __init__(self, events, code):
        self.stdout = iter(json.dumps(e) + "\n" for e in events)
        self.stderr = iter(())
        self.stdin = self
        self.code = code
        self.prompt = ""

    def write(self, text): self.prompt += text
    def close(self): pass
    def wait(self, timeout=None): return self.code
    def poll(self): return self.code


@pytest.fixture
def runner(monkeypatch, tmp_path):
    commands, processes, registered, revoked, statuses, tokens = [], [], [], [], [], []
    responses = []
    class Runtime:
        url = "http://127.0.0.1:1/mcp"
        def register(self, tools, cancel, on_tool):
            registered.append(on_tool)
            return str(len(registered))
        def revoke(self, token): revoked.append(token)
    def launch(command, **kwargs):
        commands.append(command)
        events, code = responses.pop(0)
        proc = Proc(events, code)
        processes.append(proc)
        return proc
    monkeypatch.setattr(cp, "get_runtime", lambda: Runtime())
    monkeypatch.setattr(cp, "build_tools", lambda *a: [])
    monkeypatch.setattr(cp.subprocess, "Popen", launch)
    monkeypatch.setattr(prime_delegation, "_resolve_codex_executable", lambda: "codex")
    monkeypatch.setattr(routes, "get_clarify_pending_count", lambda sid: 0)
    cancel = threading.Event()
    def run():
        return cp.run_prime("richiesta originale", tmp_path, session_id="capacity-test",
                            cancel=cancel, on_token=tokens.append, on_status=statuses.append)
    return responses, commands, processes, registered, revoked, statuses, tokens, cancel, run


def test_capacity_recovery_preserves_prompt_and_reports_actual_model(runner):
    responses, commands, processes, registered, revoked, statuses, tokens, cancel, run = runner
    responses.extend([([CAPACITY], 1), (FINAL, 0)])
    result = run()
    assert [c[c.index("--model") + 1] for c in commands] == ["gpt-6.1-sol", "gpt-6-luna"]
    assert [p.prompt for p in processes] == ["richiesta originale"] * 2
    assert result["reply"] == "Recap verificato."
    assert tokens == ["Recap verificato."]
    assert result["usage"]["model"] == "gpt-6-luna"
    assert result["usage"]["requested_model"] == "gpt-6.1-sol"
    assert result["usage"]["fallback_reason"] == "codex_capacity"
    assert codex_profiles.profile(chief=True)["model"] == "gpt-6.1-sol"
    assert revoked == ["1", "2"]
    assert {"state": "capacity_fallback", "model": "gpt-6-luna"} in statuses
    for c in commands:
        assert c[c.index("--sandbox") + 1] == "danger-full-access"
        assert 'approval_policy="never"' in c
        assert "mcp_servers.hermes_prime.required=true" in c


@pytest.mark.parametrize("activity", [
    {"type": "item.started", "item": {"type": "command_execution"}},
    {"type": "item.started", "item": {"type": "mcp_tool_call"}},
    {"type": "item.completed", "item": {"type": "agent_message", "phase": "commentary", "text": "Avvio delega."}},
    {"type": "item.updated", "item": {"type": "agent_message", "text": "Parziale"}},
])
def test_capacity_after_observed_work_is_never_retried(runner, activity):
    responses, commands, *_, run = runner
    responses.append(([activity, CAPACITY], 1))
    with pytest.raises(cp.CodexCapacityError) as caught:
        run()
    assert not caught.value.safe_to_retry
    assert len(commands) == 1


def test_fallback_capacity_is_terminal_after_two_attempts(runner):
    responses, commands, *_, run = runner
    responses.extend([([CAPACITY], 1), ([CAPACITY], 1)])
    with pytest.raises(cp.CodexCapacityError): run()
    assert len(commands) == 2


def test_quota_auth_and_generic_exit_do_not_trigger_model_fallback(runner):
    responses, commands, *_, run = runner
    for message in ("You've hit your usage limit", "not logged in", "some build failure"):
        responses.append(([{"type": "turn.failed", "error": {"message": message}}], 1))
        with pytest.raises(RuntimeError): run()
    assert len(commands) == 3


def test_cancel_during_fallback_status_prevents_second_launch(runner):
    responses, commands, processes, registered, revoked, statuses, tokens, cancel, run = runner
    responses.append(([CAPACITY], 1))
    class CancelOnStatus:
        def append(self, status): cancel.set()
    # Keep the exact production wrapper while cancelling in its status callback.
    result = cp.run_prime("richiesta", ".", session_id="capacity-test", cancel=cancel,
                          on_status=CancelOnStatus().append)
    assert result["cancelled"]
    assert len(commands) == 1


def test_tool_callback_alone_prevents_retry(runner, monkeypatch):
    responses, commands, processes, registered, revoked, statuses, tokens, cancel, run = runner
    responses.append(([CAPACITY], 1))
    original = cp.subprocess.Popen
    def launch(*args, **kwargs):
        registered[-1]("delega")
        return original(*args, **kwargs)
    monkeypatch.setattr(cp.subprocess, "Popen", launch)
    with pytest.raises(cp.CodexCapacityError) as caught: run()
    assert not caught.value.safe_to_retry
    assert len(commands) == 1


def test_capacity_has_specific_branch_before_auth_shutdown_noise():
    info = be.classify(RuntimeError("Codex Prime: Selected model is at capacity. OAuth credential warning"))
    assert info["branch"] == be.CODEX_CAPACITY
    assert "saturo" in info["message"]


def test_recovery_persists_one_reply_under_the_existing_turn_owner(runner, bridge, monkeypatch):
    responses, commands, *_, run = runner
    responses.extend([([CAPACITY], 1), (FINAL, 0)])
    store, handler = bridge
    def reply(message, workspace, **kwargs):
        return cp.run_prime(message, workspace, session_id="capacity-test", cancel=threading.Event(),
                            on_token=kwargs.get("on_token"), on_status=kwargs.get("on_status"))
    monkeypatch.setattr(routes, "_call_hermes_prime_reply_for_bridge", reply)
    routes._handle_bridge_prime(handler, {"message": "Riporta D665 e D666"})
    messages = store.history()["messages"]
    assert [(m["role"], m["content"]) for m in messages] == [
        ("user", "Riporta D665 e D666"), ("assistant", "Recap verificato.")]
    assert messages[-1]["usage"]["model"] == "gpt-6-luna"
    assert len(commands) == 2
    assert not store.live()["active"]
