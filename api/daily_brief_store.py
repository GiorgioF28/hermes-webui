"""Crash-recoverable SQLite storage for Daily Brief email intake and delivery."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BUSY_TIMEOUT_MS = 15_000
SCHEMA_VERSION = 1


def message_key(row: dict[str, Any]) -> str:
    stable = str(row.get("messageId") or "").strip()
    if stable:
        return "id:" + stable
    fallback = "\0".join(str(row.get(key) or "") for key in ("from", "subject", "receivedAt"))
    return "fields:" + hashlib.sha256(fallback.encode("utf-8")).hexdigest()


def connect(path: Path | str) -> sqlite3.Connection:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(target, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS email_queue (
            account TEXT NOT NULL, item_key TEXT NOT NULL, received_at TEXT NOT NULL,
            payload TEXT NOT NULL, PRIMARY KEY(account, item_key)
        );
        CREATE TABLE IF NOT EXISTS email_receipts (
            account TEXT NOT NULL, item_key TEXT NOT NULL, processed_at TEXT NOT NULL,
            identity_payload TEXT, PRIMARY KEY(account, item_key)
        );
        CREATE TABLE IF NOT EXISTS email_sources (
            account TEXT PRIMARY KEY, last_success_at TEXT, last_error_at TEXT,
            last_error TEXT, last_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS digest_batch (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), batch_id TEXT NOT NULL,
            cutoff TEXT NOT NULL, started_at TEXT NOT NULL, status TEXT NOT NULL,
            digest_json TEXT
        );
        CREATE TABLE IF NOT EXISTS digest_batch_items (
            batch_id TEXT NOT NULL, account TEXT NOT NULL, item_key TEXT NOT NULL,
            payload TEXT NOT NULL, PRIMARY KEY(batch_id, account, item_key)
        );
        CREATE INDEX IF NOT EXISTS email_queue_received ON email_queue(received_at);
        """
    )
    receipt_columns = {row["name"] for row in db.execute("PRAGMA table_info(email_receipts)")}
    if "identity_payload" not in receipt_columns:
        db.execute("ALTER TABLE email_receipts ADD COLUMN identity_payload TEXT")
    db.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
    return db


def _load_rows(path: Path, key: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f"Legacy Daily Brief file has invalid {key} structure")
    return [row for row in rows if isinstance(row, dict)]


def migrate_legacy(db_path: Path | str, legacy_dir: Path | str, *, now: str | None = None) -> dict[str, int]:
    """Back up then import the old JSON stores once; never mutate source files."""
    legacy = Path(legacy_dir)
    sources = ("email-archive.json", "email-inbox-accumulator.json")
    stamp = now or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    db = connect(db_path)
    try:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM meta WHERE key='legacy_migration_v1'").fetchone():
            db.rollback()
            return {"imported": 0, "receipts": 0, "alreadyMigrated": 1}
        # The marker makes SQLite authoritative. Do not even parse legacy files
        # after it exists: they may have been moved, corrupted, or stale.
        rows_by_source = {name: _load_rows(legacy / name, name) for name in sources}
        existing = [name for name in sources if (legacy / name).is_file()]
        if existing:
            backup_dir = Path(db_path).parent / "migration-backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            for name in existing:
                backup = backup_dir / name
                if not backup.exists():
                    fd, temporary = tempfile.mkstemp(prefix=name + ".", suffix=".tmp", dir=backup_dir)
                    os.close(fd)
                    try:
                        shutil.copy2(legacy / name, temporary)
                        os.replace(temporary, backup)
                    finally:
                        if os.path.exists(temporary):
                            os.unlink(temporary)
        queue: dict[tuple[str, str], dict[str, Any]] = {}
        receipts: set[tuple[str, str]] = set()
        archive_identity: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows_by_source["email-archive.json"]:
            account = str(row.get("account") or "").strip()
            key = message_key(row)
            if not account:
                continue
            pair = (account, key)
            archive_identity[pair] = row
            if row.get("processingStatus") == "processed":
                receipts.add(pair)
            else:
                queue[pair] = row
        for row in rows_by_source["email-inbox-accumulator.json"]:
            account = str(row.get("account") or "").strip()
            if account:
                pair = (account, message_key(row))
                if pair not in receipts:
                    queue.setdefault(pair, row)
        for pair in receipts:
            queue.pop(pair, None)
        for (account, key), row in queue.items():
            db.execute(
                "INSERT OR IGNORE INTO email_queue(account,item_key,received_at,payload) VALUES(?,?,?,?)",
                (account, key, str(row.get("receivedAt") or ""), json.dumps(row, ensure_ascii=False)),
            )
        for account, key in receipts:
            source = archive_identity.get((account, key), {})
            identity = {field: source.get(field, "") for field in ("account", "messageId", "from", "subject", "receivedAt")}
            db.execute(
                "INSERT OR IGNORE INTO email_receipts(account,item_key,processed_at,identity_payload) VALUES(?,?,?,?)",
                (account, key, stamp, json.dumps(identity, ensure_ascii=False)),
            )
        db.execute("INSERT INTO meta(key,value) VALUES('legacy_migration_v1',?)", (stamp,))
        db.commit()
        return {"imported": len(queue), "receipts": len(receipts), "alreadyMigrated": 0}
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def enqueue(db_path: Path | str, rows: list[dict[str, Any]]) -> dict[str, int]:
    added = skipped = 0
    db = connect(db_path)
    try:
        db.execute("BEGIN IMMEDIATE")
        for row in rows:
            account, key = str(row["account"]), message_key(row)
            receipt = db.execute(
                "SELECT 1 FROM email_receipts WHERE account=? AND item_key=?", (account, key)
            ).fetchone()
            if receipt:
                skipped += 1
                continue
            cursor = db.execute(
                "INSERT OR IGNORE INTO email_queue(account,item_key,received_at,payload) VALUES(?,?,?,?)",
                (account, key, row["receivedAt"], json.dumps(row, ensure_ascii=False)),
            )
            if cursor.rowcount:
                added += 1
            else:
                skipped += 1
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
    return {"added": added, "skipped": skipped}


