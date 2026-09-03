"""Il pannello AGENTI deve dire la verita' dei pianeti: vivo = delega in corso.

Prima: `vivo` (usato negli ultimi 14 giorni) era disegnato verde come `attivo`
e il gateway (fresco solo 120 s dopo l'avvio) non contava mai; il log d'uso
registra `programmatore`/`librarian`/`social` ma il registro piegava solo
`ricercatore`, quindi il Programmatore usato 40 minuti fa risultava "mai".
"""

from __future__ import annotations

import json
import time

from api import agent_registry


def _write_agent(root, name, body="# {name}\n\n## Ruolo\nAgente.\n"):
    path = root / "obsidian-vault" / "06-Agents" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.format(name=name), encoding="utf-8")
    return path


def _write_usage(root, rows):
    path = root / "tasks" / agent_registry.USAGE_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _bank(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_registry, "_profiles_root", lambda: None)
    for name in ("Programmatore Project Engineer", "Memory Librarian", "Research Analyst",
                 "Social Client Contact", "Orchestratore", "Hermes Prime - Chief of Staff"):
        _write_agent(tmp_path, name)
    return tmp_path


def _by_id(data):
    return {a["id"]: a for a in data["agents"]}


def test_running_delegation_marks_agent_attivo(tmp_path, monkeypatch):
    _bank(tmp_path, monkeypatch)
    data = agent_registry.build_agent_registry(
        tmp_path, live_agents={"programmatore-project-engineer": "programmatore codice"}
    )
    agents = _by_id(data)
    assert agents["programmatore-project-engineer"]["state"] == "attivo"
    assert agents["programmatore-project-engineer"]["status_label"] == "live / task attivo"
    assert agents["research-analyst"]["state"] == "dormiente"
    assert data["active_count"] == 1


def test_recent_usage_alone_is_not_green_anymore(tmp_path, monkeypatch):
    """27 minuti fa = storia, non liveness: niente pallino verde senza task."""
    _bank(tmp_path, monkeypatch)
    _write_usage(tmp_path, [{"ts": time.time() - 27 * 60, "agent_id": "memory-librarian",
                             "task_type": "memoria", "task_id": "d1"}])
    agents = _by_id(agent_registry.build_agent_registry(tmp_path, live_agents={}))
    lib = agents["memory-librarian"]
    assert lib["state"] == "dormiente"
    assert lib["last_used"] and lib["last_used"]["rel"], "l'ultimo uso resta visibile come testo"


def test_usage_logged_under_delegation_names_folds_into_canonical_agents(tmp_path, monkeypatch):
    _bank(tmp_path, monkeypatch)
    now = time.time()
    _write_usage(tmp_path, [
        {"ts": now - 40 * 60, "agent_id": "programmatore", "task_type": "codice", "task_id": "d1"},
        {"ts": now - 22 * 3600, "agent_id": "librarian", "task_type": "semplice", "task_id": "d2"},
        {"ts": now - 25 * 3600, "agent_id": "social", "task_type": "ricerca", "task_id": "d3"},
        {"ts": now - 14 * 3600, "agent_id": "ricercatore", "task_type": "ricerca", "task_id": "d4"},
    ])
    agents = _by_id(agent_registry.build_agent_registry(tmp_path, live_agents={}))
    for slug in ("programmatore-project-engineer", "memory-librarian",
                 "social-client-contact", "research-analyst"):
        assert agents[slug]["last_used"] is not None, f"{slug}: uso non piegato -> 'mai'"


def test_every_operational_delegation_alias_is_folded_by_registry():
    """Contratto tra le due tabelle di alias: niente drift silenzioso."""
    from api import prime_delegation

    for alias, canonical in prime_delegation._AGENT_NOTE_ALIASES.items():
        if canonical in agent_registry.OPERATIONAL_AGENT_SLUGS:
            assert agent_registry.canonical_agent_slug(alias) == canonical, alias


def test_registry_exposes_model_override_fields(tmp_path, monkeypatch):
    from api import agent_models

    _bank(tmp_path, monkeypatch)
    monkeypatch.setattr(agent_models, "STORE_PATH", tmp_path / "agent_models.json")
    agent_models.set_override("programmatore-project-engineer", "claude-opus-5")
    agents = _by_id(agent_registry.build_agent_registry(tmp_path, live_agents={}))
    prog = agents["programmatore-project-engineer"]
    assert prog["model_override"] == "claude-opus-5"
    assert prog["model_effective"] == "claude-opus-5"
    assert agents["research-analyst"]["model_override"] is None
    assert agents["research-analyst"]["model_effective"] == "auto"
    assert [o["id"] for o in agents["research-analyst"]["model_options"]] == [
        o["id"] for o in agent_models.MODEL_OPTIONS
    ]
