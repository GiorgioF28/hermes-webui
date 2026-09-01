"""Daily Brief storage, aggregation, and cron endpoints.

The browser only receives compact email/Instagram metadata.  Delegation output
is deliberately not part of this module or its payload contract.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import subprocess
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo


logger = logging.getLogger(__name__)

ROME = ZoneInfo("Europe/Rome")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_OUTREACH_DIR = Path(r"C:\Users\giorg\Documents\Hermes setup\outreach-ig")
DEFAULT_CHECK_DM_REPLIES_FILE = DEFAULT_OUTREACH_DIR / "data" / "check-dm-replies.json"
CHECK_DM_REPLIES_ENV = "HERMES_CHECK_DM_REPLIES_FILE"
CRON_TOKEN_ENV = "HERMES_CRON_TOKEN"
MAX_CRON_BODY_BYTES = 1024 * 1024
STALE_AFTER = timedelta(hours=26)
NOISE_RETENTION = timedelta(days=90)
NOISE_ACTIVATION_HITS = 3
_HIGH_SUBJECT_WORDS = (
    "fattura", "pagamento", "scadenza", "contratto", "urgente",
    "invoice", "payment", "overdue",
)
_LOW_SENDER_RE = re.compile(r"(?:noreply|no-reply|newsletter|notifications?@)", re.I)
_CHECK_DM_COUNT_RE = re.compile(r"Risposte nuove:\s*(\d+)", re.I)
_WRITE_LOCK = threading.Lock()


class DailyBriefValidationError(ValueError):
    """Raised when a cron payload does not satisfy the public contract."""


def _now(value: float | datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _today_local(value: datetime) -> date:
    return value.astimezone(ROME).date()


def _count(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _read_json(path: Path) -> tuple[Any, bool, bool]:
    if not path.is_file():
        return None, True, False
    try:
        return json.loads(path.read_text(encoding="utf-8")), False, False
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return None, False, True


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with _WRITE_LOCK:
        temporary.write_text(encoded, encoding="utf-8")
        os.replace(temporary, path)


def read_ig_replies(file_path: Path | str) -> tuple[list[dict[str, Any]], bool, bool]:
    """Read the existing outreach-ig reply artifact without changing its schema."""
    payload, missing, malformed = _read_json(Path(file_path))
    if missing or malformed:
        return [], missing, malformed
    try:
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
            replies.append({
                "handle": handle,
                "text": text,
                "timestamp": timestamp,
                "detectedAt": str(raw.get("detectedAt") or ""),
                "notionPageId": str(raw.get("notionPageId") or ""),
            })
        replies.sort(key=lambda row: row["timestamp"], reverse=True)
        latest_by_handle = []
        seen_handles: set[str] = set()
        for row in replies:
            if row["handle"] in seen_handles:
                continue
            seen_handles.add(row["handle"])
            latest_by_handle.append(row)
        return latest_by_handle, False, False
    except (ValueError, TypeError):
        return [], False, True


def _empty_email() -> dict[str, Any]:
    return {"count": 0, "noiseSkipped": 0, "accounts": [], "items": []}


def read_email_digest(data_dir: Path | str, *, now: float | datetime | None = None) -> tuple[dict[str, Any], bool]:
    """Return today's compact email digest and a malformed flag."""
    current = _now(now)
    payload, missing, malformed = _read_json(Path(data_dir) / "daily-email-digest.json")
    if missing or malformed or not isinstance(payload, dict):
        return _empty_email(), bool(malformed or (payload is not None and not isinstance(payload, dict)))
    raw_emails = payload.get("emails")
    raw_accounts = payload.get("accounts")
    if not isinstance(raw_emails, list) or not isinstance(raw_accounts, list):
        return _empty_email(), True
    items = []
    for raw in raw_emails:
        if not isinstance(raw, dict):
            continue
        received = _parse_datetime(raw.get("receivedAt"))
        if received is None or _today_local(received) != _today_local(current):
            continue
        importance = str(raw.get("importance") or "media").lower()
        if importance not in {"alta", "media", "bassa"}:
            importance = "media"
        account = str(raw.get("account") or "").strip()
        sender = str(raw.get("from") or "").strip()
        if not account or not sender:
            continue
        items.append({
            "account": account[:100],
            "from": sender[:320],
            "fromName": str(raw.get("fromName") or "").strip()[:200],
            "subject": str(raw.get("subject") or "").strip()[:200],
            "receivedAt": _iso(received),
            "importance": importance,
        })
    items.sort(key=lambda row: row["receivedAt"], reverse=True)
    accounts = []
    for raw in raw_accounts:
        if not isinstance(raw, dict):
            continue
        label = str(raw.get("label") or "").strip()[:100]
        if not label:
            continue
        accounts.append({
            "label": label,
            "count": sum(1 for item in items if item["account"] == label),
            "error": str(raw["error"])[:240] if raw.get("error") else None,
        })
    return {
        "count": len(items),
        "noiseSkipped": _count(payload.get("noiseSkipped")),
        "accounts": accounts,
        "items": items,
    }, False


