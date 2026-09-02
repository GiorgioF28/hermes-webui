"""Routing per-progetto del contesto memoria di Prime.

Il workspace ospita piu' progetti attivi (Hermes Prime Command Bridge,
VisionBuilts, il libricino di ricette). Le regole operative di un progetto
non devono entrare nei task di un altro, ma i task che attraversano due
progetti devono continuare a vedere entrambi i contesti.
"""

from __future__ import annotations

import re
from pathlib import Path

from api import memory_retrieval


REPO_ROOT = Path(__file__).resolve().parents[1]


# ── classificazione dello scope dal testo del task ────────────────────────────

def test_hermes_command_bridge_task_is_scoped_to_hermes():
    assert memory_retrieval.classify_task_scope(
        "sistemiamo il Command Bridge di Hermes Prime sulla 8788"
    ) == "hermes"


def test_visionbuilts_vocabulary_is_scoped_to_visionbuilts():
    for task in (
        "apri la console e controlla lo stato",
        "il workflow n8n non parte piu'",
        "aggiorna la pagina di Vision Builds",
    ):
        assert memory_retrieval.classify_task_scope(task) == "visionbuilts", task


def test_recipe_book_vocabulary_is_scoped_to_libricino():
    assert memory_retrieval.classify_task_scope(
        "aggiungi due ricette vegane al libricino"
    ) == "libricino"


def test_cross_project_task_is_left_unscoped():
    """L'eccezione: estrarre ricette dai reel dei creator per VisionBuilts.

    Due progetti nello stesso task -> nessun filtro, entrambi i contesti
    restano disponibili.
    """
    assert memory_retrieval.classify_task_scope(
        "automatizza l'estrazione di ricette e foto dai reel Instagram "
        "dei creator per il libro dell'influencer"
    ) == ""


def test_unrelated_task_is_left_unscoped():
    assert memory_retrieval.classify_task_scope("che ore sono") == ""


def test_libricino_is_a_valid_scope():
    assert "libricino" in memory_retrieval.VALID_SCOPES
    assert memory_retrieval.normalize_scope("libricino") == "libricino"


# ── il filtro non deve mai nascondere le note globali ─────────────────────────

def test_scope_filter_keeps_global_and_unscoped_notes(tmp_path):
    (tmp_path / "MEMORY.md").write_text(
        "- [Regola hermes](h.md) — deploy 8788\n"
        "- [Regola visionbuilts](v.md) — console\n"
        "- [Regola globale](g.md) — vale sempre\n",
        encoding="utf-8",
    )
    (tmp_path / "h.md").write_text("---\nscope: hermes\n---\ncorpo hermes\n", encoding="utf-8")
    (tmp_path / "v.md").write_text("---\nscope: visionbuilts\n---\ncorpo visionbuilts\n", encoding="utf-8")
    (tmp_path / "g.md").write_text("---\nscope: global\n---\ncorpo globale\n", encoding="utf-8")

    entries = memory_retrieval.parse_memory_index(tmp_path)
    scopes = {e["filename"]: e.get("scope", "") for e in entries}
    assert scopes == {"h.md": "hermes", "v.md": "visionbuilts", "g.md": "global"}

    selected = memory_retrieval.select_memories_unlocked(
        "deploy console", tmp_path, task_scope="hermes", min_score=0.0
    )
    got = {s["filename"] for s in selected}
    assert "v.md" not in got, "una nota visionbuilts non deve entrare in un task hermes"
    assert "g.md" in got, "le note globali devono passare qualunque sia lo scope"


# ── identificatori numerici nelle keyword ─────────────────────────────────────

def test_keywords_keep_numeric_identifiers():
    """8788, d269, 5163efb sono identificatori portanti in questo workspace."""
    kws = memory_retrieval._extract_keywords(
        "la webui sulla 8788 non parte, vedi run d269 e commit 5163efb"
    )
    assert "8788" in kws
    assert "d269" in kws
    assert "5163efb" in kws


# ── cablaggio nel turno Prime ─────────────────────────────────────────────────

def test_prime_turn_passes_task_scope_to_memory_retrieval():
    """Entrambi i rami (unlocked e lean) devono propagare lo scope del task."""
    source = (REPO_ROOT / "api" / "routes.py").read_text(encoding="utf-8")
    for entry_point in (
        "build_prime_unlocked_memory_detail",
        "build_prime_memory_context",
    ):
        call = re.search(
            re.escape(entry_point) + r"\((?:[^()]|\([^()]*\))*\)", source
        )
        assert call, f"chiamata a {entry_point} non trovata in routes.py"
        assert "task_scope=" in call.group(0), (
            f"{entry_point} viene chiamata senza task_scope: il filtro per progetto "
            f"resta inerte -> {call.group(0)}"
        )