def start_or_recover_batch(db_path: Path | str, cutoff: str, *, now: str) -> tuple[str, list[dict[str, Any]], str | None]:
    db = connect(db_path)
    try:
        db.execute("BEGIN IMMEDIATE")
        prior = db.execute("SELECT * FROM digest_batch WHERE singleton=1").fetchone()
        if prior and prior["status"] == "prepared":
            rows = [json.loads(row["payload"]) for row in db.execute(
                "SELECT payload FROM digest_batch_items WHERE batch_id=? ORDER BY rowid",
                (prior["batch_id"],),
            )]
            db.commit()
            return prior["batch_id"], rows, prior["digest_json"]
        if prior:
            db.execute("DELETE FROM digest_batch_items WHERE batch_id=?", (prior["batch_id"],))
            db.execute("DELETE FROM digest_batch WHERE singleton=1")
        batch_id = uuid.uuid4().hex
        selected = list(db.execute(
            "SELECT account,item_key,received_at,payload FROM email_queue WHERE received_at<=? ORDER BY received_at DESC",
            (cutoff,),
        ))
        db.execute(
            "INSERT INTO digest_batch(singleton,batch_id,cutoff,started_at,status) VALUES(1,?,?,?,'analyzing')",
            (batch_id, cutoff, now),
        )
        for row in selected:
            db.execute(
                "INSERT INTO digest_batch_items(batch_id,account,item_key,payload) VALUES(?,?,?,?)",
                (batch_id, row["account"], row["item_key"], row["payload"]),
            )
        db.commit()
        return batch_id, [json.loads(row["payload"]) for row in selected], None
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def prepare_batch(db_path: Path | str, batch_id: str, digest: dict[str, Any]) -> str:
    encoded = json.dumps(digest, ensure_ascii=False, separators=(",", ":"))
    db = connect(db_path)
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT batch_id,status FROM digest_batch WHERE singleton=1").fetchone()
        if not row or row["batch_id"] != batch_id:
            raise RuntimeError("Daily Brief batch ownership changed")
        db.execute("UPDATE digest_batch SET status='prepared',digest_json=? WHERE singleton=1", (encoded,))
        db.commit()
        return encoded
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def finish_batch(db_path: Path | str, batch_id: str, *, processed_at: str) -> int:
    db = connect(db_path)
    try:
        db.execute("BEGIN IMMEDIATE")
        batch = db.execute("SELECT status FROM digest_batch WHERE singleton=1 AND batch_id=?", (batch_id,)).fetchone()
        if not batch or batch["status"] != "prepared":
            raise RuntimeError("Daily Brief batch is not durably prepared")
        rows = list(db.execute("SELECT account,item_key,payload FROM digest_batch_items WHERE batch_id=?", (batch_id,)))
        for row in rows:
            message = json.loads(row["payload"])
            identity = {key: message.get(key, "") for key in ("account", "messageId", "from", "subject", "receivedAt")}
            db.execute(
                "INSERT OR IGNORE INTO email_receipts(account,item_key,processed_at,identity_payload) VALUES(?,?,?,?)",
                (row["account"], row["item_key"], processed_at, json.dumps(identity, ensure_ascii=False)),
            )
            db.execute("DELETE FROM email_queue WHERE account=? AND item_key=?", (row["account"], row["item_key"]))
        db.execute("DELETE FROM digest_batch_items WHERE batch_id=?", (batch_id,))
        db.execute("DELETE FROM digest_batch WHERE singleton=1")
        db.commit()
        return len(rows)
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def pending_count(db_path: Path | str) -> int:
    db = connect(db_path)
    try:
        return int(db.execute("SELECT COUNT(*) FROM email_queue").fetchone()[0])
    finally:
        db.close()


