"""Run budget con wrap-up: un timeout diventa un esito parziale consolidato.

Prima: Codex veniva ucciso a HERMES_CODEX_TIMEOUT (1000 s) e la delega finiva
"errore" con l'output grezzo troncato; il p90 reale delle deleghe era a 961 s.
Ora il budget e' diviso in due fasi: lavoro (80%) e wrap-up (20%). Se il
lavoro sfora, un secondo turno breve riceve l'output parziale e deve
verificare sul disco e consegnare un Agent Result PARZIALE. Stesso schema per
i worker Claude, dove il wrap-up e' iniettato via interrupt() + query().
"""

from __future__ import annotations

import asyncio
import subprocess
import time

import pytest

from api import prime_delegation as pd


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HERMES_CODEX_TIMEOUT", "1000")
    monkeypatch.delenv("HERMES_DELEGATION_WRAPUP_SHARE", raising=False)
    monkeypatch.setattr(pd, "_CODEX_TIMEOUT", 1000)


# ── ripartizione del budget ──────────────────────────────────────────────────

def test_budget_is_split_80_20_by_default():
    assert pd.run_budget_seconds() == 1000
    assert pd.budget_phases() == (800.0, 200.0)


def test_wrapup_share_is_configurable_and_clamped(monkeypatch):
    monkeypatch.setenv("HERMES_DELEGATION_WRAPUP_SHARE", "0.3")
    assert pd.budget_phases() == (700.0, 300.0)
    monkeypatch.setenv("HERMES_DELEGATION_WRAPUP_SHARE", "0")
    assert pd.budget_phases() == (1000.0, 0.0)
    monkeypatch.setenv("HERMES_DELEGATION_WRAPUP_SHARE", "0.9")
    soft, wrap = pd.budget_phases()
    assert wrap <= 500.0 and soft + wrap == 1000.0


# ── Codex: seconda fase di wrap-up ───────────────────────────────────────────

def _timeout(partial: str, timeout: float):
    return subprocess.TimeoutExpired(cmd=["codex"], timeout=timeout, output=partial.encode("utf-8"), stderr=b"")


def test_codex_timeout_triggers_wrapup_turn(monkeypatch, tmp_path):
    calls = []

    def fake_exec(prompt, workspace, timeout=None):
        calls.append((prompt, timeout))
        if len(calls) == 1:
            raise _timeout("ho modificato api/x.py e stavo scrivendo i test", timeout)
        return "## Technical Result\nPatch: proposed (parziale)\n- fatto: api/x.py\n- manca: test"

    monkeypatch.setattr(pd, "_codex_exec_blocking", fake_exec)
    out = asyncio.run(pd._run_codex_worker("sistema x", str(tmp_path), agent_id="programmatore"))

    assert len(calls) == 2
    assert calls[0][1] == 800.0, "prima fase = 80% del budget"
    assert calls[1][1] == 200.0, "wrap-up = 20% del budget"
    wrap_prompt = calls[1][0]
    assert "sistema x" in wrap_prompt
    assert "ho modificato api/x.py" in wrap_prompt, "l'output parziale viaggia nel wrap-up"
    assert "PARZIALE" in wrap_prompt
    assert "Programmatore" in wrap_prompt, "il wrap-up conserva la persona dell'agente"
    assert out.startswith(pd._WRAPUP_MARKER)
    assert "Patch: proposed (parziale)" in out


def test_wrapup_timeout_still_raises_with_partial(monkeypatch, tmp_path):
    def fake_exec(prompt, workspace, timeout=None):
        raise _timeout("partial-1" if timeout == 800.0 else "partial-2", timeout)

    monkeypatch.setattr(pd, "_codex_exec_blocking", fake_exec)
    with pytest.raises(pd.CodexTimeoutError) as info:
        asyncio.run(pd._run_codex_worker("t", str(tmp_path)))
    assert "partial-1" in info.value.partial_output
    assert "partial-2" in info.value.partial_output
    assert info.value.duration_seconds == 1000.0


