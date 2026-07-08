"""Test Fase 2 Punto 4: tagging scope delle note memoria + filtro retrieval.

Verifica:
- parse_note_scope_fast: lettura scope dal frontmatter YAML delle note
- parse_memory_index: include campo "scope" per ogni entry
- select_memories_for_task: filtro per scope del task
- note global: sempre incluse qualunque sia il task_scope
- task_scope="" → nessun filtro scope (backward compat)
- normalize_scope: valori noti passano, sconosciuti → "global"
"""
from __future__ import annotations

from pathlib import Path

import pytest

from api import memory_retrieval
from api.memory_retrieval import (
    normalize_scope,
    parse_note_scope_fast,
    parse_memory_index,
    select_memories_for_task,
    VALID_SCOPES,
)


# ── Helper ────────────────────────────────────────────────────────────────────

_SAMPLE_INDEX = """\
# Memory Index

- [Hermes setup](hermes-setup.md) — Hermes Agent on Windows: backend LocalAppData
- [VisionBuilts project](visionbuilts-project.md) — n8n ebook pipeline + console
- [Acqua bottiglie reminder](acqua-bottiglie-reminder.md) — ricordare a Giorgio le bottiglie
- [No commit blind](no-commit-blind.md) — non pushare file non revisionati
"""


def _make_mem_dir(tmp_path: Path, *, bodies: dict[str, str] | None = None) -> Path:
    """Crea una directory memoria fittizia con MEMORY.md e note opzionali."""
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    (mem_dir / "MEMORY.md").write_text(_SAMPLE_INDEX, encoding="utf-8")
    for filename, content in (bodies or {}).items():
        (mem_dir / filename).write_text(content, encoding="utf-8")
    return mem_dir


def _hermes_body(extra: str = "") -> str:
    return f"---\nscope: hermes\nname: hermes-setup\n---\n\nContenuto Hermes. {extra}"


def _visionbuilts_body(extra: str = "") -> str:
    return f"---\nscope: visionbuilts\nname: visionbuilts-project\n---\n\nContenuto VisionBuilts. {extra}"


def _global_body(extra: str = "") -> str:
    return f"---\nscope: global\nname: acqua-bottiglie-reminder\n---\n\nContenuto globale. {extra}"


def _no_scope_body(extra: str = "") -> str:
    return f"Contenuto senza scope ne frontmatter. {extra}"


# ── parse_note_scope_fast ─────────────────────────────────────────────────────

def test_parse_note_scope_global_default(tmp_path):
    """Nota senza frontmatter scope → scope 'global'."""
    mem_dir = _make_mem_dir(tmp_path, bodies={"no-scope.md": _no_scope_body()})
    scope = parse_note_scope_fast(mem_dir, "no-scope.md")
    assert scope == "global"


def test_parse_note_scope_hermes(tmp_path):
    """Nota con scope: hermes → 'hermes'."""
    mem_dir = _make_mem_dir(tmp_path, bodies={"hermes-setup.md": _hermes_body()})
    scope = parse_note_scope_fast(mem_dir, "hermes-setup.md")
    assert scope == "hermes"


def test_parse_note_scope_visionbuilts(tmp_path):
    """Nota con scope: visionbuilts → 'visionbuilts'."""
    mem_dir = _make_mem_dir(tmp_path, bodies={"visionbuilts-project.md": _visionbuilts_body()})
    scope = parse_note_scope_fast(mem_dir, "visionbuilts-project.md")
    assert scope == "visionbuilts"


def test_parse_note_scope_unknown_falls_back_global(tmp_path):
    """Nota con scope sconosciuto → 'global' (tramite normalize_scope)."""
    body = "---\nscope: sconosciuto\n---\n\nContenuto."
    mem_dir = _make_mem_dir(tmp_path, bodies={"weird.md": body})
    scope = parse_note_scope_fast(mem_dir, "weird.md")
    assert scope == "global"


def test_parse_note_scope_missing_file(tmp_path):
    """File assente → 'global' (nessun errore)."""
    mem_dir = _make_mem_dir(tmp_path)
    scope = parse_note_scope_fast(mem_dir, "non-esiste.md")
    assert scope == "global"


def test_parse_note_scope_path_traversal_blocked(tmp_path):
    """Tentativi di path traversal → 'global' (blocco sicurezza)."""
    mem_dir = _make_mem_dir(tmp_path)
    assert parse_note_scope_fast(mem_dir, "../sensitive.md") == "global"
    assert parse_note_scope_fast(mem_dir, "/etc/passwd") == "global"


# ── parse_memory_index includes scope ─────────────────────────────────────────

