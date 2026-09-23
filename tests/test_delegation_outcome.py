import json
from types import SimpleNamespace

import pytest

from api import prime_delegation as pd
from api.delegation_outcome import DIAGNOSTIC_LIMIT, normalise_outcome, outcome_summary
from api.delegation_store import DelegationStore, bg_task_to_canonical, classify_error


def transcript(size=2_500_000):
    return ("OpenAI Codex v0.153.4\nsession id: 01a0cafa-fbfe-7c02-b089-b295c3781cd4\n"
            "user\nTask: examine quota handling\nexec\n" + "tool output\n" * (size // 12)
            + "\nERROR: You've hit your usage limit. Try again at 11:51 PM.\n"
            "2026-09-22T21:30:00Z ERROR codex_core::session: thread not found\ntokens used\n152.573")


def test_nonzero_exit_preserves_report_separately_from_stderr(monkeypatch):
    monkeypatch.setattr(pd, "_resolve_codex_executable", lambda: "codex")
    monkeypatch.setattr(pd.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=1, stdout="Report parziale: commit abc1234, test passati.", stderr=transcript()))
    with pytest.raises(pd.CodexProcessError) as raised:
        pd._codex_exec_blocking("task", ".")
    error = raised.value
    assert classify_error(error)["category"] == "quota_exhausted"
    assert pd.is_codex_quota_error(error)
    assert len(str(error)) <= 500
    assert error.partial_output.startswith("Report parziale")
    assert len(error.diagnostic_log) <= DIAGNOSTIC_LIMIT
    assert "11:51 PM" in str(error)
    assert "152.573" in error.diagnostic_log


def test_quota_mentioned_in_task_does_not_trigger_fallback():
    log = "OpenAI Codex v1\nuser\nYou've hit your usage limit\nexec\nERROR: unrelated crash\ntokens used\n10"
    error = pd.CodexProcessError(1, log)
    assert classify_error(error)["category"] == "process_exit"
    assert not pd.is_codex_quota_error(error)


def test_empty_stdout_never_returns_diagnostic_as_final_answer(monkeypatch):
    monkeypatch.setattr(pd, "_resolve_codex_executable", lambda: "codex")
    monkeypatch.setattr(pd.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=0, stdout="", stderr=transcript(1000)))
    assert pd._codex_exec_blocking("task", ".") == ""


def test_historical_failure_repaired_without_replay_or_success_claim(tmp_path, monkeypatch):
    log = transcript()
    task = dict(id="d512", agent="programmatore", task_type="codice", task="fix tema",
                status="errore", output=log[:65536], failure_reason="Codex CLI exit 1: " + log,
                error_category="process_exit", result_partial=True, finished=100,
                summary="d512: errore, commit 01a0cafa", anchor_message_index=2097)
    clean = normalise_outcome(task)
    assert clean["output"] == ""
    assert clean["error_category"] == "quota_exhausted"
    assert "01a0cafa" not in clean["summary"]
    assert clean["status"] == "errore"
    assert clean["anchor_message_index"] == 2097
    assert normalise_outcome(clean) == clean
    assert len(task["failure_reason"]) > 1_000_000  # caller not mutated
    record = bg_task_to_canonical(task)
    record["brief"]["status"] = "delivered"
    store = DelegationStore(tmp_path)
    store.upsert(record)
    loaded = store.get("d512")
    assert loaded["brief"]["status"] == "delivered"
    assert loaded["status"] == "failed"
    assert len(json.dumps(loaded)) < 16000
    monkeypatch.setattr(pd, "_BG_TASKS", {"d512": task})
    single = pd.get_background_task("d512")
    batch = pd.get_background_tasks(max_age=1e12)[0]
    for view in (single, batch):
        assert len(view["failure_reason"]) < 500
        assert len(view["diagnostic_log"]) <= DIAGNOSTIC_LIMIT
        assert view["result_partial"]
        assert view["output"] == ""


@pytest.mark.parametrize("output", ["session id: 01a0cafa-fbfe-7c02", "branch 20260921", "commit 01a0cafa-fbfe-7c02"])
def test_summary_does_not_invent_commit(output):
    assert ", commit" not in outcome_summary(dict(id="d1", status="ok", output=output))


def test_partial_result_and_explicit_commit_survive():
    summary = outcome_summary(dict(id="d510", status="ok", result_partial=True,
                                   output="Commit: `cdcb49bd76a59b9442160f31c907c14f933cf52f`"))
    assert "parziale" in summary
    assert "commit cdcb49bd76a5" in summary


@pytest.mark.asyncio
async def test_failed_run_brief_contains_recovery_guidance_not_trace(monkeypatch, tmp_path):
    from api import prime_brief_queue as pq
    from api import delegation_store as ds
    calls = []
    monkeypatch.setattr(pd, "_BG_TASKS", {"d512": dict(id="d512", task="fix tema", agent="programmatore", status="in_corso")})
    monkeypatch.setattr(ds, "_STORE", None)
    monkeypatch.setattr(pd, "_arm_memory_fence", lambda *a: None)
    monkeypatch.setattr(pq, "get_brief_queue", lambda *a: SimpleNamespace(enqueue=lambda *a, **kw: calls.append(kw)))

    async def fail(*a, **kw):
        raise pd.CodexProcessError(1, transcript(), "Report: patch salvata, verificare il deploy.")

    monkeypatch.setattr(pd, "_run_codex_worker_with_fallback", fail)
    await pd._run_and_store("d512", "codice", "fix tema", pd._CODEX_MODEL, "Codex", str(tmp_path))
    saved = ds.get_delegation_store(tmp_path).get("d512")
    assert saved["error"]["category"] == "quota_exhausted"
    assert saved["result"]["text"].startswith("Report:")
    assert saved["status"] == "failed"
    assert calls[0]["error_category"] == "quota_exhausted"
    assert "verifica report, file e commit" in calls[0]["output"]
    assert "tool output" not in calls[0]["output"]
    assert len(calls[0]["output"]) < 1500


def test_history_restores_delivered_outcomes_without_cross_session_leak():
    from api.delegation_outcome import enrich_history_outcomes
    cards = [{"id": "d512", "brief_status": "delivered", "anchor_message_index": 2097}, {"id": "d999"}]
    records = [bg_task_to_canonical(dict(id="d512", status="errore", task="fix", output=transcript(1000),
               failure_reason="Codex CLI exit 1: " + transcript(1000), result_partial=True)),
               dict(id="d999", session_id="private-session", output="private report")]
    hydrated = enrich_history_outcomes(cards, records, "hermes-prime")
    assert len(hydrated) == len(cards)
    assert hydrated[0]["brief_status"] == "delivered"
    assert hydrated[0]["anchor_message_index"] == 2097
    assert hydrated[0]["error_category"] == "quota_exhausted"
    assert hydrated[0]["diagnostic_log"]
    assert hydrated[1] == cards[1]
    assert "diagnostic_log" not in cards[0]
