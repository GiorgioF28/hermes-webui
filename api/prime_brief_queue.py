"""Durable brief queue for Hermes Prime (Fase 1 – delega-resilienza-crediti).

Queue file: tasks/prime-brief-queue.jsonl
Each entry tracks delivery of a brief (summary) from a completed delegation
to the Command Bridge Prime chat.

Invariants (spec §B):
- brief_id = 'brief-<task_id>' is idempotent: enqueueing twice is a no-op.
- brief stays in queue until delivered.
- Fallback no-LLM text is always computed at enqueue time (no LLM needed).
- Fallback is always persisted to PrimeSessionStore; LLM brief is best-effort.
- Nessun brief può esistere solo nel DOM del browser.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_BRIEF_TERMINAL_STATUSES = frozenset({"delivered", "failed"})


def _brief_id(task_id: str) -> str:
    return f"brief-{task_id}"


_TASK_SUMMARY_MAX = 90

_SUCCESS_STATUSES = frozenset({"completed", "ok", "done", "success"})

_OUTCOME_BY_CATEGORY: dict[str, str] = {
    "quota_exhausted": (
        "Fermata: crediti esauriti dell'agente. Riparte quando la quota si "
        "resetta; nel frattempo non ho un esito affidabile."
    ),
    "timeout": (
        "Interrotta per tempo scaduto: il lavoro puo' essere parziale, va "
        "ricontrollato sul disco."
    ),
    "crash": "Fallita per un errore tecnico dell'agente. Da rilanciare.",
    "runtime_error": "Fallita per un errore tecnico dell'agente. Da rilanciare.",
    "transport": "Fallita: agente non raggiungibile. Da rilanciare.",
    "provider_unavailable": "Fallita: agente non raggiungibile. Da rilanciare.",
}

_OUTCOME_SUCCESS = "Completata. Verifico gli artefatti prima di dartela per buona."
_OUTCOME_FAILED = "Fallita. Da rilanciare."


def _summarize_task(task: str) -> str:
    """Collapse the task text to one readable line, max _TASK_SUMMARY_MAX chars."""
    one_line = " ".join(str(task or "").split())
    if len(one_line) > _TASK_SUMMARY_MAX:
        return one_line[: _TASK_SUMMARY_MAX - 3].rstrip() + "..."
    return one_line


def _build_fallback_text(
    task_id: str,
    agent: str,
    status: str,
    output: str,
    error_category: str = "",
    task: str = "",
) -> str:
    """Build deterministic fallback text (no LLM), in Italian, max 3 lines.

    Spec: docs/specs/brief-fallback-leggibile.md.
    The raw agent output is NEVER echoed in the returned text (not even
    truncated): it stays durable in tasks/delegations.jsonl and in the brief
    queue, and is reachable from the Work Log instead of the Prime chat.
    """
    who = agent or "agente"
    summary = _summarize_task(task)
    if summary:
        lines = [f"Delega {task_id} ({who}): {summary}"]
    else:
        lines = [f"Delega {task_id} ({who})."]

    category = str(error_category or "").strip().lower()
    if str(status or "").strip().lower() in _SUCCESS_STATUSES:
        lines.append(_OUTCOME_SUCCESS)
    else:
        lines.append(_OUTCOME_BY_CATEGORY.get(category, _OUTCOME_FAILED))

    if str(output or "").strip():
        lines.append("Dettaglio tecnico disponibile nel Work Log.")
    return "\n".join(lines)


def _mark_brief_on_delegation_card(store, brief: dict, message_index: Any = None) -> None:
    """Flag the durable delegation card as briefed (Bug A: reload replay).

    Keeps the card out of the client-side `requestBrief` path after a reload,
    so the anti-replay guarantee of iss-prime-brief-replay-dopo-riavvio holds.
    Best-effort: a failure here must never abort brief delivery.
    """
    try:
        task_id = str(brief.get("task_id") or "")
        if not task_id or not hasattr(store, "mark_delegation_brief"):
            return
        index = None
        if isinstance(message_index, int) and message_index >= 0:
            index = message_index
        store.mark_delegation_brief(
            task_id,
            "delivered",
            index,
            agent=str(brief.get("agent") or ""),
            task_excerpt=str(brief.get("task") or brief.get("task_type") or ""),
            status=str(brief.get("delegation_status") or ""),
        )
    except Exception:
        logger.debug("prime_brief_queue: delegation card brief flag failed", exc_info=True)


class PrimeBriefQueue:
    """Durable queue backed by tasks/prime-brief-queue.jsonl.

    Appends events (enqueue, attempt, delivered, failed) to the JSONL.
    Latest state per brief_id is reconstructed by reading the file.
    An in-process cache (_delivered) avoids re-reading for already-delivered briefs.
    """

    def __init__(self, workspace: Path | str):
        self._ws = Path(workspace)
        self._dir = self._ws / "tasks"
        self._path = self._dir / "prime-brief-queue.jsonl"
        self._lock = threading.RLock()
        self._delivered: set[str] = set()
        self._loaded = False

    # -- I/O helpers ----------------------------------------------------------

    def _ensure(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def _load_cache(self) -> None:
        """Populate in-process delivered cache from disk (idempotent, call under lock)."""
        if self._loaded:
            return
        self._loaded = True
        if not self._path.is_file():
            return
        try:
            for line in self._path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("status") == "delivered":
                    bid = str(rec.get("brief_id") or "")
                    if bid:
                        self._delivered.add(bid)
        except Exception:
            logger.debug("prime_brief_queue: load_cache failed", exc_info=True)

    def _append(self, record: dict) -> None:
        self._ensure()
        try:
            with self._path.open("a", encoding="utf-8", errors="replace") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception:
            logger.debug("prime_brief_queue: append failed", exc_info=True)

    def _read_all(self) -> dict[str, dict]:
        """Return latest state per brief_id (last-write-wins per id in JSONL)."""
        if not self._path.is_file():
            return {}
        latest: dict[str, dict] = {}
        try:
            for line in self._path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                bid = rec.get("brief_id")
                if bid:
                    latest[bid] = rec
        except Exception:
            logger.debug("prime_brief_queue: read_all failed", exc_info=True)
        return latest

    # -- public API -----------------------------------------------------------

    def enqueue(
        self,
        task_id: str,
        agent: str,
        task_type: str,
        task: str,
        status: str,
        output: str,
        error_category: str = "",
        priority: str = "normal",
        session_id: str = "hermes-prime",
    ) -> str:
        """Enqueue a brief for delivery. Idempotent: no-op if already queued/delivered.

        Returns brief_id.
        """
        bid = _brief_id(task_id)
        with self._lock:
            self._load_cache()
            if bid in self._delivered:
                return bid
            # Check on-disk state
            existing = self._read_all().get(bid)
            if existing:
                if existing.get("status") == "delivered":
                    self._delivered.add(bid)
                return bid
            # Build fallback text immediately (deterministic, no LLM)
            fb_text = _build_fallback_text(task_id, agent, status, output, error_category, task)
            rec: dict[str, Any] = {
                "brief_id": bid,
                "task_id": task_id,
                "target": "command_bridge_prime",
                "session_id": str(session_id or "hermes-prime"),
                "priority": "high" if status == "failed" else priority,
                "raw_result_ref": task_id,
                "agent": agent,
                "task_type": task_type,
                "task": task,
                "delegation_status": status,
                "error_category": error_category,
                "fallback_text": fb_text,
                "status": "pending",
                "attempts": 0,
                "queued_at": time.time(),
                "last_attempt_at": None,
                "last_error": None,
                "delivered_at": None,
            }
            self._append(rec)
            logger.debug("prime_brief_queue: enqueued %s (status=%s)", bid, status)
            return bid

    def is_delivered(self, brief_id: str) -> bool:
        """True se il brief risulta gia' consegnato (cache in-process o stato su disco).

        Serve come guardia anti-replay: dopo un riavvio il registro job
        in-memory e' vuoto e il frontend ri-POSTa le card storiche; senza
        questo check il turno LLM del brief veniva rigiocato ogni volta.
        """
        bid = str(brief_id or "")
        if not bid:
            return False
        with self._lock:
            self._load_cache()
            if bid in self._delivered:
                return True
            rec = self._read_all().get(bid)
            if rec and rec.get("status") == "delivered":
                self._delivered.add(bid)
                return True
            return False

    def get_pending(self, limit: int = 50, *, session_id: str | None = None) -> list[dict]:
        """Return pending (undelivered) briefs, highest priority first."""
        with self._lock:
            self._load_cache()
            all_briefs = self._read_all()
            pending = [
                b for b in all_briefs.values()
                if b.get("status") not in ("delivered",)
                and b.get("brief_id") not in self._delivered
                and (
                    session_id is None
                    or str(b.get("session_id") or "hermes-prime") == session_id
                )
            ]
        # Sort: high priority first, then by queued_at
        pending.sort(key=lambda b: (0 if b.get("priority") == "high" else 1, b.get("queued_at") or 0))
        return pending[:limit]

    def get_pending_count(self, *, session_id: str | None = None) -> int:
        """Count undelivered briefs."""
        return len(self.get_pending(session_id=session_id))

    def mark_delivered(self, brief_id: str) -> None:
        """Mark a brief as delivered (idempotent)."""
        with self._lock:
            self._load_cache()
            if brief_id in self._delivered:
                return
            self._delivered.add(brief_id)
            self._append({
                "brief_id": brief_id,
                "status": "delivered",
                "delivered_at": time.time(),
            })
            logger.debug("prime_brief_queue: delivered %s", brief_id)

    def mark_failed(self, brief_id: str, error: str, *, retryable: bool = True) -> None:
        """Record a failed delivery attempt."""
        with self._lock:
            all_briefs = self._read_all()
            rec = dict(all_briefs.get(brief_id) or {})
            rec["brief_id"] = brief_id
            rec["status"] = "failed_retryable" if retryable else "failed"
            rec["attempts"] = int(rec.get("attempts") or 0) + 1
            rec["last_attempt_at"] = time.time()
            rec["last_error"] = str(error)[:300]
            self._append(rec)
            logger.debug(
                "prime_brief_queue: failed %s (retryable=%s attempts=%s)",
                brief_id, retryable, rec["attempts"],
            )

    def deliver_fallback_no_llm(self, brief: dict) -> bool:
        """Persist the deterministic fallback text to PrimeSessionStore.

        Never calls any LLM. Always best-effort.
        Returns True on success.
        """
        fb_text = str(brief.get("fallback_text") or "")
        if not fb_text:
            fb_text = _build_fallback_text(
                str(brief.get("task_id") or ""),
                str(brief.get("agent") or "agente"),
                str(brief.get("delegation_status") or ""),
                "",
                str(brief.get("error_category") or ""),
                str(brief.get("task") or ""),
            )
        try:
            from api.prime_session_store import get_prime_session_store
            session_id = str(brief.get("session_id") or "hermes-prime")
            store = get_prime_session_store(session_id)
            index = store.inject_assistant_message(
                fb_text,
                meta={
                    "brief_id": brief.get("brief_id", ""),
                    "task_id": brief.get("task_id", ""),
                    "brief_type": "fallback_no_llm",
                    "delegation_status": brief.get("delegation_status", ""),
                },
            )
            _mark_brief_on_delegation_card(store, brief, index)
            return True
        except Exception:
            logger.debug("prime_brief_queue: fallback delivery failed", exc_info=True)
            return False

    def attempt_delivery(
        self,
        brief_id: str,
        *,
        try_llm: bool = True,
        hermes_prime_reply_fn: Callable | None = None,
        workspace: Path | str | None = None,
        output: str = "",
    ) -> bool:
        """Attempt to deliver a brief.

        Strategy:
        1. If try_llm=True and hermes_prime_reply_fn is provided → try LLM brief,
           persist to PrimeSessionStore, mark delivered.
        2. If LLM fails or try_llm=False → persist fallback text, mark delivered.
        3. If even fallback fails → mark failed_retryable.

        Returns True if delivered (LLM or fallback).
        """
        with self._lock:
            self._load_cache()
            if brief_id in self._delivered:
                return True
            all_briefs = self._read_all()
            brief = all_briefs.get(brief_id)
            if not brief:
                return False
            if brief.get("status") == "delivered":
                self._delivered.add(brief_id)
                return True

        # Attempt LLM brief (outside lock to avoid blocking)
        llm_delivered = False
        if try_llm and hermes_prime_reply_fn is not None:
            try:
                task_id = str(brief.get("task_id") or "")
                agent = str(brief.get("agent") or "agente")
                task = str(brief.get("task") or "")
                status = str(brief.get("delegation_status") or "")
                error_cat = str(brief.get("error_category") or "")
                excerpt = (output or "").strip()[:600]
                brief_msg = (
                    f"[BRIEF AUTOMATICO] La delega {task_id} affidata a '{agent}' è terminata.\n"
                    f"Task: {task}\n"
                    f"Stato: {status}"
                    + (f" | Errore: {error_cat}" if error_cat else "")
                    + "\n"
                    f"Risultato: {excerpt}\n\n"
                    "Fai un brief all'utente in 1-2 frasi (cosa è stato prodotto e prossimo "
                    "passo). NON usare il tool delega, NON re-delegare, risposta solo testuale."
                )
                result = hermes_prime_reply_fn(brief_msg, workspace)
                reply = (result.get("reply") or "").strip()
                if reply:
                    # Capture LLM usage (token counts + cost) so the frontend
                    # can display the same usage badge as normal chat turns.
                    # Falls back to {} when the LLM returns no usage — never
                    # invent values.
                    usage = result.get("usage") or {}
                    from api.prime_session_store import get_prime_session_store
                    session_id = str(brief.get("session_id") or "hermes-prime")
                    store = get_prime_session_store(session_id)
                    index = store.inject_assistant_message(
                        reply,
                        meta={
                            "brief_id": brief_id,
                            "task_id": brief.get("task_id", ""),
                            "brief_type": "llm",
                            "delegation_status": status,
                            "usage": usage,
                        },
                    )
                    _mark_brief_on_delegation_card(store, brief, index)
                    llm_delivered = True
            except Exception as exc:
                logger.debug(
                    "prime_brief_queue: LLM delivery failed for %s: %s", brief_id, exc
                )

        if llm_delivered:
            self.mark_delivered(brief_id)
            return True

        # Fallback: deterministic no-LLM text
        ok = self.deliver_fallback_no_llm(brief)
        if ok:
            self.mark_delivered(brief_id)
        else:
            self.mark_failed(brief_id, "fallback delivery to PrimeSessionStore failed")
        return ok

    def drain_pending(
        self,
        *,
        try_llm: bool = True,
        hermes_prime_reply_fn: Callable | None = None,
        workspace: Path | str | None = None,
        max_briefs: int = 5,
        session_id: str | None = None,
    ) -> int:
        """Attempt delivery of up to max_briefs pending briefs.

        Returns the count of briefs successfully delivered.
        Best-effort: exceptions per brief are swallowed to not abort the drain.
        """
        pending = self.get_pending(session_id=session_id)[:max_briefs]
        delivered = 0
        for brief in pending:
            bid = str(brief.get("brief_id") or "")
            if not bid:
                continue
            # Fetch output from delegation_store if workspace available
            out = str(brief.get("fallback_text") or "")
            if workspace:
                try:
                    from api.delegation_store import get_delegation_store
                    rec = get_delegation_store(workspace).get(str(brief.get("task_id") or ""))
                    if rec:
                        out = str((rec.get("result") or {}).get("text") or "") or out
                except Exception:
                    pass
            try:
                ok = self.attempt_delivery(
                    bid,
                    try_llm=try_llm,
                    hermes_prime_reply_fn=hermes_prime_reply_fn,
                    workspace=workspace,
                    output=out,
                )
                if ok:
                    delivered += 1
            except Exception:
                logger.debug("prime_brief_queue: drain: error on %s", bid, exc_info=True)
        return delivered


# ── Singleton ─────────────────────────────────────────────────────────────────
_QUEUE: PrimeBriefQueue | None = None
_QUEUE_LOCK = threading.Lock()


def get_brief_queue(workspace: Path | str) -> PrimeBriefQueue:
    """Return (or create) the singleton PrimeBriefQueue for the given workspace."""
    global _QUEUE
    ws = Path(workspace)
    with _QUEUE_LOCK:
        if _QUEUE is None or _QUEUE._ws != ws:
            _QUEUE = PrimeBriefQueue(ws)
        return _QUEUE
