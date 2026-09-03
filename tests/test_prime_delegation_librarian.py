import asyncio
import json
import time

import api.prime_delegation as delegation


def test_agent_note_loaded_by_filename_slug(tmp_path):
    agents = tmp_path / "obsidian-vault" / "06-Agents"
    agents.mkdir(parents=True)
    note = agents / "Memory Librarian.md"
    note.write_text("# Agent: Memory Librarian\n\n## Ruolo\nCustode memoria.", encoding="utf-8")

    prompt = delegation._worker_system_prompt("memory-librarian", str(tmp_path))

    assert "Custode memoria" in prompt
    assert "REGOLE DI SICUREZZA" in prompt


def test_load_memory_mcp_servers_filters_to_memory_servers(tmp_path):
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "hermes-memory": {"command": "memory", "args": ["--vault"]},
                    "notion": {"command": "notion", "env": {"NOTION_TOKEN": "from-env"}},
                    "unrelated": {"command": "skip-me"},
                }
            }
        ),
        encoding="utf-8",
    )

    servers = delegation._load_memory_mcp_servers(str(tmp_path))

    assert set(servers) == {"hermes-memory", "notion"}
    assert servers["hermes-memory"]["command"] == "memory"
    assert servers["notion"]["env"] == {"NOTION_TOKEN": "from-env"}


def test_run_and_store_marks_ok_before_enqueueing_librarian(monkeypatch, tmp_path):
    calls = []
    task_id = "d-test"
    delegation._BG_TASKS[task_id] = {
        "id": task_id,
        "session_id": "s1",
        "agent": "qa-reviewer",
        "agent_id": "qa-reviewer",
        "task_type": "semplice",
        "task": "fai qa",
        "status": "in_corso",
        "output": "",
        "started": time.time(),
        "finished": None,
    }

    async def fake_run_worker(task, model, workspace, **kwargs):
        calls.append(("worker", task, model, kwargs))
        return "Agent Result"

    def fake_enqueue(task_id_arg, task_type, task, output, workspace):
        snapshot = dict(delegation._BG_TASKS[task_id_arg])
        calls.append(("enqueue", task_id_arg, task_type, task, output, snapshot))

    monkeypatch.setattr(delegation, "_run_worker", fake_run_worker)
    monkeypatch.setattr(delegation, "_enqueue_librarian_pass", fake_enqueue)

    asyncio.run(
        delegation._run_and_store(
            task_id,
            "semplice",
            "fai qa",
            "claude-sonnet-4-6",
            "Sonnet",
            str(tmp_path),
        )
    )

    # _run_and_store ora passa anche progress=<task dict> per catturare l'output
    # parziale se il turno si interrompe (token finiti) prima della fine.
    assert calls[0][0] == "worker"
    assert calls[0][1] == "fai qa"
    assert calls[0][2] == "claude-sonnet-4-6"
    assert calls[0][3]["agent_id"] == "qa-reviewer"
    assert calls[0][3]["progress"] is delegation._BG_TASKS[task_id]
    assert calls[1][0] == "enqueue"
    assert calls[1][4] == "Agent Result"
    assert calls[1][5]["status"] == "ok"
    assert delegation._BG_TASKS[task_id]["output"] == "Agent Result"


def test_run_librarian_is_best_effort_and_records_failure(monkeypatch, tmp_path):
    task_id = "d-librarian-fail"
    delegation._BG_TASKS[task_id] = {
        "id": task_id,
        "session_id": "s1",
        "agent": "Sonnet",
        "task_type": "semplice",
        "task": "memorizza",
        "status": "ok",
        "output": "Agent Result",
        "started": time.time(),
        "finished": time.time(),
    }

    async def fake_run_worker(*args, **kwargs):
        raise RuntimeError("notion unavailable")

    monkeypatch.setattr(delegation, "_run_worker", fake_run_worker)

    asyncio.run(delegation._run_librarian(task_id, "semplice", "memorizza", "Agent Result", str(tmp_path)))

    assert delegation._BG_TASKS[task_id]["status"] == "ok"
    assert delegation._BG_TASKS[task_id]["librarian_status"] == "errore"
    assert "notion unavailable" in delegation._BG_TASKS[task_id]["librarian_output"]


def test_run_librarian_uses_librarian_agent_memory_mcp_and_skill(monkeypatch, tmp_path):
    calls = []
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"hermes-memory": {"command": "memory"}, "notion": {"command": "notion"}}}),
        encoding="utf-8",
    )
    task_id = "d-librarian-ok"
    delegation._BG_TASKS[task_id] = {
        "id": task_id,
        "session_id": "s1",
        "agent": "Sonnet",
        "task_type": "semplice",
        "task": "memorizza",
        "status": "ok",
        "output": "Agent Result",
        "started": time.time(),
        "finished": time.time(),
    }

    async def fake_run_worker(task, model, workspace, **kwargs):
        calls.append((task, model, workspace, kwargs))
        return "## Memory Update\n- ok"

    monkeypatch.setattr(delegation, "_run_worker", fake_run_worker)

    asyncio.run(delegation._run_librarian(task_id, "semplice", "memorizza", "Agent Result", str(tmp_path)))

    task, model, workspace, kwargs = calls[0]
    assert "Agent Result" in task
    # Il modello del pass e' una scelta di prodotto (oggi Haiku 4.5, economico):
    # il test vincola l'uso della costante, non il suo valore.
    assert model == delegation._LIBRARIAN_MODEL
    assert workspace == str(tmp_path)
    assert kwargs["agent_id"] == "memory-librarian"
    assert set(kwargs["mcp_servers"]) == {"hermes-memory", "notion"}
    assert kwargs["skills"] == ["sync-hermes-brain"]
    assert delegation._BG_TASKS[task_id]["librarian_status"] == "ok"
