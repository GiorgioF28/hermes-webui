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
    # Con lean disabilitato verifichiamo la struttura "append" per compat
    monkeypatch.setenv("HERMES_PRIME_USE_LEAN_PRESET", "0")
    monkeypatch.setattr(routes, "_hermes_prime_persona_text", lambda: "PERSONA_X")
    ws = _mk_ws(tmp_path)
    sp = routes._hermes_prime_system_prompt(ws)
    append = sp["append"]
    # brief presenti
    assert "Alpha" in append
    # vecchio dump vault RIMOSSO
    assert "Contesto vault verificato" not in append
    assert "Obsidian memory" not in append
    # istruzione a delegare al Librarian per il dettaglio profondo
    assert "Librarian" in append


def test_system_prompt_shape_unchanged(tmp_path, monkeypatch):
    # Backward compat con lean disabilitato
    monkeypatch.setenv("HERMES_PRIME_USE_LEAN_PRESET", "0")
    sp = routes._hermes_prime_system_prompt(_mk_ws(tmp_path))
    assert sp["type"] == "preset"
    assert sp["preset"] == "claude_code"
    assert isinstance(sp["append"], str)


def test_system_prompt_lean_is_string(tmp_path, monkeypatch):
    """Con lean abilitato, _hermes_prime_system_prompt ritorna una stringa."""
    monkeypatch.setenv("HERMES_PRIME_USE_LEAN_PRESET", "1")
    sp = routes._hermes_prime_system_prompt(_mk_ws(tmp_path))
    assert isinstance(sp, str), "lean preset deve ritornare una stringa"
    assert "Librarian" in sp or len(sp) > 0  # almeno il footer istruzione


def test_system_prompt_lean_mentions_ask_user_tool(tmp_path, monkeypatch):
    """Il box scelte del Command Bridge parte solo se Prime chiama il tool.

    Senza l'istruzione esplicita il modello scrive le alternative in prosa e
    l'evento `clarify` non viene mai emesso (root cause storica del box che
    "non ha mai funzionato").
    """
    monkeypatch.setenv("HERMES_PRIME_USE_LEAN_PRESET", "1")
    sp = routes._hermes_prime_system_prompt(_mk_ws(tmp_path))
    assert "mcp__hermes__ask_user" in sp


def test_system_prompt_preset_mentions_ask_user_tool(tmp_path, monkeypatch):
    """Stessa istruzione anche nel ramo di compatibilità (preset claude_code)."""
    monkeypatch.setenv("HERMES_PRIME_USE_LEAN_PRESET", "0")
    sp = routes._hermes_prime_system_prompt(_mk_ws(tmp_path))
    assert "mcp__hermes__ask_user" in sp["append"]


def test_ask_user_tool_is_allowed_for_prime():
    """Il tool deve essere anche nella allow-list, non solo nel prompt."""
    import inspect
    src = inspect.getsource(routes._get_claude_registry)
    assert "mcp__hermes__ask_user" in src
    assert "build_ask_user_server" in src
