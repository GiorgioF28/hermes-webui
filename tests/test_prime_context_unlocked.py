"""Profilo Prime unlocked: contesto completo + retrieval selettivo."""
from __future__ import annotations

import asyncio
from pathlib import Path

from api import memory_retrieval, routes
from api.prime_lean_preset import prime_context_profile


def _memory_fixture(tmp_path: Path) -> Path:
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    (mem_dir / "MEMORY.md").write_text(
        "\n".join([
            "# Memory Index",
            "- [VisionBuilts Ebook Platform](visionbuilts.md) — pipeline ebook, n8n e PDF",
            "- [Concorso INPS](concorso-inps.md) — piano quiz e materie del concorso",
            "- [Regole operative sempre attive](regole-operative.md) — vincoli permanenti",
        ]),
        encoding="utf-8",
    )
    (mem_dir / "visionbuilts.md").write_text(
        "---\naliases: [ebook platform, VisionBuilts]\ntags: [ebook, n8n, pdf]\n"
        "scope: visionbuilts\n---\nPipeline editor e generazione ricettari con Gotenberg.",
        encoding="utf-8",
    )
    (mem_dir / "concorso-inps.md").write_text(
        "---\naliases: [assistente informatico]\ntags: [inps, concorso, quiz]\n---\n"
        "Studio delle sedici materie e simulazioni quiz.",
        encoding="utf-8",
    )
    (mem_dir / "regole-operative.md").write_text(
        "---\ntags: [always-active, regole-operative]\nalways_active: true\n---\n"
        "Non salvare segreti. Ogni task deve avere una prossima azione.",
        encoding="utf-8",
    )
    return mem_dir


def test_ebook_selects_visionbuilts_not_inps(tmp_path):
    selected = memory_retrieval.select_memories_unlocked(
        "Sistema il formatter n8n dell'ebook e verifica il PDF",
        _memory_fixture(tmp_path),
        top_k=5,
        max_chars=4000,
    )
    titles = {entry["title"] for entry in selected}
    assert "VisionBuilts Ebook Platform" in titles
    assert "Concorso INPS" not in titles


def test_always_active_rules_are_included_without_lexical_match(tmp_path):
    selected = memory_retrieval.select_memories_unlocked(
        "previsioni meteo per domani",
        _memory_fixture(tmp_path),
        top_k=0,
        max_chars=4000,
    )
    assert [entry["title"] for entry in selected] == ["Regole operative sempre attive"]
    assert selected[0]["always_active"] is True


def test_selected_notes_and_scores_are_logged(tmp_path, caplog):
    caplog.set_level("INFO", logger="api.memory_retrieval")
    memory_retrieval.select_memories_unlocked(
        "ebook n8n formatter",
        _memory_fixture(tmp_path),
        top_k=3,
        max_chars=4000,
    )
    assert "visionbuilts.md" in caplog.text
    assert "score" in caplog.text


def test_unlocked_detail_respects_character_budget(tmp_path):
    detail = memory_retrieval.build_unlocked_memory_detail(
        "ebook n8n pdf",
        _memory_fixture(tmp_path),
        top_k=5,
        max_chars=180,
    )
    assert len(detail) <= 180
    assert "Regole operative" in detail


