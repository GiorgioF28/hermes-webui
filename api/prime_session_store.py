"""Persistent transcript and pending-turn journal for Hermes Prime bridge."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from api.config import SESSION_DIR

logger = logging.getLogger(__name__)

PRIME_SESSION_ID = "hermes-prime"

# Streaming tokens used to rewrite the whole session file once per token: with a
# ~2 MB transcript that saturates disk I/O and serialises /live, /history and
# /todos behind the store lock (the "Request timed out" toast storm). Tokens are
# now accumulated in RAM and flushed at most every PARTIAL_FLUSH_INTERVAL_S, or
# every PARTIAL_FLUSH_MAX_TOKENS, and always on turn end / abort / error.
PARTIAL_FLUSH_INTERVAL_S = 1.5
PARTIAL_FLUSH_MAX_TOKENS = 64

# Durable delegation cards (Bug A): bounded list kept inside the session file.
DELEGATIONS_CAP = 200
_DELEGATION_FIELDS = (
    "agent",
    "task_excerpt",
    "status",
    "started_at",
    "finished_at",
    "anchor_message_index",
    "brief_status",
    "brief_message_index",
)


class PrimeSessionStore:
    """Small append-only JSON store for the Command Bridge Prime transcript."""

    def __init__(self, path: Path | None = None, *, session_id: str = PRIME_SESSION_ID):
        self.session_id = str(session_id or PRIME_SESSION_ID)
        self.path = path or (Path(SESSION_DIR) / "_bridge_prime_session.json")
        self._lock = threading.RLock()
        # In-RAM streaming buffer (see PARTIAL_FLUSH_* above).
        self._buf_stream_id: str | None = None
        self._buf_partial: str = ""
        self._buf_updated_at: float = 0.0
        self._buf_dirty: bool = False
        self._buf_tokens_since_flush: int = 0
        self._buf_last_flush: float = 0.0
        self._cleanup_orphan_tmp_files()
        self.recover_stale_pending_turn("interrupted by WebUI restart")

    def _cleanup_orphan_tmp_files(self) -> None:
        """Drop empty ``*.tmp.*`` leftovers from a crashed write (best effort)."""
        try:
            parent = self.path.parent
            if not parent.is_dir():
                return
            for tmp in parent.glob(self.path.name + ".tmp.*"):
                try:
                    if tmp.is_file() and tmp.stat().st_size == 0:
                        tmp.unlink()
                        logger.warning("prime session store: removed orphan empty temp file %s", tmp.name)
                except OSError:
                    continue
        except Exception:
            logger.debug("prime session store: orphan tmp cleanup failed", exc_info=True)

    def _quarantine_corrupt_locked(self, exc: Exception) -> None:
        """Rename an unparsable session file instead of silently losing it."""
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        target = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
        suffix = 1
        while target.exists() and suffix < 100:
            target = self.path.with_name(f"{self.path.name}.corrupt-{stamp}-{suffix}")
            suffix += 1
        try:
            os.replace(self.path, target)
        except OSError:
            logger.warning(
                "prime session store: %s is corrupt (%s) and could not be quarantined; starting empty",
                self.path,
                exc,
            )
            return
        logger.warning(
            "prime session store: %s was corrupt (%s); quarantined as %s, starting from an empty session",
            self.path,
            exc,
            target.name,
        )

    def _empty(self) -> dict[str, Any]:
        now = time.time()
        return {
            "session_id": self.session_id,
            "created_at": now,
            "updated_at": now,
            "messages": [],
            "pending_turn": None,
            "journal": [],
            "settings": {},
            "delegations": [],
            "delegations_rev": 0,
        }

    def _read_locked(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except OSError as exc:
            logger.warning("prime session store: cannot read %s (%s); serving an empty session", self.path, exc)
            return self._empty()
        except (json.JSONDecodeError, ValueError) as exc:
            self._quarantine_corrupt_locked(exc)
            return self._empty()
        if not isinstance(data, dict):
            self._quarantine_corrupt_locked(ValueError("session root is not an object"))
            return self._empty()
        data.setdefault("session_id", self.session_id)
        data.setdefault("created_at", time.time())
        data.setdefault("updated_at", data.get("created_at") or time.time())
        data.setdefault("messages", [])
        data.setdefault("pending_turn", None)
        data.setdefault("journal", [])
        data.setdefault("settings", {})
        data.setdefault("delegations", [])
        data.setdefault("delegations_rev", 0)
        if not isinstance(data["delegations"], list):
            data["delegations"] = []
        try:
            data["delegations_rev"] = int(data["delegations_rev"])
        except (TypeError, ValueError):
            data["delegations_rev"] = 0
        if not isinstance(data["messages"], list):
            data["messages"] = []
        if not isinstance(data["journal"], list):
            data["journal"] = []
        if data["pending_turn"] is not None and not isinstance(data["pending_turn"], dict):
            data["pending_turn"] = None
        if not isinstance(data["settings"], dict):
            data["settings"] = {}
        self._overlay_buffer(data)
        return data

    def _overlay_buffer(self, data: dict[str, Any]) -> None:
        """Make the un-flushed streaming partial visible to every reader."""
        if not self._buf_stream_id or not self._buf_dirty:
            return
        pending = data.get("pending_turn")
        if not isinstance(pending, dict) or pending.get("stream_id") != self._buf_stream_id:
            return
        pending["partial_output"] = self._buf_partial
        if self._buf_updated_at:
            pending["updated_at"] = self._buf_updated_at

    def _write_locked(self, data: dict[str, Any]) -> None:
        data["updated_at"] = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + f".tmp.{os.getpid()}.{threading.get_ident()}")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
        # Every write goes through _read_locked()'s overlay, so persisting any
        # mutation also persists the buffered partial: the buffer is clean again.
        self._buf_dirty = False
        self._buf_tokens_since_flush = 0
        self._buf_last_flush = time.time()

    def _append_journal_locked(self, data: dict[str, Any], event: str, payload: dict[str, Any]) -> None:
        journal = data.setdefault("journal", [])
        journal.append({"ts": time.time(), "event": event, **payload})
        if len(journal) > 500:
            del journal[:-500]

    def recover_stale_pending_turn(self, reason: str = "interrupted by WebUI restart") -> bool:
        """Close a persisted in-flight Prime turn on cold load.

        WebUI-owned Prime execution cannot survive a process restart. Keeping a
        pending turn visible after load makes the browser/SDK treat stale work as
        live; promote any partial text to an interrupted assistant message and
        clear the pending marker instead.
        """
        with self._lock:
            data = self._read_locked()
            pending = data.get("pending_turn")
            if not isinstance(pending, dict):
                return False
            stream_id = str(pending.get("stream_id") or "")
            partial = str(pending.get("partial_output") or "")
            if partial:
                data["messages"].append(
                    {
                        "role": "assistant",
                        "content": partial,
                        "created_at": time.time(),
                        "interrupted": True,
                        "error": str(reason or "interrupted by WebUI restart"),
                    }
                )
            data["pending_turn"] = None
            self._append_journal_locked(
                data,
                "turn_interrupted_on_restart",
                {"stream_id": stream_id, "partial_chars": len(partial)},
            )
            self._write_locked(data)
            self._reset_buffer_locked(None)
            return True

    def begin_turn(self, message: str, attachments: list[dict] | None = None) -> str:
        stream_id = uuid.uuid4().hex
        clean_message = str(message or "")
        with self._lock:
            data = self._read_locked()
            data["messages"].append(
                {
                    "role": "user",
                    "content": clean_message,
                    "created_at": time.time(),
                    "attachments": attachments or [],
                }
            )
            data["pending_turn"] = {
                "stream_id": stream_id,
                "message": clean_message,
                "attachments": attachments or [],
                "started_at": time.time(),
                "partial_output": "",
                "recovered": False,
            }
            self._reset_buffer_locked(stream_id)
            self._append_journal_locked(data, "turn_started", {"stream_id": stream_id, "role": "user"})
            self._write_locked(data)
        return stream_id

    def _reset_buffer_locked(self, stream_id: str | None, partial: str = "") -> None:
        self._buf_stream_id = str(stream_id) if stream_id else None
        self._buf_partial = str(partial or "")
        self._buf_updated_at = 0.0
        self._buf_dirty = False
        self._buf_tokens_since_flush = 0
        self._buf_last_flush = time.time()

    def append_token(self, stream_id: str, text: str) -> None:
        """Accumulate a streaming token in RAM; flush to disk at most every ~1.5 s.

        The previous implementation rewrote the entire session file once per
        token. ``live()``/``history()`` still observe the fresh partial because
        ``_read_locked()`` overlays the in-RAM buffer, and every turn-terminal
        path (finish/cancel/error/restart-recovery) writes it out.
        """
        if not text:
            return
        with self._lock:
            if self._buf_stream_id != stream_id:
                data = self._read_locked()
                pending = data.get("pending_turn")
                if not isinstance(pending, dict) or pending.get("stream_id") != stream_id:
                    return
                self._reset_buffer_locked(stream_id, str(pending.get("partial_output") or ""))
            self._buf_partial += str(text)
            self._buf_updated_at = time.time()
            self._buf_dirty = True
            self._buf_tokens_since_flush += 1
            due = (self._buf_updated_at - self._buf_last_flush) >= PARTIAL_FLUSH_INTERVAL_S
            if due or self._buf_tokens_since_flush >= PARTIAL_FLUSH_MAX_TOKENS:
                self._flush_partial_locked()

    def _flush_partial_locked(self) -> bool:
        if not self._buf_dirty or not self._buf_stream_id:
            return False
        data = self._read_locked()  # overlays the buffer onto the pending turn
        pending = data.get("pending_turn")
        if not isinstance(pending, dict) or pending.get("stream_id") != self._buf_stream_id:
            self._buf_dirty = False
            return False
        self._append_journal_locked(
            data,
            "token_flush",
            {
                "stream_id": self._buf_stream_id,
                "chars": len(self._buf_partial),
                "tokens": self._buf_tokens_since_flush,
            },
        )
        self._write_locked(data)
        return True

    def flush_partial(self, stream_id: str | None = None) -> bool:
        """Force the buffered partial to disk (turn end / abort / error)."""
        with self._lock:
            if stream_id is not None and self._buf_stream_id != str(stream_id):
                return False
            return self._flush_partial_locked()

    def has_buffered_partial(self) -> bool:
        with self._lock:
            return bool(self._buf_dirty and self._buf_stream_id)

    def finish_turn(self, stream_id: str, reply: str, usage: dict | None = None) -> None:
        with self._lock:
            data = self._read_locked()
            pending = data.get("pending_turn")
            final_reply = str(reply or "")
            if pending and pending.get("stream_id") == stream_id:
                final_reply = final_reply or str(pending.get("partial_output") or "")
                data["pending_turn"] = None
            if final_reply:
                data["messages"].append(
                    {
                        "role": "assistant",
                        "content": final_reply,
                        "created_at": time.time(),
                        "usage": usage or {},
                    }
                )
            self._append_journal_locked(data, "turn_finished", {"stream_id": stream_id})
            self._write_locked(data)
            if self._buf_stream_id == stream_id:
                self._reset_buffer_locked(None)

    def append_clarify_request(self, clarify_id: str, payload: dict[str, Any]) -> None:
        """Persist a Prime AskUserQuestion/clarify card in the visible transcript."""
        clarify_id = str(clarify_id or "").strip()
        if not clarify_id:
            return
        with self._lock:
            data = self._read_locked()
            for msg in data.get("messages") or []:
                if (
                    isinstance(msg, dict)
                    and msg.get("_bridge_clarify_id") == clarify_id
                    and msg.get("_bridge_clarify_event") == "request"
                ):
                    return
            data["messages"].append(
                {
                    "role": "assistant",
                    "content": self._clarify_request_text(payload),
                    "created_at": time.time(),
                    "_bridge_attention_kind": "clarify",
                    "_bridge_clarify_event": "request",
                    "_bridge_clarify_id": clarify_id,
                    "_bridge_clarify_payload": dict(payload or {}),
                }
            )
            self._append_journal_locked(
                data,
                "clarify_requested",
                {"clarify_id": clarify_id, "kind": str((payload or {}).get("kind") or "clarify")},
            )
            self._write_locked(data)

    def append_clarify_response(self, clarify_id: str, response: Any) -> None:
        """Persist Giorgio's answer to a Prime clarify card."""
        clarify_id = str(clarify_id or "").strip()
        if not clarify_id:
            return
        with self._lock:
            data = self._read_locked()
            # The request message is the durable card state. Mark it resolved so
            # replay can restore the green, disabled card and selected answer.
            for msg in data.get("messages") or []:
                if (
                    isinstance(msg, dict)
                    and msg.get("_bridge_clarify_id") == clarify_id
                    and msg.get("_bridge_clarify_event") == "request"
                ):
                    msg["_bridge_clarify_resolved"] = True
                    msg["_bridge_clarify_response"] = response
                    msg["_bridge_clarify_resolved_at"] = time.time()
                    break
            for msg in data.get("messages") or []:
                if (
                    isinstance(msg, dict)
                    and msg.get("_bridge_clarify_id") == clarify_id
                    and msg.get("_bridge_clarify_event") == "response"
                ):
                    self._write_locked(data)
                    return
            data["messages"].append(
                {
                    "role": "user",
                    "content": self._clarify_response_text(response),
                    "created_at": time.time(),
                    "_clarify_response": True,
                    "_bridge_clarify_event": "response",
                    "_bridge_clarify_id": clarify_id,
                    "_bridge_clarify_response": response,
                }
            )
            self._append_journal_locked(data, "clarify_responded", {"clarify_id": clarify_id})
            self._write_locked(data)

    @staticmethod
    def _clarify_request_text(payload: dict[str, Any]) -> str:
        payload = payload or {}
        title = str(payload.get("question") or "Serve una scelta").strip()
        questions = payload.get("questions") if isinstance(payload.get("questions"), list) else []
        lines = [f"Domanda per Giorgio: {title}"]
        for idx, q in enumerate(questions, 1):
            if not isinstance(q, dict):
                continue
            q_text = str(q.get("question") or q.get("text") or q.get("prompt") or "").strip()
            if q_text and q_text != title:
                lines.append(f"{idx}. {q_text}")
            options = q.get("options") if isinstance(q.get("options"), list) else q.get("choices")
            labels = []
            for opt in options or []:
                if isinstance(opt, dict):
                    label = str(opt.get("label") or opt.get("value") or opt.get("text") or "").strip()
                else:
                    label = str(opt or "").strip()
                if label:
                    labels.append(label)
            if labels:
                lines.append("Opzioni: " + ", ".join(labels))
        if not questions:
            choices = [str(c).strip() for c in (payload.get("choices_offered") or []) if str(c).strip()]
            if choices:
                lines.append("Opzioni: " + ", ".join(choices))
        return "\n".join(lines).strip()

    @staticmethod
    def _clarify_response_text(response: Any) -> str:
        answers = response.get("answers") if isinstance(response, dict) else None
        if isinstance(answers, dict) and answers:
            lines = []
            for question, answer in answers.items():
                value = ", ".join(str(item) for item in answer) if isinstance(answer, list) else str(answer)
                lines.append(f"{question}: {value}")
            return "\n".join(lines)
        if isinstance(response, (dict, list)):
            return json.dumps(response, ensure_ascii=False)
        return str(response or "").strip()

    def cancel_turn(self, stream_id: str, reason: str = "cancelled") -> dict[str, Any]:
        with self._lock:
            data = self._read_locked()
            pending = data.get("pending_turn")
            partial = ""
            promoted = False
            if pending and pending.get("stream_id") == stream_id:
                partial = str(pending.get("partial_output") or "")
                if partial:
                    data["messages"].append(
                        {
                            "role": "assistant",
                            "content": partial,
                            "created_at": time.time(),
                            "interrupted": True,
                            "cancelled": True,
                            "error": str(reason or "cancelled"),
                        }
                    )
                    promoted = True
                pending["cancelled"] = True
                pending["error"] = str(reason or "cancelled")
                data["pending_turn"] = None
            self._append_journal_locked(
                data,
                "turn_cancelled",
                {"stream_id": stream_id, "partial_chars": len(partial), "promoted": promoted},
            )
            self._write_locked(data)
            if self._buf_stream_id == stream_id:
                self._reset_buffer_locked(None)
            return {"stream_id": stream_id, "partial_output": partial, "promoted": promoted}

    def mark_error(self, stream_id: str, error: str, *, keep_pending: bool = True) -> None:
        with self._lock:
            data = self._read_locked()
            pending = data.get("pending_turn")
            if pending and pending.get("stream_id") == stream_id:
                pending["error"] = str(error or "")
                pending["updated_at"] = time.time()
                if not keep_pending:
                    partial = str(pending.get("partial_output") or "")
                    if partial:
                        data["messages"].append(
                            {
                                "role": "assistant",
                                "content": partial,
                                "created_at": time.time(),
                                "error": str(error or ""),
                            }
                        )
                    data["pending_turn"] = None
            self._append_journal_locked(data, "turn_error", {"stream_id": stream_id, "error": str(error or "")})
            self._write_locked(data)
            if not keep_pending and self._buf_stream_id == stream_id:
                self._reset_buffer_locked(None)

    @staticmethod
    def _clamp_since_index(since_index: Any, total: int) -> int:
        try:
            value = int(since_index)
        except (TypeError, ValueError):
            return 0
        if value < 0:
            return 0
        return min(value, total)

    def history(self, since_index: Any = 0) -> dict[str, Any]:
        """Persisted transcript.

        ``since_index`` slices ``messages[since_index:]`` so a browser that has
        already rendered N messages can fetch just the tail (multi-device sync)
        instead of re-downloading the whole ~2 MB transcript. ``total`` is the
        full message count regardless of the slice.
        """
        with self._lock:
            data = self._read_locked()
            pending = data.get("pending_turn")
            if pending and pending.get("partial_output"):
                pending = dict(pending)
                pending["recovered"] = True
            messages = list(data.get("messages") or [])
            total = len(messages)
            start = self._clamp_since_index(since_index, total)
            return {
                "session_id": self.session_id,
                "messages": messages[start:] if start else messages,
                "pending_turn": pending,
                "settings": dict(data.get("settings") or {}),
                "updated_at": data.get("updated_at"),
                "since_index": start,
                "total": total,
                "message_count": total,
                "delegations": self._public_delegations(data),
                "delegations_rev": int(data.get("delegations_rev") or 0),
            }

    def live(self) -> dict[str, Any]:
        with self._lock:
            data = self._read_locked()
            pending = data.get("pending_turn")
            return {
                "ok": True,
                "active": bool(pending),
                "pending_turn": dict(pending) if isinstance(pending, dict) else None,
                "settings": dict(data.get("settings") or {}),
                "updated_at": data.get("updated_at"),
                "message_count": len(data.get("messages") or []),
                "delegations_rev": int(data.get("delegations_rev") or 0),
            }

    # -- Durable delegation cards (Bug A) ------------------------------------

    @staticmethod
    def _public_delegations(data: dict[str, Any]) -> list[dict[str, Any]]:
        return [dict(item) for item in (data.get("delegations") or []) if isinstance(item, dict)]

    def get_delegations(self) -> list[dict[str, Any]]:
        """Durable delegation card records, oldest first."""
        with self._lock:
            return self._public_delegations(self._read_locked())

    def get_delegations_rev(self) -> int:
        with self._lock:
            return int(self._read_locked().get("delegations_rev") or 0)

    def upsert_delegation(self, task_id: str, **fields: Any) -> dict[str, Any] | None:
        """Create/update the durable record backing a delegation card.

        Only non-empty values overwrite existing ones, so a later poll that
        knows less than the launch never downgrades a record. Writes (and bump
        ``delegations_rev``) happen only when something actually changed, so the
        3 s ``/api/bridge/tasks`` poll does not rewrite the session file.
        """
        tid = str(task_id or "").strip()
        if not tid:
            return None
        with self._lock:
            data = self._read_locked()
            delegations = data.get("delegations")
            if not isinstance(delegations, list):
                delegations = []
                data["delegations"] = delegations
            record = None
            for item in delegations:
                if isinstance(item, dict) and str(item.get("id") or "") == tid:
                    record = item
                    break
            changed = False
            if record is None:
                record = {
                    "id": tid,
                    "agent": "",
                    "task_excerpt": "",
                    "status": "",
                    "started_at": None,
                    "finished_at": None,
                    "anchor_message_index": None,
                    "brief_status": "",
                    "brief_message_index": None,
                }
                delegations.append(record)
                changed = True
            for key in _DELEGATION_FIELDS:
                if key not in fields:
                    continue
                value = fields[key]
                if value is None or value == "":
                    continue
                if key == "task_excerpt":
                    value = str(value).strip()[:300]
                if record.get(key) != value:
                    record[key] = value
                    changed = True
            if len(delegations) > DELEGATIONS_CAP:
                del delegations[:-DELEGATIONS_CAP]
            if not changed:
                return dict(record)
            data["delegations_rev"] = int(data.get("delegations_rev") or 0) + 1
            self._append_journal_locked(
                data,
                "delegation_updated",
                {"task_id": tid, "status": str(record.get("status") or "")},
            )
            self._write_locked(data)
            return dict(record)

    def mark_delegation_brief(
        self,
        task_id: str,
        brief_status: str,
        brief_message_index: int | None = None,
        **fields: Any,
    ) -> dict[str, Any] | None:
        """Record that a delegation brief was delivered/injected into the chat."""
        payload = dict(fields)
        payload["brief_status"] = str(brief_status or "")
        if brief_message_index is not None:
            payload["brief_message_index"] = int(brief_message_index)
        return self.upsert_delegation(task_id, **payload)

    def get_settings(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._read_locked().get("settings") or {})

    def update_settings(self, **values: Any) -> dict[str, Any]:
        with self._lock:
            data = self._read_locked()
            settings = dict(data.get("settings") or {})
            for key, value in values.items():
                if value is None:
                    settings.pop(key, None)
                else:
                    settings[key] = value
            data["settings"] = settings
            self._append_journal_locked(data, "settings_updated", {"keys": sorted(values.keys())})
            self._write_locked(data)
            return dict(settings)

    def update_todo_snapshot(self, snapshot: dict | None) -> None:
        """Store the latest todo snapshot in settings for cold-load (P2-B)."""
        with self._lock:
            data = self._read_locked()
            settings = dict(data.get("settings") or {})
            if snapshot is None:
                settings.pop("todo_snapshot", None)
            else:
                settings["todo_snapshot"] = snapshot
            data["settings"] = settings
            self._write_locked(data)

    def get_todo_snapshot(self) -> dict | None:
        """Return the last stored todo snapshot, or None (P2-B)."""
        with self._lock:
            return dict(self._read_locked().get("settings") or {}).get("todo_snapshot")

    def append_tool_event(self, stream_id: str, tool_name: str, result_summary: str = "") -> None:
        """Journal a tool call event for replay on cold-load (P2-C).

        Appended to journal as event='tool_call' alongside the existing token/
        turn events. Kept to 200 tool events per journal (older ones pruned).
        """
        with self._lock:
            data = self._read_locked()
            journal = data.setdefault("journal", [])
            journal.append({
                "ts": time.time(),
                "event": "tool_call",
                "stream_id": stream_id,
                "tool": tool_name,
                "summary": str(result_summary or "")[:500],
            })
            # Keep tool events bounded: prune oldest beyond 500 total (existing limit)
            if len(journal) > 500:
                del journal[:-500]
            self._write_locked(data)

    def inject_assistant_message(self, content: str, meta: dict | None = None) -> int:
        """Inject an assistant message directly into the transcript, bypassing turn lifecycle.

        Used by prime_brief_queue to persist brief/fallback texts durably so they
        survive browser refresh and server restart (spec §B Consegna fallback senza LLM).
        The message is visible in GET /api/bridge/prime/history.

        meta dict is merged into the message record (use for brief_id, task_id, etc.).
        Never saves secrets — callers must not pass credentials.

        Returns the index of the appended message in the transcript, so callers
        can anchor a durable delegation card to it.
        """
        with self._lock:
            data = self._read_locked()
            msg: dict = {
                "role": "assistant",
                "content": str(content or ""),
                "created_at": time.time(),
                "injected": True,
            }
            if meta:
                # Only merge safe scalar/string keys, never nested secrets
                for k, v in meta.items():
                    if isinstance(k, str) and k not in ("content", "role"):
                        msg[k] = v
            data["messages"].append(msg)
            index = len(data["messages"]) - 1
            self._append_journal_locked(
                data,
                "injected_message",
                {
                    "chars": len(str(content or "")),
                    "meta_keys": sorted((meta or {}).keys()),
                },
            )
            self._write_locked(data)
            return index

    def get_tool_events(self, stream_id: str | None = None) -> list[dict]:
        """Return tool events from the journal, optionally filtered by stream_id (P2-C)."""
        with self._lock:
            journal = list(self._read_locked().get("journal") or [])
        events = [e for e in journal if isinstance(e, dict) and e.get("event") == "tool_call"]
        if stream_id:
            events = [e for e in events if e.get("stream_id") == stream_id]
        return events

    def history_with_tool_events(self, since_index: Any = 0) -> dict:
        """Like history() but also includes recent tool events for cold-load (P2-C)."""
        with self._lock:
            payload = self.history(since_index)
            data = self._read_locked()
            pending = payload.get("pending_turn")
            journal = list(data.get("journal") or [])
        tool_events = [e for e in journal if isinstance(e, dict) and e.get("event") == "tool_call"]
        # Only return tool events for the current or most recent stream
        if pending:
            sid = pending.get("stream_id", "")
        else:
            # Find most recent stream_id from journal
            sid = ""
            for e in reversed(journal):
                if isinstance(e, dict) and e.get("stream_id"):
                    sid = e["stream_id"]
                    break
        if sid:
            tool_events = [e for e in tool_events if e.get("stream_id") == sid]
        else:
            tool_events = tool_events[-20:]  # last 20 if no stream_id
        payload["tool_events"] = tool_events
        return payload


_STORE = PrimeSessionStore()
_STORES: dict[str, PrimeSessionStore] = {PRIME_SESSION_ID: _STORE}
_STORES_LOCK = threading.Lock()


def get_prime_session_store(session_id: str = PRIME_SESSION_ID) -> PrimeSessionStore:
    """Return the isolated store for a Prime bridge session.

    The no-argument path intentionally returns the original singleton and file,
    preserving Giorgio's persisted session byte-for-byte.
    """
    sid = str(session_id or PRIME_SESSION_ID)
    if sid == PRIME_SESSION_ID:
        return _STORE
    with _STORES_LOCK:
        store = _STORES.get(sid)
        if store is None:
            if sid != "hermes-prime-tom":
                raise ValueError("Unsupported Prime session")
            store = PrimeSessionStore(
                Path(SESSION_DIR) / "_bridge_prime_session_tom.json",
                session_id=sid,
            )
            _STORES[sid] = store
        return store
