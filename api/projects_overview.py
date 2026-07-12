"""Per-project overview for the Command Bridge "Projects Strip".

Reads project notes from ``<vault>/01-Projects/*.md`` (direct fs) and rolls them
up into family cards (Hermes · VisionBuilts · Concorso INPS · Podcast Rap). Each single
note in 01-Projects is a *sub-project* of one family; the card aggregates them.

For each family the payload returns:
- ``children``: the member sub-projects (name + vault-relative path) to drill in.
- ``tasks`` ("Up next"): open ``- [ ]`` checkboxes gathered from the member
  notes AND from idea/area/inbox/resource notes that mention the family, each
  carrying its own source ``path`` so the toggle writes back to the right note.
- ``latest`` ("Latest changes"): most-recently-modified notes across the family.
- ``activity``: 14-element array (oldest -> today) of modification counts.

The flat per-note ``projects`` list is kept too, for backward compatibility.

Parsing rules / family mapping live in the small CONFIG block below.
"""
from __future__ import annotations

import re
import threading
import time
import json
from datetime import datetime, timezone
from pathlib import Path

# ── CONFIG (parsing rules + family mapping — tweak here) ─────────────────────
PROJECTS_DIR = "01-Projects"
# Headings whose bullet items count as "things to do". Notes here often write
# tasks as plain bullets (not `- [ ]`), so under one of these sections a bullet
# is treated as a task; prose under other headings is ignored.
TASK_SECTION_KEYWORDS = (
    "todo", "to do", "next", "up next", "next steps", "da fare", "prossim",
    "azione", "roadmap", "backlog", "mvp", "feature", "miglior", "blocch",
    "blocco", "task", "obiettivi prossim", "cose da",
)
MAX_TASKS = 3            # legacy per-note cap (flat list)
MAX_FAMILY_TASKS = 12    # aggregated cap per family card (UI scrolls)
MAX_LATEST = 4
ACTIVITY_DAYS = 14
_MAX_BYTES = 1_000_000

# The three families, in display order. ``stem_keywords`` map a 01-Projects note
# to its family by its filename; ``keywords`` (broader) attribute a *free* task
# (from Ideas/Areas/Inbox/Resources) to a family by note name + task text.
FAMILIES = (
    {
        "id": "hermes",
        "name": "Hermes",
        "priority": 2,
        "stem_keywords": ("hermes",),
        "keywords": ("hermes", "command bridge", "webui", "web ui", "prime", "voce", "voice", "planet", "pianeta"),
    },
    {
        "id": "visionbuilts",
        "name": "VisionBuilts",
        "priority": 2,
        "stem_keywords": ("visionbuilts", "vision builts", "giorgiof28", "creator earning", "creator-earning"),
        "keywords": ("visionbuilts", "vision builts", "giorgiof28", "creator earning", "creator-earning",
                     "ebook", "e-book", "n8n", "console", "instagram", "crm", "webhook", "gotenberg"),
    },
    {
        "id": "concorso-inps",
        "name": "Concorso INPS",
        "priority": 2,
        "stem_keywords": ("concorso inps", "assistente informatico", "inps"),
        "keywords": ("concorso inps", "assistente informatico", "inps", "studio",
                     "quiz", "manuale", "cad", "gdpr", "office automation"),
        "study": {
            "course": "Concorso INPS",
            "path": "07-Study/Concorso INPS/Indice.md",
        },
    },
    {
        "id": "rap",
        "name": "Podcast Rap",
        "priority": 0,
        "stem_keywords": ("rap", "album", "produzione musicale"),
        "keywords": ("rap", "album", "produzione musicale", "podcast", "beat", "lyrics",
                     "testo", "strofa", "ritornello", "mix", "master", "musica"),
    },
)
# Vault folders scanned for free-floating tasks (besides the project notes).
FREE_TASK_DIRS = ("00-Inbox", "02-Ideas", "03-Areas", "04-Resources")

_OPEN_TASK_RE = re.compile(r"^\s*[-*]\s+\[\s\]\s+(.*\S)\s*$")
# Plain bullet that is NOT a checkbox (open or done). Counts as a task only when
# it sits under a tasky heading. Limit indent so deep sub-bullets are skipped.
_BULLET_RE = re.compile(r"^[ \t]{0,3}[-*]\s+(?!\[[ xX]\])(.*\S)\s*$")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.*\S)\s*$")
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
_NON_TASK = {"nessuno", "nessuna", "none", "n/a", "na", "-", "tbd"}

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