def test_profile_defaults_to_lean_and_explicit_lean_is_identical(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_PRIME_CONTEXT_PROFILE", raising=False)
    monkeypatch.setenv("HERMES_PRIME_USE_LEAN_PRESET", "1")
    monkeypatch.setattr(memory_retrieval, "find_prime_memory_dir", lambda: None)
    default_prompt = routes._hermes_prime_system_prompt(str(tmp_path))
    monkeypatch.setenv("HERMES_PRIME_CONTEXT_PROFILE", "lean")
    explicit_prompt = routes._hermes_prime_system_prompt(str(tmp_path))
    assert prime_context_profile() == "lean"
    assert explicit_prompt == default_prompt


def test_lean_profile_keeps_existing_six_project_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_PRIME_CONTEXT_PROFILE", "lean")
    monkeypatch.setenv("HERMES_PRIME_USE_LEAN_PRESET", "1")
    monkeypatch.setattr(memory_retrieval, "find_prime_memory_dir", lambda: None)
    projects = tmp_path / "projects"
    projects.mkdir()
    rows = ["project_id,name,status,business_goal,next_action"]
    rows.extend(f"p{i},Lean Project {i},active,Goal {i},Next {i}" for i in range(8))
    (projects / "project-inventory.csv").write_text("\n".join(rows), encoding="utf-8")
    prompt = routes._hermes_prime_system_prompt(str(tmp_path))
    assert "Lean Project 5" in prompt
    assert "Lean Project 6" not in prompt


def test_unlocked_system_prefix_is_full_and_excludes_dynamic_brief(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_PRIME_CONTEXT_PROFILE", "unlocked")
    monkeypatch.setattr(routes, "_hermes_prime_persona_text", lambda: "PERSONA_COMPLETA")
    monkeypatch.setattr(memory_retrieval, "find_prime_memory_dir", lambda: None)
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "project-inventory.csv").write_text(
        "project_id,name,status,business_goal,next_action\n"
        "p1,VisionBuilts,active,Vendere ebook,Render PDF\n",
        encoding="utf-8",
    )
    prompt = routes._hermes_prime_system_prompt(str(tmp_path))
    assert prompt.startswith("PERSONA_COMPLETA")
    assert "mcp__hermes__ask_user" in prompt
    assert "VisionBuilts" not in prompt


def test_unlocked_turn_keeps_user_first_then_full_brief_and_detail(monkeypatch, tmp_path):
    captured = []

    class Client:
        _hermes_sdk_session_id = "aaaaaaaa-0000-4000-8000-bbbbbbbbbbbb"

        async def query(self, message, session_id="default"):
            captured.append(message)

        async def receive_response(self):
            result = type("ResultMessage", (), {"result": "ok", "event": None, "usage": {}})()
            yield result

    class Future:
        def result(self, timeout=None):
            return asyncio.run(self.drive(Client()))
        def cancel(self):
            return False
        def add_done_callback(self, callback):
            callback(self)

    class Registry:
        def get(self, session_id):
            return None
        def get_or_create(self, session_id, **kwargs):
            return None
        def submit_turn(self, session_id, drive):
            future = Future()
            future.drive = drive
            return future
        def close(self, session_id):
            return None

    projects = tmp_path / "projects"
    projects.mkdir()
    rows = ["project_id,name,status,business_goal,next_action"]
    rows.extend(f"p{i},Progetto {i},active,Goal {i},Next {i}" for i in range(8))
    (projects / "project-inventory.csv").write_text("\n".join(rows), encoding="utf-8")

    monkeypatch.setenv("HERMES_PRIME_CONTEXT_PROFILE", "unlocked")
    monkeypatch.setattr(routes, "_get_claude_registry", lambda: Registry())
    monkeypatch.setattr(routes, "_hermes_prime_system_prompt", lambda workspace: "STATIC")
    monkeypatch.setattr(routes, "_claude_attachment_note", lambda *args, **kwargs: "")
    monkeypatch.setattr("api.prime_delegation.get_background_tasks", lambda: [])
    monkeypatch.setattr("api.bridge_attachments.prime_turn_started", lambda: 1)
    monkeypatch.setattr("api.bridge_attachments.normalize_prime_attachments", lambda value, bridge=None: value)
    monkeypatch.setattr("api.bridge_attachments.record_prime_images", lambda *args, **kwargs: None)
    monkeypatch.setattr(memory_retrieval, "build_prime_unlocked_memory_detail", lambda *args, **kwargs: "DETAIL")

    routes._hermes_prime_reply_claude(
        "crea il mio ebook",
        tmp_path,
        on_token=lambda token: None,
        on_status=lambda status: None,
    )
    assert captured
    prompt = captured[0]
    assert prompt.startswith("crea il mio ebook")
    assert prompt.index("crea il mio ebook") < prompt.index("## Progetti in corso") < prompt.index("DETAIL")
    assert "Progetto 7" in prompt
    assert "[brief troncato]" not in prompt
