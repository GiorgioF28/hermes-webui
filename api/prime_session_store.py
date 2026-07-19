"""Persistent transcript and pending-turn journal for Hermes Prime bridge."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from api.config import SESSION_DIR


PRIME_SESSION_ID = "hermes-prime"


class PrimeSessionStore:
    """Small append-only JSON store for the Command Bridge Prime transcript."""

    def __init__(self, path: Path | None = None):
        self.path = path or (Path(SESSION_DIR) / "_bridge_prime_session.json")
        self._lock = threading.RLock()
        self.recover_stale_pending_turn("interrupted by WebUI restart")

    def _empty(self) -> dict[str, Any]:
        now = time.time()
        return {
            "session_id": PRIME_SESSION_ID,
            "created_at": now,
            "updated_at": now,
            "messages": [],
            "pending_turn": None,
            "journal": [],
            "settings": {},
        }

    def _read_locked(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return self._empty()
        if not isinstance(data, dict):
            return self._empty()
        data.setdefault("session_id", PRIME_SESSION_ID)
        data.setdefault("created_at", time.time())
        data.setdefault("updated_at", data.get("created_at") or time.time())
        data.setdefault("messages", [])
        data.setdefault("pending_turn", None)
        data.setdefault("journal", [])
        data.setdefault("settings", {})
        if not isinstance(data["messages"], list):
            data["messages"] = []
        if not isinstance(data["journal"], list):
            data["journal"] = []
        if data["pending_turn"] is not None and not isinstance(data["pending_turn"], dict):
            data["pending_turn"] = None
        if not isinstance(data["settings"], dict):
            data["settings"] = {}
        return data

    def _write_locked(self, data: dict[str, Any]) -> None:
        data["updated_at"] = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + f".tmp.{os.getpid()}.{threading.get_ident()}")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

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
            self._append_journal_locked(data, "turn_started", {"stream_id": stream_id, "role": "user"})
            self._write_locked(data)
        return stream_id

    def append_token(self, stream_id: str, text: str) -> None:
        if not text:
            return
        with self._lock:
            data = self._read_locked()
            pending = data.get("pending_turn")
            if not pending or pending.get("stream_id") != stream_id:
                return
            pending["partial_output"] = str(pending.get("partial_output") or "") + str(text)
            pending["updated_at"] = time.time()
            self._append_journal_locked(data, "token", {"stream_id": stream_id, "text": str(text)})
            self._write_locked(data)

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

    def history(self) -> dict[str, Any]:
        with self._lock:
            data = self._read_locked()
            pending = data.get("pending_turn")
            if pending and pending.get("partial_output"):
                pending = dict(pending)
                pending["recovered"] = True
            return {
                "session_id": PRIME_SESSION_ID,
                "messages": list(data.get("messages") or []),
                "pending_turn": pending,
                "settings": dict(data.get("settings") or {}),
                "updated_at": data.get("updated_at"),
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
            }

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

    def inject_assistant_message(self, content: str, meta: dict | None = None) -> None:
        """Inject an assistant message directly into the transcript, bypassing turn lifecycle.

        Used by prime_brief_queue to persist brief/fallback texts durably so they
        survive browser refresh and server restart (spec §B Consegna fallback senza LLM).
        The message is visible in GET /api/bridge/prime/history.

        meta dict is merged into the message record (use for brief_id, task_id, etc.).
        Never saves secrets — callers must not pass credentials.
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
            self._append_journal_locked(
                data,
                "injected_message",
                {
                    "chars": len(str(content or "")),
                    "meta_keys": sorted((meta or {}).keys()),
                },
            )
            self._write_locked(data)

    def get_tool_events(self, stream_id: str | None = None) -> list[dict]:
        """Return tool events from the journal, optionally filtered by stream_id (P2-C)."""
        with self._lock:
            journal = list(self._read_locked().get("journal") or [])
        events = [e for e in journal if isinstance(e, dict) and e.get("event") == "tool_call"]
        if stream_id:
            events = [e for e in events if e.get("stream_id") == stream_id]
        return events

    def history_with_tool_events(self) -> dict:
        """Like history() but also includes recent tool events for cold-load (P2-C)."""
        with self._lock:
            data = self._read_locked()
            pending = data.get("pending_turn")
            if pending and pending.get("partial_output"):
                pending = dict(pending)
                pending["recovered"] = True
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
        return {
            "session_id": "hermes-prime",
            "messages": list(data.get("messages") or []),
            "pending_turn": pending,
            "settings": dict(data.get("settings") or {}),
            "updated_at": data.get("updated_at"),
            "tool_events": tool_events,
        }


_STORE = PrimeSessionStore()


def get_prime_session_store() -> PrimeSessionStore:
    return _STORE
