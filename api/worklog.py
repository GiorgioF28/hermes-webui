"""Work Log / Wins ledger for the Command Bridge flex panel.

Aggregates recent events from git commits and completed markdown tasks into a
small JSON-safe payload. No derived ledger is persisted.
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from api.command_bridge_projects import (
    PROJECT_BY_ID,
    PROJECTS,
    TRACKING_ATTRIBUTION_ORDER,
    tracked_projects_payload,
)

_CACHE_TTL = 30.0
_MAX_DAYS = 90
_DEFAULT_DAYS = 14
_MAX_BYTES = 1_000_000
_RECENT_LIMIT = 24

_DONE_TASK_RE = re.compile(r"^\s*[-*]\s+\[[xX]\]\s+(.+?)\s*$")
_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")

_PROJECT_KEYWORDS = tuple(
    (
        PROJECT_BY_ID[project_id]["name"],
        re.compile(PROJECT_BY_ID[project_id]["tracking_pattern"], re.I),
    )
    for project_id in TRACKING_ATTRIBUTION_ORDER
)

_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, tuple, dict]] = {}


@dataclass(frozen=True)
class WorkEvent:
    date: str
    project: str
    type: str
    summary: str
    source: str
    sort_ts: float


def clamp_days(value, default: int = _DEFAULT_DAYS) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(_MAX_DAYS, days))


def classify_commit_type(summary: str) -> str:
    prefix = str(summary or "").split(":", 1)[0].strip().lower()
    if "(" in prefix:
        prefix = prefix.split("(", 1)[0].strip()
    if prefix == "feat":
        return "aggiunta"
    if prefix == "fix":
        return "correzione"
    if prefix in {"refactor", "perf", "style", "chore"}:
        return "miglioria"
    return "commit"


def project_for(text: str) -> str:
    haystack = str(text or "")
    for name, pattern in _PROJECT_KEYWORDS:
        if pattern.search(haystack):
            return name
    return "Altro"


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


def _iso_date_from_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).date().isoformat()


def _explicit_date(text: str) -> str | None:
    match = _DATE_RE.search(text or "")
    if not match:
        return None
    try:
        datetime.strptime(match.group(1), "%Y-%m-%d")
    except ValueError:
        return None
    return match.group(1)


def _clean_summary(summary: str) -> str:
    text = re.sub(r"\s+", " ", str(summary or "")).strip()
    text = re.sub(r"\s+#\w+\s*$", "", text).strip()
    return text[:180]


def _git_events(repo: Path, label: str, since: datetime) -> list[WorkEvent]:
    if not (repo / ".git").exists():
        return []
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "log",
                "--since",
                since.isoformat(),
                "--format=%ct%x1f%h%x1f%s",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []

    events: list[WorkEvent] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\x1f", 2)
        if len(parts) != 3:
            continue
        raw_ts, short_hash, summary = parts
        try:
            ts = float(raw_ts)
        except ValueError:
            continue
        clean = _clean_summary(summary)
        if not clean:
            continue
        events.append(
            WorkEvent(
                date=_iso_date_from_ts(ts),
                project=project_for(f"{clean} {label}"),
                type=classify_commit_type(clean),
                summary=clean,
                source=f"git:{label}:{short_hash}",
                sort_ts=ts,
            )
        )
    return events


def parse_done_tasks(path: Path, workspace: Path | None = None) -> list[WorkEvent]:
    body = _read(path)
    if not body:
        return []
    ts = _mtime(path)
    date = _iso_date_from_ts(ts) if ts else datetime.now(timezone.utc).date().isoformat()
    rel_source = path.name
    if workspace:
        try:
            rel_source = str(path.relative_to(workspace)).replace("\\", "/")
        except ValueError:
            rel_source = path.name

    events: list[WorkEvent] = []
    for line in body.splitlines():
        match = _DONE_TASK_RE.match(line)
        if not match:
            continue
        summary = _clean_summary(match.group(1))
        if not summary:
            continue
        task_date = _explicit_date(summary) or date
        events.append(
            WorkEvent(
                date=task_date,
                project=project_for(f"{summary} {rel_source}"),
                type="task-done",
                summary=summary,
                source=f"task:{rel_source}",
                sort_ts=ts,
            )
        )
    return events


def _task_events(workspace: Path) -> list[WorkEvent]:
    paths: list[Path] = []
    today = workspace / "tasks" / "today.md"
    if today.exists():
        paths.append(today)
    projects_dir = workspace / "obsidian-vault" / "01-Projects"
    if projects_dir.is_dir():
        paths.extend(sorted(projects_dir.glob("*.md"), key=lambda p: p.name.lower()))
    events: list[WorkEvent] = []
    for path in paths:
        events.extend(parse_done_tasks(path, workspace))
    return events


def _dedupe(events: list[WorkEvent]) -> list[WorkEvent]:
    seen: set[tuple[str, str, str, str]] = set()
    out: list[WorkEvent] = []
    for event in sorted(events, key=lambda e: (-e.sort_ts, e.source, e.summary)):
        key = (event.date, event.project, event.type, event.summary.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(event)
    return out


def _file_signature(paths: list[Path]) -> tuple:
    return tuple((str(p), _mtime(p)) for p in paths if p.exists())


def _signature(workspace: Path, webui_repo: Path, days: int) -> tuple:
    paths = [workspace / "tasks" / "today.md"]
    pdir = workspace / "obsidian-vault" / "01-Projects"
    if pdir.is_dir():
        paths.extend(sorted(pdir.glob("*.md"), key=lambda p: p.name.lower()))
    git_heads = []
    for repo in (workspace, webui_repo):
        head = repo / ".git" / "HEAD"
        git_heads.append((str(repo), _mtime(head)))
    return (days, tuple(git_heads), _file_signature(paths))


def build_worklog(workspace_path, webui_repo_path=None, days: int = _DEFAULT_DAYS) -> dict:
    """Aggregate recent work events. Pure except for reading files and git logs."""
    days = clamp_days(days)
    workspace = Path(str(workspace_path)).expanduser()
    webui_repo = Path(str(webui_repo_path)).expanduser() if webui_repo_path else Path(__file__).resolve().parent.parent
    now = datetime.now(timezone.utc)
    start_date = now.date() - timedelta(days=days - 1)
    since = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)

    events = []
    events.extend(_git_events(webui_repo, "hermes-webui", since))
    if workspace.resolve() != webui_repo.resolve():
        events.extend(_git_events(workspace, "hermes-setup", since))
    events.extend(_task_events(workspace))

    filtered = [
        e for e in _dedupe(events)
        if start_date <= datetime.strptime(e.date, "%Y-%m-%d").date() <= now.date()
    ]

    counts = {((start_date + timedelta(days=i)).isoformat()): 0 for i in range(days)}
    totals_by_project = {project["name"]: 0 for project in PROJECTS}
    totals_by_project["Altro"] = 0
    for event in filtered:
        counts[event.date] = counts.get(event.date, 0) + 1
        totals_by_project[event.project] = totals_by_project.get(event.project, 0) + 1

    today = now.date().isoformat()
    yesterday = (now.date() - timedelta(days=1)).isoformat()
    today_count = counts.get(today, 0)
    yesterday_count = counts.get(yesterday, 0)
    record_date, record_count = max(counts.items(), key=lambda kv: (kv[1], kv[0]))

    streak_days = 0
    cursor = now.date()
    while counts.get(cursor.isoformat(), 0) > 0:
        streak_days += 1
        cursor -= timedelta(days=1)

    return {
        "ok": True,
        "today_count": today_count,
        "yesterday_count": yesterday_count,
        "record_count": record_count,
        "record_date": record_date,
        "streak_days": streak_days,
        "totals_by_project": totals_by_project,
        "tracked_projects": tracked_projects_payload(),
        "daily": [{"date": date, "count": count} for date, count in sorted(counts.items())],
        "recent": [
            {
                "date": e.date,
                "project": e.project,
                "type": e.type,
                "summary": e.summary,
                "source": e.source,
            }
            for e in sorted(filtered, key=lambda item: (item.date, item.sort_ts), reverse=True)[:_RECENT_LIMIT]
        ],
    }


def get_worklog(workspace_path, webui_repo_path=None, days: int = _DEFAULT_DAYS) -> dict:
    """Cached build_worklog with TTL and lightweight source signatures."""
    days = clamp_days(days)
    workspace = Path(str(workspace_path)).expanduser()
    webui_repo = Path(str(webui_repo_path)).expanduser() if webui_repo_path else Path(__file__).resolve().parent.parent
    key = f"{workspace}|{webui_repo}|{days}"
    sig = _signature(workspace, webui_repo, days)
    now = time.time()
    with _cache_lock:
        cached = _cache.get(key)
        if cached and (now - cached[0]) < _CACHE_TTL and cached[1] == sig:
            return cached[2]
    data = build_worklog(workspace, webui_repo, days)
    with _cache_lock:
        _cache[key] = (now, sig, data)
    return data
