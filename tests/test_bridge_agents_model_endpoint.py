"""POST /api/bridge/agents/model — cambia il modello di un sotto-agente dal pannello."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from api import agent_models, agent_registry, routes


class _Handler:
    def __init__(self):
        self.wfile = io.BytesIO()
        self.status = None
        self.headers = {}

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.headers[name] = value

    def end_headers(self):
        pass


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    agents = tmp_path / "obsidian-vault" / "06-Agents"
    agents.mkdir(parents=True)
    (agents / "Research Analyst.md").write_text("# Research Analyst\n\n## Ruolo\nRicerca.\n", encoding="utf-8")
    monkeypatch.setattr(agent_models, "STORE_PATH", tmp_path / "agent_models.json")
    monkeypatch.setattr(agent_registry, "_profiles_root", lambda: None)
    monkeypatch.setattr(routes, "DEFAULT_WORKSPACE", Path(tmp_path))
    return tmp_path


def _payload(handler):
    return json.loads(handler.wfile.getvalue().decode("utf-8"))


def test_set_model_persists_and_returns_registry(workspace):
    h = _Handler()
    routes._handle_bridge_agents_model(h, {"agent_id": "research-analyst", "model": "claude-fable-5-1"})
    assert h.status in (None, 200)
    data = _payload(h)
    assert data["ok"] is True
    agent = next(a for a in data["agents"] if a["id"] == "research-analyst")
    assert agent["model_override"] == "claude-fable-5-1"
    assert agent_models.get_overrides() == {"research-analyst": "claude-fable-5-1"}


def test_bad_model_is_400(workspace):
    h = _Handler()
    routes._handle_bridge_agents_model(h, {"agent_id": "research-analyst", "model": "gpt-9"})
    assert h.status == 400
    assert agent_models.get_overrides() == {}


def test_missing_agent_is_400(workspace):
    h = _Handler()
    routes._handle_bridge_agents_model(h, {"model": "codex"})
    assert h.status == 400
