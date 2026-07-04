"""Live agent registry for the Command Bridge.

Builds a JSON-safe snapshot from ``obsidian-vault/06-Agents/*.md`` plus the
best-effort delegation usage ledger in ``tasks/agent-usage.jsonl``.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from api.agent_health import _runtime_status_is_fresh

AGENTS_DIR = "06-Agents"
USAGE_LOG = "agent-usage.jsonl"
LIVE_WINDOW_SECONDS = 14 * 24 * 60 * 60
ACTIVE_USAGE_WINDOW_SECONDS = 10 * 60
_CACHE_TTL = 15.0
_MAX_BYTES = 1_000_000
GATEWAY_STATE_FILE = "gateway_state.json"

PROFILE_TO_AGENT_SLUG = {
    "orchestratore": "orchestratore",
    "programmatore": "programmatore-project-engineer",
    "ricercatore": "research-analyst",
    "social": "social-client-contact",
    "librarian": "memory-librarian",
}

_EXCLUDED_STEMS = {
    "readme",
    "agent handoff protocol",
    "regole operative agenti",
    "censimento agenti operativi",
}
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.M)
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)
_FIELD_RE = re.compile(r"^(?:[-*]\s*)?\**([^:\n]+?)\**\s*:\s*(.+?)\s*$", re.M)

_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, object, dict]] = {}


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return slug or "agent"


def _read(path: Path) -> str:
    try:
        if path.stat().st_size > _MAX_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _dir_signature(path: Path) -> float:
    try:
        return max((_mtime(p) for p in path.glob("*.md")), default=0.0)
    except OSError:
        return 0.0


def _profiles_root() -> Path | None:
    try:
        from hermes_constants import get_default_hermes_root

        return get_default_hermes_root() / "profiles"
    except Exception:
        return None


def _gateway_signature() -> float:
    profiles_root = _profiles_root()
    if profiles_root is None:
        return 0.0
    return max(
        (_mtime(profiles_root / profile / GATEWAY_STATE_FILE) for profile in PROFILE_TO_AGENT_SLUG),
        default=0.0,
    )


def _rel_time(ts: float, now: float | None = None) -> str:
    if not ts:
        return ""
    delta = max(0.0, (time.time() if now is None else now) - ts)
    if delta < 60:
        return "ora"
    if delta < 3600:
        return f"{int(delta // 60)}m fa"
    if delta < 86400:
        return f"{int(delta // 3600)}h fa"
    return f"{int(delta // 86400)}g fa"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def _frontmatter(body: str) -> dict[str, str]:
    if not body.startswith("---"):
        return {}
    end = body.find("\n---", 3)
    if end < 0:
        return {}
    out: dict[str, str] = {}
    for line in body[3:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower().replace("-", "_")
        value = value.strip().strip('"\'')
        if value:
            out[key] = value
    return out


def _title(body: str, fallback: str) -> str:
    match = _HEADING_RE.search(body)
    title = match.group(1).strip() if match else fallback
    return re.sub(r"^Agent:\s*", "", title, flags=re.I).strip() or fallback


def _section_text(body: str, wanted: str) -> str:
    matches = list(_SECTION_RE.finditer(body))
    for i, match in enumerate(matches):
        if match.group(1).strip().lower() != wanted.lower():
            continue
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        for line in body[start:end].splitlines():
            text = line.strip()
            if text and not text.startswith("#") and not text.startswith("---"):
                return re.sub(r"^[-*]\s+", "", text).strip()
    return ""


def _field(body: str, names: tuple[str, ...]) -> str:
    wanted = {n.lower().replace("_", " ") for n in names}
    for match in _FIELD_RE.finditer(body):
        key = match.group(1).strip().lower().replace("_", " ")
        if key in wanted:
            return match.group(2).strip().strip("`*_")
    return ""


def parse_agent_note(path: Path) -> dict:
    body = _read(path)
    meta = _frontmatter(body)
    name = meta.get("name") or _title(body, path.stem)
    role = (
        meta.get("summary")
        or meta.get("role")
        or _section_text(body, "Ruolo")
        or name
    )
    model = (
        meta.get("model")
        or _field(body, ("model", "modello"))
        or "auto"
    )
    return {
        "id": _slug(name),
        "name": name,
        "role": " ".join(role.splitlines()).strip(),
        "model": model,
        "path": path.name,
    }


def _load_usage(workspace: Path) -> dict[str, dict]:
    path = workspace / "tasks" / USAGE_LOG
    latest: dict[str, dict] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return latest
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        agent_id = _slug(str(row.get("agent_id") or ""))
        ts = row.get("ts")
        if not agent_id or not isinstance(ts, (int, float)):
            continue
        prev = latest.get(agent_id)
        if prev is None or float(ts) > float(prev.get("ts") or 0):
            latest[agent_id] = row
    return latest


def _gateway_states(now: float | None = None) -> dict[str, dict]:
    profiles_root = _profiles_root()
    if profiles_root is None:
        return {}

    reference = datetime.fromtimestamp(time.time() if now is None else now, timezone.utc)
    states: dict[str, dict] = {}
    for profile, slug in PROFILE_TO_AGENT_SLUG.items():
        path = profiles_root / profile / GATEWAY_STATE_FILE
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        try:
            active = max(0, int(payload.get("active_agents") or 0))
        except (TypeError, ValueError):
            active = 0
        discord = None
        platforms = payload.get("platforms")
        if isinstance(platforms, dict):
            discord_payload = platforms.get("discord")
            if isinstance(discord_payload, dict):
                raw_discord = discord_payload.get("state")
                if isinstance(raw_discord, str) and raw_discord:
                    discord = raw_discord
        states[slug] = {
            "profile": profile,
            "running": _runtime_status_is_fresh(payload, now=reference),
            "active": active,
            "discord": discord,
        }
    return states


def build_agent_registry(workspace_path) -> dict:
    """Read agent notes and usage log, returning the live registry payload."""
    workspace = Path(str(workspace_path)).expanduser()
    agents_dir = workspace / "obsidian-vault" / AGENTS_DIR
    usage = _load_usage(workspace)
    now = time.time()
    gateways = _gateway_states(now)
    if not agents_dir.is_dir():
        return {"ok": True, "agents": [], "count": 0, "exists": False}

    agents = []
    for path in sorted(agents_dir.glob("*.md"), key=lambda p: p.name.lower()):
        if path.stem.strip().lower() in _EXCLUDED_STEMS:
            continue
        agent = parse_agent_note(path)
        used = usage.get(agent["id"]) or usage.get(_slug(path.stem))
        ts = float(used.get("ts") or 0) if used else 0.0
        usage_live = bool(ts and (now - ts) <= LIVE_WINDOW_SECONDS)
        usage_active = bool(ts and (now - ts) <= ACTIVE_USAGE_WINDOW_SECONDS)
        gateway = gateways.get(agent["id"])
        if gateway and gateway.get("running"):
            if int(gateway.get("active") or 0) > 0 or usage_active:
                state = "attivo"
            else:
                state = "in_attesa"
        else:
            state = "vivo" if usage_live else "dormiente"
        agent["state"] = state
        agent["gateway"] = gateway or {
            "profile": next((profile for profile, slug in PROFILE_TO_AGENT_SLUG.items() if slug == agent["id"]), None),
            "running": False,
            "active": 0,
            "discord": None,
        }
        agent["last_used"] = {"ts": _iso(ts), "rel": _rel_time(ts, now)} if ts else None
        agents.append(agent)

    state_priority = {"attivo": 0, "in_attesa": 1, "vivo": 2, "dormiente": 3}
    agents.sort(
        key=lambda a: (
            state_priority.get(a["state"], 3),
            -float((usage.get(a["id"]) or {}).get("ts") or 0),
            a["name"].lower(),
        )
    )
    return {"ok": True, "agents": agents, "count": len(agents), "exists": True}


def get_agent_registry(workspace_path) -> dict:
    """Cached registry with TTL plus agents-dir and usage-log mtime invalidation."""
    workspace = Path(str(workspace_path)).expanduser()
    agents_dir = workspace / "obsidian-vault" / AGENTS_DIR
    usage_log = workspace / "tasks" / USAGE_LOG
    sig = (_dir_signature(agents_dir), _mtime(usage_log), _gateway_signature())
    key = str(workspace)
    now = time.time()
    with _cache_lock:
        cached = _cache.get(key)
        if cached and (now - cached[0]) < _CACHE_TTL and cached[1] == sig:
            return cached[2]
    data = build_agent_registry(workspace)
    with _cache_lock:
        _cache[key] = (now, sig, data)
    return data


def record_agent_usage(workspace_path, agent_id: str, task_type: str, task_id: str) -> None:
    """Append one usage event. Best-effort: callers should never fail on this."""
    if not str(agent_id or "").strip():
        return
    clean_id = _slug(agent_id)
    row = {
        "ts": time.time(),
        "agent_id": clean_id,
        "task_type": str(task_type or ""),
        "task_id": str(task_id or ""),
    }
    path = Path(str(workspace_path)).expanduser() / "tasks" / USAGE_LOG
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        return
