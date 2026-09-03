import subprocess
import time

import pytest

from api import prime_delegation as pd


def setup_function():
    pd.reset_codex_fallback_for_tests()


def teardown_function():
    pd.reset_codex_fallback_for_tests()


def test_codex_quota_detector_matches_real_plan_limit_messages():
    samples = [
        'Codex CLI exit 1: HTTP 429: {"error":{"type":"usage_limit_exceeded"}}',
        "Codex CLI exit 1: Plan limit reached. You've reached the limit of messages per 5 hours.",
        "Codex CLI exit 1: You've used up your usage",
        "Codex CLI exit 1: rate_limit exceeded",
    ]
    for sample in samples:
        assert pd.is_codex_quota_error(RuntimeError(sample)), sample

    assert not pd.is_codex_quota_error(RuntimeError("Codex CLI exit 1: not logged in, please login"))
    assert not pd.is_codex_quota_error(RuntimeError("Codex CLI exit 2: test failure"))
    assert not pd.is_codex_quota_error(RuntimeError("Claude usage limit reached"))


def test_codex_timeout_is_never_reclassified_as_quota():
    quota_timeout = RuntimeError(
        "Codex CLI timeout dopo 1000s.\nParziale prima del timeout:\n"
        "You've hit your usage limit. Upgrade to Pro or try again later."
    )
    assert not pd.is_codex_quota_error(quota_timeout)

    plain_timeout = RuntimeError("Codex CLI timeout dopo 1000s.")
    assert not pd.is_codex_quota_error(plain_timeout)


def test_codex_timeout_with_task_output_mentioning_quota_is_not_quota_error():
    # Regressione 2026-07-17: deleghe lunghe (parita' Bridge) sforavano il
    # timeout da 1000s e il parziale conteneva parole come "quota"/"usage
    # limit"/"rate limit" perche' il LAVORO parlava di quota pill. Il timeout
    # veniva scambiato per crediti esauriti -> cooldown sticky 1h -> deleghe
    # successive dirette su Sonnet con crediti Codex al 48%.
    timeout = RuntimeError(
        "Codex CLI timeout dopo 1000s.\nParziale prima del timeout:\n"
        "Implementata la quota pill usage/token nel Command Bridge; gestito "
        "il rate limit dell'endpoint /api/usage/limits; usage limit render "
        "in .cb-chat-head; commit abc1234 con suite verde."
    )
    assert not pd.is_codex_quota_error(timeout)

    # Anche una frase inequivocabile non cambia la causa terminale: timeout.
    quota_timeout = RuntimeError(
        "Codex CLI timeout dopo 1000s.\nParziale prima del timeout:\n"
        "Lavoro sulla quota pill...\nYou've hit your usage limit."
    )
    assert not pd.is_codex_quota_error(quota_timeout)


@pytest.mark.asyncio
async def test_codex_timeout_preserves_stderr_partial(monkeypatch):
    def fake_exec(task, workspace):
        raise subprocess.TimeoutExpired(
            cmd=["codex", "exec"], timeout=1000,
            output=b"", stderr=b"You've hit your usage limit.",
        )

    monkeypatch.setattr(pd, "_codex_exec_blocking", fake_exec)

    with pytest.raises(pd.CodexTimeoutError) as ei:
        await pd._run_codex_worker("task", ".")
    assert "hit your usage limit" in ei.value.partial_output
    assert ei.value.duration_seconds == 1000
    assert not pd.is_codex_quota_error(ei.value)


@pytest.mark.asyncio
async def test_codex_timeout_fails_without_sonnet_fallback(monkeypatch, tmp_path):
    worker_calls = []

    async def fake_codex(*args, **kwargs):
        raise pd.CodexTimeoutError(1000, "report parziale utile")

    async def fake_worker(*args, **kwargs):
        worker_calls.append(args)
        return "non deve partire"

    monkeypatch.setattr(pd, "_run_codex_worker", fake_codex)
    monkeypatch.setattr(pd, "_run_worker", fake_worker)

    with pytest.raises(pd.CodexTimeoutError):
        await pd._run_codex_worker_with_fallback("task", str(tmp_path), progress={})
    assert worker_calls == []


