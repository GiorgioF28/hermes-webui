from __future__ import annotations

from pathlib import Path


def test_control_center_summary_reads_hermes_workspace(monkeypatch, tmp_path):
    from api import routes

    root = tmp_path / "Hermes setup"
    (root / "obsidian-vault" / "01-Projects").mkdir(parents=True)
    (root / "projects").mkdir()
    (root / "tasks").mkdir()
    (root / "obsidian-vault" / "01-Projects" / "Hermes Control Center.md").write_text(
        "# Hermes Control Center\n\n"
        "## Obiettivo\n\nBuild the operating surface.\n\n"
        "## Stato attuale\n\nActive.\n\n"
        "## Prossima azione\n\nWire the panel.\n",
        encoding="utf-8",
    )
    (root / "projects" / "project-inventory.csv").write_text(
        '"project_id","name","repo_url","status","business_goal","next_action","ai_agent","last_reviewed"\n'
        '"hermes-control-center","Hermes Control Center","","active","Build OS","Wire panel","Codex","2026-06-04"\n',
        encoding="utf-8",
    )
    (root / "projects" / "active-repos.csv").write_text(
        '"repo_full_name","project_id","status","business_goal","next_action","ai_agent"\n',
        encoding="utf-8",
    )
    (root / "tasks" / "today.md").write_text(
        "# Today\n\n- [ ] Implement command center\n- [x] Audit WebUI\n",
        encoding="utf-8",
    )
    (root / "tasks" / "backlog.csv").write_text(
        "task_id,project_id,title,priority,status,due_date,next_action,source\n"
        "T-1,hermes-control-center,Build summary endpoint,P0,open,2026-06-04,Return projects/tasks,local\n"
        "T-2,hermes-control-center,Old task,P2,done,2026-06-04,Done,local\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_CONTROL_CENTER_ROOT", str(root))

    payload = routes._control_center_summary_payload()

    assert payload["vault_present"] is True
    assert payload["note_count"] == 1
    assert payload["today_open_count"] == 1
    assert payload["today_done_count"] == 1
    assert payload["active_projects"][0]["project_id"] == "hermes-control-center"
    assert payload["open_backlog"][0]["task_id"] == "T-1"
    assert payload["project_notes"][0]["next_action"] == "Wire the panel."
    assert payload["project_cockpit"][0]["project"]["project_id"] == "hermes-control-center"
    assert payload["project_cockpit"][0]["note"]["title"] == "Hermes Control Center"
    assert payload["project_cockpit"][0]["open_tasks"][0]["task_id"] == "T-1"
    assert payload["project_cockpit"][0]["open_task_count"] == 1
    assert payload["action_queue"][0]["task_id"] == "T-1"
    assert payload["risks"]["counts"]["projects_missing_note"] == 0


