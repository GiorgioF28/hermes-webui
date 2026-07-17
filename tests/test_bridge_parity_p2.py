"""Tests for Command Bridge Parity P2+P3 features.

P2-A: Slash commands (/interrupt, /steer, /retry, /goal, /compress, /tools, help)
P2-B: Todo state live + cold-load
P2-C: Tool worklog events
P3-A: History sanitizer
P3-B: Context length aware compact threshold
P3-C: Configurable toolset
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import pytest


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


# ── P2-A static frontend checks ──────────────────────────────────────────

def test_slash_interrupt_and_compress_aliases_in_source():
    source = Path("static/command_bridge.js").read_text(encoding="utf-8")
    assert "cmd === '/interrupt'" in source
    assert "cmd === '/compress'" in source
    assert "cancelPrimeTurn()" in source  # /interrupt calls this


def test_slash_steer_in_source():
    source = Path("static/command_bridge.js").read_text(encoding="utf-8")
    assert "cmd === '/steer'" in source
    assert "_primeStreaming" in source  # checks if a turn is active


def test_slash_retry_reads_history_in_source():
    source = Path("static/command_bridge.js").read_text(encoding="utf-8")
    assert "cmd === '/retry'" in source
    assert "api('/api/bridge/prime/history')" in source  # retry reads from store


def test_slash_goal_in_source():
    source = Path("static/command_bridge.js").read_text(encoding="utf-8")
    assert "cmd === '/goal'" in source
    assert "apiPost('/api/bridge/prime/goal'" in source


def test_slash_tools_in_source():
    source = Path("static/command_bridge.js").read_text(encoding="utf-8")
    assert "cmd === '/tools'" in source
    assert "apiPost('/api/bridge/prime/tools'" in source


def test_unknown_slash_command_shows_help_in_source():
    source = Path("static/command_bridge.js").read_text(encoding="utf-8")
    # Unknown commands must show help and return true (not send to Prime)
    assert "Comandi disponibili" in source


# ── P2-A backend: /goal endpoint ─────────────────────────────────────────

def test_bridge_prime_goal_get_set(monkeypatch, tmp_path):
    from api import prime_session_store, routes

    store = prime_session_store.PrimeSessionStore(tmp_path / "ps.json")
    monkeypatch.setattr(prime_session_store, "_STORE", store)

    # GET: no goal set
    h = _Handler()
    routes._handle_bridge_prime_goal(h, {"action": "get"})
    payload = json.loads(h.wfile.getvalue().decode())
    assert payload["ok"] is True
    assert payload["goal"] is None

    # SET: set a goal
    h2 = _Handler()
    routes._handle_bridge_prime_goal(h2, {"action": "set", "goal": "Finire bridge parity"})
    payload2 = json.loads(h2.wfile.getvalue().decode())
    assert payload2["ok"] is True
    assert payload2["goal"] == "Finire bridge parity"
    assert store.get_settings().get("goal") == "Finire bridge parity"

    # GET again: goal persisted
    h3 = _Handler()
    routes._handle_bridge_prime_goal(h3, {"action": "get"})
    payload3 = json.loads(h3.wfile.getvalue().decode())
    assert payload3["goal"] == "Finire bridge parity"


def test_bridge_prime_goal_set_empty_clears(monkeypatch, tmp_path):
    from api import prime_session_store, routes

    store = prime_session_store.PrimeSessionStore(tmp_path / "ps.json")
    monkeypatch.setattr(prime_session_store, "_STORE", store)

    # First set a goal
    h = _Handler()
    routes._handle_bridge_prime_goal(h, {"action": "set", "goal": "Obiettivo temp"})
    assert json.loads(h.wfile.getvalue().decode())["goal"] == "Obiettivo temp"

    # Set empty string clears goal (stored as None)
    h2 = _Handler()
    routes._handle_bridge_prime_goal(h2, {"action": "set", "goal": ""})
    payload2 = json.loads(h2.wfile.getvalue().decode())
    assert payload2["ok"] is True
    assert payload2["goal"] is None


# ── P2-A backend: /tools endpoint ────────────────────────────────────────

def test_bridge_prime_tools_get_returns_lean_default(monkeypatch, tmp_path):
    from api import prime_session_store, routes

    store = prime_session_store.PrimeSessionStore(tmp_path / "ps.json")
    monkeypatch.setattr(prime_session_store, "_STORE", store)

    h = _Handler()
    routes._handle_bridge_prime_tools(h, {"action": "get"})
    payload = json.loads(h.wfile.getvalue().decode())
    assert payload["ok"] is True
    assert payload["toolset"] == "lean"
    assert "lean" in payload["presets"]
    assert "full" in payload["presets"]
    assert "readonly" in payload["presets"]


def test_bridge_prime_tools_set_full(monkeypatch, tmp_path):
    from api import prime_session_store, routes

    store = prime_session_store.PrimeSessionStore(tmp_path / "ps.json")
    monkeypatch.setattr(prime_session_store, "_STORE", store)

    class _FakeRegistry:
        def close(self, session_id):
            pass

    monkeypatch.setattr(routes, "_get_claude_registry", lambda: _FakeRegistry())

    h = _Handler()
    routes._handle_bridge_prime_tools(h, {"action": "set", "toolset": "full"})
    payload = json.loads(h.wfile.getvalue().decode())
    assert payload["ok"] is True
    assert payload["toolset"] == "full"
    assert store.get_settings().get("toolset") == "full"


def test_bridge_prime_tools_set_invalid_returns_400(monkeypatch, tmp_path):
    from api import prime_session_store, routes

    store = prime_session_store.PrimeSessionStore(tmp_path / "ps.json")
    monkeypatch.setattr(prime_session_store, "_STORE", store)

    h = _Handler()
    routes._handle_bridge_prime_tools(h, {"action": "set", "toolset": "invalid_preset"})
    assert h.status == 400


def test_bridge_prime_tools_set_readonly(monkeypatch, tmp_path):
    from api import prime_session_store, routes

    store = prime_session_store.PrimeSessionStore(tmp_path / "ps.json")
    monkeypatch.setattr(prime_session_store, "_STORE", store)

    class _FakeRegistry:
        def close(self, session_id):
            pass

    monkeypatch.setattr(routes, "_get_claude_registry", lambda: _FakeRegistry())

    h = _Handler()
    routes._handle_bridge_prime_tools(h, {"action": "set", "toolset": "readonly"})
    payload = json.loads(h.wfile.getvalue().decode())
    assert payload["ok"] is True
    assert payload["toolset"] == "readonly"
    assert store.get_settings().get("toolset") == "readonly"


# ── P2-B todo snapshot on PrimeSessionStore ───────────────────────────────

def test_prime_store_todo_snapshot(tmp_path):
    from api.prime_session_store import PrimeSessionStore

    store = PrimeSessionStore(tmp_path / "ps.json")
    assert store.get_todo_snapshot() is None

    snapshot = {
        "todos": [{"id": "1", "content": "Fare X", "status": "pending"}],
        "summary": {"total": 1, "pending": 1},
    }
    store.update_todo_snapshot(snapshot)
    retrieved = store.get_todo_snapshot()
    assert retrieved is not None
    assert retrieved["todos"][0]["content"] == "Fare X"

    # Clear snapshot
    store.update_todo_snapshot(None)
    assert store.get_todo_snapshot() is None


def test_prime_store_todo_snapshot_persists_across_reload(tmp_path):
    from api.prime_session_store import PrimeSessionStore

    path = tmp_path / "ps.json"
    store = PrimeSessionStore(path)
    snapshot = {"todos": [{"id": "t99", "content": "Persistere", "status": "pending"}]}
    store.update_todo_snapshot(snapshot)

    # New store instance reading same file
    store2 = PrimeSessionStore(path)
    retrieved = store2.get_todo_snapshot()
    assert retrieved is not None
    assert retrieved["todos"][0]["id"] == "t99"


# ── P2-B todos endpoint ───────────────────────────────────────────────────

def test_bridge_prime_todos_endpoint_no_snapshot(monkeypatch, tmp_path):
    from api import prime_session_store, routes

    store = prime_session_store.PrimeSessionStore(tmp_path / "ps.json")
    monkeypatch.setattr(prime_session_store, "_STORE", store)

    h = _Handler()
    routes._handle_bridge_prime_todos(h)
    payload = json.loads(h.wfile.getvalue().decode())
    assert payload["ok"] is True
    assert payload["todo_state"] is None


def test_bridge_prime_todos_endpoint_with_snapshot(monkeypatch, tmp_path):
    from api import prime_session_store, routes

    store = prime_session_store.PrimeSessionStore(tmp_path / "ps.json")
    monkeypatch.setattr(prime_session_store, "_STORE", store)

    snap = {"todos": [{"id": "t1", "content": "Test", "status": "in_progress"}]}
    store.update_todo_snapshot(snap)

    h = _Handler()
    routes._handle_bridge_prime_todos(h)
    payload = json.loads(h.wfile.getvalue().decode())
    assert payload["ok"] is True
    assert payload["todo_state"] is not None
    assert payload["todo_state"]["todos"][0]["id"] == "t1"


# ── P2-B frontend todos ───────────────────────────────────────────────────

def test_frontend_todos_panel_in_source():
    source = Path("static/command_bridge.js").read_text(encoding="utf-8")
    assert 'id="cbTodos"' in source
    assert "function renderTodos(snapshot)" in source
    assert "function loadPrimeTodos()" in source
    assert "todo: function (d) { renderTodos(d); }" in source


# ── P2-C tool events on PrimeSessionStore ────────────────────────────────

def test_prime_store_tool_events(tmp_path):
    from api.prime_session_store import PrimeSessionStore

    store = PrimeSessionStore(tmp_path / "ps.json")
    sid = store.begin_turn("test")
    store.append_tool_event(sid, "Read", "file.py -> 50 lines")
    store.append_tool_event(sid, "Grep", "found 3 matches")

    events = store.get_tool_events(sid)
    assert len(events) == 2
    assert events[0]["tool"] == "Read"
    assert events[1]["tool"] == "Grep"
    assert events[0]["summary"] == "file.py -> 50 lines"

    # Filter by different stream_id returns empty list
    other_events = store.get_tool_events("other-stream-id")
    assert other_events == []

    # Without filter returns all tool events
    all_events = store.get_tool_events()
    assert len(all_events) == 2


def test_prime_store_tool_events_have_correct_event_type(tmp_path):
    from api.prime_session_store import PrimeSessionStore

    store = PrimeSessionStore(tmp_path / "ps.json")
    sid = store.begin_turn("tool test")
    store.append_tool_event(sid, "Write", "saved changes.py")

    events = store.get_tool_events(sid)
    assert len(events) == 1
    assert events[0]["event"] == "tool_call"
    assert events[0]["stream_id"] == sid


def test_prime_store_tool_summary_truncated_at_500_chars(tmp_path):
    from api.prime_session_store import PrimeSessionStore

    store = PrimeSessionStore(tmp_path / "ps.json")
    sid = store.begin_turn("truncation test")
    long_summary = "x" * 1000
    store.append_tool_event(sid, "Read", long_summary)

    events = store.get_tool_events(sid)
    assert len(events) == 1
    assert len(events[0]["summary"]) <= 500


# ── P2-C frontend tool rendering ─────────────────────────────────────────

def test_frontend_tool_card_in_source():
    source = Path("static/command_bridge.js").read_text(encoding="utf-8")
    assert "function renderToolCard(toolName" in source
    assert "tool: function (d)" in source


# ── P3-A sanitizer ───────────────────────────────────────────────────────

def test_sanitizer_drops_empty_messages():
    from api.prime_sanitizer import sanitize_prime_history_for_model

    msgs = [
        {"role": "user", "content": "Ciao"},
        {"role": "assistant", "content": ""},
        {"role": "assistant", "content": "  "},
    ]
    result = sanitize_prime_history_for_model(msgs)
    assert len(result) == 1
    assert result[0]["role"] == "user"


def test_sanitizer_drops_interrupted_empty_messages():
    from api.prime_sanitizer import sanitize_prime_history_for_model

    msgs = [
        {"role": "user", "content": "Testa"},
        {"role": "assistant", "content": "", "interrupted": True, "cancelled": True},
        {"role": "assistant", "content": "risposta valida"},
    ]
    result = sanitize_prime_history_for_model(msgs)
    assert len(result) == 2
    assert result[1]["content"] == "risposta valida"


def test_sanitizer_truncates_long_content():
    from api.prime_sanitizer import sanitize_prime_history_for_model, _MAX_ASSISTANT_CHARS

    long_reply = "x" * (_MAX_ASSISTANT_CHARS + 1000)
    msgs = [{"role": "assistant", "content": long_reply}]
    result = sanitize_prime_history_for_model(msgs)
    assert len(result) == 1
    # Content should be truncated (within threshold + suffix length)
    assert len(result[0]["content"]) <= _MAX_ASSISTANT_CHARS + 200
    assert "troncato" in result[0]["content"]


def test_sanitizer_keeps_user_with_attachments():
    from api.prime_sanitizer import sanitize_prime_history_for_model

    msgs = [
        {"role": "user", "content": "", "attachments": [{"name": "foto.png", "path": "/x"}]},
    ]
    result = sanitize_prime_history_for_model(msgs)
    assert len(result) == 1


def test_sanitizer_strips_internal_metadata():
    from api.prime_sanitizer import sanitize_prime_history_for_model

    msgs = [
        {"role": "user", "content": "msg", "created_at": 1234567, "usage": {"input_tokens": 10}},
        {"role": "assistant", "content": "reply", "interrupted": True, "error": "timeout"},
    ]
    result = sanitize_prime_history_for_model(msgs)
    assert len(result) == 2
    assert "created_at" not in result[0]
    assert "usage" not in result[0]
    assert "interrupted" not in result[1]
    assert "error" not in result[1]


def test_sanitizer_output_has_only_role_and_content():
    from api.prime_sanitizer import sanitize_prime_history_for_model

    msgs = [
        {
            "role": "user",
            "content": "ciao",
            "created_at": 123,
            "attachments": [],
            "cancelled": False,
            "recovered": False,
        },
    ]
    result = sanitize_prime_history_for_model(msgs)
    assert len(result) == 1
    assert set(result[0].keys()) == {"role", "content"}


def test_sanitizer_drops_non_dict_entries():
    from api.prime_sanitizer import sanitize_prime_history_for_model

    msgs = [
        "not a dict",
        None,
        {"role": "user", "content": "valido"},
        42,
    ]
    result = sanitize_prime_history_for_model(msgs)
    assert len(result) == 1
    assert result[0]["content"] == "valido"


def test_sanitizer_empty_input():
    from api.prime_sanitizer import sanitize_prime_history_for_model

    assert sanitize_prime_history_for_model([]) == []
    assert sanitize_prime_history_for_model(None) == []


# ── P3-B context length for compact ──────────────────────────────────────

def test_compact_threshold_for_model_fallback():
    from api.prime_auto_compact import compact_threshold_for_model

    # Without model state, should return the base threshold (non-negative int)
    result = compact_threshold_for_model(None)
    assert isinstance(result, int)
    assert result >= 0


def test_compact_threshold_for_model_scales_with_context():
    from api import prime_auto_compact

    # Mock _prime_model_context_length to return a known value
    original = prime_auto_compact._prime_model_context_length
    try:
        prime_auto_compact._prime_model_context_length = lambda ms: 200_000
        result = prime_auto_compact.compact_threshold_for_model({"model": "claude-sonnet-4-6"})
        # 30% of 200k = 60k, clamped between 30k and 150k
        assert 30_000 <= result <= 150_000
    finally:
        prime_auto_compact._prime_model_context_length = original


def test_compact_threshold_for_model_clamps_small_context():
    from api import prime_auto_compact

    original = prime_auto_compact._prime_model_context_length
    try:
        # Very small context: 30% = 6k, should clamp to 30k min
        prime_auto_compact._prime_model_context_length = lambda ms: 20_000
        result = prime_auto_compact.compact_threshold_for_model({"model": "small-model"})
        assert result == 30_000
    finally:
        prime_auto_compact._prime_model_context_length = original


def test_compact_threshold_for_model_clamps_large_context():
    from api import prime_auto_compact

    original = prime_auto_compact._prime_model_context_length
    try:
        # Very large context: 30% of 1M = 300k, should clamp to 150k max
        prime_auto_compact._prime_model_context_length = lambda ms: 1_000_000
        result = prime_auto_compact.compact_threshold_for_model({"model": "big-model"})
        assert result == 150_000
    finally:
        prime_auto_compact._prime_model_context_length = original


def test_compact_threshold_falls_back_when_lookup_fails():
    from api import prime_auto_compact

    original = prime_auto_compact._prime_model_context_length
    try:
        def raise_exc(ms):
            raise RuntimeError("lookup failed")
        prime_auto_compact._prime_model_context_length = raise_exc
        result = prime_auto_compact.compact_threshold_for_model({"model": "bad-model"})
        # Fallback to env-based threshold
        base = prime_auto_compact.compact_threshold_tokens()
        assert result == base
    finally:
        prime_auto_compact._prime_model_context_length = original


# ── P3-C toolset ─────────────────────────────────────────────────────────

def test_resolve_prime_toolset_default():
    from api.prime_lean_preset import resolve_prime_toolset

    assert resolve_prime_toolset({}) == "lean"
    assert resolve_prime_toolset(None) == "lean"
    assert resolve_prime_toolset({"toolset": "invalid"}) == "lean"


def test_resolve_prime_toolset_from_settings():
    from api.prime_lean_preset import resolve_prime_toolset

    assert resolve_prime_toolset({"toolset": "full"}) == "full"
    assert resolve_prime_toolset({"toolset": "readonly"}) == "readonly"
    assert resolve_prime_toolset({"toolset": "lean"}) == "lean"


def test_resolve_prime_toolset_case_insensitive():
    from api.prime_lean_preset import resolve_prime_toolset

    assert resolve_prime_toolset({"toolset": "FULL"}) == "full"
    assert resolve_prime_toolset({"toolset": "Readonly"}) == "readonly"


def test_build_lean_prompt_with_toolset_notes_full():
    from api.prime_lean_preset import build_lean_system_prompt_with_toolset

    result = build_lean_system_prompt_with_toolset(["Base prompt"], toolset="full")
    result_str = result if isinstance(result, str) else str(result)
    assert "FULL" in result_str or "full" in result_str.lower()


def test_build_lean_prompt_with_toolset_notes_readonly():
    from api.prime_lean_preset import build_lean_system_prompt_with_toolset

    result = build_lean_system_prompt_with_toolset(["Base prompt"], toolset="readonly")
    result_str = result if isinstance(result, str) else str(result)
    assert "READONLY" in result_str or "readonly" in result_str.lower()


def test_build_lean_prompt_with_toolset_lean_no_extra_note():
    from api.prime_lean_preset import build_lean_system_prompt_with_toolset

    result_lean = build_lean_system_prompt_with_toolset(["Base prompt"], toolset="lean")
    result_full = build_lean_system_prompt_with_toolset(["Base prompt"], toolset="full")
    # Full should be longer (has extra note) or at least different from lean
    assert result_lean != result_full


def test_valid_toolsets_constant():
    from api.prime_lean_preset import VALID_TOOLSETS, TOOLSET_DEFAULT

    assert "lean" in VALID_TOOLSETS
    assert "full" in VALID_TOOLSETS
    assert "readonly" in VALID_TOOLSETS
    assert TOOLSET_DEFAULT == "lean"