def record_sources(db_path: Path | str, accounts: list[dict[str, Any]], *, now: str) -> None:
    db = connect(db_path)
    try:
        db.execute("BEGIN IMMEDIATE")
        for row in accounts:
            if not isinstance(row, dict):
                continue
            label = str(row.get("label") or "").strip()[:100]
            if not label:
                continue
            error = str(row.get("error") or "").strip()[:240] or None
            if error:
                db.execute(
                    "INSERT INTO email_sources(account,last_success_at,last_error_at,last_error,last_count) VALUES(?,NULL,?,?,0) "
                    "ON CONFLICT(account) DO UPDATE SET last_error_at=excluded.last_error_at,last_error=excluded.last_error",
                    (label, now, error),
                )
            else:
                try:
                    count = max(0, int(row.get("count", 0)))
                except (TypeError, ValueError, OverflowError):
                    count = 0
                db.execute(
                    "INSERT INTO email_sources(account,last_success_at,last_error_at,last_error,last_count) VALUES(?,?,NULL,NULL,?) "
                    "ON CONFLICT(account) DO UPDATE SET last_success_at=excluded.last_success_at,last_error_at=NULL,last_error=NULL,last_count=excluded.last_count",
                    (label, now, count),
                )
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def source_statuses(db_path: Path | str) -> list[dict[str, Any]]:
    db = connect(db_path)
    try:
        return [dict(row) for row in db.execute(
            "SELECT account AS label,last_success_at,last_error_at,last_error,last_count FROM email_sources ORDER BY account"
        )]
    finally:
        db.close()


def export_legacy(db_path: Path | str, legacy_dir: Path | str, *, now: str | None = None) -> dict[str, int]:
    """Write a reviewed rollback snapshot without deleting or changing SQLite."""
    legacy = Path(legacy_dir)
    stamp = now or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    archive_path = legacy / "email-archive.json"
    existing_archive = json.loads(archive_path.read_text(encoding="utf-8")) if archive_path.exists() else {"items": []}
    if not isinstance(existing_archive, dict) or not isinstance(existing_archive.get("items"), list):
        raise ValueError("existing archive is invalid; refusing rollback export")
    db = connect(db_path)
    try:
        pending = [json.loads(row[0]) for row in db.execute("SELECT payload FROM email_queue ORDER BY received_at")]
        receipts = [dict(row) for row in db.execute("SELECT account,item_key,processed_at,identity_payload FROM email_receipts")]
    finally:
        db.close()
    archive = {message_key(row) + "\0" + str(row.get("account") or ""): row for row in existing_archive["items"] if isinstance(row, dict)}
    for row in pending:
        saved = dict(row)
        saved["processingStatus"] = "pending"
        archive[message_key(saved) + "\0" + str(saved.get("account") or "")] = saved
    for receipt in receipts:
        key = str(receipt["item_key"])
        identity = json.loads(receipt["identity_payload"]) if receipt.get("identity_payload") else {}
        tombstone: dict[str, Any] = {
            **identity,
            "account": receipt["account"], "processingStatus": "processed",
            "processedAt": receipt["processed_at"],
        }
        if not identity:
            tombstone["_dedupeKey"] = key
            if key.startswith("id:"):
                tombstone["messageId"] = key[3:]
        archive_key = key + "\0" + str(receipt["account"])
        # A previous JSON archive may still contain the same row as pending.
        # A receipt is authoritative and must win when rolling back to the
        # legacy consumer, otherwise it will analyze that body again.
        existing = archive.get(archive_key)
        if existing is not None:
            existing["processingStatus"] = "processed"
            existing["processedAt"] = receipt["processed_at"]
            existing.pop("bodyText", None)
            existing.pop("bodyExcerpt", None)
        else:
            archive[archive_key] = tombstone
    accumulator = {"version": 2, "updatedAt": stamp, "items": pending}
    archive_payload = {"version": 1, "updatedAt": stamp, "items": list(archive.values())}
    backup_dir = Path(db_path).parent / "rollback-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for source in (archive_path, legacy / "email-inbox-accumulator.json"):
        if source.is_file():
            backup = backup_dir / (source.name + ".before-export")
            if not backup.exists():
                shutil.copy2(source, backup)
    for target, payload in ((archive_path, archive_payload), (legacy / "email-inbox-accumulator.json", accumulator)):
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return {"pending": len(pending), "receipts": len(receipts), "archiveRows": len(archive)}