def test_control_center_project_next_action_updates_inventory_and_note(monkeypatch, tmp_path):
    from api import routes

    root = tmp_path / "Hermes setup"
    note = root / "obsidian-vault" / "01-Projects" / "Hermes Control Center.md"
    note.parent.mkdir(parents=True)
    (root / "projects").mkdir()
    (root / "tasks").mkdir()
    note.write_text(
        "# Hermes Control Center\n\n"
        "## Obiettivo\n\nBuild.\n\n"
        "## Prossima azione\n\nOld action.\n\n"
        "## Asset o link\n\nLocal.\n",
        encoding="utf-8",
    )
    inventory = root / "projects" / "project-inventory.csv"
    inventory.write_text(
        '"project_id","name","repo_url","status","business_goal","next_action","ai_agent","last_reviewed"\n'
        '"hermes-control-center","Hermes Control Center","","active","Build OS","Old action","Codex","2026-06-04"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_CONTROL_CENTER_ROOT", str(root))

    result = routes._cc_write_project_inventory_next_action(
        inventory,
        "hermes-control-center",
        "Ship controlled action queue",
    )
    note_updated = routes._cc_update_markdown_heading(
        note,
        "Prossima azione",
        "Ship controlled action queue",
    )

    assert result["project_id"] == "hermes-control-center"
    assert note_updated is True
    assert "Ship controlled action queue" in inventory.read_text(encoding="utf-8")
    note_text = note.read_text(encoding="utf-8")
    assert "## Prossima azione\n\nShip controlled action queue" in note_text
    assert "## Asset o link" in note_text


def test_mark_today_task_done_appends_when_project_card_task_was_not_in_today(tmp_path):
    from api import routes

    today = tmp_path / "today.md"
    today.write_text("# Today\n\n- [x] Existing task\n", encoding="utf-8")

    result = routes._cc_mark_today_task_done(today, "Project card task")

    assert result["updated"] is True
    assert result["appended"] is True
    assert "- [x] Project card task" in today.read_text(encoding="utf-8")


def test_control_center_frontend_is_wired():
    repo = Path(__file__).resolve().parent.parent
    html = (repo / "static" / "index.html").read_text(encoding="utf-8")
    panels = (repo / "static" / "panels.js").read_text(encoding="utf-8")
    css = (repo / "static" / "style.css").read_text(encoding="utf-8")
    icons = (repo / "static" / "icons.js").read_text(encoding="utf-8")

    assert "daily_command" in panels
    assert 'data-panel="command"' in html
    assert 'id="mainCommand"' in html
    assert "loadCommandCenter" in panels
    assert "api('/api/control-center/summary')" in panels
    assert "Automations" in panels
    assert "data.automations" in panels
    assert "function _renderControlCenterSummary" in panels
    assert "daily_command_project_cockpit" in panels
    assert "daily_command_action_queue" in panels
    assert "daily_command_risks" in panels
    assert "project_cockpit" in panels
    assert "action_queue" in panels
    assert "risks" in panels
    assert "'concorso-inps-assistente-informatico'" in panels
    assert "CC_PROJECT_HIDDEN" in panels
    assert "{id:'concorso-inps'" not in panels
    assert "data-cc-path" in panels
    assert "bringControlCenterTaskToChat" in panels
    assert "setControlCenterProjectNextAction" in panels
    assert "runAgentWorker" in panels
    assert "Worker -" in panels
    assert "/api/control-center/project-next-action" in panels
    assert "data-cc-task-id" in panels
    assert "data-agent-id" in panels
    assert "daily_command_bring_to_chat" in panels
    assert "showing-command" in css
    assert ".control-center-view" in css
    assert ".cc-stat-grid" in css
    assert ".cc-cockpit-list" in css
    assert ".cc-chat-btn" in css
    assert ".cc-agent-run-btn" in css
    assert ".cc-risk-grid" in css
    assert "'send':" in icons
    assert "'target':" in icons


def test_work_mode_prioritizes_urgent_and_tracks_recent_minutes(monkeypatch, tmp_path):
    from api import routes

    root = tmp_path / "Hermes setup"
    (root / "obsidian-vault" / "01-Projects").mkdir(parents=True)
    (root / "projects").mkdir()
    (root / "tasks").mkdir()
    (root / "projects" / "project-inventory.csv").write_text(
        "project_id,name,repo_url,status,business_goal,next_action,ai_agent,last_reviewed\n"
        "urgent,Urgent Project,,active,Deliver,Do urgent work,Codex,2026-06-07\n"
        "rotation,Rotation Project,,active,Build,Do normal work,Codex,2026-06-07\n",
        encoding="utf-8",
    )
    (root / "projects" / "active-repos.csv").write_text(
        "repo_full_name,project_id,status,business_goal,next_action,ai_agent\n",
        encoding="utf-8",
    )
    (root / "tasks" / "today.md").write_text("# Today\n", encoding="utf-8")
    today = __import__("time").strftime("%Y-%m-%d")
    (root / "tasks" / "backlog.csv").write_text(
        "task_id,project_id,title,priority,status,due_date,next_action,source\n"
        f"T-1,urgent,Contact client,P0,open,{today},Send message,test\n"
        "T-2,rotation,Build feature,P1,open,2099-01-01,Open editor,test\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_CONTROL_CENTER_ROOT", str(root))
    routes._work_write_state(root, {
        "active": None,
        "history": [{
            "project_id": "rotation",
            "project_name": "Rotation Project",
            "started_at": __import__("time").time() - 1800,
            "ended_at": __import__("time").time(),
            "minutes": 30,
            "status": "completed",
        }],
    })

    payload = routes._work_state_payload()

    assert payload["candidates"][0]["project_id"] == "urgent"
    assert payload["candidates"][0]["urgent_count"] == 1
    rotation = next(row for row in payload["candidates"] if row["project_id"] == "rotation")
    assert rotation["recent_minutes"] == 30
    assert payload["history"][0]["project_id"] == "rotation"


def test_work_frontend_is_wired():
    repo = Path(__file__).resolve().parent.parent
    html = (repo / "static" / "index.html").read_text(encoding="utf-8")
    panels = (repo / "static" / "panels.js").read_text(encoding="utf-8")
    css = (repo / "static" / "style.css").read_text(encoding="utf-8")

    assert 'data-panel="work"' in html
    assert 'id="mainWork"' in html
    assert "loadWorkMode" in panels
    assert "startWorkSession" in panels
    assert "openWorkFocusChat" in panels
    assert "/api/work/start" in panels
    assert "/api/work/finish" in panels
    assert "showing-work" in css
    assert ".work-bubble" in css
    assert ".work-timer" in css
