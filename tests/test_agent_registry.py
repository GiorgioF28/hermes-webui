import json
from datetime import datetime, timezone

import pytest

from api import agent_registry


@pytest.fixture(autouse=True)
def _no_real_gateways(monkeypatch):
    """Isola i test dai gateway reali della macchina.

    Con la liveness basata sul pid, i cinque gateway vivi sul PC facevano
    risultare "in_attesa" agenti che i test si aspettano "dormiente". I test
    che vogliono un gateway lo scrivono in tmp_path e ripatchano _profiles_root.
    """
    monkeypatch.setattr(agent_registry, "_profiles_root", lambda: None)


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
        "Programmatore Project Engineer",
        "# Agent: Programmatore Project Engineer\n\n## Ruolo\nControlla regressioni e qualita.\n\nModello: claude-sonnet-4-6\n",
    )
    _write_agent(tmp_path, "README", "# Hermes Agents\n")
    _write_agent(tmp_path, "Agent Handoff Protocol", "# Agent Handoff Protocol\n")
    _write_agent(tmp_path, "Regole Operative Agenti", "# Regole Operative Agenti\n")
    _write_agent(tmp_path, "Censimento Agenti Operativi", "# Censimento Agenti Operativi\n")

    data = agent_registry.build_agent_registry(tmp_path)

    assert data["exists"] is True
    assert data["count"] == 1
    agent = data["agents"][0]
    assert agent["id"] == "programmatore-project-engineer"
    assert agent["name"] == "Programmatore Project Engineer"
    assert agent["role"] == "Controlla regressioni e qualita."
    assert agent["model"] == "claude-sonnet-4-6"
    assert agent["state"] == "dormiente"
    assert agent["last_used"] is None


def test_agent_registry_keeps_only_operational_agents(tmp_path):
    """The payload exposed to the UI contains exactly the six delegable agents."""
    _write_agent(tmp_path, "Hermes Prime - Chief of Staff", "# Agent: Hermes Prime - Chief of Staff\n")
    _write_agent(tmp_path, "Research Analyst", "# Agent: Research Analyst\n\n## Ruolo\nResearch.\n")
    _write_agent(tmp_path, "Memory Librarian", "# Agent: Memory Librarian\n\n## Ruolo\nMemoria.\n")
    _write_agent(tmp_path, "Programmatore Project Engineer", "# Agent: Programmatore Project Engineer\n")
    _write_agent(tmp_path, "Social Client Contact", "# Agent: Social Client Contact\n")
    _write_agent(tmp_path, "Orchestratore", "# Agent: Orchestratore\n")
    _write_agent(tmp_path, "QA Reviewer", "# Agent: QA Reviewer\n\n## Ruolo\nQA.\n")
    _write_agent(tmp_path, "Business Strategist", "# Agent: Business Strategist\n\n## Ruolo\nBusiness.\n")
    _write_agent(tmp_path, "PDF Ebook Designer", "# Agent: PDF Ebook Designer\n\n## Ruolo\nPDF.\n")
    _write_agent(tmp_path, "Social Outreach Playbook VisionBuilts", "# Playbook\n")

    data = agent_registry.get_operational_agent_registry(tmp_path)

    exposed_slugs = {agent["id"] for agent in data["agents"]}
    assert data["count"] == 6
    assert exposed_slugs == agent_registry.OPERATIONAL_AGENT_SLUGS
    assert "qa-reviewer" not in exposed_slugs
    assert "business-strategist" not in exposed_slugs


def test_agent_registry_folds_alias_usage_into_canonical_agent(monkeypatch, tmp_path):
    """La nota alias `ricercatore` non e' una riga a se': il suo uso e' del Research Analyst."""
    now = 1_700_000_000.0
    monkeypatch.setattr(agent_registry.time, "time", lambda: now)
    _write_agent(tmp_path, "Research Analyst", "# Agent: Research Analyst\n\n## Ruolo\nResearch.\n")
    _write_agent(tmp_path, "ricercatore", "# Agent: Ricercatore\n\nAlias di Research Analyst.\n")
    usage = tmp_path / "tasks" / "agent-usage.jsonl"
    usage.parent.mkdir()
    usage.write_text(
        json.dumps({"ts": now - 60, "agent_id": "ricercatore", "task_type": "ricerca", "task_id": "d1"}),
        encoding="utf-8",
    )

    agents = agent_registry.get_operational_agent_registry(tmp_path)["agents"]

    assert [a["id"] for a in agents] == ["research-analyst"]
    # Uso recente = storia (last_used), non liveness: senza delega in corso
    # l'agente e' dormiente. (Prima: "vivo", disegnato verde dal pannello.)
    assert agents[0]["state"] == "dormiente"
    assert agents[0]["last_used"]["rel"] == "1m fa"


def test_agent_registry_marks_live_and_sorts_live_first(monkeypatch, tmp_path):
    now = 1_700_000_000.0
    monkeypatch.setattr(agent_registry.time, "time", lambda: now)
    _write_agent(tmp_path, "Memory Librarian", "# Agent: Memory Librarian\n\n## Ruolo\nMemoria.\n")
    _write_agent(tmp_path, "Orchestratore", "# Agent: Orchestratore\n\n## Ruolo\nCoordina.\n")
    _write_agent(tmp_path, "Research Analyst", "# Agent: Research Analyst\n\n## Ruolo\nResearch.\n")
    usage = tmp_path / "tasks" / "agent-usage.jsonl"
    usage.parent.mkdir()
    usage.write_text(
        "\n".join(
            [
                json.dumps({"ts": now - 60, "agent_id": "research-analyst", "task_type": "ricerca", "task_id": "d1"}),
                json.dumps({"ts": now - 15 * 86400, "agent_id": "memory-librarian", "task_type": "memoria", "task_id": "d2"}),
            ]
        ),
        encoding="utf-8",
    )

    agents = agent_registry.build_agent_registry(tmp_path)["agents"]

    assert [a["name"] for a in agents] == ["Research Analyst", "Memory Librarian", "Orchestratore"]
    # L'ordine resta per ultimo uso, ma lo stato non e' piu' "vivo": senza
    # delega in corso e' dormiente (l'uso recente e' solo last_used).
    assert agents[0]["state"] == "dormiente"
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

    # Gateway fresco ma fermo + uso 5 minuti fa: e' "in attesa", non "attivo".
    # Attivo lo decide solo una delega in corso (live_agents) o il gateway che
    # dichiara agenti attivi: l'uso passato non accende piu' il pallino.
    assert agent["state"] == "in_attesa"
    assert agent["last_used"]["rel"] == "5m fa"


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

    # Gateway stantio (5 minuti, soglia 120 s) e uso di ieri: dormiente, con
    # last_used a testimoniare l'uso. (Prima: "vivo".)
    assert agents["memory-librarian"]["state"] == "dormiente"
    assert agents["memory-librarian"]["last_used"] is not None
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