def read_last_run(data_dir: Path | str) -> tuple[str | None, bool]:
    payload, missing, malformed = _read_json(Path(data_dir) / "daily-brief-run.json")
    if missing or malformed or not isinstance(payload, dict):
        return None, bool(malformed or (payload is not None and not isinstance(payload, dict)))
    parsed = _parse_datetime(payload.get("lastRun"))
    return (_iso(parsed), False) if parsed else (None, True)


def build_daily_brief_payload(
    data_dir: Path | str = DEFAULT_DATA_DIR,
    *,
    replies_file: Path | str | None = None,
    now: float | datetime | None = None,
) -> dict[str, Any]:
    current = _now(now)
    configured = replies_file or os.getenv(CHECK_DM_REPLIES_ENV) or DEFAULT_CHECK_DM_REPLIES_FILE
    replies, not_initialized, ig_malformed = read_ig_replies(configured)
    email, _email_malformed = read_email_digest(data_dir, now=current)
    last_run, _run_malformed = read_last_run(data_dir)
    last_dt = _parse_datetime(last_run)
    stale = last_dt is None or current - last_dt > STALE_AFTER
    return {
        "ok": True,
        "generatedAt": _iso(current),
        "lastRun": last_run,
        "stale": stale,
        "email": email,
        "ig": {
            "count": len(replies),
            "items": replies,
            "notInitialized": not_initialized,
            "malformed": ig_malformed,
        },
    }


def _vip_senders(data_dir: Path) -> set[str]:
    payload, missing, malformed = _read_json(data_dir / "email-vip.json")
    if missing or malformed:
        return set()
    raw = payload if isinstance(payload, list) else payload.get("senders", []) if isinstance(payload, dict) else []
    values = []
    for item in raw if isinstance(raw, list) else []:
        values.append(item.get("pattern") if isinstance(item, dict) else item)
    return {str(value).strip().lower() for value in values if str(value or "").strip()}


def classify_importance(email: dict[str, Any], *, vip_senders: set[str] | None = None) -> str:
    sender = str(email.get("from") or "").strip().lower()
    subject = str(email.get("subject") or "").lower()
    if sender in (vip_senders or set()) or any(word in subject for word in _HIGH_SUBJECT_WORDS):
        return "alta"
    if bool(email.get("listUnsubscribe")) or _LOW_SENDER_RE.search(sender):
        return "bassa"
    return "media"


def _noise_payload(data_dir: Path) -> dict[str, Any]:
    payload, _missing, malformed = _read_json(data_dir / "email-noise-list.json")
    if malformed or not isinstance(payload, dict):
        return {"version": 1, "updatedAt": None, "senders": [], "domains": [], "subjectPatterns": []}
    return {
        "version": 1,
        "updatedAt": payload.get("updatedAt"),
        "senders": payload.get("senders") if isinstance(payload.get("senders"), list) else [],
        "domains": payload.get("domains") if isinstance(payload.get("domains"), list) else [],
        "subjectPatterns": payload.get("subjectPatterns") if isinstance(payload.get("subjectPatterns"), list) else [],
    }


def _active_noise_patterns(rows: list[Any]) -> set[str]:
    result = set()
    for row in rows:
        if not isinstance(row, dict) or _count(row.get("hits")) < NOISE_ACTIVATION_HITS:
            continue
        pattern = str(row.get("pattern") or "").strip().lower()
        if pattern:
            result.add(pattern)
    return result


def matches_noise(email: dict[str, Any], noise: dict[str, Any]) -> bool:
    sender = str(email.get("from") or "").strip().lower()
    domain = sender.rsplit("@", 1)[-1] if "@" in sender else ""
    subject = str(email.get("subject") or "").lower()
    senders = _active_noise_patterns(noise.get("senders", []))
    domains = _active_noise_patterns(noise.get("domains", []))
    subjects = _active_noise_patterns(noise.get("subjectPatterns", []))
    return sender in senders or domain in domains or any(pattern in subject for pattern in subjects)


