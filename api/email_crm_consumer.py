"""Drain durable Daily Brief email events into existing Notion People pages."""

from __future__ import annotations

import hashlib
from pathlib import Path
import threading

from api.daily_brief_store import finish_crm_event, pending_crm_events
from api.daily_brief import _email_db_path
from api.notion_crm import NotionCRM
from api.verified_reply_sync import IdentityReview, ReplyLedger, ReplySyncError, sync_reply

_CONSUMER_LOCK = threading.Lock()


def _plain(prop: dict) -> str:
    kind = prop.get("type")
    value = prop.get(kind) or {}
    if kind == "email":
        return str(value or "")
    if kind in {"title", "rich_text"}:
        return "".join(item.get("plain_text", item.get("text", {}).get("content", "")) for item in value)
    return ""


def consume_email_crm(data_dir: Path | str, notion=None, *, limit: int = 100) -> dict[str, int]:
    """Lookup exact sender email, update one page, and ack only after Notion readback."""
    database = _email_db_path(data_dir)
    base = database.parent
    notion = notion or NotionCRM.from_env()
    ledger = ReplyLedger(base / "reply-ledger.sqlite3")
    counts = {"updated": 0, "duplicate": 0, "stale": 0, "reconciled": 0,
              "review": 0, "error": 0}
    # Prevent overlapping cron requests in this process from applying one
    # inbound event concurrently. SQLite still protects durable ack/retry.
    if not _CONSUMER_LOCK.acquire(blocking=False):
        return counts
    try:
        for event in pending_crm_events(database, limit=limit):
            email = str(event.get("identity_key") or "").strip().casefold()
            message_id = str(event.get("message_id") or "")
            try:
                if event.get("channel") == "instagram" and email.startswith("igsid:"):
                    raise IdentityReview("instagram_igsid_without_verified_handle")
                identity_prop, identity_kind = (("Handle IG", "title")
                                                if event.get("channel") == "instagram"
                                                else ("Email", "email"))
                pages = notion.query_identity_pages(identity_prop, identity_kind, email)
                # Defend against permissive or case-insensitive backend matching.
                pages = [page for page in pages if _plain((page.get("properties") or {}).get(identity_prop) or {}).strip().lstrip("@").casefold() == email]
                if len(pages) != 1:
                    raise IdentityReview("notion_identity_unmatched" if not pages else "notion_identity_conflict")
                page = pages[0]
                page_id = page["id"]
                event = {**event, "crm_page_id": page_id, "identity_verified": True}

                def identity_check(parsed, current):
                    props = current.get("properties") or {}
                    parent = current.get("parent") or {}
                    return (current.get("id") == page_id
                            and parent.get("database_id") == notion.database_id
                            and _plain(props.get(identity_prop) or {}).strip().lstrip("@").casefold() == email)

                result = sync_reply(event, notion=notion, ledger=ledger, identity_check=identity_check)
                finish_crm_event(database, event["account"], event["channel"], message_id)
                counts[result["action"]] += 1
            except IdentityReview as exc:
                # Keep unresolved identities durably in review; no page is
                # created. The compact event remains in the CRM outbox for review.
                finish_crm_event(database, event["account"], event["channel"], message_id,
                                 review=str(exc)[:100])
                counts["review"] += 1
            except Exception:
                finish_crm_event(database, event["account"], event["channel"], message_id,
                                 error=hashlib.sha256(message_id.encode()).hexdigest()[:16])
                counts["error"] += 1
    finally:
        _CONSUMER_LOCK.release()
    return counts
