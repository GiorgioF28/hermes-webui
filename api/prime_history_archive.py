"""Reversible, opt-in archival primitives for Prime transcript history.

This module is deliberately not wired into the Command Bridge.  It keeps the
stored message slots stable: archived entries become tombstones at the same
absolute indexes, so history cursors and delegation anchors do not shift.
Callers must hold the Prime store lock and use ``apply_archive_atomically``;
that entry point rejects an active turn. UI history and model context must filter
tombstones; archived content is available only through explicit retrieval.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ARCHIVE_SCHEMA = 1
TOMBSTONE_KEY = "_prime_archive"


class ArchiveError(ValueError):
    """Unsafe or invalid archive operation."""


def parse_cutoff(value: str | datetime) -> datetime:
    """Return an aware UTC cutoff; naive datetimes are rejected."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ArchiveError("cutoff must include a timezone")
    return value.astimezone(timezone.utc)


def _message_time(message: dict[str, Any]) -> datetime | None:
    """Read known timestamp fields. Unknown/missing times are never guessed."""
    for key in ("created_at", "timestamp", "ts"):
        value = message.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                return datetime.fromtimestamp(value, timezone.utc)
            except (OverflowError, OSError, ValueError):
                continue
        if isinstance(value, str) and value.strip():
            try:
                parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
                if parsed.tzinfo is not None:
                    return parsed.astimezone(timezone.utc)
            except ValueError:
                continue
    return None


def select_archive_entries(messages: list[Any], cutoff: str | datetime) -> list[dict[str, Any]]:
    """Select dated messages strictly before cutoff, retaining original indexes."""
    boundary = parse_cutoff(cutoff)
    selected = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or TOMBSTONE_KEY in message:
            continue
        stamp = _message_time(message)
        if stamp is not None and stamp < boundary:
            selected.append({"index": index, "message": message})
    return selected


def summarize_entries(entries: list[dict[str, Any]], cutoff: str | datetime) -> str:
    """Create a content-free structural summary suitable for the private bundle."""
    roles: dict[str, int] = {}
    dated = []
    for row in entries:
        message = row["message"]
        role = str(message.get("role") or "unknown")
        roles[role] = roles.get(role, 0) + 1
        stamp = _message_time(message)
        if stamp:
            dated.append(stamp)
    first = min(dated).isoformat() if dated else "unknown"
    last = max(dated).isoformat() if dated else "unknown"
    role_text = ", ".join(f"{role}: {count}" for role, count in sorted(roles.items())) or "none"
    return (
        "# Prime archive summary\n\n"
        f"- Cutoff (Europe/Rome): {cutoff}\n"
        f"- Archived messages: {len(entries)} (original indexes retained)\n"
        f"- Date span (UTC): {first} — {last}\n"
        f"- Roles: {role_text}\n"
        "- Decisions: not inferred by this offline structural summary.\n"
        "- Open tasks: not inferred by this offline structural summary.\n"
        "- Current-state distinction: this bundle is historical; it does not assert current task state.\n"
    )


def build_archive_bundle(
    source_bytes: bytes,
    cutoff: str | datetime,
    *,
    summary: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a private archive bundle and candidate state without writing either."""
    try:
        state = json.loads(source_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchiveError("source is not valid UTF-8 JSON") from exc
    if not isinstance(state, dict) or not isinstance(state.get("messages"), list):
        raise ArchiveError("source must be a Prime state object with messages")
    cutoff_dt = parse_cutoff(cutoff)
    entries = select_archive_entries(state["messages"], cutoff_dt)
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    bundle = {
        "schema": ARCHIVE_SCHEMA,
        "kind": "prime-history-archive",
        "cutoff": cutoff_dt.isoformat(),
        "source_sha256": source_hash,
        "source_bytes": len(source_bytes),
        "message_count_before": len(state["messages"]),
        "archived_count": len(entries),
        "preserved_count": len(state["messages"]) - len(entries),
        "archived_entries": entries,
        "summary": summary if summary is not None else summarize_entries(entries, cutoff_dt),
        "rollback": "Restore archived_entries[index].message into the same message slot; verify source_sha256 first.",
    }
    candidate = json.loads(json.dumps(state, ensure_ascii=False))
    for row in entries:
        candidate["messages"][row["index"]] = {
            TOMBSTONE_KEY: {
                "archive_id": source_hash,
                "original_index": row["index"],
                "role": row["message"].get("role"),
            }
        }
    candidate["_prime_archive"] = {
        "schema": ARCHIVE_SCHEMA,
        "archive_id": source_hash,
        "cutoff": cutoff_dt.isoformat(),
        "archived_indexes": [row["index"] for row in entries],
    }
    return bundle, candidate


def retrieve_archived_message(bundle: dict[str, Any], original_index: int) -> dict[str, Any] | None:
    """Explicit archive retrieval by original absolute message index."""
    if bundle.get("kind") != "prime-history-archive" or bundle.get("schema") != ARCHIVE_SCHEMA:
        raise ArchiveError("unsupported archive bundle")
    for row in bundle.get("archived_entries", []):
        if row.get("index") == original_index:
            return row["message"]
    return None


def restore_archived_messages(state: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
    """Return a restored copy; refuses slot or archive identity mismatches."""
    if bundle.get("kind") != "prime-history-archive" or bundle.get("schema") != ARCHIVE_SCHEMA:
        raise ArchiveError("unsupported archive bundle")
    restored = json.loads(json.dumps(state, ensure_ascii=False))
    messages = restored.get("messages")
    if not isinstance(messages, list):
        raise ArchiveError("state has no message list")
    for row in bundle.get("archived_entries", []):
        index = row.get("index")
        if not isinstance(index, int) or index < 0 or index >= len(messages):
            raise ArchiveError("archive index is outside the current message slots")
        marker = messages[index]
        if not isinstance(marker, dict) or marker.get(TOMBSTONE_KEY, {}).get("archive_id") != bundle.get("source_sha256"):
            raise ArchiveError("archive tombstone identity mismatch")
        messages[index] = row["message"]
    restored.pop("_prime_archive", None)
    return restored


def write_json_atomic(path: str | Path, payload: Any) -> None:
    """Durably replace one JSON file; a failed write leaves the destination intact."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, prefix=target.name + ".tmp.", delete=False) as stream:
            temp_name = stream.name
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
        temp_name = None
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def apply_archive_atomically(
    path: str | Path,
    expected_source_sha256: str,
    candidate_state: dict[str, Any],
    *,
    active_turn: bool,
) -> None:
    """Apply a prepared state only under the caller's store lock and on same source.

    ``active_turn`` must be sampled from the live Prime store while that lock is
    held. Hash mismatch prevents overwriting messages written after the dry-run.
    """
    if active_turn:
        raise ArchiveError("cannot apply archive while a turn is active")
    target = Path(path)
    current_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    if current_hash != expected_source_sha256:
        raise ArchiveError("source changed after archive planning")
    write_json_atomic(target, candidate_state)