def _noise_entry(value: Any) -> str:
    raw = value.get("pattern") if isinstance(value, dict) else value
    return str(raw or "").strip().lower()[:320]


def merge_noise_list(
    data_dir: Path | str,
    additions: dict[str, Any],
    *,
    now: float | datetime | None = None,
    consecutive: bool = False,
) -> dict[str, Any]:
    base_dir = Path(data_dir)
    current = _now(now)
    noise = _noise_payload(base_dir)
    cutoff = current - NOISE_RETENTION
    yesterday = _today_local(current - timedelta(days=1))
    for key in ("senders", "domains", "subjectPatterns"):
        raw_additions = additions.get(key, [])
        if not isinstance(raw_additions, list):
            raise DailyBriefValidationError(f"{key} must be a list")
        kept: dict[str, dict[str, Any]] = {}
        for raw in noise.get(key, []):
            if not isinstance(raw, dict):
                continue
            pattern = _noise_entry(raw)
            last_seen = _parse_datetime(raw.get("lastSeen"))
            if not pattern or last_seen is None or last_seen < cutoff:
                continue
            kept[pattern] = {
                "pattern": pattern,
                "hits": _count(raw.get("hits")),
                "lastSeen": _iso(last_seen) if last_seen else None,
            }
        seen_additions: set[str] = set()
        for raw in raw_additions:
            pattern = _noise_entry(raw)
            if not pattern or pattern in seen_additions:
                continue
            seen_additions.add(pattern)
            entry = kept.get(pattern, {"pattern": pattern, "hits": 0, "lastSeen": None})
            last_seen = _parse_datetime(entry.get("lastSeen"))
            if consecutive and last_seen and _today_local(last_seen) != yesterday:
                entry["hits"] = 0
            entry["hits"] = _count(entry.get("hits")) + 1
            entry["lastSeen"] = _iso(current)
            kept[pattern] = entry
        noise[key] = sorted(kept.values(), key=lambda row: row["pattern"])
    noise["updatedAt"] = _iso(current)
    _atomic_write_json(base_dir / "email-noise-list.json", noise)
    return noise


def _normalized_accounts(raw_accounts: Any, emails: list[dict[str, Any]]) -> list[dict[str, Any]]:
    accounts: dict[str, dict[str, Any]] = {}
    if isinstance(raw_accounts, list):
        for raw in raw_accounts:
            if not isinstance(raw, dict):
                continue
            label = str(raw.get("label") or "").strip()[:100]
            if label:
                accounts[label] = {"label": label, "count": 0, "error": str(raw["error"])[:240] if raw.get("error") else None}
    for email in emails:
        label = email["account"]
        accounts.setdefault(label, {"label": label, "count": 0, "error": None})["count"] += 1
    return list(accounts.values())


