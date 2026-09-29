import asyncio
import json
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from api import codex_prime as cp, prime_delegation as pd, memory_retrieval as mr, codex_profiles, config


def test_worker_cli_pins_luna_and_keeps_full_stdin(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(pd, "_resolve_codex_executable", lambda: "codex.cmd")
    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=0, stdout="done", stderr="")
    monkeypatch.setattr(subprocess, "run", run)
    task = "task & (with metacharacters)\n" + "x" * 30000
    pd._codex_exec_blocking(task, str(tmp_path))
    cmd, args = calls[0]
    assert cmd[cmd.index("--model") + 1] == "gpt-5.6-luna"
    assert 'model_reasoning_effort="medium"' in cmd
    assert args["input"] == task and cmd[-1] == "-"


def test_chief_cli_pins_astra_high(monkeypatch, tmp_path):
    calls = []
    runtime = SimpleNamespace(url="http://127.0.0.1:1/mcp", register=lambda *a: "test", revoke=lambda *a: None)
    monkeypatch.setattr(cp, "get_runtime", lambda: runtime)
    monkeypatch.setattr(cp, "build_tools", lambda *a: [])
    monkeypatch.setattr(pd, "_resolve_codex_executable", lambda: "codex.cmd")
    def popen(cmd, **kwargs):
        calls.append(cmd)
        raise RuntimeError("process intercepted")
    monkeypatch.setattr(subprocess, "Popen", popen)
    with pytest.raises(RuntimeError, match="intercepted"):
        cp.run_prime("test", tmp_path, session_id="s1", cancel=threading.Event())
    assert calls[0][calls[0].index("--model") + 1] == "gpt-6-astra"
    assert 'model_reasoning_effort="high"' in calls[0]


def test_canonical_memory_links_and_traversal(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    vault = workspace / "obsidian-vault"
    vault.mkdir(parents=True)
    mem = tmp_path / "memory"
    mem.mkdir()
    (vault / "known note.md").write_text("scope: hermes\nSOURCE", encoding="utf-8")
    (tmp_path / "secret.md").write_text("PRIVATE", encoding="utf-8")
    monkeypatch.setattr(config, "DEFAULT_WORKSPACE", workspace)
    target = "../obsidian-vault/known%20note.md"
    assert "SOURCE" in mr.load_memory_body(mem, target)
    assert mr.parse_note_metadata_fast(mem, target)["scope"] == "hermes"
    assert mr.load_memory_body(mem, "../secret.md") == ""
    assert mr.load_memory_body(mem, str(tmp_path / "secret.md")) == ""
    assert mr.load_memory_body(mem, "https://example.com/note.md") == ""


def test_routing_packet_bounded_and_no_body_dump(monkeypatch, tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir()
    lines = []
    for i in range(10):
        lines.append(f"- [Hermes routing {i}](note{i}.md) — delegation routing")
        (mem / f"note{i}.md").write_text("PRIVATE_BODY_MARKER" * 1000, encoding="utf-8")
    (mem / "MEMORY.md").write_text("\n".join(lines), encoding="utf-8")
    monkeypatch.setenv("HERMES_PRIME_MEMORY_DIR", str(mem))
    packet = mr.build_worker_memory_routes("Hermes delegation routing", tmp_path)
    assert len(packet) <= 1600 and packet.count("\n- ") == 4
    assert "PRIVATE_BODY_MARKER" not in packet and "MEMORY.md" in packet
    context = mr.build_memory_context("Hermes", mem, include_index=False)
    assert "## Memoria (indice)" not in context
    assert "dettaglio rilevante" in context


def test_memory_discovery_prefers_workspace_and_refuses_ambiguity(monkeypatch, tmp_path):
    import re
    monkeypatch.delenv("HERMES_PRIME_MEMORY_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(config, "DEFAULT_WORKSPACE", workspace)
    slug = re.sub(r"[^a-z0-9]", "-", str(workspace.resolve()).casefold())
    for name in ["aaa-unrelated", slug]:
        mem = tmp_path / ".claude/projects" / name / "memory"
        mem.mkdir(parents=True)
        (mem / "MEMORY.md").write_text("index", encoding="utf-8")
    assert mr.find_prime_memory_dir().parent.name == slug
    assert mr.find_prime_memory_dir(tmp_path / "unknown") is None


def test_team_status_summary_then_lossless_result_pages():
    output = "x" * 15001
    task = {"id": "d1", "output": output, "diagnostic_log": "LOG" * 20000, "task": "PROMPT" * 20000}
    summary = cp.compact_team_status([task])[0]
    assert "output" not in summary and "diagnostic_log" not in summary and "task" not in summary
    assert summary["output_chars"] == len(output)
    pages = [cp.compact_team_status([task], task_id="d1", offset=i)[0] for i in [0,6000,12000]]
    assert "".join(page["output"] for page in pages) == output
    assert pages[-1]["next_offset"] is None


@pytest.mark.asyncio
async def test_auto_librarian_defaults_luna_and_records_actual_model(monkeypatch, tmp_path):
    from api import agent_models
    monkeypatch.setattr(agent_models, "get_overrides", lambda: {})
    monkeypatch.setattr(pd, "_BG_TASKS", {"test": {}})
    monkeypatch.setattr(pd, "_persist_bg_task", lambda *a: None)
    async def codex(prompt, workspace, **kwargs):
        assert kwargs["agent_id"] == "memory-librarian"
        return "Memory Update"
    monkeypatch.setattr(pd, "_run_codex_worker_with_fallback", codex)
    await pd._run_librarian_serial("test", "memoria", "task", "result", str(tmp_path))
    assert pd._BG_TASKS["test"]["librarian_model"] == "gpt-5.6-luna"
    assert pd._BG_TASKS["test"]["librarian_provider"] == "codex"


def test_model_metadata_roundtrip():
    from api.delegation_store import bg_task_to_canonical
    record = {"id":"d1", "agent":"social", "task":"x", "status":"ok", "runtime":"codex",
              "runtime_model":"gpt-5.6-luna", "reasoning_effort":"medium", "librarian_model":"gpt-5.6-luna", "librarian_provider":"codex"}
    canonical = bg_task_to_canonical(record)
    assert canonical["runtime"]["model"] == "gpt-5.6-luna"
    restored = pd._canonical_to_legacy(canonical)
    assert restored["runtime_model"] == "gpt-5.6-luna" and restored["reasoning_effort"] == "medium"
    assert restored["librarian_provider"] == "codex"