@pytest.mark.asyncio
async def test_codex_start_error_still_falls_back_to_sonnet(monkeypatch, tmp_path):
    worker_calls = []

    async def fake_codex(*args, **kwargs):
        raise pd.CodexStartError("Codex CLI non avviabile")

    async def fake_worker(task, model, workspace, **kwargs):
        worker_calls.append((task, model, workspace))
        return "fallback riuscito"

    monkeypatch.setattr(pd, "_run_codex_worker", fake_codex)
    monkeypatch.setattr(pd, "_run_worker", fake_worker)

    result = await pd._run_codex_worker_with_fallback("task", str(tmp_path), progress={})
    assert result == "fallback riuscito"
    # Senza agente il fallback e' il Claude di default (per agente: vedi
    # test_auto_policy_gpt_first).
    assert worker_calls == [("task", pd.codex_fallback_model(None), str(tmp_path))]


def test_codex_fallback_cooldown_is_configurable(monkeypatch):
    monkeypatch.setenv("HERMES_CODEX_FALLBACK_COOLDOWN_SECONDS", "1800")

    status = pd._mark_codex_exhausted("Plan limit reached", now=100.0)

    assert status["active"] is True
    assert status["remaining"] == 1800.0
    assert pd._codex_fallback_active(1899.0) is True
    assert pd._codex_fallback_active(1901.0) is False