def ingest_email_digest(
    data_dir: Path | str,
    body: dict[str, Any],
    *,
    now: float | datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(body, dict) or not isinstance(body.get("emails"), list):
        raise DailyBriefValidationError("emails must be a list")
    current = _now(now)
    base_dir = Path(data_dir)
    today = _today_local(current)
    noise = _noise_payload(base_dir)
    vip = _vip_senders(base_dir)
    stored: list[dict[str, Any]] = []
    low_senders: set[str] = set()
    dropped_old = 0
    noise_dropped = 0
    for index, raw in enumerate(body["emails"]):
        if not isinstance(raw, dict):
            raise DailyBriefValidationError(f"emails[{index}] must be an object")
        missing = [field for field in ("account", "from", "subject", "receivedAt") if not isinstance(raw.get(field), str) or not raw[field].strip()]
        if missing:
            raise DailyBriefValidationError(f"emails[{index}] missing: {', '.join(missing)}")
        received = _parse_datetime(raw["receivedAt"])
        if received is None:
            raise DailyBriefValidationError(f"emails[{index}].receivedAt invalid")
        if _today_local(received) != today:
            dropped_old += 1
            continue
        normalized = {
            "account": raw["account"].strip()[:100],
            "from": raw["from"].strip()[:320],
            "fromName": str(raw.get("fromName") or "").strip()[:200],
            "subject": raw["subject"].strip()[:200],
            "receivedAt": _iso(received),
        }
        if matches_noise(normalized, noise):
            noise_dropped += 1
            continue
        normalized["importance"] = classify_importance(raw, vip_senders=vip)
        if normalized["importance"] == "bassa" and normalized["from"].lower() not in vip:
            low_senders.add(normalized["from"].lower())
        stored.append(normalized)
    if low_senders:
        merge_noise_list(base_dir, {"senders": sorted(low_senders), "domains": [], "subjectPatterns": []}, now=current, consecutive=True)
    input_noise = _count(body.get("noiseSkipped"))
    digest = {
        "version": 1,
        "generatedAt": _iso(current),
        "accounts": _normalized_accounts(body.get("accounts"), stored),
        "noiseSkipped": input_noise + noise_dropped,
        "emails": stored,
    }
    _atomic_write_json(base_dir / "daily-email-digest.json", digest)
    update_run_status(base_dir, now=current, last_error=None)
    logger.info(
        "daily_brief_email_ingest stored=%d noiseSkipped=%d droppedOld=%d",
        len(stored), input_noise + noise_dropped, dropped_old,
    )
    return {"ok": True, "stored": len(stored), "skipped": dropped_old + noise_dropped}


def update_run_status(
    data_dir: Path | str,
    *,
    now: float | datetime | None = None,
    last_error: str | None,
) -> None:
    _atomic_write_json(Path(data_dir) / "daily-brief-run.json", {
        "lastRun": _iso(_now(now)),
        "lastError": str(last_error)[:240] if last_error else None,
        "source": "n8n",
    })


def run_check_dm(
    data_dir: Path | str = DEFAULT_DATA_DIR,
    *,
    outreach_dir: Path | str = DEFAULT_OUTREACH_DIR,
    now: float | datetime | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    try:
        completed = runner(
            ["node", "src/check-dm.js"],
            cwd=str(Path(outreach_dir)),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        exit_code = int(completed.returncode)
        match = _CHECK_DM_COUNT_RE.search(str(completed.stdout or ""))
        replies_found = int(match.group(1)) if match else None
        ok = exit_code == 0
        reason = None if ok else "check-dm non completato"
    except subprocess.TimeoutExpired:
        exit_code, replies_found, ok, reason = -1, None, False, "check-dm timeout"
    except (OSError, ValueError):
        exit_code, replies_found, ok, reason = -1, None, False, "check-dm non avviabile"
    update_run_status(data_dir, now=now, last_error=reason)
    result = {"ok": ok, "exitCode": exit_code, "repliesFound": replies_found}
    if reason:
        result["reason"] = reason
    return result


def _cron_authorized(handler: Any) -> bool:
    expected = os.getenv(CRON_TOKEN_ENV, "").strip()
    provided = str(handler.headers.get("X-Hermes-Cron-Token") or "")
    return bool(expected and provided and hmac.compare_digest(provided, expected))


def _read_cron_body(handler: Any) -> dict[str, Any]:
    raw_length = handler.headers.get("Content-Length", 0)
    try:
        length = int(raw_length)
    except (TypeError, ValueError) as exc:
        raise DailyBriefValidationError("invalid content length") from exc
    if length < 0 or length > MAX_CRON_BODY_BYTES:
        raise DailyBriefValidationError("invalid content length")
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DailyBriefValidationError("invalid json") from exc
    if not isinstance(payload, dict):
        raise DailyBriefValidationError("body must be an object")
    return payload


def handle_cron_daily_brief(handler: Any, path: str, *, data_dir: Path | str = DEFAULT_DATA_DIR) -> bool:
    """Handle machine-only Daily Brief routes before browser auth/CSRF."""
    from api.helpers import j

    if not _cron_authorized(handler):
        j(handler, {"ok": False, "error": "forbidden"}, status=403)
        return True
    try:
        if path == "/api/cron/daily-brief/email":
            result = ingest_email_digest(data_dir, _read_cron_body(handler))
        elif path == "/api/cron/daily-brief/noise":
            result = merge_noise_list(data_dir, _read_cron_body(handler))
            result = {"ok": True, "senders": len(result["senders"]), "domains": len(result["domains"]), "subjectPatterns": len(result["subjectPatterns"])}
        elif path == "/api/cron/daily-brief/check-dm":
            result = run_check_dm(data_dir)
        else:
            return False
    except DailyBriefValidationError as exc:
        j(handler, {"ok": False, "error": str(exc)}, status=422)
        return True
    except Exception:
        logger.exception("daily brief cron endpoint failed path=%s", path)
        j(handler, {"ok": False, "error": "daily_brief_internal_error"}, status=500)
        return True
    j(handler, result, status=200)
    return True