def _rel(path: Path, vault: Path) -> str:
    return str(path.relative_to(vault)).replace("\\", "/")


def _subproject_id(path: str) -> str:
    return re.sub(r"[^\w]+", "-", str(path or "").removesuffix(".md").lower()).strip("-")


def _load_client_status(vault: Path) -> dict[str, list[dict]]:
    """Read optional card client state from Hermes setup config, not frontend JS."""
    root = vault.parent
    path = root / "config" / "command-bridge-clients.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, list[dict]] = {}
    cards = raw.get("cards") if isinstance(raw, dict) else None
    if not isinstance(cards, dict):
        return out
    for family_id, clients in cards.items():
        if not isinstance(clients, list):
            continue
        clean = []
        for item in clients:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            clean.append({
                "name": name[:80],
                "status": str(item.get("status") or "").strip()[:220],
                "waiting_on": str(item.get("waiting_on") or "").strip()[:280],
            })
        out[str(family_id)] = clean
    return out


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


def _looks_like_task(text: str) -> bool:
    t = text.strip().strip("*_`").strip()
    low = t.lower()
    if len(t) < 4 or low in _NON_TASK or low.startswith("nessun"):
        return False
    return True


def _open_tasks_with_flag(body: str) -> list[tuple[str, bool]]:
    """Things to do as (text, under_tasky_heading).

    Captures open ``- [ ]`` checkboxes anywhere, plus plain bullets that sit
    under a tasky heading (where these notes actually write their to-dos).
    """
    out: list[tuple[str, bool]] = []
    section_is_tasky = False
    for line in body.splitlines():
        h = _HEADING_RE.match(line)
        if h:
            title = h.group(1).strip().lower()
            section_is_tasky = any(k in title for k in TASK_SECTION_KEYWORDS)
            continue
        m = _OPEN_TASK_RE.match(line)
        if m:
            out.append((m.group(1).strip(), section_is_tasky))
            continue
        if section_is_tasky:
            b = _BULLET_RE.match(line)
            if b and _looks_like_task(b.group(1)):
                out.append((b.group(1).strip(), True))
    return out


def parse_open_tasks(body: str) -> list[str]:
    """Open tasks, tasky-heading ones first, capped at MAX_TASKS (legacy/flat)."""
    prioritized = [t for t, pri in _open_tasks_with_flag(body) if pri]
    plain = [t for t, pri in _open_tasks_with_flag(body) if not pri]
    return (prioritized + plain)[:MAX_TASKS]


def _family_for_stem(stem: str) -> str | None:
    s = stem.lower()
    for fam in FAMILIES:
        if any(k in s for k in fam["stem_keywords"]):
            return fam["id"]
    return None


def _family_for_text(*texts: str) -> str | None:
    """Attribute a free task to a family by note name + task text keywords.

    Checks the specific families (VisionBuilts, Rap) before Hermes so a generic
    Hermes mention doesn't swallow a clearly-VisionBuilts task.
    """
    blob = " ".join(t.lower() for t in texts if t)
    for fid in ("visionbuilts", "concorso-inps", "rap", "hermes"):
        fam = next(f for f in FAMILIES if f["id"] == fid)
        if any(k in blob for k in fam["keywords"]):
            return fid
    return None


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


def _latest_entries(notes, vault: Path) -> list[dict]:
    ordered = sorted(set(notes), key=_mtime, reverse=True)
    return [{
        "name": p.stem,
        "path": _rel(p, vault),
        "mtime": _mtime(p),
        "rel": _rel_time(_mtime(p)),
    } for p in ordered[:MAX_LATEST]]


