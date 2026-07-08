"""Token usage collector for Command Bridge insights.

Reads verified local usage sources without mutating them:
- Claude Code transcripts under ``~/.claude/projects``
- Codex rollout sessions under ``~/.codex/sessions``
- Hermes delegation records in ``tasks/delegations.jsonl``

Only the collector cache is written.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from api.paths import _platform_default_hermes_home

CACHE_SCHEMA_VERSION = 2
CACHE_FILE_NAME = "token_insights_cache.json"
LIMITS_CACHE_SECONDS = 60
DELEGATION_TOLERANCE_SECONDS = 120

_LIMITS_CACHE: dict[str, object] = {"expires_at": 0.0, "payload": None}


def _env_path(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    return Path(raw).expanduser() if raw else default


def claude_projects_dir() -> Path:
    return _env_path("CLAUDE_PROJECTS_DIR", Path.home() / ".claude" / "projects")


def codex_sessions_dir() -> Path:
    return _env_path("CODEX_SESSIONS_DIR", Path.home() / ".codex" / "sessions")


def delegations_file() -> Path:
    return _env_path("DELEGATIONS_FILE", Path.cwd().parent / "tasks" / "delegations.jsonl")


def cache_path() -> Path:
    override = os.getenv("TOKEN_INSIGHTS_CACHE")
    if override:
        return Path(override).expanduser()
    state_override = os.getenv("HERMES_WEBUI_STATE_DIR")
    if state_override:
        return Path(state_override).expanduser() / CACHE_FILE_NAME
    return _platform_default_hermes_home() / "webui" / CACHE_FILE_NAME


def _safe_int(value) -> int:
    try:
        return max(int(float(value or 0)), 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value) -> float:
    try:
        return max(float(value or 0), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _parse_ts(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        pass
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return 0.0


def _day_key(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _empty_file_aggregate(runtime: str) -> dict:
    return {
        "runtime": runtime,
        "sessions": [],
        "turns": [],
        "models": {},
        "daily": {},
        "totals": {},
        "rate_limits": None,
        "as_of": None,
    }


def _add_model(models: dict, model: str, runtime: str, usage: dict) -> None:
    key = model or "unknown"
    row = models.setdefault(key, {
        "model": key,
        "runtime": runtime,
        "input": 0,
        "output": 0,
        "cache_read": 0,
        "cached": 0,
        "sessions": 0,
    })
    row["input"] += _safe_int(usage.get("input"))
    row["output"] += _safe_int(usage.get("output"))
    row["cache_read"] += _safe_int(usage.get("cache_read"))
    row["cached"] += _safe_int(usage.get("cached"))
    row["sessions"] += 1


def _add_daily(daily: dict, ts: float, runtime: str, tokens: int) -> None:
    if not ts:
        return
    key = _day_key(ts)
    row = daily.setdefault(key, {"date": key, "claude": 0, "codex": 0})
    row[runtime] = row.get(runtime, 0) + tokens


def _parse_claude_file(path: Path) -> dict:
    out = _empty_file_aggregate("claude")
    slug = path.parent.name
    turn_index_by_session: dict[str, int] = {}
    tool_use_names: dict[str, str] = {}
    pending_tool_names: list[str] = []

    def _content_list(message: dict) -> list:
        content = message.get("content")
        return content if isinstance(content, list) else []

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except Exception:
                continue
            message = row.get("message") if isinstance(row.get("message"), dict) else row
            role = str(message.get("role") or row.get("type") or "").lower()
            if role == "user" or row.get("type") == "user":
                names = []
                for part in _content_list(message):
                    if not isinstance(part, dict) or part.get("type") != "tool_result":
                        continue
                    tool_id = str(part.get("tool_use_id") or part.get("id") or "").strip()
                    names.append(tool_use_names.get(tool_id) or "unknown")
                if names:
                    pending_tool_names = names
                continue
            if row.get("type") not in ("assistant", "message") and role != "assistant":
                continue
            message = row.get("message") if isinstance(row.get("message"), dict) else row
            assistant_tool_names = {}
            for part in _content_list(message):
                if not isinstance(part, dict) or part.get("type") != "tool_use":
                    continue
                tool_id = str(part.get("id") or "").strip()
                name = str(part.get("name") or "unknown").strip() or "unknown"
                if tool_id:
                    tool_use_names[tool_id] = name
                assistant_tool_names[tool_id or name] = name
            usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
            if not usage:
                if assistant_tool_names:
                    tool_use_names.update(assistant_tool_names)
                continue
            ts = _parse_ts(row.get("timestamp") or message.get("timestamp"))
            model = str(message.get("model") or row.get("model") or "claude").strip() or "claude"
            session_id = str(row.get("sessionId") or row.get("session_id") or path.stem)
            input_tokens = _safe_int(usage.get("input_tokens"))
            output_tokens = _safe_int(usage.get("output_tokens"))
            cache_read = _safe_int(usage.get("cache_read_input_tokens"))
            cache_write = _safe_int(usage.get("cache_creation_input_tokens"))
            total = input_tokens + output_tokens + cache_read + cache_write
            item = {
                "id": session_id,
                "path": str(path),
                "project": slug,
                "runtime": "claude",
                "model": model,
                "timestamp": ts,
                "date": _day_key(ts) if ts else "",
                "input": input_tokens,
                "output": output_tokens,
                "cache_read": cache_read,
                "cache_write": cache_write,
                "tokens": total,
                "is_sidechain": bool(row.get("isSidechain")),
                "title": f"{slug}/{session_id}",
            }
            out["sessions"].append(item)
            _add_model(out["models"], model, "claude", item)
            _add_daily(out["daily"], ts, "claude", total)
            if not item["is_sidechain"]:
                turn_no = turn_index_by_session.get(session_id, 0) + 1
                turn_index_by_session[session_id] = turn_no
                out["turns"].append({
                    "id": session_id,
                    "path": str(path),
                    "project": slug,
                    "runtime": "claude",
                    "model": model,
                    "timestamp": ts,
                    "date": _day_key(ts) if ts else "",
                    "turn": turn_no,
                    "input": input_tokens,
                    "output": output_tokens,
                    "cache_read": cache_read,
                    "cache_creation": cache_write,
                    "initial_context": input_tokens + cache_read + cache_write if turn_no == 1 else 0,
                    "tool": ", ".join(dict.fromkeys(pending_tool_names)) if pending_tool_names else "",
                    "title": f"{slug}/{session_id}",
                })
                pending_tool_names = []
    return out


def _find_payload(row: dict) -> dict:
    payload = row.get("payload")
    return payload if isinstance(payload, dict) else {}


def _parse_codex_file(path: Path) -> dict:
    out = _empty_file_aggregate("codex")
    last_token_count = None
    last_ts = 0.0
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except Exception:
                continue
            payload = _find_payload(row)
            if payload.get("type") != "token_count":
                continue
            last_token_count = payload
            last_ts = _parse_ts(row.get("timestamp") or payload.get("timestamp") or last_ts)
    if not last_token_count:
        return out
    info = last_token_count.get("info") if isinstance(last_token_count.get("info"), dict) else {}
    usage = info.get("total_token_usage") if isinstance(info.get("total_token_usage"), dict) else {}
    rate_limits = last_token_count.get("rate_limits") if isinstance(last_token_count.get("rate_limits"), dict) else None
    total = _safe_int(usage.get("total_tokens"))
    if not total:
        total = _safe_int(usage.get("input_tokens")) + _safe_int(usage.get("output_tokens"))
    model = str(info.get("model") or "codex").strip() or "codex"
    item = {
        "id": path.stem,
        "path": str(path),
        "runtime": "codex",
        "model": model,
        "timestamp": last_ts,
        "date": _day_key(last_ts) if last_ts else "",
        "input": _safe_int(usage.get("input_tokens")),
        "output": _safe_int(usage.get("output_tokens")),
        "cached": _safe_int(usage.get("cached_input_tokens")),
        "reasoning_output": _safe_int(usage.get("reasoning_output_tokens")),
        "tokens": total,
        "title": path.stem,
    }
    out["sessions"].append(item)
    out["rate_limits"] = rate_limits
    out["as_of"] = datetime.fromtimestamp(last_ts, timezone.utc).isoformat() if last_ts else None
    _add_model(out["models"], model, "codex", item)
    _add_daily(out["daily"], last_ts, "codex", total)
    return out


def _read_cache() -> dict:
    path = cache_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": CACHE_SCHEMA_VERSION, "files": {}}
    if data.get("version") != CACHE_SCHEMA_VERSION or not isinstance(data.get("files"), dict):
        return {"version": CACHE_SCHEMA_VERSION, "files": {}}
    return data


def _write_cache(data: dict) -> None:
    path = cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _collect_files(root: Path, pattern: str = "*.jsonl") -> list[Path]:
    try:
        if root.exists():
            return sorted(p for p in root.rglob(pattern) if p.is_file())
    except Exception:
        return []
    return []


def _cached_or_parse(path: Path, runtime: str, cache: dict) -> dict:
    key = str(path)
    try:
        st = path.stat()
    except OSError:
        return _empty_file_aggregate(runtime)
    cached = cache.get("files", {}).get(key)
    if (
        isinstance(cached, dict)
        and cached.get("mtime") == st.st_mtime
        and cached.get("size") == st.st_size
        and isinstance(cached.get("aggregate"), dict)
    ):
        return cached["aggregate"]
    try:
        aggregate = _parse_claude_file(path) if runtime == "claude" else _parse_codex_file(path)
    except Exception:
        aggregate = _empty_file_aggregate(runtime)
    cache.setdefault("files", {})[key] = {"mtime": st.st_mtime, "size": st.st_size, "aggregate": aggregate}
    return aggregate


def _read_delegations(path: Path) -> list[dict]:
    out = []
    try:
        fh = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return out
    with fh:
        for line in fh:
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict):
                row["_started_ts"] = _parse_ts(row.get("started"))
                row["_finished_ts"] = _parse_ts(row.get("finished")) or row["_started_ts"]
                out.append(row)
    return out


def _match_delegation(session: dict, delegations: list[dict]) -> dict | None:
    ts = _safe_float(session.get("timestamp"))
    if not ts:
        return None
    matches = []
    for d in delegations:
        start = _safe_float(d.get("_started_ts")) - DELEGATION_TOLERANCE_SECONDS
        finish = _safe_float(d.get("_finished_ts")) + DELEGATION_TOLERANCE_SECONDS
        if start <= ts <= finish:
            matches.append(d)
    return matches[0] if len(matches) == 1 else None


def _estimate_claude_cost(model: str, item: dict) -> float:
    name = (model or "").lower()
    if "opus" in name:
        in_m, out_m, cache_write_m, cache_read_m = 15.0, 75.0, 18.75, 1.5
    elif "haiku" in name:
        in_m, out_m, cache_write_m, cache_read_m = 0.8, 4.0, 1.0, 0.08
    else:
        in_m, out_m, cache_write_m, cache_read_m = 3.0, 15.0, 3.75, 0.3
    return (
        item.get("input", 0) * in_m
        + item.get("output", 0) * out_m
        + item.get("cache_write", 0) * cache_write_m
        + item.get("cache_read", 0) * cache_read_m
    ) / 1_000_000


def _empty_result(days: int) -> dict:
    return {
        "period_days": days,
        "totals": {
            "claude": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "tokens": 0, "cost_usd": 0.0},
            "codex": {"input": 0, "output": 0, "cached": 0, "tokens": 0},
        },
        "by_agent": [],
        "by_model": [],
        "daily": [],
        "top_sessions": [],
        "unattributed": {"claude": 0, "codex": 0, "tokens": 0, "sessions": 0},
    }


def collect(days: int = 30) -> dict:
    days = min(max(int(days or 30), 1), 365)
    now = time.time()
    cutoff = now - (days * 86400)
    cache = _read_cache()
    file_aggs = []
    for path in _collect_files(claude_projects_dir()):
        file_aggs.append(_cached_or_parse(path, "claude", cache))
    for path in _collect_files(codex_sessions_dir()):
        file_aggs.append(_cached_or_parse(path, "codex", cache))
    _write_cache(cache)

    sessions = []
    latest_rate = None
    latest_rate_ts = 0.0
    for agg in file_aggs:
        for item in agg.get("sessions", []):
            if _safe_float(item.get("timestamp")) >= cutoff:
                sessions.append(item)
        if agg.get("rate_limits"):
            agg_ts = max((_safe_float(s.get("timestamp")) for s in agg.get("sessions", [])), default=0.0)
            if agg_ts >= latest_rate_ts:
                latest_rate_ts = agg_ts
                latest_rate = {"rate_limits": agg.get("rate_limits"), "as_of": agg.get("as_of")}

    result = _empty_result(days)
    delegations = _read_delegations(delegations_file())
    by_agent: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    daily: dict[str, dict] = {}
    unattributed = {"claude": 0, "codex": 0, "tokens": 0, "sessions": 0}

    for item in sessions:
        runtime = item.get("runtime") or "unknown"
        tokens = _safe_int(item.get("tokens"))
        if runtime == "claude":
            result["totals"]["claude"]["input"] += _safe_int(item.get("input"))
            result["totals"]["claude"]["output"] += _safe_int(item.get("output"))
            result["totals"]["claude"]["cache_read"] += _safe_int(item.get("cache_read"))
            result["totals"]["claude"]["cache_write"] += _safe_int(item.get("cache_write"))
            result["totals"]["claude"]["tokens"] += tokens
            result["totals"]["claude"]["cost_usd"] += _estimate_claude_cost(str(item.get("model") or ""), item)
        elif runtime == "codex":
            result["totals"]["codex"]["input"] += _safe_int(item.get("input"))
            result["totals"]["codex"]["output"] += _safe_int(item.get("output"))
            result["totals"]["codex"]["cached"] += _safe_int(item.get("cached"))
            result["totals"]["codex"]["tokens"] += tokens

        day = item.get("date") or (_day_key(item.get("timestamp")) if item.get("timestamp") else "")
        if day:
            drow = daily.setdefault(day, {"date": day, "claude": 0, "codex": 0})
            drow[runtime] = drow.get(runtime, 0) + tokens

        model = str(item.get("model") or "unknown")
        mrow = by_model.setdefault(f"{runtime}:{model}", {"model": model, "runtime": runtime, "input": 0, "output": 0, "tokens": 0, "sessions": 0})
        mrow["input"] += _safe_int(item.get("input"))
        mrow["output"] += _safe_int(item.get("output"))
        mrow["tokens"] += tokens
        mrow["sessions"] += 1

        delegation = _match_delegation(item, delegations)
        if delegation:
            agent = str(delegation.get("agent") or delegation.get("agent_id") or "unknown").strip() or "unknown"
            arow = by_agent.setdefault(f"{runtime}:{agent}", {"agent": agent, "runtime": runtime, "tokens": 0, "deleghe": 0})
            arow["tokens"] += tokens
            arow["deleghe"] += 1
        else:
            unattributed[runtime] = unattributed.get(runtime, 0) + tokens
            unattributed["tokens"] += tokens
            unattributed["sessions"] += 1

    result["totals"]["claude"]["cost_usd"] = round(result["totals"]["claude"]["cost_usd"], 6)
    result["by_agent"] = sorted(by_agent.values(), key=lambda r: (-r["tokens"], r["agent"]))[:50]
    result["by_model"] = sorted(by_model.values(), key=lambda r: (-r["tokens"], r["runtime"], r["model"]))[:50]
    result["daily"] = [daily.get(_day_key(now - (days - 1 - i) * 86400), {"date": _day_key(now - (days - 1 - i) * 86400), "claude": 0, "codex": 0}) for i in range(days)]
    result["top_sessions"] = sorted(
        [{
            "title": s.get("title") or s.get("id") or "session",
            "task": s.get("title") or s.get("id") or "session",
            "runtime": s.get("runtime"),
            "model": s.get("model"),
            "tokens": _safe_int(s.get("tokens")),
            "date": s.get("date") or "",
        } for s in sessions],
        key=lambda r: -r["tokens"],
    )[:5]
    result["unattributed"] = unattributed
    if latest_rate:
        result["_latest_codex_rate_limits"] = latest_rate
    return result


def _empty_breakdown(days: int) -> dict:
    return {
        "period_days": days,
        "initial_context": {"avg": 0, "min": 0, "max": 0, "per_session": []},
        "per_turn": {"input_avg": 0, "input_median": 0, "input_max": 0, "output_avg": 0, "output_median": 0, "output_max": 0},
        "mcp_estimate": {"servers": [], "total_est": 0, "note": "Estimate from MCP config JSON size; Claude transcript does not expose tool schema tokens directly."},
        "heavy_turns": [],
        "by_tool_cache_creation": [],
        "drag": {"sessions": [], "total_cache_read": 0},
        "prime_auto_compact": {"events": [], "count": 0, "last": None},
        "scope": "claude_only",
    }


def _avg(values: list[int]) -> int:
    return int(round(sum(values) / len(values))) if values else 0


def _median(values: list[int]) -> int:
    return int(round(statistics.median(values))) if values else 0


def _workspace_root() -> Path:
    raw = os.getenv("HERMES_WORKSPACE_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.cwd().parent if Path.cwd().name == "hermes-webui" else Path.cwd()


def _mcp_config_candidates() -> list[Path]:
    home = Path.home()
    return [
        _workspace_root() / ".mcp.json",
        home / ".claude.json",
        home / ".claude" / ".mcp.json",
        home / ".claude" / "mcp.json",
        home / ".config" / "claude" / "mcp.json",
    ]


def _extract_mcp_servers(data: object) -> dict[str, object]:
    if not isinstance(data, dict):
        return {}
    direct = data.get("mcpServers")
    if isinstance(direct, dict):
        return direct
    projects = data.get("projects")
    if isinstance(projects, dict):
        merged = {}
        for project in projects.values():
            if isinstance(project, dict) and isinstance(project.get("mcpServers"), dict):
                merged.update(project["mcpServers"])
        return merged
    return {}


def _server_tool_count(config: object) -> int:
    if not isinstance(config, dict):
        return 0
    tools = config.get("tools")
    if isinstance(tools, list):
        return len(tools)
    schemas = config.get("toolSchemas") or config.get("tool_schemas")
    if isinstance(schemas, dict):
        return len(schemas)
    if isinstance(schemas, list):
        return len(schemas)
    return 0


def _mcp_estimate() -> dict:
    servers: dict[str, dict] = {}
    for path in _mcp_config_candidates():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for name, config in _extract_mcp_servers(data).items():
            if not isinstance(config, dict):
                continue
            disabled = bool(config.get("disabled")) or config.get("enabled") is False
            if disabled:
                continue
            key = str(name or "unknown").strip() or "unknown"
            serialized = json.dumps(config, sort_keys=True, ensure_ascii=False)
            row = servers.setdefault(key, {"name": key, "tools": 0, "est_tokens": 0})
            row["tools"] = max(row["tools"], _server_tool_count(config))
            row["est_tokens"] += max(int(round(len(serialized) / 4)), 0)
    rows = sorted(servers.values(), key=lambda r: (-r["est_tokens"], r["name"]))
    return {
        "servers": rows,
        "total_est": sum(_safe_int(r.get("est_tokens")) for r in rows),
        "note": "Estimate from MCP config JSON size; Claude transcript does not expose tool schema tokens directly.",
    }


def context_breakdown(days: int = 30) -> dict:
    days = min(max(int(days or 30), 1), 365)
    now = time.time()
    cutoff = now - (days * 86400)
    cache = _read_cache()
    file_aggs = [_cached_or_parse(path, "claude", cache) for path in _collect_files(claude_projects_dir())]
    _write_cache(cache)

    turns = []
    for agg in file_aggs:
        for turn in agg.get("turns", []):
            if _safe_float(turn.get("timestamp")) >= cutoff:
                turns.append(turn)

    result = _empty_breakdown(days)
    result["mcp_estimate"] = _mcp_estimate()
    try:
        from api.prime_auto_compact import read_prime_auto_compact_events, compute_compact_savings
        compact_events = read_prime_auto_compact_events(days)
        compact_savings = compute_compact_savings(compact_events)
    except Exception:
        compact_events = []
        compact_savings = {}
    result["prime_auto_compact"] = {
        "events": compact_events[-20:],
        "count": len(compact_events),
        "last": compact_events[-1] if compact_events else None,
        "savings": compact_savings,
    }
    if not turns:
        return result

    sessions: dict[str, list[dict]] = {}
    for turn in turns:
        sessions.setdefault(str(turn.get("id") or "unknown"), []).append(turn)

    initial_rows = []
    initial_values = []
    input_values = []
    output_values = []
    by_tool: dict[str, int] = {}
    heavy_turns = []
    drag_sessions = []
    total_cache_read = 0

    for session_id, session_turns in sessions.items():
        ordered = sorted(session_turns, key=lambda r: (_safe_float(r.get("timestamp")), _safe_int(r.get("turn"))))
        first = ordered[0]
        initial = _safe_int(first.get("initial_context")) or (
            _safe_int(first.get("input")) + _safe_int(first.get("cache_read")) + _safe_int(first.get("cache_creation"))
        )
        initial_values.append(initial)
        initial_rows.append({
            "session": session_id,
            "project": first.get("project") or "",
            "turns": len(ordered),
            "initial_context": initial,
            "ts": first.get("timestamp") or 0,
        })

        cache_read_total = sum(_safe_int(t.get("cache_read")) for t in ordered)
        total_cache_read += cache_read_total
        half = len(ordered) // 2
        second_half = ordered[half:]
        second_half_cache_read = sum(_safe_int(t.get("cache_read")) for t in second_half)
        est_saving = max(second_half_cache_read - (initial * len(second_half)), 0)
        drag_sessions.append({
            "session": session_id,
            "turns": len(ordered),
            "cache_read": cache_read_total,
            "est_saving_if_split": est_saving,
            "curve": [_safe_int(t.get("cache_read")) for t in ordered],
        })

        for turn in ordered:
            input_values.append(_safe_int(turn.get("input")))
            output_values.append(_safe_int(turn.get("output")))
            cache_creation = _safe_int(turn.get("cache_creation"))
            if cache_creation:
                tool = str(turn.get("tool") or "unknown").strip() or "unknown"
                by_tool[tool] = by_tool.get(tool, 0) + cache_creation
                heavy_turns.append({
                    "session": session_id,
                    "turn": _safe_int(turn.get("turn")),
                    "tool": tool,
                    "cache_creation": cache_creation,
                    "ts": turn.get("timestamp") or 0,
                })

    result["initial_context"] = {
        "avg": _avg(initial_values),
        "min": min(initial_values) if initial_values else 0,
        "max": max(initial_values) if initial_values else 0,
        "per_session": sorted(initial_rows, key=lambda r: -_safe_int(r.get("initial_context"))),
    }
    result["per_turn"] = {
        "input_avg": _avg(input_values),
        "input_median": _median(input_values),
        "input_max": max(input_values) if input_values else 0,
        "output_avg": _avg(output_values),
        "output_median": _median(output_values),
        "output_max": max(output_values) if output_values else 0,
    }
    result["heavy_turns"] = sorted(heavy_turns, key=lambda r: -_safe_int(r.get("cache_creation")))[:10]
    result["by_tool_cache_creation"] = [
        {"tool": tool, "tokens": tokens}
        for tool, tokens in sorted(by_tool.items(), key=lambda item: (-item[1], item[0]))
    ]
    result["drag"] = {
        "sessions": sorted(drag_sessions, key=lambda r: -_safe_int(r.get("cache_read"))),
        "total_cache_read": total_cache_read,
    }
    return result


def codex_limits_snapshot() -> dict | None:
    latest = None
    latest_ts = 0.0
    cache = _read_cache()
    for path in _collect_files(codex_sessions_dir()):
        agg = _cached_or_parse(path, "codex", cache)
        if not agg.get("rate_limits"):
            continue
        ts = max((_safe_float(s.get("timestamp")) for s in agg.get("sessions", [])), default=0.0)
        if ts >= latest_ts:
            latest_ts = ts
            latest = agg
    _write_cache(cache)
    if not latest or not latest.get("rate_limits"):
        return None
    rl = latest["rate_limits"]
    primary = rl.get("primary") if isinstance(rl.get("primary"), dict) else {}
    secondary = rl.get("secondary") if isinstance(rl.get("secondary"), dict) else {}
    return {
        "primary_used_percent": _safe_float(primary.get("used_percent")),
        "secondary_used_percent": _safe_float(secondary.get("used_percent")),
        "primary_resets_at": primary.get("resets_at"),
        "secondary_resets_at": secondary.get("resets_at"),
        "plan": rl.get("plan_type") or rl.get("plan") or None,
        "as_of": latest.get("as_of"),
    }


def claude_limits_snapshot() -> dict | None:
    """Best-effort Claude quota lookup.

    Claude Code OAuth quota internals vary by installed CLI version. This avoids
    logging or returning credentials; unsupported local shapes simply return
    null for the UI.
    """
    endpoint = os.getenv("CLAUDE_USAGE_ENDPOINT", "").strip()
    token = os.getenv("CLAUDE_USAGE_TOKEN", "").strip()
    if not endpoint or not token:
        return None
    try:
        req = Request(endpoint, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        with urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read(128 * 1024).decode("utf-8", "replace"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def limits() -> dict:
    now = time.time()
    cached = _LIMITS_CACHE.get("payload")
    if cached is not None and _safe_float(_LIMITS_CACHE.get("expires_at")) > now:
        return cached  # type: ignore[return-value]
    payload = {"codex": codex_limits_snapshot(), "claude": claude_limits_snapshot()}
    _LIMITS_CACHE["payload"] = payload
    _LIMITS_CACHE["expires_at"] = now + LIMITS_CACHE_SECONDS
    return payload
