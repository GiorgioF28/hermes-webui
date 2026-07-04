"""Fase 1 ciclo-memoria: contesto leggero di Prime."""
from api import routes

_CSV = (
    '"project_id","name","repo_url","status","business_goal","next_action","ai_agent","last_reviewed"\n'
    '"p1","Alpha","","active","Vendere ebook","Inviare 5 DM","Codex","2026-06-01"\n'
    '"p2","Beta","","supporting","Interfaccia web","Testare microfono","Codex","2026-06-01"\n'
    '"p3","Gamma","","archived","Vecchio","Niente","Codex","2026-05-01"\n'
)


def _mk_ws(tmp_path):
    proj = tmp_path / "projects"
    proj.mkdir()
    (proj / "project-inventory.csv").write_text(_CSV, encoding="utf-8")
    return str(tmp_path)


def test_brief_includes_active_projects(tmp_path):
    brief = routes._in_progress_projects_brief(_mk_ws(tmp_path))
    assert "Alpha" in brief
    assert "Vendere ebook" in brief
    assert "Inviare 5 DM" in brief


def test_brief_excludes_non_active(tmp_path):
    brief = routes._in_progress_projects_brief(_mk_ws(tmp_path))
    assert "Beta" not in brief   # supporting -> escluso
    assert "Gamma" not in brief  # archived -> escluso


def test_brief_empty_when_no_csv(tmp_path):
    assert routes._in_progress_projects_brief(str(tmp_path)) == ""


def test_brief_is_bounded(tmp_path):
    brief = routes._in_progress_projects_brief(_mk_ws(tmp_path), max_chars=40)
    assert len(brief) <= 60  # 40 + suffisso troncamento


def test_system_prompt_is_lean(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "_hermes_prime_persona_text", lambda: "PERSONA_X")
    ws = _mk_ws(tmp_path)
    sp = routes._hermes_prime_system_prompt(ws)
    append = sp["append"]
    # persona + brief presenti
    assert "PERSONA_X" in append
    assert "Alpha" in append
    # vecchio dump vault RIMOSSO
    assert "Contesto vault verificato" not in append
    assert "Obsidian memory" not in append
    # istruzione a delegare al Librarian per il dettaglio profondo
    assert "Librarian" in append


def test_system_prompt_shape_unchanged(tmp_path):
    sp = routes._hermes_prime_system_prompt(_mk_ws(tmp_path))
    assert sp["type"] == "preset"
    assert sp["preset"] == "claude_code"
    assert isinstance(sp["append"], str)
