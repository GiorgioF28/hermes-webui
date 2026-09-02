"""Nessuna singola nota puo' monopolizzare o azzerare il contesto memoria.

Una nota sovradimensionata in testa alla coda interrompeva la selezione
(`break`), scartando tutte le note piccole e pertinenti che seguivano: sul
profilo lean il risultato era zero note di memoria.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from api import memory_retrieval


def _bank(tmp_path: Path) -> Path:
    """Banco con una nota enorme in testa e due piccole rilevanti dietro."""
    (tmp_path / "MEMORY.md").write_text(
        "- [Nota enorme](big.md) — deploy console stato repo\n"
        "- [Nota piccola A](a.md) — deploy console regola\n"
        "- [Nota piccola B](b.md) — deploy console procedura\n",
        encoding="utf-8",
    )
    (tmp_path / "big.md").write_text("X" * 40_000, encoding="utf-8")
    (tmp_path / "a.md").write_text("regola A", encoding="utf-8")
    (tmp_path / "b.md").write_text("regola B", encoding="utf-8")
    return tmp_path


# ── profilo lean (quello attivo) ──────────────────────────────────────────────

def test_oversized_note_does_not_wipe_the_rest(tmp_path):
    got = memory_retrieval.select_memories_for_task(
        "deploy console", _bank(tmp_path), budget_tokens=2000
    )
    names = [e["filename"] for e in got]
    assert "a.md" in names and "b.md" in names, (
        f"le note piccole e pertinenti sono state scartate insieme alla grande: {names}"
    )


def test_oversized_note_is_truncated_not_dropped(tmp_path):
    got = memory_retrieval.select_memories_for_task(
        "deploy console", _bank(tmp_path), budget_tokens=2000
    )
    big = next((e for e in got if e["filename"] == "big.md"), None)
    assert big is not None, "la nota grande deve entrare troncata, non sparire"
    assert len(big["body"]) < 40_000
    assert "[…" in big["body"], "il troncamento deve essere visibile nel corpo"


def test_total_budget_is_still_respected(tmp_path):
    budget = 2000
    got = memory_retrieval.select_memories_for_task(
        "deploy console", _bank(tmp_path), budget_tokens=budget
    )
    assert sum(e["body_tokens"] for e in got) <= budget


def test_single_note_cannot_exceed_its_share(tmp_path):
    budget = 2000
    got = memory_retrieval.select_memories_for_task(
        "deploy console", _bank(tmp_path), budget_tokens=budget
    )
    share = memory_retrieval.memory_note_share()
    for e in got:
        assert e["body_tokens"] <= max(int(budget * share), 1) + 1, e["filename"]


# ── profilo unlocked ──────────────────────────────────────────────────────────

def test_unlocked_caps_each_note_to_its_share(tmp_path):
    got = memory_retrieval.select_memories_unlocked(
        "deploy console", _bank(tmp_path), max_chars=12_000, min_score=0.0
    )
    names = [e["filename"] for e in got]
    assert "a.md" in names and "b.md" in names, names
    cap = int(12_000 * memory_retrieval.memory_note_share())
    for e in got:
        assert e["body_chars"] <= cap + 40, (e["filename"], e["body_chars"])


# ── configurabilita' ──────────────────────────────────────────────────────────

def test_share_is_configurable(monkeypatch):
    monkeypatch.setenv("HERMES_PRIME_MEMORY_NOTE_SHARE", "0.25")
    assert memory_retrieval.memory_note_share() == pytest.approx(0.25)


def test_share_falls_back_on_nonsense(monkeypatch):
    for bad in ("", "abc", "0", "-1", "5"):
        monkeypatch.setenv("HERMES_PRIME_MEMORY_NOTE_SHARE", bad)
        share = memory_retrieval.memory_note_share()
        assert 0.0 < share <= 1.0, bad
