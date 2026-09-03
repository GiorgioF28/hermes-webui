"""Accesso agli strumenti di memoria per i worker Claude.

Politica: il filesystem del Vault (MCP `hermes-memory`) e' solo del Librarian
(il recinto ripristina comunque le scritture degli altri); Notion invece serve
a tutti — il Ricercatore carica i batch nel CRM, il Social aggiorna gli stati —
quindi il server MCP `notion` va dato a ogni worker, anche sul fallback Claude.
Su Codex arriva gia' dal config.toml del CLI.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from api import prime_delegation as pd


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {
            "hermes-memory": {"command": "memory"},
            "notion": {"command": "notion"},
            "github": {"command": "gh"},
        }}),
        encoding="utf-8",
    )
    return tmp_path


def test_librarian_gets_vault_and_notion(workspace):
    assert set(pd._worker_mcp_servers(str(workspace), "librarian")) == {"hermes-memory", "notion"}


@pytest.mark.parametrize("agent", ["ricercatore", "social", "programmatore", "orchestratore", ""])
def test_everyone_else_gets_notion_only(workspace, agent):
    assert set(pd._worker_mcp_servers(str(workspace), agent)) == {"notion"}


def test_claude_worker_receives_notion_mcp(monkeypatch, workspace):
    calls = []

    async def fake_run_worker(task, model, ws, **kwargs):
        calls.append(kwargs)
        return "Risultato concreto della delega. " * 20

    monkeypatch.setattr(pd, "_run_worker", fake_run_worker)
    monkeypatch.setattr(pd, "_persist_bg_task", lambda *a, **k: None)
    monkeypatch.setattr(pd, "_enqueue_librarian_pass", lambda *a, **k: None)
    monkeypatch.setattr(pd, "_BG_TASKS", {"d1": {
        "id": "d1", "session_id": "hermes-prime", "agent": "ricercatore", "agent_id": "ricercatore",
        "task_type": "ricerca", "task": "t", "status": "in_corso", "output": "",
        "started": time.time(), "finished": None,
    }})

    asyncio.run(pd._run_and_store("d1", "ricerca", "t", "claude-sonnet-5", "Sonnet 5", str(workspace)))

    assert set(calls[0]["mcp_servers"]) == {"notion"}
