"""Idempotent, evidence-gated updates for existing VisionBuilts CRM pages.

This adapter deliberately has no message-send capability and does not infer
identity or commercial interest. Source adapters must supply a verified page
mapping before an event can reach Notion.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol


PROTECTED_STATUSES = {"In trattativa", "Chiuso", "Perso"}
ALLOWED_STATUS = {"Contattato", "Risposto", *PROTECTED_STATUSES}
CHANNELS = {"instagram", "email"}


class ReplySyncError(RuntimeError):
    pass


class IdentityReview(ReplySyncError):
    pass


class ReplyNotion(Protocol):
    def get_page(self, page_id: str) -> dict: ...
    def patch_page(self, page_id: str, properties: dict) -> dict: ...


@dataclass(frozen=True)
class ReplyEvent:
    channel: str
    account: str
    message_id: str
    occurred_at: str
    summary: str
    next_action: str
    identity_key: str
    crm_page_id: str
    identity_verified: bool
    inbound: bool = True
    autoresponder: bool = False
    echo: bool = False

    @classmethod
    def parse(cls, raw: dict) -> "ReplyEvent":
        try:
            event = cls(**raw)
        except (TypeError, ValueError) as exc:
            raise ReplySyncError("invalid_reply_event") from exc
        if event.channel not in CHANNELS or not all((event.account, event.message_id, event.identity_key)):
            raise ReplySyncError("invalid_reply_identity")
        try:
            dt = datetime.fromisoformat(event.occurred_at.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                raise ValueError
        except (TypeError, ValueError):
            raise ReplySyncError("invalid_occurred_at") from None
        if not event.inbound or event.autoresponder or event.echo:
            raise ReplySyncError("not_a_human_inbound_reply")
        if not event.identity_verified or not event.crm_page_id:
            raise IdentityReview("identity_unverified")
        if len(event.summary) > 500 or len(event.next_action) > 300:
            raise ReplySyncError("reply_summary_too_long")
        return event

    @property
    def key(self) -> str:
        material = "\0".join((self.account, self.channel, self.message_id))
        return hashlib.sha256(material.encode()).hexdigest()


class ReplyLedger:
    """Minimal durable receipts; an event is acknowledged only after read-back."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        db = self._connect()
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS reply_receipts (
                event_key TEXT PRIMARY KEY, page_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL, synced_at TEXT NOT NULL,
                verified_hash TEXT NOT NULL
            )""")
        finally:
            self._close(db)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @staticmethod
    def _close(db):
        db.close()

    def get(self, key: str):
        db = self._connect()
        try:
            return db.execute("SELECT page_id FROM reply_receipts WHERE event_key=?", (key,)).fetchone()
        finally:
            self._close(db)

    def ack(self, event: ReplyEvent, verification_hash: str):
        db = self._connect()
        try:
            db.execute("INSERT OR IGNORE INTO reply_receipts VALUES (?, ?, ?, ?, ?)", (
                event.key, event.crm_page_id, event.occurred_at,
                datetime.now(timezone.utc).isoformat(), verification_hash,
            ))
            db.commit()
        finally:
            self._close(db)


def _plain(prop: dict) -> str:
    kind = prop.get("type")
    value = prop.get(kind) or {}
    if kind in {"select", "status"}:
        return (value or {}).get("name") or ""
    if kind == "date":
        return (value or {}).get("start") or ""
    if kind == "rich_text":
        return "".join(x.get("plain_text", "") for x in value)
    return ""


def _rich(value: str):
    # Notion caps each text object at 2,000 chars; the property itself supports
    # multiple objects. Preserve existing notes in full.
    return {"rich_text": [
        {"type": "text", "text": {"content": value[index:index + 2000]}}
        for index in range(0, len(value), 2000)
    ]}


def _instant(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _same_notion_date(actual: str, expected: str) -> bool:
    if not actual or not expected:
        return False
    left, right = _instant(actual), _instant(expected)
    # Notion date-only/minute precision is a valid normalization of a source
    # timestamp; never accept a mismatch outside the represented precision.
    if len(actual) == 10:
        return left.date() == right.date()
    if len(actual) <= 16:
        return left.replace(second=0, microsecond=0) == right.replace(second=0, microsecond=0)
    return left == right


def sync_reply(
    raw_event: dict,
    *,
    notion: ReplyNotion,
    ledger: ReplyLedger,
    identity_check: Callable[[ReplyEvent, dict], bool],
) -> dict:
    """Update one existing page; identity_check must verify the source mapping."""
    event = ReplyEvent.parse(raw_event)
    with ledger._lock:
        if ledger.get(event.key):
            return {"action": "duplicate", "page_id": event.crm_page_id}
        before = notion.get_page(event.crm_page_id)
        if before.get("id") != event.crm_page_id or not identity_check(event, before):
            raise IdentityReview("crm_identity_mismatch")
        props = before.get("properties") or {}
        status = _plain(props.get("Stato") or {})
        last_date = _plain(props.get("Data risposta") or {})
        if status in PROTECTED_STATUSES:
            # Keep terminal/in-progress stages, while recording the newer reply.
            next_status = status
        elif status in {"Contattato", "Risposto"}:
            next_status = "Risposto"
        else:
            # A stale status is usable only when CRM itself documents the sent DM.
            dm_sent = _plain(props.get("DM inviato") or {})
            contacted_at = _plain(props.get("Data contatto") or {})
            if not dm_sent or not contacted_at:
                raise IdentityReview("contacted_state_not_verified")
            next_status = "Risposto"
        if last_date and _instant(last_date) > _instant(event.occurred_at):
            # A prior PATCH may have succeeded while its readback failed. If
            # Notion already contains this event marker, reconcile the receipt.
            old_marker = f"[reply:{event.channel}:{event.message_id}]"
            if old_marker in _plain(props.get("Note") or {}):
                verified = {"Stato": status, "Data risposta": last_date}
                ledger.ack(event, hashlib.sha256(json.dumps(verified, sort_keys=True).encode()).hexdigest())
                return {"action": "reconciled", "page_id": event.crm_page_id}
            return {"action": "stale", "page_id": event.crm_page_id}

        old_notes = _plain(props.get("Note") or {})
        note_marker = f"[reply:{event.channel}:{event.message_id}]"
        reply_note = f"{note_marker} {event.summary}\nProssima azione: {event.next_action}".strip()
        notes = old_notes if note_marker in old_notes else "\n".join(
            part for part in (old_notes, reply_note) if part
        )
        patch = {
            "Stato": {((props.get("Stato") or {}).get("type") or "select"): {"name": next_status}},
            "Data risposta": {"date": {"start": event.occurred_at}},
            "Note": _rich(notes),
        }
        # Optional properties are written only when the live page exposes them.
        if "Canale risposta" in props:
            patch["Canale risposta"] = {((props.get("Canale risposta") or {}).get("type") or "select"): {"name": event.channel.capitalize()}}
        if "Prossima azione" in props:
            patch["Prossima azione"] = _rich(event.next_action)
        notion.patch_page(event.crm_page_id, patch)
        after = notion.get_page(event.crm_page_id)
        after_props = after.get("properties") or {}
        expected = {"Stato": next_status, "Data risposta": event.occurred_at}
        if "Canale risposta" in patch:
            expected["Canale risposta"] = event.channel.capitalize()
        if "Prossima azione" in patch:
            expected["Prossima azione"] = event.next_action
        actual = {name: _plain(after_props.get(name) or {}) for name in expected}
        scalar_mismatch = actual.get("Stato") != expected["Stato"]
        date_mismatch = not _same_notion_date(actual.get("Data risposta", ""), event.occurred_at)
        optional_mismatch = any(actual.get(k) != v for k, v in expected.items()
                                if k not in {"Stato", "Data risposta"})
        if scalar_mismatch or date_mismatch or optional_mismatch or note_marker not in _plain(after_props.get("Note") or {}):
            raise ReplySyncError("notion_readback_mismatch")
        receipt = hashlib.sha256(json.dumps(actual, sort_keys=True).encode()).hexdigest()
        ledger.ack(event, receipt)
        return {"action": "updated", "page_id": event.crm_page_id}
