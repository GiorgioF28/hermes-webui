from pathlib import Path

from api.study_section import build_summary, parse_subject, retrieve_by_tag, save_chapter_memory


def test_parse_subject_sample_sections(tmp_path: Path) -> None:
    note = tmp_path / "Materia prova.md"
    note.write_text(
        """# Materia prova

## Argomenti
- [x] Primo argomento
- [ ] Secondo argomento

## Cose imparate
| Data | Argomento | Riassunto |
|---|---|---|
| 2026-07-10 | Primo argomento | Regola sintetica |

## Quiz
| Data | Fonte | Punteggio | Errori da rivedere |
|---|---|---:|---|
| 2026-07-10 | Concorsando | 24/30 | Definizioni |

## Da ripassare
- Secondo argomento
""",
        encoding="utf-8",
    )

    result = parse_subject(note, tmp_path)

    assert result["parse_ok"] is True
    assert result["argomenti"]["done"] == 1
    assert result["argomenti"]["total"] == 2
    assert result["imparate"] == [{"titolo": "Primo argomento", "testo": "Regola sintetica"}]
    assert result["quiz"][0]["fonte"] == "Concorsando"
    assert result["da_ripassare"] == ["Secondo argomento"]


def test_malformed_note_never_breaks_summary(tmp_path: Path) -> None:
    study_dir = tmp_path / "Concorso INPS"
    study_dir.mkdir()
    (study_dir / "Indice.md").write_text(
        """# Indice

## Quiz - parti da qui
- [Concorsando](https://example.test/quiz)
**Strategia punteggio**: prova.

## Calendario 2h/giorno
| Slot | Durata | Cosa fare |
|---|---:|---|
| Quiz | 70 min | Allenamento |

## Log sessioni
| Data | Ore fatte | Materia |
|---|---:|---|
| - | 0 | - |

## Contatore quiz
| Tipo | Totale quiz |
|---|---:|
| Per materia | 3 |
""",
        encoding="utf-8",
    )
    (study_dir / "Nota malformata.md").write_text("testo senza sezioni\n| tabella rotta", encoding="utf-8")

    summary = build_summary(study_dir)

    assert summary["dashboard"]["quiz_totali"] == 3
    assert summary["dashboard"]["quiz_links"][0]["url"] == "https://example.test/quiz"
    assert len(summary["subjects"]) == 1
    assert summary["subjects"][0]["parse_ok"] is True
    assert summary["subjects"][0]["argomenti"] == {"done": 0, "total": 0, "items": []}


def test_study_webui_is_wired() -> None:
    repo = Path(__file__).resolve().parent.parent
    routes = (repo / "api" / "routes.py").read_text(encoding="utf-8")
    panels = (repo / "static" / "panels.js").read_text(encoding="utf-8")
    css = (repo / "static" / "style.css").read_text(encoding="utf-8")

    assert 'parsed.path == "/api/study/summary"' in routes
    assert 'parsed.path == "/api/study/chapters"' in routes
    assert 'parsed.path == "/api/study/retrieval"' in routes
    assert 'parsed.path == "/api/study/save-memory"' in routes
    assert 'parsed.path == "/api/study/professor/start"' in routes
    assert "from api.study_section import build_summary" in routes
    assert "loadStudySection" in panels
    assert "api('/api/study/summary')" in panels
    assert "startStudyProfessorChat" in panels
    assert "saveStudyMemory" in panels
    assert "study-subject-grid" in panels
    assert "study-professor" in panels
    assert ".study-view" in css
    assert ".study-subject-card" in css
    assert ".study-professor" in css


