"""Persistent daily project checklist for the Command Bridge.

Definitions and date-scoped ad-hoc tasks live in ``daily_checklist.json``;
completion history lives separately in ``daily_checklist_log.json``.  History
entries retain item snapshots so renamed or deleted tasks remain readable.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from api.config import STATE_DIR


_LOCK = threading.RLock()
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_TEXT = 200

_SEED = {
    "projects": [
        {
            "id": "visionbuilts-clienti",
            "name": "VisionBuilts clienti",
            "active": True,
            "dailies": [
                {"id": "vb-outreach", "text": "Far girare il workflow outreach influencer (scan + DM bozza)", "active": True},
                {"id": "vb-dm-console", "text": "Rivedere e inviare i DM in console VisionBuilts", "active": True},
                {"id": "vb-ebook-step", "text": "Avanzare di 1 step il workflow ebook", "active": True},
            ],
        },
        {
            "id": "ebook-amazon",
            "name": "Ebook cucina mio su Amazon",
            "active": True,
            "dailies": [
                {"id": "amazon-top-seller", "text": "Analizzare 3 top seller Amazon della nicchia", "active": True},
                {"id": "amazon-content", "text": "Produrre/validare contenuto ebook (ricette o foto AI)", "active": True},
            ],
        },
        {
            "id": "shaker-levelup",
            "name": "Macchinette shaker proteici",
            "active": True,
            "dailies": [
                {"id": "shaker-plan", "text": "Avanzare di 1 step il piano macchinette (fornitori, costi, azienda)", "active": True},
                {"id": "shaker-alessio", "text": "Follow-up o preparazione messaggio ad Alessio", "active": True},
            ],
        },
        {
            "id": "studio-trading",
            "name": "Studio trading (YouTube, Luca Boaretto)",
            "active": True,
            "dailies": [
                {"id": "trading-video", "text": "Guardare/studiare 1 video LuckyBearLuke e prendere appunti", "active": True},
                {"id": "trading-chart", "text": "Applicare o ripassare 1 concetto su grafico", "active": True},
            ],
        },
    ],
    "adhoc": [],
}


def _local_today() -> date:
    return date.today()


def _local_now() -> datetime:
    return datetime.now().astimezone()


def _validate_date(value: str | None, today_fn=_local_today) -> date:
    if value in (None, ""):
        return today_fn()
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        raise ValueError("date deve essere nel formato YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("date non valida") from exc
    if parsed.isoformat() != value:
        raise ValueError("date deve essere nel formato YYYY-MM-DD")
    return parsed


def _clean_text(value, field="text") -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} deve essere testo")
    cleaned = " ".join(value.replace("\x00", " ").split()).strip()
    if not cleaned:
        raise ValueError(f"{field} e' obbligatorio")
    if len(cleaned) > _MAX_TEXT:
        raise ValueError(f"{field} supera {_MAX_TEXT} caratteri")
    return cleaned


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return json.loads(json.dumps(default))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Storage checklist non leggibile: {path.name}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Storage checklist non valido: {path.name}")
    return value


class DailyChecklistStore:
    def __init__(self, data_dir: Path | str = STATE_DIR, *, today_fn=_local_today, now_fn=_local_now):
        self.data_dir = Path(data_dir)
        self.definitions_path = self.data_dir / "daily_checklist.json"
        self.log_path = self.data_dir / "daily_checklist_log.json"
        self.today_fn = today_fn
        self.now_fn = now_fn

    def _definitions(self) -> dict:
        if not self.definitions_path.exists():
            _atomic_write(self.definitions_path, _SEED)
        data = _read_json(self.definitions_path, _SEED)
        data.setdefault("projects", [])
        data.setdefault("adhoc", [])
        return data

    def _log(self) -> dict:
        return _read_json(self.log_path, {})

    @staticmethod
    def _active_projects(definitions: dict) -> list[dict]:
        return [p for p in definitions.get("projects", []) if p.get("active", True)]

    @staticmethod
    def _project_map(definitions: dict) -> dict[str, dict]:
        return {str(p.get("id")): p for p in definitions.get("projects", [])}

    def _items_for(self, definitions: dict, day: date) -> dict[str, dict]:
        items: dict[str, dict] = {}
        for project in self._active_projects(definitions):
            project_id = str(project.get("id") or "")
            for daily in project.get("dailies", []):
                if daily.get("active", True):
                    item = dict(daily)
                    item.update({"project_id": project_id, "adhoc": False})
                    items[str(item.get("id"))] = item
        for adhoc in definitions.get("adhoc", []):
            if adhoc.get("date") == day.isoformat():
                item = dict(adhoc)
                item["adhoc"] = True
                items[str(item.get("id"))] = item
        return items

    @staticmethod
    def _completed(log: dict, day: date) -> set[str]:
        row = log.get(day.isoformat()) or {}
        return {str(item_id) for item_id in row.get("completed", [])}

    def _expected_by_project(self, definitions: dict) -> dict[str, set[str]]:
        result = {}
        for project in self._active_projects(definitions):
            ids = {
                str(d.get("id"))
                for d in project.get("dailies", [])
                if d.get("active", True) and d.get("id")
            }
            result[str(project.get("id"))] = ids
        return result

    def _is_complete(self, log: dict, day: date, expected: set[str]) -> bool:
        return bool(expected) and expected.issubset(self._completed(log, day))

    def _streak_pair(self, log: dict, expected: set[str], today: date) -> dict:
        anchor = today if self._is_complete(log, today, expected) else today - timedelta(days=1)
        current = 0
        cursor = anchor
        while self._is_complete(log, cursor, expected):
            current += 1
            cursor -= timedelta(days=1)

        dated_rows = []
        for raw_day in log:
            try:
                parsed = date.fromisoformat(raw_day)
            except (TypeError, ValueError):
                continue
            if parsed <= today:
                dated_rows.append(parsed)
        record = 0
        run = 0
        if dated_rows:
            cursor = min(dated_rows)
            while cursor <= today:
                if self._is_complete(log, cursor, expected):
                    run += 1
                    record = max(record, run)
                else:
                    run = 0
                cursor += timedelta(days=1)
        return {"current": current, "record": record}

    def streaks(self, definitions: dict | None = None, log: dict | None = None, today: date | None = None) -> dict:
        with _LOCK:
            definitions = definitions or self._definitions()
            log = log or self._log()
            today = today or self.today_fn()
            expected = self._expected_by_project(definitions)
            projects = {project_id: self._streak_pair(log, ids, today) for project_id, ids in expected.items()}
            global_ids = set().union(*expected.values()) if expected else set()
            return {"projects": projects, "global": self._streak_pair(log, global_ids, today)}

    def _history(self, definitions: dict, log: dict, limit: int = 14) -> list[dict]:
        project_map = self._project_map(definitions)
        known = self._items_for(definitions, self.today_fn())
        for adhoc in definitions.get("adhoc", []):
            known[str(adhoc.get("id"))] = dict(adhoc)
        rows = []
        for raw_day in sorted(log, reverse=True):
            row = log.get(raw_day) or {}
            snapshots = row.get("items") or {}
            items = []
            for item_id in row.get("completed", []):
                snapshot = snapshots.get(str(item_id)) or known.get(str(item_id)) or {}
                project_id = str(snapshot.get("project_id") or "")
                project = project_map.get(project_id) or {}
                items.append({
                    "id": str(item_id),
                    "text": str(snapshot.get("text") or item_id),
                    "project_id": project_id,
                    "project_name": str(snapshot.get("project_name") or project.get("name") or project_id or "Altro"),
                })
            if items:
                rows.append({"date": raw_day, "items": items})
            if len(rows) >= limit:
                break
        return rows

    def get_state(self, day_value: str | None = None) -> dict:
        day = _validate_date(day_value, self.today_fn)
        with _LOCK:
            definitions = self._definitions()
            log = self._log()
            completed = self._completed(log, day)
            all_items = self._items_for(definitions, day)
            streak_data = self.streaks(definitions, log, self.today_fn())
            projects = []
            done_total = 0
            item_total = 0
            for project in self._active_projects(definitions):
                project_id = str(project.get("id"))
                items = []
                for item in all_items.values():
                    if item.get("project_id") != project_id:
                        continue
                    rendered = {
                        "id": str(item.get("id")),
                        "text": str(item.get("text") or ""),
                        "adhoc": bool(item.get("adhoc")),
                        "done": str(item.get("id")) in completed,
                    }
                    items.append(rendered)
                done = sum(1 for item in items if item["done"])
                done_total += done
                item_total += len(items)
                projects.append({
                    "id": project_id,
                    "name": str(project.get("name") or project_id),
                    "items": items,
                    "done": done,
                    "total": len(items),
                    "streak": streak_data["projects"].get(project_id, {"current": 0, "record": 0}),
                })
            return {
                "ok": True,
                "date": day.isoformat(),
                "projects": projects,
                "done": done_total,
                "total": item_total,
                "streaks": streak_data,
                "history": self._history(definitions, log),
            }

    def toggle(self, item_id, day_value=None, done=True) -> dict:
        item_id = str(item_id or "").strip()
        if not item_id:
            raise ValueError("id e' obbligatorio")
        if not isinstance(done, bool):
            raise ValueError("done deve essere true o false")
        day = _validate_date(day_value, self.today_fn)
        with _LOCK:
            definitions = self._definitions()
            items = self._items_for(definitions, day)
            if item_id not in items:
                raise ValueError("daily non trovata per la data richiesta")
            log = self._log()
            row = log.setdefault(day.isoformat(), {"completed": [], "items": {}})
            completed = {str(value) for value in row.get("completed", [])}
            snapshots = row.setdefault("items", {})
            if done:
                completed.add(item_id)
                item = items[item_id]
                project = self._project_map(definitions).get(str(item.get("project_id"))) or {}
                snapshots[item_id] = {
                    "project_id": str(item.get("project_id") or ""),
                    "project_name": str(project.get("name") or item.get("project_id") or ""),
                    "text": str(item.get("text") or ""),
                }
            else:
                completed.discard(item_id)
                snapshots.pop(item_id, None)
            row["completed"] = sorted(completed)
            row["updated_at"] = self.now_fn().isoformat(timespec="seconds")
            _atomic_write(self.log_path, log)
        return self.get_state(day.isoformat())

    def add_adhoc(self, project_id, text, day_value=None) -> dict:
        project_id = str(project_id or "").strip()
        text = _clean_text(text)
        day = _validate_date(day_value, self.today_fn)
        with _LOCK:
            definitions = self._definitions()
            project = self._project_map(definitions).get(project_id)
            if not project or not project.get("active", True):
                raise ValueError("project_id non valido o non attivo")
            definitions["adhoc"].append({
                "id": f"adhoc-{uuid.uuid4().hex}",
                "date": day.isoformat(),
                "project_id": project_id,
                "text": text,
            })
            _atomic_write(self.definitions_path, definitions)
        return self.get_state(day.isoformat())

    def delete_adhoc(self, item_id) -> dict:
        item_id = str(item_id or "").strip()
        if not item_id:
            raise ValueError("id ad-hoc obbligatorio")
        with _LOCK:
            definitions = self._definitions()
            match = next((item for item in definitions.get("adhoc", []) if str(item.get("id")) == item_id), None)
            if not match:
                raise ValueError("task ad-hoc non trovata")
            definitions["adhoc"] = [item for item in definitions["adhoc"] if str(item.get("id")) != item_id]
            _atomic_write(self.definitions_path, definitions)
        return self.get_state(str(match.get("date") or ""))

    def configure(self, payload: dict) -> dict:
        """Add/update/deactivate recurring projects and dailies via API."""
        action = str(payload.get("action") or "").strip()
        with _LOCK:
            definitions = self._definitions()
            projects = self._project_map(definitions)
            project_id = str(payload.get("project_id") or "").strip()
            project = projects.get(project_id)
            if action == "add_daily":
                if not project:
                    raise ValueError("project_id non valido")
                daily = {"id": f"daily-{uuid.uuid4().hex}", "text": _clean_text(payload.get("text")), "active": True}
                project.setdefault("dailies", []).append(daily)
            elif action == "update_daily":
                if not project:
                    raise ValueError("project_id non valido")
                daily_id = str(payload.get("id") or "").strip()
                daily = next((item for item in project.get("dailies", []) if str(item.get("id")) == daily_id), None)
                if not daily:
                    raise ValueError("daily non trovata")
                if "text" in payload:
                    daily["text"] = _clean_text(payload.get("text"))
                if "active" in payload:
                    if not isinstance(payload["active"], bool):
                        raise ValueError("active deve essere true o false")
                    daily["active"] = payload["active"]
            elif action == "update_project":
                if not project:
                    raise ValueError("project_id non valido")
                if "name" in payload:
                    project["name"] = _clean_text(payload.get("name"), "name")
                if "active" in payload:
                    if not isinstance(payload["active"], bool):
                        raise ValueError("active deve essere true o false")
                    project["active"] = payload["active"]
            else:
                raise ValueError("action non valida")
            _atomic_write(self.definitions_path, definitions)
        return self.get_state()


def get_store() -> DailyChecklistStore:
    return DailyChecklistStore(STATE_DIR)
