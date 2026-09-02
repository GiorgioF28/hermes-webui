"""Test Cantiere 2: retrieval selettivo della memoria per-richiesta.

Verifica:
- parse_memory_index: parsing corretto dell'indice MEMORY.md
- format_memory_index: formato one-liner corretto
- _extract_keywords: esclude stopwords, lunghezza minima
- score_entry_relevance: match keyword/title/description
- select_memories_for_task: top-k entro budget, score > 0
- build_memory_context: index_only=True solo indice; con task → indice + corpi
- find_prime_memory_dir: env override funziona
- budget token: non supera il limite
- nessun match → solo indice (nessun dump di fallback)
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from api import memory_retrieval


# ── Fixtures ──────────────────────────────────────────────────────────────────

_SAMPLE_INDEX = """\
# Memory Index

- [VisionBuilts project](visionbuilts-project.md) — n8n ebook pipeline + console; editable architecture
- [Hermes setup](hermes-setup.md) — Hermes Agent on Windows: real backend in %LOCALAPPDATA%\\hermes
- [Cloudflare deploy verify](cloudflare-deploy-verify.md) — da 2026-07-01 Hermes collegato all'API Cloudflare
- [Acqua bottiglie reminder](acqua-bottiglie-reminder.md) — ricordare a Giorgio le bottiglie
"""


def _make_mem_dir(tmp_path: Path, *, bodies: dict[str, str] | None = None) -> Path:
    """Crea una directory memoria fittizia con MEMORY.md e note opzionali."""
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    (mem_dir / "MEMORY.md").write_text(_SAMPLE_INDEX, encoding="utf-8")
    for filename, content in (bodies or {}).items():
        (mem_dir / filename).write_text(content, encoding="utf-8")
    return mem_dir


# ── parse_memory_index ────────────────────────────────────────────────────────

def test_parse_index_returns_all_entries(tmp_path):
    mem_dir = _make_mem_dir(tmp_path)
    entries = memory_retrieval.parse_memory_index(mem_dir)
    assert len(entries) == 4
    titles = [e["title"] for e in entries]
    assert "VisionBuilts project" in titles
    assert "Hermes setup" in titles
    assert "Acqua bottiglie reminder" in titles


def test_parse_index_has_filename_and_description(tmp_path):
    mem_dir = _make_mem_dir(tmp_path)
    entries = memory_retrieval.parse_memory_index(mem_dir)
    vs = next(e for e in entries if e["title"] == "VisionBuilts project")
    assert vs["filename"] == "visionbuilts-project.md"
    assert "n8n ebook" in vs["description"]


def test_parse_index_missing_file_returns_empty(tmp_path):
    entries = memory_retrieval.parse_memory_index(tmp_path)
    assert entries == []


def test_parse_index_skips_non_link_lines(tmp_path):
    mem_dir = tmp_path / "mem"
    mem_dir.mkdir()
    (mem_dir / "MEMORY.md").write_text(
        "# Header\n\nRiga normale senza link.\n- [Note](note.md) — descrizione\n",
        encoding="utf-8",
    )
    entries = memory_retrieval.parse_memory_index(mem_dir)
    assert len(entries) == 1
    assert entries[0]["title"] == "Note"


# ── format_memory_index ───────────────────────────────────────────────────────

def test_format_memory_index_markdown(tmp_path):
    mem_dir = _make_mem_dir(tmp_path)
    entries = memory_retrieval.parse_memory_index(mem_dir)
    text = memory_retrieval.format_memory_index(entries)
    assert "## Memoria (indice)" in text
    assert "- [VisionBuilts project](visionbuilts-project.md)" in text
    assert "n8n ebook" in text


def test_format_memory_index_empty():
    text = memory_retrieval.format_memory_index([])
    assert text == ""


# ── _extract_keywords ──────────────────────────────────────────────────────────

def test_extract_keywords_basic():
    kws = memory_retrieval._extract_keywords("Configura Cloudflare deploy")
    assert "cloudflare" in kws
    assert "deploy" in kws


def test_extract_keywords_removes_stopwords():
    kws = memory_retrieval._extract_keywords("cosa farlo dove quando")
    assert not any(w in {"cosa", "farlo", "dove", "quando"} for w in kws)


def test_extract_keywords_min_len():
    kws = memory_retrieval._extract_keywords("abc a bb")
    # min_len=4 → solo parole con 4+ caratteri
    assert "abc" not in kws  # 3 chars
    assert "bb" not in kws


def test_extract_keywords_dedup():
    kws = memory_retrieval._extract_keywords("hermes hermes hermes setup")
    count = sum(1 for w in kws if w == "hermes")
    assert count == 1


# ── score_entry_relevance ──────────────────────────────────────────────────────

def test_score_relevant_entry():
    entry = {"title": "VisionBuilts project", "description": "n8n pipeline ebook"}
    kws = ["visionbuilts", "ebook"]
    assert memory_retrieval.score_entry_relevance(entry, kws) == 2


def test_score_irrelevant_entry():
    entry = {"title": "Acqua bottiglie reminder", "description": "riempire le bottiglie"}
    kws = ["hermes", "python"]
    assert memory_retrieval.score_entry_relevance(entry, kws) == 0


def test_score_empty_keywords():
    entry = {"title": "VisionBuilts project", "description": "n8n pipeline"}
    assert memory_retrieval.score_entry_relevance(entry, []) == 0


def test_score_case_insensitive():
    entry = {"title": "HERMES Setup", "description": "Windows backend"}
    kws = ["hermes", "windows"]
    assert memory_retrieval.score_entry_relevance(entry, kws) == 2


# ── select_memories_for_task ──────────────────────────────────────────────────

def test_select_returns_relevant_bodies(tmp_path):
    bodies = {
        "visionbuilts-project.md": "Dettaglio lungo su n8n e VisionBuilts per il pipeline ebook.",
        "hermes-setup.md": "Hermes su Windows, backend LocalAppData.",
        "cloudflare-deploy-verify.md": "Cloudflare API deploy verifica commit.",
        "acqua-bottiglie-reminder.md": "Bottiglie acqua in frigo.",
    }
    mem_dir = _make_mem_dir(tmp_path, bodies=bodies)
    selected = memory_retrieval.select_memories_for_task(
        "come funziona visionbuilts n8n pipeline",
        mem_dir,
        budget_tokens=2000,
    )
    titles = [e["title"] for e in selected]
    assert "VisionBuilts project" in titles
    assert "Acqua bottiglie reminder" not in titles


def test_select_respects_budget(tmp_path):
    # Ogni body è ~200 chars → ~50 token, budget=60.
    # La quota per-nota tronca entrambi i body invece di far entrare solo il
    # primo: cambia il numero di note, non l'invariante di budget.
    body_text = "X" * 200  # ~50 tokens
    bodies = {
        "visionbuilts-project.md": body_text,
        "hermes-setup.md": body_text,
    }
    mem_dir = _make_mem_dir(tmp_path, bodies=bodies)
    budget = 60
    selected = memory_retrieval.select_memories_for_task(
        "visionbuilts hermes setup",
        mem_dir,
        budget_tokens=budget,
    )
    assert sum(entry["body_tokens"] for entry in selected) <= budget


def test_select_empty_when_no_match(tmp_path):
    bodies = {"visionbuilts-project.md": "n8n ebook pipeline"}
    mem_dir = _make_mem_dir(tmp_path, bodies=bodies)
    selected = memory_retrieval.select_memories_for_task(
        "concorso inps assistente informatico",
        mem_dir,
        budget_tokens=2000,
    )
    assert selected == []


def test_select_empty_budget_zero(tmp_path):
    mem_dir = _make_mem_dir(tmp_path)
    selected = memory_retrieval.select_memories_for_task(
        "hermes visionbuilts",
        mem_dir,
        budget_tokens=0,
    )
    assert selected == []


def test_select_skips_missing_body(tmp_path):
    """Se il file corpo non esiste, l'entry viene saltata silenziosamente."""
    mem_dir = _make_mem_dir(tmp_path, bodies={})  # nessun corpo scritto
    selected = memory_retrieval.select_memories_for_task(
        "visionbuilts n8n",
        mem_dir,
        budget_tokens=2000,
    )
    assert selected == []