def test_build_summary_includes_chapters_and_tags(tmp_path: Path) -> None:
    study_dir = tmp_path / "Concorso INPS"
    study_dir.mkdir()
    (study_dir / "Indice.md").write_text(
        """# Indice

## Materie e copertura
| # | Materia | Nota |
|---|---|---|
| 1 | Office automation | [[Office automation]] |
""",
        encoding="utf-8",
    )
    (study_dir / "Office automation.md").write_text(
        """# Office automation

## Argomenti
- [ ] Fogli di calcolo
""",
        encoding="utf-8",
    )
    chapter_dir = study_dir / "Office automation"
    chapter_dir.mkdir()
    (chapter_dir / "Excel shortcuts.md").write_text(
        """---
subject: Office automation
chapter: Excel shortcuts
chapter_slug: excel_shortcuts
tags: [office_automation/excel_shortcuts, excel/shortcuts]
---

# Excel shortcuts

## Riassunto

- Shortcut principali.

## Da ripassare

- F2 modifica la cella.
""",
        encoding="utf-8",
    )

    summary = build_summary(study_dir)

    subject = summary["subjects"][0]
    assert subject["name"] == "Office automation"
    assert subject["chapters"][0]["name"] == "Excel shortcuts"
    assert subject["chapters"][0]["tag"] == "office_automation/excel_shortcuts"
    assert "excel/shortcuts" in subject["chapters"][0]["tags"]
    assert subject["chapters"][0]["da_ripassare"] == ["F2 modifica la cella."]


def test_retrieve_by_tag_returns_chapter_and_recent_errors(tmp_path: Path) -> None:
    root = tmp_path / "07-Study"
    study_dir = root / "Concorso INPS"
    chapter_dir = study_dir / "Office automation"
    chapter_dir.mkdir(parents=True)
    (study_dir / "Indice.md").write_text("# Indice\n", encoding="utf-8")
    (study_dir / "Office automation.md").write_text("# Office automation\n", encoding="utf-8")
    (chapter_dir / "Excel shortcuts.md").write_text(
        """---
subject: Office automation
chapter: Excel shortcuts
chapter_slug: excel_shortcuts
tags: [office_automation/excel_shortcuts, excel/shortcuts]
---

# Excel shortcuts
""",
        encoding="utf-8",
    )
    (study_dir / "Error log.md").write_text(
        """# Error log

| Data | Materia | Capitolo | Fonte | Domanda | Risposta corretta | Errore | Tag |
|---|---|---|---|---|---|---|---|
| 2026-07-11 | Office automation | Excel shortcuts | Test | F2? | Modifica cella | Confusa con rinomina file | excel/shortcuts |
""",
        encoding="utf-8",
    )

    result = retrieve_by_tag("Concorso INPS", "excel/shortcuts", study_root=root)

    assert result["found"] is True
    assert result["chapter"]["name"] == "Excel shortcuts"
    assert "# Excel shortcuts" in result["page_markdown"]
    assert result["recent_errors"]
    assert "Confusa con rinomina file" in result["recent_errors"][0]["text"]


def test_save_chapter_memory_is_additive_and_tagged(tmp_path: Path) -> None:
    root = tmp_path / "07-Study"
    study_dir = root / "Concorso INPS"
    study_dir.mkdir(parents=True)
    (study_dir / "Indice.md").write_text("# Indice\n", encoding="utf-8")
    (study_dir / "Office automation.md").write_text("# Office automation\n", encoding="utf-8")

    saved = save_chapter_memory(
        course="Concorso INPS",
        subject="Office automation",
        chapter="Excel shortcuts",
        summary="F2 modifica la cella attiva.",
        explanation="Ctrl+; inserisce la data corrente.",
        quiz={"question": "A cosa serve F2 in Excel?", "error": "Pensavo rinominasse il file"},
        study_root=root,
    )

    chapter_path = study_dir / "Office automation" / "Excel shortcuts.md"
    original_subject = (study_dir / "Office automation.md").read_text(encoding="utf-8")
    chapter_text = chapter_path.read_text(encoding="utf-8")
    error_log = (study_dir / "Error log.md").read_text(encoding="utf-8")

    assert saved["tag"] == "office_automation/excel_shortcuts"
    assert original_subject == "# Office automation\n"
    assert "F2 modifica la cella attiva." in chapter_text
    assert "Ctrl+; inserisce la data corrente." in chapter_text
    assert "A cosa serve F2 in Excel?" in error_log
    assert "office_automation/excel_shortcuts" in error_log
