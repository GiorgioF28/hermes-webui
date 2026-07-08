import io
import json
import pathlib
import sys
from types import SimpleNamespace

REPO_ROOT = pathlib.Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO_ROOT))
PANELS_JS = (REPO_ROOT / "static" / "panels.js").read_text(encoding="utf-8")
UI_JS = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
MESSAGES_JS = (REPO_ROOT / "static" / "messages.js").read_text(encoding="utf-8")
INDEX_HTML = (REPO_ROOT / "static" / "index.html").read_text(encoding="utf-8")


class _FakeHandler:
    def __init__(self):
        self.status = None
        self.sent_headers = []
        self.body = bytearray()
        self.wfile = self
        self.headers = {}

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.sent_headers.append((name, value))

    def end_headers(self):
        pass

    def write(self, data):
        self.body.extend(data)

    def json_body(self):
        return json.loads(bytes(self.body).decode("utf-8"))


def _jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _seed_env(monkeypatch, tmp_path, now=1783260000):
    import api.token_insights as token_insights

    claude = tmp_path / "claude_projects"
    codex = tmp_path / "codex_sessions"
    delegations = tmp_path / "delegations.jsonl"
    cache = tmp_path / "cache.json"
    monkeypatch.setenv("CLAUDE_PROJECTS_DIR", str(claude))
    monkeypatch.setenv("CODEX_SESSIONS_DIR", str(codex))
    monkeypatch.setenv("DELEGATIONS_FILE", str(delegations))
    monkeypatch.setenv("TOKEN_INSIGHTS_CACHE", str(cache))
    monkeypatch.delenv("CLAUDE_USAGE_ENDPOINT", raising=False)
    monkeypatch.delenv("CLAUDE_USAGE_TOKEN", raising=False)
    monkeypatch.setattr(token_insights.time, "time", lambda: now)
    token_insights._LIMITS_CACHE["payload"] = None
    token_insights._LIMITS_CACHE["expires_at"] = 0
    return claude, codex, delegations, cache


def test_collect_parses_claude_codex_and_correlates_delegations(monkeypatch, tmp_path):
    import api.token_insights as token_insights

    claude, codex, delegations, _cache = _seed_env(monkeypatch, tmp_path)
    _jsonl(claude / "C--Users-giorg-Documents-Hermes-setup" / "claude-session.jsonl", [
        {
            "type": "assistant",
            "timestamp": "2026-07-05T10:00:00Z",
            "sessionId": "claude-session",
            "message": {
                "role": "assistant",
                "model": "claude-opus-4-20250514",
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 200,
                    "cache_read_input_tokens": 300,
                    "cache_creation_input_tokens": 40,
                },
            },
        }
    ])
    _jsonl(codex / "2026" / "07" / "05" / "rollout-2026-07-05-a.jsonl", [
        {"timestamp": "2026-07-05T09:00:00Z", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11}}}},
        {
            "timestamp": "2026-07-05T10:01:00Z",
            "payload": {
                "type": "token_count",
                "info": {"total_token_usage": {"input_tokens": 2000, "cached_input_tokens": 1500, "output_tokens": 300, "reasoning_output_tokens": 50, "total_tokens": 2300}},
                "rate_limits": {
                    "limit_id": "codex",
                    "plan_type": "plus",
                    "primary": {"used_percent": 16.0, "window_minutes": 300, "resets_at": 1783282052},
                    "secondary": {"used_percent": 10.0, "window_minutes": 10080, "resets_at": 1783410961},
                },
            },
        },
    ])
    _jsonl(delegations, [
        {"id": "d1", "agent": "programmatore", "started": "2026-07-05T09:58:30Z", "finished": "2026-07-05T10:03:00Z"}
    ])

    data = token_insights.collect(7)

    assert data["totals"]["claude"]["input"] == 1000
    assert data["totals"]["claude"]["output"] == 200
    assert data["totals"]["claude"]["cache_read"] == 300
    assert data["totals"]["codex"]["input"] == 2000
    assert data["totals"]["codex"]["cached"] == 1500
    by_agent = {(r["runtime"], r["agent"]): r for r in data["by_agent"]}
    assert by_agent[("claude", "programmatore")]["tokens"] == 1540
    assert by_agent[("codex", "programmatore")]["tokens"] == 2300
    assert {r["runtime"] for r in data["by_model"]} == {"claude", "codex"}
    assert data["daily"][-1]["claude"] == 1540
    assert data["daily"][-1]["codex"] == 2300

    limits = token_insights.limits()
    assert limits["codex"]["primary_used_percent"] == 16.0
    assert limits["codex"]["secondary_used_percent"] == 10.0
    assert limits["codex"]["plan"] == "plus"
    assert limits["claude"] is None


