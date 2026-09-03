"""Il recinto memoria e' agganciato alla delega: cio' che un sotto-agente
scrive nel Vault viene ripristinato e segnalato nel suo esito."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from api import memory_fence, prime_delegation as pd


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "obsidian-vault").mkdir()
    (tmp_path / "obsidian-vault" / "Progetto.md").write_text("stato: A\n", encoding="utf-8")
    return tmp_path


def _task(task_id, agent):
    return {
        "id": task_id, "session_id": "hermes-prime", "agent": agent, "agent_id": agent,
        "task_type": "ricerca", "task": "t", "status": "in_corso", "output": "",
        "started": time.time(), "finished": None, "runtime": "codex",
        "fallback_runtime": "", "fallback_model": "", "fallback_reason": "",
    }


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(pd, "_BG_TASKS", {})
    monkeypatch.setattr(pd, "_persist_bg_task", lambda *a, **k: None)
    monkeypatch.setattr(pd, "_enqueue_librarian_pass", lambda *a, **k: None)
    monkeypatch.setattr(memory_fence, "SNAPSHOT_ROOT", tmp_path / "snap")
    monkeypatch.delenv("HERMES_MEMORY_FENCE", raising=False)


def _fake_worker_that_writes_memory(ws: Path):
    async def fake(task, workspace, *, agent_id=None, progress=None):
        (ws / "obsidian-vault" / "Progetto.md").write_text("stato: B scritto dal sotto-agente\n", encoding="utf-8")
        (ws / "obsidian-vault" / "Nuova.md").write_text("nota abusiva", encoding="utf-8")
        return "Risultato concreto della delega. " * 20
    return fake


def test_subagent_memory_writes_are_reverted_and_reported(monkeypatch, tmp_path):
    ws = _workspace(tmp_path)
    monkeypatch.setattr(pd, "_run_codex_worker_with_fallback", _fake_worker_that_writes_memory(ws))
    pd._BG_TASKS["d1"] = _task("d1", "ricercatore")

    asyncio.run(pd._run_and_store("d1", "ricerca", "t", pd._CODEX_MODEL, "Codex", str(ws)))

    assert (ws / "obsidian-vault" / "Progetto.md").read_text(encoding="utf-8") == "stato: A\n"
    assert not (ws / "obsidian-vault" / "Nuova.md").exists()
    t = pd._BG_TASKS["d1"]
    assert t["status"] == "ok"
    assert "RECINTO MEMORIA" in t["output"]
    assert "Progetto.md" in t["output"]
    assert t["memory_fence"]["violations"] == 2


def test_librarian_is_allowed_to_write_memory(monkeypatch, tmp_path):
    ws = _workspace(tmp_path)
    monkeypatch.setattr(pd, "_run_codex_worker_with_fallback", _fake_worker_that_writes_memory(ws))
    pd._BG_TASKS["d2"] = _task("d2", "librarian")

    asyncio.run(pd._run_and_store("d2", "memoria", "t", pd._CODEX_MODEL, "Codex", str(ws)))

    assert "scritto dal sotto-agente" in (ws / "obsidian-vault" / "Progetto.md").read_text(encoding="utf-8")
    assert (ws / "obsidian-vault" / "Nuova.md").exists()
    assert "RECINTO MEMORIA" not in pd._BG_TASKS["d2"]["output"]


def test_fence_survives_worker_failure(monkeypatch, tmp_path):
    ws = _workspace(tmp_path)

    async def failing(task, workspace, *, agent_id=None, progress=None):
        (ws / "obsidian-vault" / "Progetto.md").write_text("stato: B\n", encoding="utf-8")
        raise RuntimeError("boom")

    monkeypatch.setattr(pd, "_run_codex_worker_with_fallback", failing)
    pd._BG_TASKS["d3"] = _task("d3", "social")

    asyncio.run(pd._run_and_store("d3", "ricerca", "t", pd._CODEX_MODEL, "Codex", str(ws)))

    assert (ws / "obsidian-vault" / "Progetto.md").read_text(encoding="utf-8") == "stato: A\n"
    assert pd._BG_TASKS["d3"]["status"] in ("errore", "failed")