def test_wrapup_disabled_keeps_single_phase(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_DELEGATION_WRAPUP_SHARE", "0")
    calls = []

    def fake_exec(prompt, workspace, timeout=None):
        calls.append(timeout)
        raise _timeout("p", timeout)

    monkeypatch.setattr(pd, "_codex_exec_blocking", fake_exec)
    with pytest.raises(pd.CodexTimeoutError):
        asyncio.run(pd._run_codex_worker("t", str(tmp_path)))
    assert calls == [1000.0]


def test_fallback_wrapper_marks_progress_partial(monkeypatch, tmp_path):
    async def fake_codex(task, workspace, *, agent_id=None):
        return pd._WRAPUP_MARKER + "esito parziale"

    monkeypatch.setattr(pd, "_run_codex_worker", fake_codex)
    progress = {}
    out = asyncio.run(pd._run_codex_worker_with_fallback("t", str(tmp_path), agent_id="ricercatore", progress=progress))
    assert "esito parziale" in out
    assert progress["result_partial"] is True


def test_run_and_store_records_partial_status(monkeypatch, tmp_path):
    async def fake_codex_with_fallback(task, workspace, *, agent_id=None, progress=None):
        progress["result_partial"] = True
        return pd._WRAPUP_MARKER + "Risultato parziale consolidato. " * 20

    monkeypatch.setattr(pd, "_run_codex_worker_with_fallback", fake_codex_with_fallback)
    monkeypatch.setattr(pd, "_persist_bg_task", lambda *a, **k: None)
    monkeypatch.setattr(pd, "_enqueue_librarian_pass", lambda *a, **k: None)
    monkeypatch.setattr(pd, "_BG_TASKS", {"d1": {
        "id": "d1", "session_id": "hermes-prime", "agent": "programmatore", "agent_id": "programmatore",
        "task_type": "codice", "task": "t", "status": "in_corso", "output": "",
        "started": time.time(), "finished": None,
    }})
    asyncio.run(pd._run_and_store("d1", "codice", "t", pd._CODEX_MODEL, "Codex", str(tmp_path)))
    t = pd._BG_TASKS["d1"]
    assert t["status"] == "parziale"
    assert t["result_partial"] is True
    assert "parziale" in t["output"].lower()


# ── Claude: wrap-up iniettato via interrupt + query ──────────────────────────

class _Text:
    def __init__(self, text):
        self.text = text


class AssistantMessage:
    def __init__(self, text):
        self.content = [_Text(text)]


class ResultMessage:
    def __init__(self, result, is_error=False):
        self.result = result
        self.is_error = is_error


_Text.__name__ = "TextBlock"


class _FakeClient:
    instances = []

    def __init__(self, options=None):
        self.options = options
        self.queries = []
        self.interrupted = False
        _FakeClient.instances.append(self)

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def query(self, text, **kwargs):
        self.queries.append(text)

    async def interrupt(self):
        self.interrupted = True

    async def receive_response(self):
        if len(self.queries) == 1:
            yield AssistantMessage("lavoro in corso...")
            await asyncio.sleep(3600)   # non finisce mai: deve scattare il budget
        else:
            yield ResultMessage("## Agent Result (parziale)\nfatto: A; manca: B")


def test_claude_worker_injects_wrapup_after_soft_budget(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CODEX_TIMEOUT", "1")
    monkeypatch.setattr(pd, "_CODEX_TIMEOUT", 1)
    monkeypatch.setattr(pd, "ClaudeSDKClient", _FakeClient)
    monkeypatch.setattr(pd, "ClaudeAgentOptions", lambda **kw: kw)
    _FakeClient.instances.clear()
    progress = {}

    out = asyncio.run(pd._run_worker("task lungo", "claude-sonnet-5", str(tmp_path), agent_id="ricercatore", progress=progress))

    client = _FakeClient.instances[0]
    assert client.interrupted is True
    assert len(client.queries) == 2
    assert "PARZIALE" in client.queries[1]
    assert out.startswith(pd._WRAPUP_MARKER)
    assert "fatto: A" in out
    assert progress["result_partial"] is True