def test_collect_cache_reuses_unchanged_file_aggregate(monkeypatch, tmp_path):
    import api.token_insights as token_insights

    claude, _codex, delegations, _cache = _seed_env(monkeypatch, tmp_path)
    delegations.write_text("", encoding="utf-8")
    _jsonl(claude / "slug" / "s.jsonl", [
        {"type": "assistant", "timestamp": "2026-07-05T10:00:00Z", "message": {"role": "assistant", "model": "claude-sonnet", "usage": {"input_tokens": 9, "output_tokens": 1}}}
    ])
    first = token_insights.collect(7)
    assert first["totals"]["claude"]["input"] == 9

    monkeypatch.setattr(token_insights, "_parse_claude_file", lambda path: (_ for _ in ()).throw(AssertionError("cache miss")))
    second = token_insights.collect(7)
    assert second["totals"]["claude"]["input"] == 9


def test_token_insights_endpoint_clamps_days_and_empty_sources(monkeypatch, tmp_path):
    import api.routes as routes
    import api.token_insights as token_insights

    _seed_env(monkeypatch, tmp_path)
    handler = _FakeHandler()
    parsed = SimpleNamespace(path="/api/insights/tokens", query="days=999")
    routes._handle_token_insights(handler, parsed)
    assert handler.status == 200
    data = handler.json_body()
    assert data["period_days"] == 365
    assert data["totals"]["claude"]["tokens"] == 0
    assert data["top_sessions"] == []

    handler = _FakeHandler()
    routes._handle_usage_limits(handler, SimpleNamespace(path="/api/usage/limits", query=""))
    assert handler.status == 200
    assert handler.json_body() == {"codex": None, "claude": None}


