import json

from api import agent_registry


def _write_agent(root, name, body):
    path = root / "obsidian-vault" / "06-Agents" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
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

    assert [a["name"] for a in agents] == ["Research Analyst", "Business Strategist", "QA Reviewer"]
    assert agents[0]["state"] == "vivo"
    assert agents[0]["last_used"]["rel"] == "1m fa"
    assert agents[1]["state"] == "dormiente"
    assert agents[2]["state"] == "dormiente"


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
