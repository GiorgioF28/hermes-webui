"""Il pass del Librarian parte solo quando c'e' qualcosa da memorizzare.

Prima partiva dopo OGNI delega riuscita (126 pass su 473 deleghe), in coda
dietro le deleghe stesse, anche per esiti vuoti o di poche righe. Resta
dentro il lock delle deleghe: e' cio' che impedisce scritture parallele sulla
memoria.
"""

from __future__ import annotations

import asyncio

import pytest

from api import prime_delegation as pd


LONG = "Esito concreto della ricerca. " * 40  # ~1200 char


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("HERMES_LIBRARIAN_MIN_OUTPUT_CHARS", raising=False)


def test_real_result_needs_the_pass():
    needed, reason = pd._librarian_pass_needed("ricerca", "ricercatore", LONG)
    assert needed is True
    assert reason == ""


def test_short_output_is_skipped():
    needed, reason = pd._librarian_pass_needed("ricerca", "ricercatore", "ok, fatto.")
    assert needed is False
    assert "brev" in reason.lower() or "cort" in reason.lower() or "car" in reason.lower()


def test_threshold_is_configurable(monkeypatch):
    monkeypatch.setenv("HERMES_LIBRARIAN_MIN_OUTPUT_CHARS", "5")
    assert pd._librarian_pass_needed("ricerca", "ricercatore", "ok, fatto.")[0] is True


def test_librarian_own_tasks_are_not_re_memorised():
    assert pd._librarian_pass_needed("memoria", "librarian", LONG)[0] is False
    assert pd._librarian_pass_needed("ricerca", "memory-librarian", LONG)[0] is False


def test_explicit_no_memory_marker_skips():
    assert pd._librarian_pass_needed("ricerca", "ricercatore", LONG + "\n[no-memory]")[0] is False
    assert pd._librarian_pass_needed("ricerca", "ricercatore", "NIENTE DA MEMORIZZARE\n" + LONG)[0] is False


def test_enqueue_marks_skipped_without_scheduling(monkeypatch, tmp_path):
    calls = []

    async def fake_run(*args, **kwargs):
        calls.append(args)

    monkeypatch.setattr(pd, "_run_librarian", fake_run)
    monkeypatch.setattr(pd, "_BG_TASKS", {"d1": {"id": "d1", "agent": "ricercatore", "agent_id": "ricercatore",
                                                 "task_type": "ricerca", "task": "t", "status": "ok"}})
    monkeypatch.setattr(pd, "_persist_bg_task", lambda *a, **k: None)

    async def scenario():
        pd._enqueue_librarian_pass("d1", "ricerca", "t", "ok.", str(tmp_path))
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert calls == []
    assert pd._BG_TASKS["d1"]["librarian_status"] == "skipped"
    assert pd._BG_TASKS["d1"]["librarian_output"]


def test_enqueue_still_schedules_when_needed(monkeypatch, tmp_path):
    calls = []

    async def fake_run(*args, **kwargs):
        calls.append(args)

    monkeypatch.setattr(pd, "_run_librarian", fake_run)
    monkeypatch.setattr(pd, "_BG_TASKS", {"d1": {"id": "d1", "agent": "ricercatore", "agent_id": "ricercatore",
                                                 "task_type": "ricerca", "task": "t", "status": "ok"}})
    monkeypatch.setattr(pd, "_persist_bg_task", lambda *a, **k: None)

    async def scenario():
        pd._enqueue_librarian_pass("d1", "ricerca", "t", LONG, str(tmp_path))
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert len(calls) == 1
    assert pd._BG_TASKS["d1"]["librarian_status"] == "in_corso"