def test_parse_index_includes_scope_field(tmp_path):
    """parse_memory_index include campo 'scope' per ogni entry."""
    bodies = {
        "hermes-setup.md": _hermes_body(),
        "visionbuilts-project.md": _visionbuilts_body(),
        "acqua-bottiglie-reminder.md": _global_body(),
        "no-commit-blind.md": _no_scope_body(),
    }
    mem_dir = _make_mem_dir(tmp_path, bodies=bodies)
    entries = parse_memory_index(mem_dir)
    assert all("scope" in e for e in entries), "ogni entry deve avere il campo 'scope'"
    # Verifica valori corretti
    by_file = {e["filename"]: e["scope"] for e in entries}
    assert by_file.get("hermes-setup.md") == "hermes"
    assert by_file.get("visionbuilts-project.md") == "visionbuilts"
    assert by_file.get("acqua-bottiglie-reminder.md") == "global"
    # nota senza scope → fallback "global"
    assert by_file.get("no-commit-blind.md") == "global"


# ── select_memories_for_task with scope filter ────────────────────────────────

def test_select_filters_by_scope(tmp_path):
    """Task con scope='hermes' esclude note con scope='visionbuilts'."""
    bodies = {
        "hermes-setup.md": _hermes_body("hermes setup backend windows"),
        "visionbuilts-project.md": _visionbuilts_body("visionbuilts n8n pipeline"),
        "acqua-bottiglie-reminder.md": _global_body("acqua bottiglie reminder"),
        "no-commit-blind.md": _no_scope_body("commit blind push"),
    }
    mem_dir = _make_mem_dir(tmp_path, bodies=bodies)
    # Task che potrebbe matchare sia hermes che visionbuilts, ma scope="hermes"
    selected = select_memories_for_task(
        "hermes setup visionbuilts n8n pipeline",
        mem_dir,
        budget_tokens=5000,
        task_scope="hermes",
    )
    titles = [e["title"] for e in selected]
    assert "VisionBuilts project" not in titles, "visionbuilts scope esclusa con task_scope=hermes"
    assert "Hermes setup" in titles, "hermes scope inclusa"


def test_select_includes_global_always(tmp_path):
    """Note con scope='global' compaiono con qualsiasi task_scope."""
    bodies = {
        "hermes-setup.md": _hermes_body("hermes backend"),
        "visionbuilts-project.md": _visionbuilts_body("visionbuilts pipeline"),
        "acqua-bottiglie-reminder.md": _global_body("acqua bottiglie reminder"),
        "no-commit-blind.md": _no_scope_body("no commit"),
    }
    mem_dir = _make_mem_dir(tmp_path, bodies=bodies)
    # Task che matcha solo la nota global (acqua) con scope="hermes"
    selected = select_memories_for_task(
        "acqua bottiglie reminder Giorgio",
        mem_dir,
        budget_tokens=5000,
        task_scope="hermes",
    )
    titles = [e["title"] for e in selected]
    assert "Acqua bottiglie reminder" in titles, "nota global deve essere inclusa"
    assert "VisionBuilts project" not in titles, "nota visionbuilts esclusa con scope=hermes"


def test_select_no_scope_filter_includes_all(tmp_path):
    """task_scope='' → nessun filtro scope, tutte le note rilevanti appaiono."""
    bodies = {
        "hermes-setup.md": _hermes_body("hermes backend setup"),
        "visionbuilts-project.md": _visionbuilts_body("visionbuilts n8n pipeline ebook"),
        "acqua-bottiglie-reminder.md": _global_body("acqua bottiglie"),
        "no-commit-blind.md": _no_scope_body("commit push hermes visionbuilts"),
    }
    mem_dir = _make_mem_dir(tmp_path, bodies=bodies)
    selected = select_memories_for_task(
        "hermes setup visionbuilts n8n pipeline",
        mem_dir,
        budget_tokens=10000,
        task_scope="",  # nessun filtro scope
    )
    titles = [e["title"] for e in selected]
    assert "Hermes setup" in titles
    assert "VisionBuilts project" in titles


# ── normalize_scope ────────────────────────────────────────────────────────────

def test_normalize_scope_known():
    """Scope noti passano attraverso normalize_scope invariati."""
    assert normalize_scope("hermes") == "hermes"
    assert normalize_scope("visionbuilts") == "visionbuilts"
    assert normalize_scope("rap") == "rap"
    assert normalize_scope("global") == "global"


def test_normalize_scope_unknown():
    """Scope sconosciuti → 'global'."""
    assert normalize_scope("xyz") == "global"
    assert normalize_scope("") == "global"
    assert normalize_scope("sconosciuto") == "global"
    assert normalize_scope("HERMES") == "hermes"  # case-insensitive → noto
    assert normalize_scope("UNKNOWN") == "global"  # case-insensitive ma sconosciuto


def test_valid_scopes_contains_expected():
    """VALID_SCOPES include tutti gli scope attesi."""
    assert "hermes" in VALID_SCOPES
    assert "visionbuilts" in VALID_SCOPES
    assert "global" in VALID_SCOPES
    assert "rap" in VALID_SCOPES
