import json
from datetime import datetime, timezone

from api import agent_registry


def _write_agent(root, name, body):
    path = root / "obsidian-vault" / "06-Agents" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _write_gateway(profiles_root, profile, now, *, running=True, active=0, discord="connected"):
    path = profiles_root / profile / "gateway_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "gateway_state": "running" if running else "stopped",
        "updated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "active_agents": active,
        "platforms": {"discord": {"state": discord}},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_agent_registry_excludes_system_notes_and_parses_role_model(tmp_path):
    _write_agent(
        tmp_path,
        "QA Reviewer",
        "# Agent: QA Reviewer\n\n## Ruolo\nControlla regressioni e qualita.\n\nModello: claude-sonnet-4-6\n",
    )
    _write_agent(tmp_path, "README", "# Hermes Agents\n")
    _write_agent(tmp_path, "Agent Handoff Protocol", "# Agent Handoff Protocol\n")
    _write_agent(tmp_path, "Regole Operative Agenti", "# Regole Operative Agenti\n")
    _write_agent(tmp_path, "Censimento Agenti Operativi", "# Censimento Agenti Operativi\n")

    data = agent_registry.build_agent_registry(tmp_path)

    assert data["exists"] is True
    assert data["count"] == 1
    agent = data["agents"][0]
    assert agent["id"] == "qa-reviewer"
    assert agent["name"] == "QA Reviewer"
    assert agent["role"] == "Controlla regressioni e qualita."
    assert agent["model"] == "claude-sonnet-4-6"
    assert agent["state"] == "dormiente"
    assert agent["last_used"] is None


def test_agent_registry_marks_live_and_sorts_live_first(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    monkeypatch.setattr(agent_registry.time, "time", lambda: now)
    _write_agent(tmp_path, "QA Reviewer", "# Agent: QA Reviewer\n\n## Ruolo\nQA.\n")
    _write_agent(tmp_path, "Business Strategist", "# Agent: Business Strategist\n\n## Ruolo\nBusiness.\n")
    _write_agent(tmp_path, "Research Analyst", "# Agent: Research Analyst\n\n## Ruolo\nResearch.\n")
    usage = tmp_path / "tasks" / "agent-usage.jsonl"
    usage.parent.mkdir()
    usage.write_text(
        "\n".join(
            [
                json.dumps({"ts": now - 60, "agent_id": "research-analyst", "task_type": "ricerca", "task_id": "d1"}),
                json.dumps({"ts": now - 15 * 86400, "agent_id": "qa-reviewer", "task_type": "qa", "task_id": "d2"}),
            ]
        ),
        encoding="utf-8",
    )

    agents = agent_registry.build_agent_registry(tmp_path)["agents"]

    assert [a["name"] for a in agents] == ["Research Analyst", "QA Reviewer", "Business Strategist"]
    assert agents[0]["state"] == "vivo"
    assert agents[0]["last_used"]["rel"] == "1m fa"
    assert agents[1]["state"] == "dormiente"
    assert agents[2]["state"] == "dormiente"


def test_agent_registry_marks_fresh_active_gateway_attivo(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    profiles_root = tmp_path / "profiles"
    monkeypatch.setattr(agent_registry.time, "time", lambda: now)
    monkeypatch.setattr(agent_registry, "_profiles_root", lambda: profiles_root)
    _write_agent(tmp_path, "Orchestratore", "# Agent: Orchestratore\n\n## Ruolo\nCoordina.\n")
    _write_gateway(profiles_root, "orchestratore", now, active=2)

    agent = agent_registry.build_agent_registry(tmp_path)["agents"][0]

    assert agent["state"] == "attivo"
    assert agent["gateway"] == {
        "profile": "orchestratore",
        "running": True,
        "active": 2,
        "discord": "connected",
    }


def test_agent_registry_marks_fresh_idle_gateway_in_attesa(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    profiles_root = tmp_path / "profiles"
    monkeypatch.setattr(agent_registry.time, "time", lambda: now)
    monkeypatch.setattr(agent_registry, "_profiles_root", lambda: profiles_root)
    _write_agent(tmp_path, "Programmatore", "# Agent: Programmatore Project Engineer\n\n## Ruolo\nSviluppa.\n")
    _write_gateway(profiles_root, "programmatore", now, active=0)

    agent = agent_registry.build_agent_registry(tmp_path)["agents"][0]

    assert agent["id"] == "programmatore-project-engineer"
    assert agent["state"] == "in_attesa"
    assert agent["gateway"]["running"] is True
    assert agent["gateway"]["active"] == 0


def test_agent_registry_marks_idle_gateway_attivo_with_recent_usage(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    profiles_root = tmp_path / "profiles"
    monkeypatch.setattr(agent_registry.time, "time", lambda: now)
    monkeypatch.setattr(agent_registry, "_profiles_root", lambda: profiles_root)
    _write_agent(tmp_path, "Research Analyst", "# Agent: Research Analyst\n\n## Ruolo\nRicerca.\n")
    _write_gateway(profiles_root, "ricercatore", now, active=0)
    usage = tmp_path / "tasks" / "agent-usage.jsonl"
    usage.parent.mkdir()
    usage.write_text(
        json.dumps({"ts": now - 300, "agent_id": "research-analyst", "task_type": "ricerca", "task_id": "d1"}),
        encoding="utf-8",
    )

    agent = agent_registry.build_agent_registry(tmp_path)["agents"][0]

    assert agent["state"] == "attivo"


def test_agent_registry_stale_or_missing_gateway_falls_back_to_usage(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    profiles_root = tmp_path / "profiles"
    monkeypatch.setattr(agent_registry.time, "time", lambda: now)
    monkeypatch.setattr(agent_registry, "_profiles_root", lambda: profiles_root)
    _write_agent(tmp_path, "Memory Librarian", "# Agent: Memory Librarian\n\n## Ruolo\nMemoria.\n")
    _write_agent(tmp_path, "Social", "# Agent: Social Client Contact\n\n## Ruolo\nContatti.\n")
    _write_gateway(profiles_root, "librarian", now - 300, active=3)
    usage = tmp_path / "tasks" / "agent-usage.jsonl"
    usage.parent.mkdir()
    usage.write_text(
        json.dumps({"ts": now - 86400, "agent_id": "memory-librarian", "task_type": "memoria", "task_id": "d2"}),
        encoding="utf-8",
    )

    agents = {a["id"]: a for a in agent_registry.build_agent_registry(tmp_path)["agents"]}

    assert agents["memory-librarian"]["state"] == "vivo"
    assert agents["memory-librarian"]["gateway"]["running"] is False
    assert agents["social-client-contact"]["state"] == "dormiente"
    assert agents["social-client-contact"]["gateway"] == {
        "profile": "social",
        "running": False,
        "active": 0,
        "discord": None,
    }


def test_record_agent_usage_appends_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_registry.time, "time", lambda: 123.0)

    agent_registry.record_agent_usage(tmp_path, "QA Reviewer", "qa", "d9")
    agent_registry.record_agent_usage(tmp_path, "", "qa", "d10")

    rows = (tmp_path / "tasks" / "agent-usage.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0]) == {
        "ts": 123.0,
        "agent_id": "qa-reviewer",
        "task_type": "qa",
        "task_id": "d9",
    }