@pytest.mark.asyncio
async def test_codex_quota_falls_back_to_sonnet_and_reuses_cooldown(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CODEX_FALLBACK_COOLDOWN_SECONDS", "600")
    codex_calls = []
    worker_calls = []

    async def fake_codex(task, workspace, *, agent_id=None):
        codex_calls.append((task, workspace))
        raise RuntimeError("Codex CLI exit 1: HTTP 429 usage_limit_exceeded")

    async def fake_worker(task, model, workspace, **kwargs):
        worker_calls.append((task, model, workspace, kwargs))
        progress = kwargs.get("progress")
        if progress is not None:
            progress["output"] = "sonnet result"
        return "sonnet result"

    monkeypatch.setattr(pd, "_run_codex_worker", fake_codex)
    monkeypatch.setattr(pd, "_run_worker", fake_worker)
    monkeypatch.setattr(pd.time, "time", lambda: 1000.0)

    progress = {}
    assert await pd._run_codex_worker_with_fallback("task one", str(tmp_path), progress=progress) == "sonnet result"
    assert codex_calls == [("task one", str(tmp_path))]
    assert worker_calls[0][1] == pd.codex_fallback_model(None)
    assert progress["fallback_runtime"] == "sonnet"
    assert progress["fallback_model"] == pd.codex_fallback_model(None)

    progress2 = {}
    assert await pd._run_codex_worker_with_fallback("task two", str(tmp_path), progress=progress2) == "sonnet result"
    assert codex_calls == [("task one", str(tmp_path))]
    assert worker_calls[1][0] == "task two"
    assert progress2["fallback_runtime"] == "sonnet"


def _task(task_id):
    return {
        "id": task_id,
        "session_id": "hermes-prime",
        "agent": "programmatore",
        "agent_id": "programmatore",
        "task_type": "codice",
        "task": "task mirato",
        "status": "in_corso",
        "output": "",
        "started": time.time(),
        "finished": None,
        "runtime": "codex",
        "fallback_runtime": "",
        "fallback_model": "",
        "fallback_reason": "",
    }


@pytest.mark.asyncio
async def test_timeout_persists_failed_with_duration_and_partial(monkeypatch, tmp_path):
    import api.delegation_store as ds

    ds._STORE = None
    task_id = "d-timeout-real"
    pd._BG_TASKS[task_id] = _task(task_id)
    monkeypatch.setattr(pd, "_enqueue_librarian_pass", lambda *args: None)

    async def fake_codex(*args, **kwargs):
        raise pd.CodexTimeoutError(1000, "report parziale utile")

    async def forbidden_fallback(*args, **kwargs):
        raise AssertionError("Sonnet non deve partire su timeout")

    monkeypatch.setattr(pd, "_run_codex_worker", fake_codex)
    monkeypatch.setattr(pd, "_run_worker", forbidden_fallback)
    try:
        await pd._run_and_store(task_id, "codice", "task mirato", pd._CODEX_MODEL, "Codex", str(tmp_path))
        task = pd._BG_TASKS[task_id]
        assert task["status"] == "errore"
        assert task["output"] == "report parziale utile"
        assert "1000s" in task["failure_reason"]
        rec = ds.get_delegation_store(tmp_path).get(task_id)
        assert rec["status"] == "failed"
        assert rec["error"]["category"] == "timeout"
        assert rec["result"]["text"] == "report parziale utile"
    finally:
        pd._BG_TASKS.pop(task_id, None)
        ds._STORE = None


@pytest.mark.asyncio
async def test_quota_fallback_attempt_with_limit_output_persists_failed(monkeypatch, tmp_path):
    import api.delegation_store as ds

    ds._STORE = None
    task_id = "d-quota-real"
    pd._BG_TASKS[task_id] = _task(task_id)
    fallback_calls = []
    monkeypatch.setattr(pd, "_enqueue_librarian_pass", lambda *args: None)

    async def fake_codex(*args, **kwargs):
        raise pd.CodexProcessError(1, "HTTP 429 usage_limit_exceeded")

    async def fake_worker(*args, **kwargs):
        fallback_calls.append(args)
        return "You've hit your session limit - resets 12:40am"

    monkeypatch.setattr(pd, "_run_codex_worker", fake_codex)
    monkeypatch.setattr(pd, "_run_worker", fake_worker)
    try:
        await pd._run_and_store(task_id, "codice", "task mirato", pd._CODEX_MODEL, "Codex", str(tmp_path))
        assert len(fallback_calls) == 1
        assert pd._BG_TASKS[task_id]["status"] == "errore"
        assert "session limit" in pd._BG_TASKS[task_id]["output"]
        rec = ds.get_delegation_store(tmp_path).get(task_id)
        assert rec["status"] == "failed"
        assert rec["error"]["category"] == "quota_exhausted"
    finally:
        pd._BG_TASKS.pop(task_id, None)
        ds._STORE = None


@pytest.mark.asyncio
async def test_successful_output_persists_done(monkeypatch, tmp_path):
    import api.delegation_store as ds

    ds._STORE = None
    task_id = "d-success-real"
    pd._BG_TASKS[task_id] = _task(task_id)
    monkeypatch.setattr(pd, "_enqueue_librarian_pass", lambda *args: None)

    async def fake_codex(*args, **kwargs):
        return "Implementazione completata, commit abc1234 e test verdi."

    monkeypatch.setattr(pd, "_run_codex_worker", fake_codex)
    try:
        await pd._run_and_store(task_id, "codice", "task mirato", pd._CODEX_MODEL, "Codex", str(tmp_path))
        assert pd._BG_TASKS[task_id]["status"] == "ok"
        rec = ds.get_delegation_store(tmp_path).get(task_id)
        assert rec["status"] == "done"
        assert rec["error"]["message"] == ""
    finally:
        pd._BG_TASKS.pop(task_id, None)
        ds._STORE = None


def test_empty_and_useless_truncated_outputs_fail_validation():
    with pytest.raises(pd.DelegationOutputError) as empty:
        pd._validate_delegation_output("")
    assert empty.value.category == "empty_output"

    with pytest.raises(pd.DelegationOutputError) as truncated:
        pd._validate_delegation_output("[truncated]")
    assert truncated.value.category == "truncated_output"

    assert pd._validate_delegation_output("Analisi utile completata. [truncated]") == "Analisi utile completata. [truncated]"


def test_nonzero_codex_exit_is_typed_failure(monkeypatch, tmp_path):
    class Proc:
        returncode = 2
        stdout = "parziale"
        stderr = "test failure"

    monkeypatch.setattr(pd, "_resolve_codex_executable", lambda: "codex.cmd")
    monkeypatch.setattr(pd.subprocess, "run", lambda *args, **kwargs: Proc())

    with pytest.raises(pd.CodexProcessError) as failed:
        pd._codex_exec_blocking("task", str(tmp_path))
    assert failed.value.exit_code == 2
    assert failed.value.category == "process_exit"