# ── build_memory_context ──────────────────────────────────────────────────────

def test_build_context_index_only(tmp_path):
    mem_dir = _make_mem_dir(tmp_path)
    text = memory_retrieval.build_memory_context("", mem_dir, index_only=True)
    assert "## Memoria (indice)" in text
    assert "VisionBuilts project" in text
    # Senza corpi: nessuna sezione dettaglio
    assert "## Memoria (dettaglio" not in text


def test_build_context_with_task_adds_bodies(tmp_path):
    bodies = {"visionbuilts-project.md": "Dettaglio VisionBuilts n8n pipeline"}
    mem_dir = _make_mem_dir(tmp_path, bodies=bodies)
    text = memory_retrieval.build_memory_context(
        "come funziona visionbuilts pipeline",
        mem_dir,
        budget_tokens=2000,
    )
    assert "## Memoria (indice)" in text
    assert "## Memoria (dettaglio" in text
    assert "Dettaglio VisionBuilts n8n pipeline" in text


def test_build_context_no_match_returns_index_only(tmp_path):
    bodies = {"visionbuilts-project.md": "Dettaglio VisionBuilts"}
    mem_dir = _make_mem_dir(tmp_path, bodies=bodies)
    text = memory_retrieval.build_memory_context(
        "concorso inps assistente",
        mem_dir,
        budget_tokens=2000,
    )
    # Solo indice, nessun dettaglio (nessun match)
    assert "## Memoria (indice)" in text
    assert "## Memoria (dettaglio" not in text


