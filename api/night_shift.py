"""Offline Hermes Night Shift packet, plan writing, and daily archiving.

This module is intentionally independent from the running WebUI server. It reads
workspace files, prepares JSON-safe context for a planner, and writes archived
daily artifacts without deleting source notes.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from api import projects_overview, worklog

_OPEN_TASK_RE = re.compile(r"^\s*[-*]\s+\[\s\]\s+(.*\S)\s*$")
_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_FIELD_RE = re.compile(r"^\s*(next_action|prossima azione|blocchi|blocks?)\s*:\s*(.+?)\s*$", re.I)
_HEADING_RE = re.compile(r"^#{1,6}\s+(.*\S)\s*$")
_MAX_FILE_BYTES = 1_000_000
_MEMORY_DIRS = ("obsidian-vault", "tasks", "docs", "projects")
_SENSITIVE_NAMES = {"auth.json", ".env", ".env.local", ".env.docker"}
_SENSITIVE_FRAGMENTS = ("secret", "token", "password", "credential", "apikey", "api-key")


def _workspace(path) -> Path:
    return Path(str(path)).expanduser().resolve()


def _default_day() -> date:
    return date.today() - timedelta(days=1)


def _parse_day(value: str | date | None) -> date:
    if value is None:
        return _default_day()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _read_text(path: Path) -> str:
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return path.name


def _is_sensitive(path: Path) -> bool:
    name = path.name.lower()
    if name in _SENSITIVE_NAMES:
        return True
    return any(fragment in name for fragment in _SENSITIVE_FRAGMENTS)


def _date_from_mtime(path: Path) -> date | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).date()
    except OSError:
        return None


def _file_record(path: Path, root: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": _rel(path, root),
        "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        "size": stat.st_size,
    }


def _edited_memory_files(workspace: Path, day: date) -> list[dict[str, Any]]:
    edited: list[dict[str, Any]] = []
    for dirname in _MEMORY_DIRS:
        root = workspace / dirname
        if not root.exists():
            continue
        for path in sorted(root.rglob("*"), key=lambda p: str(p).lower()):
            if not path.is_file() or _is_sensitive(path):
                continue
            if _date_from_mtime(path) == day:
                edited.append(_file_record(path, workspace))
    return edited


def _parse_open_tasks(path: Path, workspace: Path, project: str | None = None) -> list[dict[str, str]]:
    body = _read_text(path)
    if not body:
        return []
    tasks: list[dict[str, str]] = []
    for line in body.splitlines():
        match = _OPEN_TASK_RE.match(line)
        if not match:
            continue
        text = match.group(1).strip()
        tasks.append({
            "project": project or worklog.project_for(f"{text} {_rel(path, workspace)}"),
            "task": text,
            "source": _rel(path, workspace),
        })
    return tasks


def _open_tasks_by_project(workspace: Path) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    paths: list[tuple[Path, str | None]] = []
    today = workspace / "tasks" / "today.md"
    if today.exists():
        paths.append((today, None))
    projects_dir = workspace / "obsidian-vault" / "01-Projects"
    if projects_dir.is_dir():
        paths.extend((p, p.stem) for p in sorted(projects_dir.glob("*.md"), key=lambda item: item.name.lower()))
    for path, project in paths:
        for task in _parse_open_tasks(path, workspace, project):
            grouped.setdefault(task["project"], []).append(task)
    return dict(sorted(grouped.items(), key=lambda item: item[0].lower()))


def _csv_records(path: Path) -> list[dict[str, str]]:
    if not path.exists() or _is_sensitive(path):
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            return [
                {str(k or "").strip(): str(v or "").strip() for k, v in row.items()}
                for row in reader
            ]
    except (OSError, csv.Error, UnicodeDecodeError):
        return []


def _section_lines(body: str, keywords: tuple[str, ...], limit: int = 6) -> list[str]:
    capture = False
    lines: list[str] = []
    for line in body.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            title = heading.group(1).strip().lower()
            capture = any(keyword in title for keyword in keywords)
            continue
        if capture and line.strip():
            lines.append(line.strip())
            if len(lines) >= limit:
                break
    return lines


def _project_note_state(workspace: Path) -> list[dict[str, Any]]:
    projects_dir = workspace / "obsidian-vault" / "01-Projects"
    if not projects_dir.is_dir():
        return []
    states: list[dict[str, Any]] = []
    for note in sorted(projects_dir.glob("*.md"), key=lambda p: p.name.lower()):
        body = _read_text(note)
        fields: dict[str, list[str]] = {"next_action": [], "blocks": []}
        for line in body.splitlines():
            match = _FIELD_RE.match(line)
            if not match:
                continue
            key = match.group(1).lower()
            dest = "next_action" if "next" in key or "prossima" in key else "blocks"
            fields[dest].append(match.group(2).strip())
        if not fields["next_action"]:
            fields["next_action"] = _section_lines(body, ("next", "prossima", "azione"), limit=3)
        if not fields["blocks"]:
            fields["blocks"] = _section_lines(body, ("block", "blocchi", "blocked"), limit=3)
        states.append({
            "project": note.stem,
            "path": _rel(note, workspace),
            "next_action": fields["next_action"][:3],
            "blocks": fields["blocks"][:3],
            "open_tasks": projects_overview.parse_open_tasks(body),
        })
    return states


def _worklog_for_day(workspace: Path, day: date) -> dict[str, Any]:
    data = worklog.build_worklog(workspace, Path(__file__).resolve().parent.parent, days=14)
    day_s = day.isoformat()
    recent = [item for item in data.get("recent", []) if item.get("date") == day_s]
    totals: dict[str, int] = {}
    for item in recent:
        project = str(item.get("project") or "Altro")
        totals[project] = totals.get(project, 0) + 1
    return {
        "date": day_s,
        "count": len(recent),
        "totals_by_project": dict(sorted(totals.items())),
        "events": recent,
        "source_summary": data,
    }


def build_briefing_packet(workspace, day: str | date | None = None) -> dict[str, Any]:
    """Build the JSON-safe Night Shift briefing packet for ``day``.

    ``day`` defaults to yesterday. The function only reads workspace files and
    git history; it does not call an LLM and does not mutate files.
    """
    root = _workspace(workspace)
    target_day = _parse_day(day)
    vault = root / "obsidian-vault"
    projects = projects_overview.build_projects_overview(vault)
    return {
        "ok": True,
        "workspace": str(root),
        "day": target_day.isoformat(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "worklog": _worklog_for_day(root, target_day),
        "edited_memory_files": _edited_memory_files(root, target_day),
        "open_tasks_by_project": _open_tasks_by_project(root),
        "projects": {
            "inventory": _csv_records(root / "projects" / "project-inventory.csv"),
            "active_repos": _csv_records(root / "projects" / "active-repos.csv"),
            "notes": _project_note_state(root),
            "overview": projects,
        },
    }


def _archive_path_for_today(workspace: Path, previous_body: str, today_path: Path) -> Path:
    match = _DATE_RE.search(previous_body)
    if match:
        stamp = match.group(1)
    else:
        mtime_day = _date_from_mtime(today_path) or _default_day()
        stamp = mtime_day.isoformat()
    archive_dir = workspace / "obsidian-vault" / "05-Daily" / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    candidate = archive_dir / f"today-{stamp}.md"
    if not candidate.exists():
        return candidate
    suffix = 2
    while True:
        next_candidate = archive_dir / f"today-{stamp}-{suffix}.md"
        if not next_candidate.exists():
            return next_candidate
        suffix += 1


def _as_list(value) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _task_text(task) -> str:
    if isinstance(task, dict):
        text = task.get("text") or task.get("task") or task.get("title") or ""
        priority = task.get("priority")
        if priority:
            return f"{text} #{priority}".strip()
        return str(text).strip()
    return str(task).strip()


def render_today_plan(plan: dict[str, Any]) -> str:
    """Render a planner JSON payload into ``tasks/today.md`` markdown."""
    plan_date = str(plan.get("date") or date.today().isoformat())
    lines = [f"# Today - {plan_date}", ""]
    priorities = [_task_text(item) for item in _as_list(plan.get("priorities") or plan.get("daily_priorities"))]
    if priorities:
        lines.extend(["## Priorita del giorno", *[f"- [ ] {item}" for item in priorities if item], ""])
    routine = _as_list(plan.get("routine") or plan.get("timeboxed_routine") or plan.get("time_boxes"))
    if routine:
        lines.append("## Routine time-boxed")
        for block in routine:
            if isinstance(block, dict):
                label = block.get("time") or block.get("slot") or block.get("window") or "Blocco"
                title = block.get("title") or block.get("focus") or block.get("goal") or ""
                lines.append(f"- {label}: {title}".rstrip())
                for task in _as_list(block.get("tasks")):
                    text = _task_text(task)
                    if text:
                        lines.append(f"  - [ ] {text}")
            else:
                lines.append(f"- {str(block).strip()}")
        lines.append("")
    projects = _as_list(plan.get("projects") or plan.get("project_tasks"))
    if projects:
        lines.append("## Task per progetto")
        for project in projects:
            if isinstance(project, dict):
                name = project.get("project") or project.get("name") or "Altro"
                lines.extend(["", f"### {name}"])
                if project.get("next_action"):
                    lines.append(f"Prossima azione: {project['next_action']}")
                if project.get("blocks") or project.get("blockers"):
                    blockers = "; ".join(str(x) for x in _as_list(project.get("blocks") or project.get("blockers")))
                    lines.append(f"Blocchi: {blockers}")
                for task in _as_list(project.get("tasks")):
                    text = _task_text(task)
                    if text:
                        lines.append(f"- [ ] {text}")
            else:
                lines.extend(["", f"### {str(project).strip()}"])
        lines.append("")
    notes = _as_list(plan.get("notes") or plan.get("operational_notes"))
    if notes:
        lines.extend(["## Note operative", *[f"- {str(note).strip()}" for note in notes if str(note).strip()], ""])
    return "\n".join(lines).rstrip() + "\n"


def write_today_plan(workspace, plan: dict[str, Any]) -> dict[str, Any]:
    """Archive the previous ``tasks/today.md`` before writing the new plan."""
    root = _workspace(workspace)
    today_path = root / "tasks" / "today.md"
    today_path.parent.mkdir(parents=True, exist_ok=True)
    archived_to = None
    if today_path.exists():
        previous = _read_text(today_path)
        archive_path = _archive_path_for_today(root, previous, today_path)
        archive_path.write_text(previous, encoding="utf-8")
        archived_to = _rel(archive_path, root)
    body = render_today_plan(plan)
    today_path.write_text(body, encoding="utf-8")
    return {"ok": True, "path": _rel(today_path, root), "archived_to": archived_to}


def archive_worklog_detail(workspace, day: str | date | None = None) -> dict[str, Any]:
    """Append yesterday's raw worklog detail to ``05-Daily/<date>.md``."""
    root = _workspace(workspace)
    target_day = _parse_day(day)
    day_s = target_day.isoformat()
    daily_dir = root / "obsidian-vault" / "05-Daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    path = daily_dir / f"{day_s}.md"
    data = _worklog_for_day(root, target_day)
    section = [
        f"## Night Shift worklog detail - {day_s}",
        "",
        f"Totale eventi: {data['count']}",
        "",
    ]
    for event in data.get("events", []):
        section.append(
            f"- [{event.get('project', 'Altro')}] {event.get('type', 'evento')}: "
            f"{event.get('summary', '')} ({event.get('source', '')})"
        )
    section.append("")
    existing = _read_text(path) if path.exists() else f"# Daily - {day_s}\n\n"
    marker = section[0]
    if marker in existing:
        return {"ok": True, "path": _rel(path, root), "changed": False}
    path.write_text(existing.rstrip() + "\n\n" + "\n".join(section), encoding="utf-8")
    return {"ok": True, "path": _rel(path, root), "changed": True}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hermes Night Shift offline utilities")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_packet = sub.add_parser("packet")
    p_packet.add_argument("--workspace", required=True)
    p_packet.add_argument("--day")
    p_apply = sub.add_parser("apply-plan")
    p_apply.add_argument("--workspace", required=True)
    p_apply.add_argument("--plan", required=True)
    p_apply.add_argument("--day")
    args = parser.parse_args(argv)
    if args.cmd == "packet":
        print(json.dumps(build_briefing_packet(args.workspace, args.day), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "apply-plan":
        plan = _load_json(Path(args.plan))
        result = {
            "today": write_today_plan(args.workspace, plan),
            "worklog_archive": archive_worklog_detail(args.workspace, args.day),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