def _build_families(vault: Path, project_notes, stem_map) -> list[dict]:
    """Roll the per-note projects up into Command Bridge family cards."""
    members: dict[str, list[Path]] = {f["id"]: [] for f in FAMILIES}
    clients_by_family = _load_client_status(vault)
    for note in project_notes:
        fid = _family_for_stem(note.stem)
        if fid:
            members[fid].append(note)

    # Gather free-floating tasks from Ideas/Areas/Inbox/Resources, attributed
    # to a family by keyword. (text, rel_path, family_id)
    free: list[tuple[str, str, str]] = []
    for d in FREE_TASK_DIRS:
        base = vault / d
        if not base.is_dir():
            continue
        for nf in base.rglob("*.md"):
            body = _read(nf)
            if not body:
                continue
            rel = _rel(nf, vault)
            for text, _pri in _open_tasks_with_flag(body):
                fam = _family_for_text(text, nf.stem, rel)
                if fam:
                    free.append((text, rel, fam))

    families: list[dict] = []
    for fam in FAMILIES:
        fid = fam["id"]
        mlist = members[fid]
        # primary: exact family-name note if present, else most-recent member.
        primary = next((p for p in mlist if p.stem.lower() == fam["name"].lower()), None)
        if primary is None and mlist:
            primary = max(mlist, key=_mtime)
        # children sorted: primary first, then by name.
        children_notes = sorted(
            mlist, key=lambda p: (p is not primary, p.stem.lower())
        )
        children = [
            {"id": _subproject_id(_rel(p, vault)), "name": p.stem, "path": _rel(p, vault)}
            for p in children_notes
        ]

        # Tasks: tasky-heading member tasks, then plain member tasks, then free
        # tasks; dedup by text (case-insensitive); each keeps its source path.
        member_pri: list[tuple[str, str]] = []
        member_plain: list[tuple[str, str]] = []
        for p in children_notes:
            rel = _rel(p, vault)
            for text, pri in _open_tasks_with_flag(_read(p)):
                (member_pri if pri else member_plain).append((text, rel))
        ordered_tasks = member_pri + member_plain + [(t, rp) for t, rp, ff in free if ff == fid]
        seen: set[str] = set()
        tasks: list[dict] = []
        for text, rel in ordered_tasks:
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            tasks.append({
                "text": text,
                "path": rel,
                "subproject_id": _subproject_id(rel),
                "subproject": Path(rel).stem,
            })
            if len(tasks) >= MAX_FAMILY_TASKS:
                break

        # latest + activity across members and the notes they wikilink to.
        related: set[Path] = set(mlist)
        for p in mlist:
            for m in _WIKILINK_RE.finditer(_read(p)):
                tp = stem_map.get(m.group(1).strip().lower())
                if tp:
                    related.add(tp)

        families.append({
            "id": fid,
            "name": fam["name"],
            "primary_path": _rel(primary, vault) if primary else "",
            "children": children,
            "tasks": tasks,
            "latest": _latest_entries(related, vault),
            "activity": _activity_buckets([_mtime(p) for p in related]),
            "priority": int(fam.get("priority") if fam.get("priority") is not None else 1),
            "clients_active": clients_by_family.get(fid, []),
            "study": fam.get("study") or None,
        })
    return families


def build_projects_overview(vault_path) -> dict:
    """Walk 01-Projects -> flat ``projects`` + grouped ``families``. Pure fn."""
    vault = Path(str(vault_path)).expanduser()
    pdir = vault / PROJECTS_DIR
    if not pdir.is_dir():
        return {"families": [], "projects": [], "count": 0, "exists": False}

    stem_map: dict[str, Path] = {}
    for p in vault.rglob("*.md"):
        stem_map.setdefault(p.stem.lower(), p)

    project_notes = sorted(pdir.glob("*.md"), key=lambda p: p.name.lower())
    projects = []
    for note in project_notes:
        body = _read(note)
        related = {note}
        for m in _WIKILINK_RE.finditer(body):
            tp = stem_map.get(m.group(1).strip().lower())
            if tp:
                related.add(tp)
        projects.append({
            "id": re.sub(r"[^\w]+", "-", note.stem).strip("-").lower() or "project",
            "name": note.stem,
            "path": _rel(note, vault),
            "mtime": _mtime(note),
            "tasks": parse_open_tasks(body),
            "latest": _latest_entries(related, vault),
            "activity": _activity_buckets([_mtime(p) for p in related]),
        })

    families = _build_families(vault, project_notes, stem_map)
    return {"families": families, "projects": projects, "count": len(projects), "exists": True}


def get_projects_overview(vault_path) -> dict:
    """Cached build_projects_overview (TTL + vault-dir mtime invalidation)."""
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
