"""Authenticated asynchronous endpoint for VisionBuilts email intake."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from datetime import datetime

from api.config import STATE_DIR
from api.helpers import j
from api.intake_agent import extract_event
from api.intake_store import IntakeQueueFullError, IntakeStore
from api.notion_crm import NotionCRM, NotionIdentityConflict, NotionSchemaError


logger = logging.getLogger(__name__)
MAX_BODY_BYTES = 64 * 1024
MAX_QUEUE = 100
RATE_LIMIT = 30
RATE_WINDOW = 60.0
_ALLOWED_TOP = {
    "schema_version", "event_id", "channel", "event_type", "occurred_at", "received_at",
    "source", "sender", "message", "hints",
}
_ALLOWED_SOURCE = {"provider", "account", "external_message_id", "external_thread_id", "workflow"}
_ALLOWED_SENDER = {"external_id", "name", "email", "handle", "profile_url"}
_ALLOWED_MESSAGE = {
    "subject", "text", "envelope_from", "reply_to", "to", "content_sha256", "attachments",
}
_ALLOWED_HINTS = {"visionbuilts_form", "language"}
_ALLOWED_ATTACHMENT = {"name", "mime_type", "bytes"}
_RATE_LOCK = threading.Lock()
_RATE_BUCKETS: dict[str, deque[float]] = defaultdict(deque)


class EventValidationError(ValueError):
    def __init__(self, errors: list[dict]):
        self.errors = errors
        super().__init__("invalid_event")


def _iso_utc(value) -> bool:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.tzinfo is not None
    except (TypeError, ValueError):
        return False


def _unknown_fields(obj, allowed: set[str], prefix: str, errors: list[dict]) -> None:
    if not isinstance(obj, dict):
        errors.append({"field": prefix, "code": "not_object"})
        return
    for name in sorted(set(obj) - allowed):
        errors.append({"field": f"{prefix}.{name}" if prefix else name, "code": "unknown"})


def validate_event(event) -> dict:
    errors: list[dict] = []
    if not isinstance(event, dict):
        raise EventValidationError([{"field": "$", "code": "not_object"}])
    _unknown_fields(event, _ALLOWED_TOP, "", errors)
    for field, allowed in (
        ("source", _ALLOWED_SOURCE), ("sender", _ALLOWED_SENDER),
        ("message", _ALLOWED_MESSAGE), ("hints", _ALLOWED_HINTS),
    ):
        _unknown_fields(event.get(field, {}), allowed, field, errors)
    if event.get("schema_version") != 1:
        errors.append({"field": "schema_version", "code": "unsupported"})
    event_id = event.get("event_id")
    if not isinstance(event_id, str) or not 1 <= len(event_id) <= 200:
        errors.append({"field": "event_id", "code": "invalid_length"})
    if event.get("channel") != "email":
        errors.append({"field": "channel", "code": "unsupported"})
    if event.get("event_type") != "inbound_message":
        errors.append({"field": "event_type", "code": "unsupported"})
    for name in ("occurred_at", "received_at"):
        if not _iso_utc(event.get(name)):
            errors.append({"field": name, "code": "invalid_datetime"})
    source = event.get("source") if isinstance(event.get("source"), dict) else {}
    if source.get("account") != "admin@visionbuilts.net":
        errors.append({"field": "source.account", "code": "unexpected"})
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    if not isinstance(sender.get("external_id"), str) or not sender.get("external_id"):
        errors.append({"field": "sender.external_id", "code": "required"})
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    subject = message.get("subject", "")
    text = message.get("text", "")
    if not isinstance(subject, str) or len(subject) > 500:
        errors.append({"field": "message.subject", "code": "invalid_length"})
    if not isinstance(text, str) or len(text) > 12000:
        errors.append({"field": "message.text", "code": "invalid_length"})
    if not (text or subject or sender.get("email")):
        errors.append({"field": "message", "code": "empty"})
    to = message.get("to", [])
    if not isinstance(to, list) or len(to) > 20 or any(not isinstance(v, str) for v in to):
        errors.append({"field": "message.to", "code": "invalid"})
    attachments = message.get("attachments", [])
    if not isinstance(attachments, list) or len(attachments) > 20:
        errors.append({"field": "message.attachments", "code": "invalid"})
    else:
        for i, item in enumerate(attachments):
            _unknown_fields(item, _ALLOWED_ATTACHMENT, f"message.attachments[{i}]", errors)
            if isinstance(item, dict) and item.get("bytes") is not None and (
                not isinstance(item["bytes"], int) or isinstance(item["bytes"], bool) or item["bytes"] < 0
            ):
                errors.append({"field": f"message.attachments[{i}].bytes", "code": "invalid"})
    digest = message.get("content_sha256", "")
    if digest and (not isinstance(digest, str) or not all(c in "0123456789abcdef" for c in digest) or len(digest) != 64):
        errors.append({"field": "message.content_sha256", "code": "invalid"})
    if errors:
        raise EventValidationError(errors)
    return event


def _rate_limited(client_ip: str, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    with _RATE_LOCK:
        bucket = _RATE_BUCKETS[client_ip]
        while bucket and now - bucket[0] >= RATE_WINDOW:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT:
            return True
        bucket.append(now)
        return False


def _error_code(exc: Exception) -> str:
    if isinstance(exc, NotionIdentityConflict):
        return "notion_identity_conflict"
    if isinstance(exc, NotionSchemaError):
        return "notion_schema_incompatible"
    text = str(exc).split(":", 1)[0]
    allowed = "".join(c for c in text.lower() if c.isalnum() or c in "_-")
    return (allowed or "intake_processing_error")[:80]


class IntakeService:
    def __init__(
        self,
        store: IntakeStore | None = None,
        *,
        extractor=extract_event,
        notion_factory=NotionCRM.from_env,
        start_workers: bool = True,
    ):
        self.store = store or IntakeStore(STATE_DIR / "intake.sqlite3")
        self.extractor = extractor
        self.notion_factory = notion_factory
        self.wakeup = threading.Event()
        self._workers: list[threading.Thread] = []
        if start_workers:
            for index in range(2):
                worker = threading.Thread(
                    target=self._worker_loop,
                    name=f"hermes-intake-{index + 1}",
                    daemon=True,
                )
                worker.start()
                self._workers.append(worker)

    def enqueue(self, event: dict) -> tuple[dict, bool]:
        row, duplicate = self.store.insert_or_get(event, max_active=MAX_QUEUE)
        if not duplicate:
            self.wakeup.set()
        return row, duplicate

    def process_one(self) -> bool:
        row = self.store.claim_next()
        if row is None:
            return False
        started = time.monotonic()
        event = row["event"]
        try:
            extraction = self.extractor(event)
            classification = extraction["classification"]
            if classification == "irrelevant":
                self.store.finish(row["intake_id"], "ignored", notion_action="unchanged")
                action = "ignored"
            elif classification == "needs_review":
                self.store.finish(row["intake_id"], "done", notion_action="needs_review")
                action = "needs_review"
            else:
                self.store.mark_syncing(row["intake_id"])
                notion = self.notion_factory()
                result = notion.upsert(event, extraction)
                self.store.finish(
                    row["intake_id"], "done",
                    notion_page_id=result.page_id, notion_action=result.action,
                )
                action = result.action
            logger.info(
                "intake_complete intake_id=%s event_hash=%s channel=%s action=%s duration_ms=%d",
                row["intake_id"], hashlib.sha256(event["event_id"].encode()).hexdigest()[:12],
                event["channel"], action, int((time.monotonic() - started) * 1000),
            )
        except Exception as exc:
            code = _error_code(exc)
            outcome = self.store.retry_or_fail(row["intake_id"], code)
            logger.warning(
                "intake_failed intake_id=%s event_hash=%s error_code=%s outcome=%s",
                row["intake_id"], hashlib.sha256(event["event_id"].encode()).hexdigest()[:12],
                code, outcome,
            )
        return True

    def _worker_loop(self) -> None:
        while True:
            if not self.process_one():
                self.wakeup.wait(5)
                self.wakeup.clear()


_SERVICE = None
_SERVICE_LOCK = threading.Lock()


def get_intake_service() -> IntakeService:
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            _SERVICE = IntakeService()
        return _SERVICE


def _send_error(handler, status: int, code: str, *, errors=None, headers=None):
    payload = {"ok": False, "error": code}
    if errors:
        payload["errors"] = errors
    j(handler, payload, status=status, extra_headers=headers)
    return True


def handle_intake_email(handler, service: IntakeService | None = None) -> bool:
    """Authenticate before reading the bounded body, then enqueue idempotently."""
    expected = os.getenv("HERMES_INTAKE_TOKEN", "").strip()
    if not expected:
        return _send_error(handler, 503, "intake_not_configured")
    header = str(handler.headers.get("Authorization") or "")
    provided = header[7:] if header.startswith("Bearer ") else ""
    if not provided or not hmac.compare_digest(provided, expected):
        return _send_error(handler, 401, "invalid_intake_token")
    client = str((getattr(handler, "client_address", None) or ["unknown"])[0])
    if _rate_limited(client):
        return _send_error(handler, 429, "intake_rate_limited", headers={"Retry-After": "60"})
    raw_length = handler.headers.get("Content-Length")
    try:
        length = int(raw_length)
    except (TypeError, ValueError):
        return _send_error(handler, 400, "invalid_content_length")
    if length < 0:
        return _send_error(handler, 400, "invalid_content_length")
    if length > MAX_BODY_BYTES:
        handler.close_connection = True
        return _send_error(handler, 413, "payload_too_large")
    raw = handler.rfile.read(length)
    try:
        event = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _send_error(handler, 400, "invalid_json")
    try:
        event = validate_event(event)
    except EventValidationError as exc:
        return _send_error(handler, 422, "invalid_event", errors=exc.errors)
    service = service or get_intake_service()
    try:
        row, duplicate = service.enqueue(event)
    except IntakeQueueFullError:
        return _send_error(handler, 429, "intake_queue_full", headers={"Retry-After": "30"})
    response = {
        "ok": True,
        "intake_id": row["intake_id"],
        "event_id": event["event_id"],
        "status": row["status"],
        "duplicate": duplicate,
    }
    j(handler, response, status=200 if duplicate else 202)
    return True