def test_context_breakdown_parses_turns_tools_drag_and_mcp(monkeypatch, tmp_path):
    import api.token_insights as token_insights

    claude, _codex, _delegations, _cache = _seed_env(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("HERMES_WORKSPACE_DIR", str(workspace))
    (workspace / ".mcp.json").write_text(json.dumps({
        "mcpServers": {
            "memory": {
                "command": "node",
                "args": ["server.js"],
                "tools": [{"name": "remember", "input_schema": {"type": "object"}}],
            }
        }
    }), encoding="utf-8")
    _jsonl(claude / "slug" / "s1.jsonl", [
        {"type": "assistant", "timestamp": "2026-07-05T10:00:00Z", "sessionId": "s1", "message": {"role": "assistant", "model": "claude-sonnet", "usage": {"input_tokens": 100, "output_tokens": 10, "cache_creation_input_tokens": 1000}}},
        {"type": "assistant", "timestamp": "2026-07-05T10:01:00Z", "sessionId": "s1", "message": {"role": "assistant", "model": "claude-sonnet", "content": [{"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "x.py"}}], "usage": {"input_tokens": 5, "output_tokens": 1, "cache_read_input_tokens": 1000}}},
        {"type": "user", "timestamp": "2026-07-05T10:02:00Z", "sessionId": "s1", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "large file"}]}},
        {"type": "assistant", "timestamp": "2026-07-05T10:03:00Z", "sessionId": "s1", "message": {"role": "assistant", "model": "claude-sonnet", "usage": {"input_tokens": 20, "output_tokens": 5, "cache_read_input_tokens": 1500, "cache_creation_input_tokens": 900}}},
        {"type": "assistant", "timestamp": "2026-07-05T10:04:00Z", "sessionId": "s1", "message": {"role": "assistant", "model": "claude-sonnet", "usage": {"input_tokens": 30, "output_tokens": 15, "cache_read_input_tokens": 2000, "cache_creation_input_tokens": 100}}},
    ])
    _jsonl(claude / "slug" / "s2.jsonl", [
        {"type": "assistant", "timestamp": "2026-07-05T09:00:00Z", "sessionId": "s2", "message": {"role": "assistant", "model": "claude-sonnet", "usage": {"input_tokens": 50, "output_tokens": 7, "cache_creation_input_tokens": 500}}}
    ])

    data = token_insights.context_breakdown(7)

    assert data["period_days"] == 7
    assert data["scope"] == "claude_only"
    assert data["initial_context"]["avg"] == 825
    assert data["initial_context"]["min"] == 550
    assert data["initial_context"]["max"] == 1100
    assert data["per_turn"]["input_median"] == 30
    assert data["per_turn"]["output_avg"] == 8
    assert data["mcp_estimate"]["total_est"] > 0
    assert data["mcp_estimate"]["servers"][0]["name"] == "memory"
    read_heavy = [r for r in data["heavy_turns"] if r["tool"] == "Read"]
    assert read_heavy and read_heavy[0]["cache_creation"] == 900 and read_heavy[0]["turn"] == 3
    by_tool = {r["tool"]: r["tokens"] for r in data["by_tool_cache_creation"]}
    assert by_tool["Read"] == 900
    drag = {r["session"]: r for r in data["drag"]["sessions"]}
    assert drag["s1"]["cache_read"] == 4500
    assert drag["s1"]["est_saving_if_split"] == 1300
    assert drag["s1"]["curve"] == [0, 1000, 1500, 2000]
    assert data["drag"]["total_cache_read"] == 4500


def test_token_breakdown_endpoint_clamps_days_and_empty_sources(monkeypatch, tmp_path):
    import api.routes as routes

    _seed_env(monkeypatch, tmp_path)
    monkeypatch.setenv("PRIME_AUTO_COMPACT_EVENTS", str(tmp_path / "prime-auto.jsonl"))
    handler = _FakeHandler()
    parsed = SimpleNamespace(path="/api/insights/tokens/breakdown", query="days=999")
    routes._handle_token_breakdown(handler, parsed)
    assert handler.status == 200
    data = handler.json_body()
    assert data["period_days"] == 365
    assert data["initial_context"]["per_session"] == []
    assert data["heavy_turns"] == []
    assert data["drag"]["total_cache_read"] == 0
    pac = data["prime_auto_compact"]
    assert pac["events"] == []
    assert pac["count"] == 0
    assert pac["last"] is None
    # Cantiere 1: campo savings aggiunto
    assert "savings" in pac
    assert pac["savings"]["total_compacts"] == 0


def test_token_breakdown_includes_prime_auto_compact_events(monkeypatch, tmp_path):
    import api.token_insights as token_insights

    _seed_env(monkeypatch, tmp_path)
    event_log = tmp_path / "prime-auto.jsonl"
    monkeypatch.setenv("PRIME_AUTO_COMPACT_EVENTS", str(event_log))
    event_log.write_text(
        json.dumps({
            "event": "prime_auto_compact",
            "ts": 1783260000,
            "session_id": "hermes-prime",
            "before_tokens": 70000,
            "after_tokens": 18000,
            "status": "ok",
        }) + "\n",
        encoding="utf-8",
    )

    data = token_insights.context_breakdown(7)

    assert data["prime_auto_compact"]["count"] == 1
    assert data["prime_auto_compact"]["last"]["before_tokens"] == 70000
    assert data["prime_auto_compact"]["last"]["after_tokens"] == 18000


def test_token_breakdown_invalidates_old_cache_version(monkeypatch, tmp_path):
    import api.token_insights as token_insights

    claude, _codex, _delegations, cache = _seed_env(monkeypatch, tmp_path)
    _jsonl(claude / "slug" / "fresh.jsonl", [
        {"type": "assistant", "timestamp": "2026-07-05T10:00:00Z", "sessionId": "fresh", "message": {"role": "assistant", "model": "claude-sonnet", "usage": {"input_tokens": 11, "output_tokens": 2, "cache_creation_input_tokens": 33}}}
    ])
    cache.write_text(json.dumps({
        "version": 1,
        "files": {
            str(claude / "slug" / "fresh.jsonl"): {
                "mtime": (claude / "slug" / "fresh.jsonl").stat().st_mtime,
                "size": (claude / "slug" / "fresh.jsonl").stat().st_size,
                "aggregate": {"runtime": "claude", "sessions": [], "turns": []},
            }
        },
    }), encoding="utf-8")

    data = token_insights.context_breakdown(7)

    assert data["initial_context"]["avg"] == 44
    assert json.loads(cache.read_text(encoding="utf-8"))["version"] == token_insights.CACHE_SCHEMA_VERSION


def test_token_insights_frontend_wires_section_quota_and_live_counter():
    assert "/api/insights/tokens?days=${period}" in PANELS_JS
    assert "/api/insights/tokens/breakdown?days=${period}" in PANELS_JS
    assert "insights_context_breakdown_title" in PANELS_JS
    assert "insights_token_usage_title" in PANELS_JS
    assert "tokenQuotaPill" in INDEX_HTML
    assert "/api/usage/limits" in UI_JS
    assert "TOKEN_QUOTA_PRIMARY_WARN" in UI_JS
    assert "liveTokenCounter" in INDEX_HTML
    assert "_beginLiveTokenCounter" in MESSAGES_JS
    assert "_syncLiveTokenCounter" in MESSAGES_JS
