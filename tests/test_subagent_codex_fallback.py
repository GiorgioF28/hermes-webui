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


def test_codex_quota_detector_matches_timeout_with_quota_partial():
    # A quota esaurita il CLI stampa il messaggio e resta appeso fino al timeout:
    # il testo dell'errore timeout include il parziale e deve far scattare il fallback.
    quota_timeout = RuntimeError(
        "Codex CLI timeout dopo 1000s.\nParziale prima del timeout:\n"
        "You've hit your usage limit. Upgrade to Pro or try again later."
    )
    assert pd.is_codex_quota_error(quota_timeout)

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

    # Ma se nel parziale c'e' la frase inequivocabile del CLI, il fallback
    # deve comunque scattare (a quota esaurita il CLI stampa e resta appeso).
    quota_timeout = RuntimeError(
        "Codex CLI timeout dopo 1000s.\nParziale prima del timeout:\n"
        "Lavoro sulla quota pill...\nYou've hit your usage limit."
    )
    assert pd.is_codex_quota_error(quota_timeout)


@pytest.mark.asyncio
async def test_codex_timeout_preserves_stderr_partial(monkeypatch):
    import subprocess

    def fake_exec(task, workspace):
        raise subprocess.TimeoutExpired(
            cmd=["codex", "exec"], timeout=1000,
            output=b"", stderr=b"You've hit your usage limit.",
        )

    monkeypatch.setattr(pd, "_codex_exec_blocking", fake_exec)

    with pytest.raises(RuntimeError) as ei:
        await pd._run_codex_worker("task", ".")
    assert "hit your usage limit" in str(ei.value)
    assert pd.is_codex_quota_error(ei.value)


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
    assert worker_calls[0][1] == "claude-sonnet-4-6"
    assert progress["fallback_runtime"] == "sonnet"
    assert progress["fallback_model"] == "claude-sonnet-4-6"

    progress2 = {}
    assert await pd._run_codex_worker_with_fallback("task two", str(tmp_path), progress=progress2) == "sonnet result"
    assert codex_calls == [("task one", str(tmp_path))]
    assert worker_calls[1][0] == "task two"
    assert progress2["fallback_runtime"] == "sonnet"
