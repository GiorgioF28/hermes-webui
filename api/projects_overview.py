"""Per-project overview for the Command Bridge "Projects Strip".

Reads project notes from ``<vault>/01-Projects/*.md`` (direct fs). For each
project returns:
- ``tasks`` ("Up next"): up to 3 open ``- [ ]`` checkboxes, prioritising those
  under a configured section heading (## TODO / ## Next / ## Up next / ...).
- ``latest`` ("Latest changes"): the 3 most-recently-modified files among the
  project note and the notes it wikilinks to (name + relative time).
- ``activity``: a 14-element array (oldest -> today) counting modifications,
  bucketed from file mtimes.

Parsing rules live in the small CONFIG block below so they are easy to tweak.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# ── CONFIG (parsing rules — tweak here) ──────────────────────────────────────
PROJECTS_DIR = "01-Projects"
TASK_SECTION_KEYWORDS = ("todo", "next", "up next", "da fare", "prossimi", "next steps")
MAX_TASKS = 3
MAX_LATEST = 3
ACTIVITY_DAYS = 14
_MAX_BYTES = 1_000_000

_OPEN_TASK_RE = re.compile(r"^\s*[-*]\s+\[\s\]\s+(.*\S)\s*$")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.*\S)\s*$")
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")

_CACHE_TTL = 15.0
_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, float, dict]] = {}


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


def _rel_time(ts: float) -> str:
    if not ts:
        return ""
    delta = max(0.0, time.time() - ts)
    if delta < 60:
        return "ora"
    if delta < 3600:
        return f"{int(delta // 60)}m fa"
    if delta < 86400:
        return f"{int(delta // 3600)}h fa"
    return f"{int(delta // 86400)}g fa"


def parse_open_tasks(body: str) -> list[str]:
    """Open `- [ ]` tasks, those under TODO/Next-style headings first. Max MAX_TASKS."""
    section_is_tasky = False
    prioritized: list[str] = []
    plain: list[str] = []
    for line in body.splitlines():
        h = _HEADING_RE.match(line)
        if h:
            title = h.group(1).strip().lower()
            section_is_tasky = any(k in title for k in TASK_SECTION_KEYWORDS)
            continue
        m = _OPEN_TASK_RE.match(line)
        if m:
            (prioritized if section_is_tasky else plain).append(m.group(1).strip())
    return (prioritized + plain)[:MAX_TASKS]


def _activity_buckets(mtimes, days: int = ACTIVITY_DAYS) -> list[int]:
    """14-element array, index 0 = oldest day .. last = today."""
    today = datetime.now(timezone.utc).date()
    buckets = [0] * days
    for ts in mtimes:
        if not ts:
            continue
        d = datetime.fromtimestamp(ts, timezone.utc).date()
        delta = (today - d).days
        if 0 <= delta < days:
            buckets[days - 1 - delta] += 1
    return buckets


def build_projects_overview(vault_path) -> dict:
    """Walk 01-Projects and return per-project overview payloads. Pure function."""
    vault = Path(str(vault_path)).expanduser()
    pdir = vault / PROJECTS_DIR
    if not pdir.is_dir():
        return {"projects": [], "count": 0, "exists": False}

    stem_map: dict[str, Path] = {}
    for p in vault.rglob("*.md"):
        stem_map.setdefault(p.stem.lower(), p)

    projects = []
    for note in sorted(pdir.glob("*.md"), key=lambda p: p.name.lower()):
        body = _read(note)
        tasks = parse_open_tasks(body)
        related = {note}
        for m in _WIKILINK_RE.finditer(body):
            tp = stem_map.get(m.group(1).strip().lower())
            if tp:
                related.add(tp)
        related_sorted = sorted(related, key=_mtime, reverse=True)
        latest = [{
            "name": p.stem,
            "path": str(p.relative_to(vault)).replace("\\", "/"),
            "mtime": _mtime(p),
            "rel": _rel_time(_mtime(p)),
        } for p in related_sorted[:MAX_LATEST]]
        activity = _activity_buckets([_mtime(p) for p in related])
        projects.append({
            "id": re.sub(r"[^\w]+", "-", note.stem).strip("-").lower() or "project",
            "name": note.stem,
            "path": str(note.relative_to(vault)).replace("\\", "/"),
            "mtime": _mtime(note),
            "tasks": tasks,
            "latest": latest,
            "activity": activity,
        })
    return {"projects": projects, "count": len(projects), "exists": True}


def get_projects_overview(vault_path) -> dict:
    """Cached build_projects_overview (TTL + projects-dir mtime invalidation)."""
    vault = str(Path(str(vault_path)).expanduser())
    pdir = Path(vault) / PROJECTS_DIR
    sig = _mtime(pdir)
    now = time.time()
    with _cache_lock:
        cached = _cache.get(vault)
        if cached and (now - cached[0]) < _CACHE_TTL and cached[1] == sig:
            return cached[2]
    data = build_projects_overview(vault)
    with _cache_lock:
        _cache[vault] = (now, sig, data)
    return data
