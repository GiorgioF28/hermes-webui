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

    def _empty(self) -> dict[str, Any]:
        now = time.time()
        return {
            "session_id": PRIME_SESSION_ID,
            "created_at": now,
            "updated_at": now,
            "messages": [],
            "pending_turn": None,
            "journal": [],
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
        if not isinstance(data["messages"], list):
            data["messages"] = []
        if not isinstance(data["journal"], list):
            data["journal"] = []
        if data["pending_turn"] is not None and not isinstance(data["pending_turn"], dict):
            data["pending_turn"] = None
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
                "updated_at": data.get("updated_at"),
            }


_STORE = PrimeSessionStore()


def get_prime_session_store() -> PrimeSessionStore:
    return _STORE
