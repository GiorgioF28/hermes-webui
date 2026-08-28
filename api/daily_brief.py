"""Read-only aggregation for the Command Bridge Daily Brief view."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from api.delegation_store import DelegationStore


DEFAULT_CHECK_DM_REPLIES_FILE = Path(
    r"C:\Users\giorg\Documents\Hermes setup\outreach-ig\data\check-dm-replies.json"
)
CHECK_DM_REPLIES_ENV = "HERMES_CHECK_DM_REPLIES_FILE"
_TERMINAL_STATUSES = {"done", "failed", "ok", "errore", "parziale", "interrotta"}


def _timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        try:
            return float(raw)
        except ValueError:
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return None
    return None


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _brief_item(record: dict[str, Any], ts: float) -> dict[str, Any]:
    result = record.get("result") if isinstance(record.get("result"), dict) else {}
    error = record.get("error") if isinstance(record.get("error"), dict) else {}
    outcome = str(result.get("text") or record.get("output") or error.get("message") or "").strip()
    status = str(record.get("status") or "").lower()
    return {
        "id": str(record.get("id") or ""),
        "agent": str(record.get("agent") or record.get("agent_id") or "agente"),
        "task": " ".join(str(record.get("task") or record.get("task_type") or "Delega").split())[:240],
        "outcome": outcome[:4_000],
        "status": "failed" if status in {"failed", "errore", "interrotta"} else "done",
        "timestamp": _iso(ts),
    }


def read_recent_briefs(workspace: Path | str, *, now: float | None = None, days: int = 7) -> list[dict[str, Any]]:
    current = float(now if now is not None else time.time())
    cutoff = current - max(1, days) * 86_400
    rows: list[tuple[float, dict[str, Any]]] = []
    for record in DelegationStore(workspace).get_all():
        if not isinstance(record, dict) or str(record.get("status") or "").lower() not in _TERMINAL_STATUSES:
            continue
        ts = _timestamp(record.get("finished_at") or record.get("finished") or record.get("created_at") or record.get("started"))
        if ts is None or ts < cutoff or ts > current + 300:
            continue
        rows.append((ts, _brief_item(record, ts)))
    rows.sort(key=lambda pair: pair[0], reverse=True)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for ts, item in rows:
        day = datetime.fromtimestamp(ts).astimezone().date().isoformat()
        grouped.setdefault(day, []).append(item)
    return [{"date": day, "items": items} for day, items in grouped.items()]


def read_ig_replies(file_path: Path | str) -> tuple[list[dict[str, Any]], bool, bool]:
    path = Path(file_path)
    if not path.is_file():
        return [], True, False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_rows = payload.get("replies") if isinstance(payload, dict) else payload
        if not isinstance(raw_rows, list):
            raise ValueError("replies must be a list")
        replies = []
        for raw in raw_rows:
            if not isinstance(raw, dict):
                continue
            handle = str(raw.get("handle") or "").strip().lstrip("@").lower()
            text = str(raw.get("text") or "").strip()[:500]
            timestamp = str(raw.get("timestamp") or "").strip()
            if not handle or not timestamp:
                continue
            row = {
                "handle": handle,
                "text": text,
                "timestamp": timestamp,
                "detectedAt": str(raw.get("detectedAt") or ""),
                "notionPageId": str(raw.get("notionPageId") or ""),
            }
            replies.append(row)
        replies.sort(key=lambda row: row["timestamp"], reverse=True)
        latest_by_handle = []
        seen_handles: set[str] = set()
        for row in replies:
            if row["handle"] in seen_handles:
                continue
            seen_handles.add(row["handle"])
            latest_by_handle.append(row)
        return latest_by_handle, False, False
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
        return [], False, True


def build_daily_brief_payload(
    workspace: Path | str,
    *,
    replies_file: Path | str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    configured = replies_file or os.getenv(CHECK_DM_REPLIES_ENV) or DEFAULT_CHECK_DM_REPLIES_FILE
    replies, not_initialized, malformed = read_ig_replies(configured)
    return {
        "ok": True,
        "briefs": read_recent_briefs(workspace, now=now),
        "ig_replies": replies,
        "checkDmNotInitialized": not_initialized,
        "checkDmMalformed": malformed,
    }