def test_build_context_missing_memdir_returns_empty(tmp_path):
    missing = tmp_path / "does_not_exist"
    text = memory_retrieval.build_memory_context("task", missing)
    assert text == ""


# ── load_memory_body security ─────────────────────────────────────────────────

def test_load_body_no_path_traversal(tmp_path):
    mem_dir = _make_mem_dir(tmp_path)
    # Tentativo di path traversal → stringa vuota
    body = memory_retrieval.load_memory_body(mem_dir, "../sensitive.txt")
    assert body == ""


def test_load_body_no_absolute_path(tmp_path):
    mem_dir = _make_mem_dir(tmp_path)
    body = memory_retrieval.load_memory_body(mem_dir, "/etc/passwd")
    assert body == ""


def test_load_body_missing_file_returns_empty(tmp_path):
    mem_dir = _make_mem_dir(tmp_path)
    body = memory_retrieval.load_memory_body(mem_dir, "notexist.md")
    assert body == ""


# ── find_prime_memory_dir ─────────────────────────────────────────────────────

def test_find_prime_memory_dir_env_override(monkeypatch, tmp_path):
    mem_dir = _make_mem_dir(tmp_path)
    monkeypatch.setenv("HERMES_PRIME_MEMORY_DIR", str(mem_dir))
    found = memory_retrieval.find_prime_memory_dir()
    assert found == mem_dir


def test_find_prime_memory_dir_env_invalid(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_PRIME_MEMORY_DIR", str(tmp_path / "nonexistent"))
    found = memory_retrieval.find_prime_memory_dir()
    assert found is None


def test_find_prime_memory_dir_scans_claude_projects(monkeypatch, tmp_path):
    # Simula ~/.claude/projects/my-project/memory/MEMORY.md
    projects_dir = tmp_path / ".claude" / "projects"
    proj_dir = projects_dir / "my-project"
    mem_dir = proj_dir / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "MEMORY.md").write_text("- [Note](note.md) — desc", encoding="utf-8")

    # Override home per il test
    monkeypatch.delenv("HERMES_PRIME_MEMORY_DIR", raising=False)
    monkeypatch.setattr(memory_retrieval.Path, "home", staticmethod(lambda: tmp_path))

    found = memory_retrieval.find_prime_memory_dir()
    assert found == mem_dir


def test_find_prime_memory_dir_no_claude_projects(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_PRIME_MEMORY_DIR", raising=False)
    monkeypatch.setattr(memory_retrieval.Path, "home", staticmethod(lambda: tmp_path))
    # tmp_path non ha .claude/projects → ritorna None
    found = memory_retrieval.find_prime_memory_dir()
    assert found is None


# ── memory_budget_tokens ──────────────────────────────────────────────────────

def test_memory_budget_default(monkeypatch):
    monkeypatch.delenv("HERMES_MEMORY_BUDGET_TOKENS", raising=False)
    assert memory_retrieval.memory_budget_tokens() == memory_retrieval.DEFAULT_MEMORY_BUDGET_TOKENS


def test_memory_budget_from_env(monkeypatch):
    monkeypatch.setenv("HERMES_MEMORY_BUDGET_TOKENS", "5000")
    assert memory_retrieval.memory_budget_tokens() == 5000


def test_memory_budget_zero(monkeypatch):
    monkeypatch.setenv("HERMES_MEMORY_BUDGET_TOKENS", "0")
    assert memory_retrieval.memory_budget_tokens() == 0
