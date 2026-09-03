"""Il log durevole delle deleghe non deve crescere senza limite.

Stato trovato: tasks/delegations.jsonl a 167 MB per 473 deleghe (2900 righe,
~7 per delega, ogni riga con l'output intero; una delega fallita da 12,4 MB
salvata tre volte) e delegations-state.json a 50 MB, riscritto per intero a
ogni upsert. All'avvio il server legge e parsa tutto.
"""

from __future__ import annotations

import itertools
import json

import pytest

from api import delegation_store, prime_delegation as pd


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(pd, "_BG_TASKS", {})
    monkeypatch.setattr(pd, "_DELEGATIONS_LOADED", False)
    monkeypatch.setattr(pd, "_TASK_SEQ", itertools.count(1))
    monkeypatch.delenv("HERMES_DELEGATION_PERSIST_MAX_CHARS", raising=False)


def _row(tid, output, status="ok"):
    return {
        "id": tid, "session_id": "hermes-prime", "agent": "ricercatore", "agent_id": "ricercatore",
        "task_type": "ricerca", "task": "t", "status": status, "output": output,
        "started": 1000.0, "finished": 1100.0, "librarian_status": "", "librarian_output": "",
    }


def test_persisted_output_is_capped(tmp_path):
    pd._BG_TASKS["d1"] = _row("d1", "x" * 200_000)
    pd._persist_bg_task("d1", str(tmp_path))
    lines = (tmp_path / "tasks" / "delegations.jsonl").read_text(encoding="utf-8").splitlines()
    saved = json.loads(lines[0])
    assert len(saved["output"]) <= delegation_store.PERSIST_TEXT_MAX_CHARS + 200
    assert "troncat" in saved["output"].lower()
    assert len(pd._BG_TASKS["d1"]["output"]) == 200_000, "in RAM resta intero"


def test_cap_is_configurable(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_DELEGATION_PERSIST_MAX_CHARS", "1000")
    pd._BG_TASKS["d1"] = _row("d1", "x" * 5_000)
    pd._persist_bg_task("d1", str(tmp_path))
    saved = json.loads((tmp_path / "tasks" / "delegations.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert len(saved["output"]) <= 1200


def test_startup_compacts_log_to_latest_record_per_delegation(tmp_path):
    log = tmp_path / "tasks" / "delegations.jsonl"
    log.parent.mkdir(parents=True)
    rows = [_row("d1", "prima versione", status="in_corso"), _row("d1", "y" * 300_000), _row("d2", "fine")]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    pd._load_bg_tasks(str(tmp_path))

    lines = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert [r["id"] for r in lines] == ["d1", "d2"], "una riga per delega, l'ultima"
    assert len(lines[0]["output"]) <= delegation_store.PERSIST_TEXT_MAX_CHARS + 200
    assert len(pd._BG_TASKS["d1"]["output"]) <= delegation_store.PERSIST_TEXT_MAX_CHARS + 200
    assert next(pd._TASK_SEQ) == 3, "il contatore riparte dopo l'id piu' alto"


def test_canonical_record_caps_result_text():
    rec = delegation_store.bg_task_to_canonical(_row("d9", "z" * 500_000))
    assert len(rec["result"]["text"]) <= delegation_store.PERSIST_TEXT_MAX_CHARS + 200
    assert "troncat" in rec["result"]["text"].lower()


def test_store_compact_shrinks_state_file(tmp_path):
    store = delegation_store.DelegationStore(tmp_path)
    huge = delegation_store.bg_task_to_canonical(_row("d7", "w" * 10))
    huge["result"]["text"] = "w" * 2_000_000   # come un record scritto prima del tetto
    huge["librarian"]["output"] = "l" * 500_000
    store._write_state({"d7": huge})
    before = (tmp_path / "tasks" / "delegations-state.json").stat().st_size

    changed = store.compact()

    after = (tmp_path / "tasks" / "delegations-state.json").stat().st_size
    assert changed >= 1
    assert after < before / 10
    rec = store.get("d7")
    assert len(rec["result"]["text"]) <= delegation_store.PERSIST_TEXT_MAX_CHARS + 200
    assert len(rec["librarian"]["output"]) <= delegation_store.PERSIST_TEXT_MAX_CHARS + 200
