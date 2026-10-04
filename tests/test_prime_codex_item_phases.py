"""Offline contract tests for Codex Prime commentary/final item handling."""
import json
import threading

from api import codex_prime, prime_delegation, codex_profiles, routes


class FakeRuntime:
    url = "http://127.0.0.1:1/mcp"
    def register(self, *args): return "test-token"
    def revoke(self, token): pass


class FakeProc:
    def __init__(self, events):
        self.stdout = iter(json.dumps(e) + "\n" for e in events)
        self.stderr = iter(())
        self.stdin = self
        self.returncode = 0
    def write(self, text): pass
    def close(self): pass
    def wait(self, timeout=None): return 0
    def poll(self): return 0
    def kill(self): pass


def _run(monkeypatch, items):
    proc = FakeProc([*items, {"type": "turn.completed", "usage": {"output_tokens": 3}}])
    monkeypatch.setattr(codex_prime, "get_runtime", lambda: FakeRuntime())
    monkeypatch.setattr(codex_prime, "build_tools", lambda *a: [])
    def launch(command, **kwargs):
        assert command[command.index("--sandbox") + 1] == "danger-full-access"
        assert 'approval_policy="never"' in command
        assert "--approve-for-me" not in command
        assert command[-1] == "-"
        assert "mcp_servers.hermes_prime.required=true" in command
        return proc
    monkeypatch.setattr(codex_prime.subprocess, "Popen", launch)
    monkeypatch.setattr(prime_delegation, "_resolve_codex_executable", lambda: "codex")
    monkeypatch.setattr(codex_profiles, "cli_args", lambda **k: [])
    monkeypatch.setattr(routes, "get_clarify_pending_count", lambda sid: 0)
    streamed = []
    result = codex_prime.run_prime("prompt", ".", session_id="hermes-prime",
        cancel=threading.Event(), on_token=streamed.append)
    return result, streamed


def test_canonical_final_replaces_commentary_without_losing_stream_context(monkeypatch):
    result, streamed = _run(monkeypatch, [
        {"type":"item.completed", "item":{"type":"agent_message", "phase":"commentary", "text":"Sto analizzando."}},
        {"type":"item.completed", "item":{"type":"agent_message", "phase":"final_answer", "text":"Risposta definitiva."}},
    ])
    assert result["reply"] == "Risposta definitiva."
    assert streamed == ["Sto analizzando.", "\n\nRisposta definitiva."]


def test_missing_phase_uses_last_completed_message_as_legacy_final(monkeypatch):
    result, _ = _run(monkeypatch, [
        {"type":"item.completed", "item":{"type":"agent_message", "text":"Passaggio intermedio."}},
        {"type":"item.completed", "item":{"type":"agent_message", "text":"Finale legacy."}},
    ])
    assert result["reply"] == "Finale legacy."
