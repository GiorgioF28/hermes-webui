"""Persistent, idempotent storage for machine-to-machine intake events."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


TERMINAL_STATUSES = {"done", "ignored", "failed"}


class IntakeQueueFullError(OverflowError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class IntakeStore:
    """Small SQLite queue with atomic insert/dedupe and worker claims."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_lock = threading.Lock()
        self._initialized = False
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def initialize(self) -> None:
        with self._init_lock:
            if self._initialized:
                return
            with self._connect() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS intake_events (
                        intake_id TEXT PRIMARY KEY,
                        event_id TEXT NOT NULL UNIQUE,
                        channel TEXT NOT NULL,
                        source_account TEXT NOT NULL,
                        content_sha256 TEXT NOT NULL DEFAULT '',
                        occurred_at TEXT NOT NULL,
                        status TEXT NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        notion_page_id TEXT,
                        notion_action TEXT,
                        error_code TEXT,
                        event_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        finished_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_intake_status_updated
                        ON intake_events(status, updated_at);
                    CREATE INDEX IF NOT EXISTS idx_intake_secondary_dedupe
                        ON intake_events(channel, source_account, content_sha256, occurred_at);
                    """
                )
            self._initialized = True

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict | None:
        return dict(row) if row is not None else None

    def insert_or_get(self, event: dict, *, max_active: int | None = None) -> tuple[dict, bool]:
        """Insert an event or return its existing row. Boolean means duplicate."""
        now = _utc_now()
        event_id = event["event_id"]
        channel = event["channel"]
        source_account = event["source"]["account"]
        occurred_at = event["occurred_at"]
        digest = event["message"].get("content_sha256", "")
        payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        intake_id = f"intake_{uuid.uuid4().hex}"

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM intake_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            if existing is None and digest:
                try:
                    occurred = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
                    low = (occurred - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
                    high = (occurred + timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
                    existing = conn.execute(
                        """SELECT * FROM intake_events
                           WHERE channel = ? AND source_account = ? AND content_sha256 = ?
                             AND occurred_at BETWEEN ? AND ?
                           ORDER BY created_at LIMIT 1""",
                        (channel, source_account, digest, low, high),
                    ).fetchone()
                except ValueError:
                    existing = None
            if existing is not None:
                conn.execute("COMMIT")
                return self._row(existing), True
            if max_active is not None:
                active = conn.execute(
                    "SELECT COUNT(*) AS n FROM intake_events WHERE status IN ('queued','extracting','syncing')"
                ).fetchone()
                if int(active["n"]) >= max_active:
                    conn.execute("ROLLBACK")
                    raise IntakeQueueFullError("intake_queue_full")
            conn.execute(
                """INSERT INTO intake_events (
                       intake_id, event_id, channel, source_account, content_sha256,
                       occurred_at, status, event_json, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)""",
                (
                    intake_id,
                    event_id,
                    channel,
                    source_account,
                    digest,
                    occurred_at,
                    payload,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM intake_events WHERE intake_id = ?", (intake_id,)
            ).fetchone()
            conn.execute("COMMIT")
            return self._row(row), False

    def active_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM intake_events WHERE status IN ('queued','extracting','syncing')"
            ).fetchone()
        return int(row["n"])

    def claim_next(self) -> dict | None:
        """Atomically move one queued event to extracting."""
        now = _utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT intake_id FROM intake_events
                   WHERE status = 'queued' ORDER BY created_at LIMIT 1"""
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            cur = conn.execute(
                """UPDATE intake_events
                   SET status = 'extracting', attempts = attempts + 1, updated_at = ?
                   WHERE intake_id = ? AND status = 'queued'""",
                (now, row["intake_id"]),
            )
            if cur.rowcount != 1:
                conn.execute("ROLLBACK")
                return None
            claimed = conn.execute(
                "SELECT * FROM intake_events WHERE intake_id = ?", (row["intake_id"],)
            ).fetchone()
            conn.execute("COMMIT")
        result = self._row(claimed)
        result["event"] = json.loads(result.pop("event_json"))
        return result

    def mark_syncing(self, intake_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE intake_events SET status='syncing', updated_at=? WHERE intake_id=?",
                (_utc_now(), intake_id),
            )

    def finish(
        self,
        intake_id: str,
        status: str,
        *,
        notion_page_id: str | None = None,
        notion_action: str | None = None,
        error_code: str | None = None,
    ) -> None:
        if status not in TERMINAL_STATUSES:
            raise ValueError("invalid terminal intake status")
        now = _utc_now()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT event_json FROM intake_events WHERE intake_id=?", (intake_id,)
            ).fetchone()
            if row is None:
                return
            event = json.loads(row["event_json"])
            text = str(event.get("message", {}).get("text") or "")
            event.setdefault("message", {})["text"] = text[:500]
            minimized = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            conn.execute(
                """UPDATE intake_events SET status=?, notion_page_id=?, notion_action=?,
                       error_code=?, event_json=?, updated_at=?, finished_at=?
                   WHERE intake_id=?""",
                (
                    status,
                    notion_page_id,
                    notion_action,
                    error_code,
                    minimized,
                    now,
                    now,
                    intake_id,
                ),
            )

    def retry_or_fail(self, intake_id: str, error_code: str, max_attempts: int = 3) -> str:
        now = _utc_now()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT attempts FROM intake_events WHERE intake_id=?", (intake_id,)
            ).fetchone()
            if row is None:
                return "missing"
            if int(row["attempts"]) < max_attempts:
                conn.execute(
                    "UPDATE intake_events SET status='queued', error_code=?, updated_at=? WHERE intake_id=?",
                    (error_code[:80], now, intake_id),
                )
                return "queued"
        self.finish(intake_id, "failed", error_code=error_code[:80])
        return "failed"

    def get_by_event_id(self, event_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM intake_events WHERE event_id=?", (event_id,)
            ).fetchone()
        return self._row(row)

    def prune(self, *, payload_days: int = 7, record_days: int = 30) -> None:
        now = datetime.now(timezone.utc)
        payload_cutoff = (now - timedelta(days=payload_days)).isoformat().replace("+00:00", "Z")
        record_cutoff = (now - timedelta(days=record_days)).isoformat().replace("+00:00", "Z")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT intake_id,event_json FROM intake_events WHERE created_at < ?",
                (payload_cutoff,),
            ).fetchall()
            for row in rows:
                event = json.loads(row["event_json"])
                event.setdefault("message", {})["text"] = ""
                conn.execute(
                    "UPDATE intake_events SET event_json=? WHERE intake_id=?",
                    (json.dumps(event, ensure_ascii=False, separators=(",", ":")), row["intake_id"]),
                )
            conn.execute(
                "DELETE FROM intake_events WHERE created_at < ? AND status IN ('done','ignored','failed')",
                (record_cutoff,),
            )
