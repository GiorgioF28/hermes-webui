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
