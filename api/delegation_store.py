"""Canonical delegation state store (Fase 1 – delega-resilienza-crediti).

Two-layer persistence for every Hermes delegation:
- tasks/delegations.jsonl        → append-only event log (backward compat)
- tasks/delegations-state.json   → canonical materialised state, one record
                                   per id, written atomically (os.replace).

This module is the single source of truth for canonical delegation state.
_BG_TASKS (prime_delegation.py) remains the in-process live cache; this
store is the durable layer that survives server restarts.

Invariants (spec §Invarianti di prodotto):
- Nessun risultato delega può esistere solo in RAM.
- Duplicati JSONL sono accettabili come eventi, NON come stato mostrato.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Status normalisation ──────────────────────────────────────────────────────
# Legacy states (prime_delegation.py pre-Fase-1) → canonical states.
_LEGACY_STATUS_MAP: dict[str, str] = {
    "in_corso": "running",
    "ok": "done",
    "errore": "failed",
    "interrotta": "failed",
    "parziale": "done",       # done + result.partial=True
}
_CANONICAL_STATUSES = frozenset({"pending", "running", "done", "failed"})

# ── Error classification ──────────────────────────────────────────────────────
_QUOTA_MARKERS = (
    "usage_limit_exceeded", "usage_limit_reached", "usage limit exceeded",
    "hit your usage limit", "plan limit reached", "limit of messages per 5 hours",
    "used up your usage", "out of credit", "credit balance", "credit_balance",
    "insufficient_quota",
)
_TIMEOUT_MARKERS = ("codex cli timeout", "timed out", "watchdog", "subprocess timeout")
_AUTH_MARKERS = (
    "unauthorized", "forbidden", "token scaduto", "login required", " 401 ", " 403 ",
)
_TRANSPORT_MARKERS = (
    "connection reset", "broken pipe", "transport", "sse", "network error",
)
_PROVIDER_UNAVAILABLE_MARKERS = (
    "non trovato", "not found", "no such file", "executable",
    "codex cli non trovato", "filenotfounderror",
)


def classify_error(exc_or_text: Any) -> dict[str, Any]:
    """Return structured error dict: code, message, category, provider, retryable.

    Categories (spec §Policy errori):
    quota_exhausted | timeout | crash | transport | auth | provider_unavailable | unknown
    """
    raw = str(exc_or_text)
    text = raw.lower()
    provider = _guess_provider(text)
    is_timeout = any(m in text for m in _TIMEOUT_MARKERS)

    if not is_timeout and any(m in text for m in _QUOTA_MARKERS):
        category, retryable = "quota_exhausted", True
    elif is_timeout:
        category, retryable = "timeout", True
    elif any(m in text for m in _AUTH_MARKERS):
        category, retryable = "auth", False
    elif any(m in text for m in _TRANSPORT_MARKERS):
        category, retryable = "transport", True
    elif any(m in text for m in _PROVIDER_UNAVAILABLE_MARKERS):
        category, retryable = "provider_unavailable", False
    else:
        category, retryable = "unknown", False

    exc_type = type(exc_or_text).__name__ if not isinstance(exc_or_text, str) else ""
    return {
        "code": exc_type,
        "message": raw[:500],
        "category": category,
        "provider": provider,
        "retryable": retryable,
    }


def _guess_provider(text_lower: str) -> str:
    if "codex" in text_lower:
        return "codex"
    if "gemini" in text_lower or "google" in text_lower:
        return "gemini"
    if "claude" in text_lower or "anthropic" in text_lower:
        return "claude"
    return "unknown"


# ── Status helpers ────────────────────────────────────────────────────────────

def normalise_status(raw: str) -> str:
    """Map any status string (legacy or canonical) to a canonical value."""
    s = str(raw or "").strip()
    if s in _CANONICAL_STATUSES:
        return s
    return _LEGACY_STATUS_MAP.get(s, "unknown")


def normalise_librarian_status(raw: str) -> str:
    mapping = {
        "in_corso": "running",
        "ok": "done",
        "errore": "failed",
        "interrotta": "failed",
        "": "pending",
    }
    s = str(raw or "").strip()
    if s in ("pending", "running", "done", "failed", "skipped"):
        return s
    return mapping.get(s, "pending")


def status_to_legacy(canonical: str) -> str:
    """Convert canonical status back to legacy UI format (for GET /api/bridge/tasks)."""
    m = {"done": "ok", "failed": "errore", "running": "in_corso", "pending": "in_corso"}
    return m.get(canonical, canonical)


def _ui_summary_from_legacy_task(t: dict) -> str:
    tid = str(t.get("id") or "").strip()
    task = " ".join(str(t.get("task") or t.get("task_type") or "task").split())
    output = " ".join(str(t.get("output") or "").split())
    status = str(t.get("status") or "")
    state = "in corso" if status == "in_corso" else ("completato" if status in ("ok", "parziale") else ("interrotta" if status == "interrotta" else "errore"))
    commit = ""
    match = re.search(r"\b(?:commit\s+)?([0-9a-f]{7,12})\b", output, flags=re.I)
    if match:
        commit = ", commit " + match.group(1)
    return f"{tid} - {task[:72]}: {state}{commit}"[:140]


# ── Canonical record builder ──────────────────────────────────────────────────

def make_canonical_record(
    task_id: str,
    *,
    session_id: str = "hermes-prime",
    agent: str = "",
    agent_id: str = "",
    task_type: str = "",
    task: str = "",
    status: str = "pending",
    created_at: float | None = None,
    started_at: float | None = None,
    finished_at: float | None = None,
    result: dict | None = None,
    error: dict | None = None,
    runtime: dict | None = None,
    librarian: dict | None = None,
    brief: dict | None = None,
    ui: dict | None = None,
) -> dict[str, Any]:
    """Build a full canonical record matching the spec §A schema."""
    now = time.time()
    return {
        "id": task_id,
        "session_id": session_id,
        "agent": agent,
        "agent_id": agent_id,
        "task_type": task_type,
        "task": task,
        "status": normalise_status(status),
        "created_at": created_at or now,
        "started_at": started_at,
        "finished_at": finished_at,
        "result": result or {
            "text": "",
            "raw_excerpt": "",
            "artifact_paths": [],
            "stdout_tail": "",
            "stderr_tail": "",
            "partial": False,
        },
        "error": error or {
            "code": "",
            "message": "",
            "category": "unknown",
            "provider": "none",
            "retryable": False,
        },
        "runtime": runtime or {
            "primary": "codex",
            "model": "",
            "fallback_runtime": "",
            "fallback_model": "",
            "fallback_reason": "",
        },
        "librarian": librarian or {
            "status": "pending",
            "provider": "none",
            "model": "",
            "started_at": None,
            "finished_at": None,
            "error": None,
            "output": "",
        },
        "brief": brief or {
            "status": "pending",
            "delivery_target": "command_bridge_prime",
            "queued_at": None,
            "delivered_at": None,
            "attempts": 0,
            "last_error": None,
            "fallback_text": "",
        },
        "ui": ui or {
            "anchor_session_id": session_id,
            "anchor_message_index": None,
            "anchor_created_at": None,
            "summary": "",
        },
    }


def bg_task_to_canonical(t: dict) -> dict[str, Any]:
    """Convert a legacy _BG_TASKS record to canonical schema (non-destructive)."""
    tid = str(t.get("id") or "")
    raw_status = str(t.get("status") or "")
    canonical_status = normalise_status(raw_status)
    raw_output = str(t.get("output") or "")
    partial = (raw_status == "parziale")

    rt_raw = str(t.get("runtime") or "codex")
    # "sonnet-fallback" → primary=codex with fallback noted
    rt_primary = "codex" if "codex" in rt_raw or "sonnet-fallback" in rt_raw else "claude"

    lib_status = normalise_librarian_status(str(t.get("librarian_status") or ""))

    return make_canonical_record(
        tid,
        session_id=str(t.get("session_id") or "hermes-prime"),
        agent=str(t.get("agent") or ""),
        agent_id=str(t.get("agent_id") or ""),
        task_type=str(t.get("task_type") or ""),
        task=str(t.get("task") or ""),
        status=canonical_status,
        created_at=t.get("started") or time.time(),
        started_at=t.get("started"),
        finished_at=t.get("finished"),
        result={
            "text": raw_output,
            "raw_excerpt": raw_output[:1200],
            "artifact_paths": [],
            "stdout_tail": "",
            "stderr_tail": "",
            "partial": partial,
        },
        runtime={
            "primary": rt_primary,
            "model": "",
            "fallback_runtime": str(t.get("fallback_runtime") or ""),
            "fallback_model": str(t.get("fallback_model") or ""),
            "fallback_reason": str(t.get("fallback_reason") or ""),
        },
        librarian={
            "status": lib_status,
            "provider": "claude",   # legacy Librarian was always claude
            "model": "claude-sonnet-4-6",
            "started_at": None,
            "finished_at": None,
            "error": None,
            "output": str(t.get("librarian_output") or ""),
        },
        ui={
            "anchor_session_id": str(t.get("anchor_session_id") or t.get("session_id") or "hermes-prime"),
            "anchor_message_index": t.get("anchor_message_index"),
            "anchor_created_at": t.get("anchor_created_at"),
            "summary": str(t.get("summary") or _ui_summary_from_legacy_task(t)),
        },
    )


# ── Store ─────────────────────────────────────────────────────────────────────

class DelegationStore:
    """Thread-safe canonical store backed by two files in <workspace>/tasks/.

    Thread safety: single RLock covers all reads and writes so that
    append-JSONL + write-state is always atomic from the caller's perspective.
    """

    def __init__(self, workspace: Path | str):
        self._ws = Path(workspace)
        self._dir = self._ws / "tasks"
        self._jsonl = self._dir / "delegations.jsonl"
        self._state = self._dir / "delegations-state.json"
        self._lock = threading.RLock()

    # -- low-level I/O --------------------------------------------------------

    def _ensure(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def _append_jsonl(self, record: dict) -> None:
        """Append one event line to the append-only log."""
        self._ensure()
        try:
            with self._jsonl.open("a", encoding="utf-8", errors="replace") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception:
            logger.debug("delegation_store: JSONL append failed", exc_info=True)

    def _read_state(self) -> dict[str, dict]:
        """Read the canonical state dict (id → record). Returns {} on any error."""
        if not self._state.is_file():
            return {}
        try:
            data = json.loads(self._state.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError, ValueError):
            return {}

    def _write_state(self, state: dict[str, dict]) -> None:
        """Write canonical state atomically via tmp-rename."""
        self._ensure()
        tmp = self._state.with_suffix(
            f"{self._state.suffix}.tmp.{os.getpid()}.{threading.get_ident()}"
        )
        try:
            tmp.write_text(
                json.dumps(state, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            os.replace(tmp, self._state)
        except Exception:
            logger.debug("delegation_store: atomic state write failed", exc_info=True)
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    # -- public API -----------------------------------------------------------

    def upsert(self, record: dict) -> dict:
        """Persist a delegation record: JSONL append + canonical state update.

        Normalises status to canonical values before writing.
        Returns the (potentially normalised) stored record.
        """
        with self._lock:
            tid = str(record.get("id") or "")
            if not tid:
                return record
            record = dict(record)
            record["status"] = normalise_status(record.get("status", "unknown"))
            self._append_jsonl(record)
            state = self._read_state()
            state[tid] = record
            self._write_state(state)
            return record

    def get(self, task_id: str) -> dict | None:
        """Return canonical record for task_id, or None."""
        with self._lock:
            return self._read_state().get(str(task_id))

    def get_all(self) -> list[dict]:
        """Return all canonical records (one per id, no duplicates)."""
        with self._lock:
            return list(self._read_state().values())

    def get_pending_briefs_count(self) -> int:
        """Count records whose brief is not yet delivered."""
        with self._lock:
            count = 0
            for rec in self._read_state().values():
                s = (rec.get("brief") or {}).get("status", "")
                if s not in ("delivered", "skipped", ""):
                    count += 1
            return count

    def mark_brief_delivered(self, task_id: str, delivered_at: float | None = None) -> None:
        """Mark brief for task_id as delivered in canonical state."""
        with self._lock:
            state = self._read_state()
            rec = state.get(str(task_id))
            if rec is None:
                return
            rec = dict(rec)
            brief = dict(rec.get("brief") or {})
            brief["status"] = "delivered"
            brief["delivered_at"] = delivered_at or time.time()
            rec["brief"] = brief
            state[str(task_id)] = rec
            self._append_jsonl(rec)
            self._write_state(state)

    def mark_brief_failed(self, task_id: str, error: str) -> None:
        """Increment attempts and mark brief failed (retryable)."""
        with self._lock:
            state = self._read_state()
            rec = state.get(str(task_id))
            if rec is None:
                return
            rec = dict(rec)
            brief = dict(rec.get("brief") or {})
            brief["status"] = "failed"
            brief["attempts"] = int(brief.get("attempts") or 0) + 1
            brief["last_error"] = str(error)[:300]
            brief["last_attempt_at"] = time.time()
            rec["brief"] = brief
            state[str(task_id)] = rec
            self._append_jsonl(rec)
            self._write_state(state)

    def recover_crashed(self, live_task_ids: set[str]) -> list[str]:
        """Mark running delegations whose id is NOT in live_task_ids as failed/crash.

        Called at boot after _load_bg_tasks() has re-populated _BG_TASKS.
        Returns list of recovered task ids.
        """
        with self._lock:
            state = self._read_state()
            recovered: list[str] = []
            now = time.time()
            for tid, rec in list(state.items()):
                if rec.get("status") != "running":
                    continue
                if tid in live_task_ids:
                    continue
                rec = dict(rec)
                rec["status"] = "failed"
                rec["finished_at"] = rec.get("finished_at") or now
                rec["error"] = {
                    "code": "WorkerInterrupted",
                    "message": "worker interrupted by WebUI restart",
                    "category": "crash",
                    "provider": (rec.get("runtime") or {}).get("primary", "unknown"),
                    "retryable": False,
                }
                state[tid] = rec
                self._append_jsonl(rec)
                recovered.append(tid)
            if recovered:
                self._write_state(state)
            return recovered

    def rebuild_from_jsonl(self) -> dict[str, dict]:
        """Reconstruct canonical state from JSONL (crash-recovery fallback).

        Use when delegations-state.json is missing or corrupt.
        Keeps the last seen record per id, normalises statuses.
        """
        with self._lock:
            if not self._jsonl.is_file():
                return {}
            latest: dict[str, dict] = {}
            try:
                lines = self._jsonl.read_text(encoding="utf-8", errors="replace").splitlines()
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    tid = rec.get("id")
                    if tid:
                        rec["status"] = normalise_status(rec.get("status", "unknown"))
                        latest[tid] = rec
            except Exception:
                logger.debug("delegation_store: rebuild_from_jsonl failed", exc_info=True)
            if latest:
                self._write_state(latest)
            return latest

    def migrate_from_legacy(self) -> int:
        """One-time migration (spec §Piano di migrazione):
        - Read tasks/delegations.jsonl
        - Group by id, keep last record
        - Normalise states
        - Write delegations-state.json
        - For terminal records without brief.delivered_at, enqueue brief

        Returns number of records migrated.
        """
        rebuilt = self.rebuild_from_jsonl()
        return len(rebuilt)


# ── Singleton ─────────────────────────────────────────────────────────────────
_STORE: DelegationStore | None = None
_STORE_LOCK = threading.Lock()


def get_delegation_store(workspace: Path | str) -> DelegationStore:
    """Return (or create) the singleton DelegationStore for the given workspace."""
    global _STORE
    ws = Path(workspace)
    with _STORE_LOCK:
        if _STORE is None or _STORE._ws != ws:
            _STORE = DelegationStore(ws)
        return _STORE
