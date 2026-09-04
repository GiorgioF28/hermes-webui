"""Clarify prompt state for the WebUI.

This mirrors the approval flow structure, but the response is a free-form
clarification string instead of an approval decision.
"""

from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from typing import Any, Optional

from api.session_events import publish_session_list_changed


DEFAULT_TIMEOUT_SECONDS = 120
NO_RESPONSE_FALLBACK = (
    "The user did not provide a response within the time limit. "
    "Use your best judgement to make the choice and proceed."
)
_lock = threading.Lock()
_pending: dict[str, dict] = {}
_gateway_queues: dict[str, list] = {}
_gateway_notify_cbs: dict[str, object] = {}

# ── SSE subscriber registry ─────────────────────────────────────────────
_clarify_sse_subscribers: dict[str, list[queue.Queue]] = {}


class _ClarifyEntry:
    """One pending clarify request inside a session."""

    __slots__ = ("event", "data", "result", "clarify_id")

    def __init__(self, data: dict):
        self.event = threading.Event()
        self.data = data
        self.result: Optional[Any] = None
        self.clarify_id: str = data.get("clarify_id", "") or uuid.uuid4().hex[:12]


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_options(raw_options: Any) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    if not isinstance(raw_options, list):
        return options
    for item in raw_options[:4]:
        if isinstance(item, dict):
            label = _clean_text(item.get("label") or item.get("value") or item.get("text"))
            description = _clean_text(item.get("description") or item.get("detail") or item.get("help"))
        else:
            label = _clean_text(item)
            description = ""
        if label:
            options.append({"label": label, "description": description})
    return options


def _normalize_questions(raw_questions: Any, multi_select: Any = None) -> list[dict[str, Any]]:
    if isinstance(raw_questions, dict):
        raw_items = [raw_questions]
    elif isinstance(raw_questions, list):
        raw_items = raw_questions[:4]
    else:
        raw_items = []
    questions: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        text = _clean_text(item.get("question") or item.get("text") or item.get("prompt"))
        if not text:
            continue
        q_multi = item.get("multiSelect")
        if q_multi is None:
            q_multi = item.get("multi_select")
        if q_multi is None:
            q_multi = bool(multi_select)
        questions.append(
            {
                "id": _clean_text(item.get("id")) or f"question_{idx + 1}",
                "header": _clean_text(item.get("header") or item.get("label")),
                "question": text,
                "options": _normalize_options(item.get("options") or item.get("choices")),
                "multiSelect": bool(q_multi),
            }
        )
    return questions


AGENT_DEFAULT_TIMEOUT_SECONDS = 600  # default di agent.clarify_timeout in hermes-agent


def resolve_clarify_timeout(cfg: dict | None = None) -> int | None:
    """Timeout delle domande all'utente, in lockstep con l'agente (upstream #7163).

    Ordine: ``agent.clarify_timeout`` (la chiave dell'agente), poi il vecchio
    ``clarify.timeout``, altrimenti il default dell'agente (600 s). Un valore
    <= 0 significa **attesa illimitata** e ritorna ``None``: la card resta
    finche' l'utente risponde o il turno viene annullato, senza countdown.
    """
    if cfg is None:
        try:
            from api.config import get_config

            cfg = get_config()
        except Exception:
            cfg = {}
    cfg = cfg if isinstance(cfg, dict) else {}
    candidates = (
        (cfg.get("agent") or {}).get("clarify_timeout") if isinstance(cfg.get("agent"), dict) else None,
        (cfg.get("clarify") or {}).get("timeout") if isinstance(cfg.get("clarify"), dict) else None,
    )
    for raw in candidates:
        if raw is None:
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        return None if value <= 0 else value
    return AGENT_DEFAULT_TIMEOUT_SECONDS


def _timeout_or_default(timeout_seconds: int | None) -> int:
    """None = default; 0 resta 0 (illimitato), non viene sostituito dal default."""
    return DEFAULT_TIMEOUT_SECONDS if timeout_seconds is None else int(timeout_seconds)


def normalize_prompt_payload(
    question: Any,
    choices: Any = None,
    *,
    session_id: str = "",
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Build the pending-prompt payload used by the clarify UI.

    The legacy Hermes clarify tool passes ``question: str`` and ``choices:
    list[str]``. Plan/design tools can pass an AskUserQuestion-style payload
    with ``questions[]``, option labels/descriptions, and ``multiSelect``.
    """
    if isinstance(question, dict):
        payload = dict(question)
        raw_questions = payload.get("questions")
        if raw_questions is None and any(k in payload for k in ("question", "prompt", "text")):
            raw_questions = payload
        questions = _normalize_questions(raw_questions, payload.get("multiSelect") or payload.get("multi_select"))
        if questions:
            title = _clean_text(payload.get("title") or payload.get("question") or payload.get("prompt"))
            if not title:
                title = "Answer the following questions" if len(questions) > 1 else questions[0]["question"]
            return {
                "question": title,
                "questions": questions,
                "choices_offered": [opt["label"] for opt in questions[0].get("options", [])],
                "session_id": session_id,
                "kind": "ask_user_question",
                "requested_at": time.time(),
                "timeout_seconds": _timeout_or_default(timeout_seconds),
            }

    if isinstance(question, list):
        questions = _normalize_questions(question)
        if questions:
            return {
                "question": "Answer the following questions" if len(questions) > 1 else questions[0]["question"],
                "questions": questions,
                "choices_offered": [opt["label"] for opt in questions[0].get("options", [])],
                "session_id": session_id,
                "kind": "ask_user_question",
                "requested_at": time.time(),
                "timeout_seconds": _timeout_or_default(timeout_seconds),
            }

    structured_choices = _normalize_options(choices)
    if structured_choices and any(opt.get("description") for opt in structured_choices):
        text = _clean_text(question)
        questions = [
            {
                "id": "question_1",
                "header": "",
                "question": text,
                "options": structured_choices,
                "multiSelect": False,
            }
        ]
        return {
            "question": text,
            "questions": questions,
            "choices_offered": [opt["label"] for opt in structured_choices],
            "session_id": session_id,
            "kind": "ask_user_question",
            "requested_at": time.time(),
            "timeout_seconds": _timeout_or_default(timeout_seconds),
        }

    choices_list = [_clean_text(choice) for choice in (choices or []) if _clean_text(choice)]
    return {
        "question": _clean_text(question),
        "choices_offered": choices_list,
        "session_id": session_id,
        "kind": "clarify",
        "requested_at": time.time(),
        "timeout_seconds": _timeout_or_default(timeout_seconds),
    }


def is_valid_ask_user_payload(payload: Any) -> bool:
    """Return whether an ask-user payload has a question and real choices.

    ``ask_user`` is a choice tool, not a generic empty clarification prompt.
    Requiring at least two options prevents malformed/empty SDK tool calls from
    becoming a second, unanswerable card in the browser.
    """
    if not isinstance(payload, dict):
        return False
    questions = payload.get("questions")
    if isinstance(questions, list) and questions:
        for question in questions:
            if not isinstance(question, dict):
                return False
            if not _clean_text(question.get("question")):
                return False
            if len(_normalize_options(question.get("options") or question.get("choices"))) < 2:
                return False
        return True
    return bool(
        _clean_text(payload.get("question"))
        and len(_normalize_options(payload.get("choices_offered"))) >= 2
    )


def format_response_for_agent(prompt: dict[str, Any] | None, response: Any) -> str:
    """Return the string sent back to the blocked tool call."""
    if response is None:
        return NO_RESPONSE_FALLBACK
    if isinstance(prompt, dict) and prompt.get("kind") == "ask_user_question":
        answers = response.get("answers") if isinstance(response, dict) else response
        if isinstance(answers, dict) and answers:
            return json.dumps(answers, ensure_ascii=False)
    if isinstance(response, (dict, list)):
        return json.dumps(response, ensure_ascii=False)
    text = _clean_text(response)
    return text or NO_RESPONSE_FALLBACK


def register_gateway_notify(session_key: str, cb) -> None:
    """Register a per-session callback for sending clarify requests to the UI."""
    with _lock:
        _gateway_notify_cbs[session_key] = cb


def _clear_queue_locked(session_key: str) -> list[_ClarifyEntry]:
    entries = _gateway_queues.pop(session_key, [])
    _pending.pop(session_key, None)
    return entries


def unregister_gateway_notify(session_key: str) -> None:
    """Unregister the per-session callback and unblock any waiting clarify prompt."""
    with _lock:
        _gateway_notify_cbs.pop(session_key, None)
        entries = _clear_queue_locked(session_key)
    if entries:
        publish_session_list_changed("attention_cleared")
    for entry in entries:
        entry.event.set()


def clear_pending(session_key: str) -> int:
    """Clear any pending clarify prompts for the session without removing the callback."""
    with _lock:
        entries = _clear_queue_locked(session_key)
    if entries:
        publish_session_list_changed("attention_cleared")
    for entry in entries:
        entry.event.set()
    return len(entries)


def _with_timeout_metadata(data: dict) -> dict:
    item = dict(data or {})
    requested_at = float(item.get("requested_at") or time.time())
    timeout_seconds = _timeout_or_default(item.get("timeout_seconds"))
    if timeout_seconds <= 0:
        # Attesa illimitata: nessuna scadenza, nessun countdown nella card.
        expires_at = None
    else:
        expires_at = float(item.get("expires_at") or requested_at + timeout_seconds)
    item["requested_at"] = requested_at
    item["timeout_seconds"] = timeout_seconds
    item["expires_at"] = expires_at
    return item


def _clarify_sse_notify(session_id: str, head: dict | None, total: int) -> None:
    """Push a clarify event to all SSE subscribers for a session."""
    payload = {"pending": dict(head) if head else None, "pending_count": total}
    for q in _clarify_sse_subscribers.get(session_id, ()):
        try:
            q.put_nowait(payload)
        except queue.Full:
            pass  # drop if subscriber is slow


def sse_subscribe(session_id: str) -> queue.Queue:
    """Register a bounded Queue for SSE push to a given session."""
    q: queue.Queue = queue.Queue(maxsize=16)
    with _lock:
        _clarify_sse_subscribers.setdefault(session_id, []).append(q)
    return q


def sse_unsubscribe(session_id: str, q: queue.Queue) -> None:
    """Remove a subscriber Queue; clean up empty session entries."""
    with _lock:
        subs = _clarify_sse_subscribers.get(session_id)
        if subs:
            try:
                subs.remove(q)
            except ValueError:
                pass
            if not subs:
                _clarify_sse_subscribers.pop(session_id, None)


def submit_pending(session_key: str, data: dict) -> _ClarifyEntry:
    """Queue a pending clarify request and notify the UI callback if registered."""
    data = _with_timeout_metadata(data)
    with _lock:
        gw_queue = _gateway_queues.setdefault(session_key, [])
        # De-duplicate while unresolved: if the most recent pending clarify is
        # semantically identical, reuse it instead of stacking duplicates.
        if gw_queue:
            last = gw_queue[-1]
            if (
                str(last.data.get("question", "")) == str(data.get("question", ""))
                and list(last.data.get("choices_offered") or [])
                == list(data.get("choices_offered") or [])
            ):
                entry = last
                # Dedup re-uses the existing entry with its original clarify_id.
                # If a future caller pre-populates clarify_id in data, it is
                # silently discarded here — the original entry's id wins.
                # Today no caller sets clarify_id (it's generated by __init__),
                # so this is a non-issue.
                cb = _gateway_notify_cbs.get(session_key)
                # Keep _pending aligned to the oldest unresolved entry.
                _pending[session_key] = gw_queue[0].data
                if cb:
                    try:
                        cb(dict(entry.data))
                    except Exception:
                        pass
                # Safe to call while holding _lock: publish() only takes the
                # leaf _SESSION_EVENTS_LOCK and never re-acquires this lock.
                publish_session_list_changed("attention_pending")
                return entry

        entry = _ClarifyEntry(data)
        # Ensure clarify_id is present in the serialised data the frontend receives.
        entry.data["clarify_id"] = entry.clarify_id
        gw_queue.append(entry)
        _pending[session_key] = gw_queue[0].data
        cb = _gateway_notify_cbs.get(session_key)
        # Notify SSE subscribers from inside _lock for ordering guarantees.
        _clarify_sse_notify(session_key, dict(gw_queue[0].data), len(gw_queue))
    publish_session_list_changed("attention_pending")
    if cb:
        try:
            cb(data)
        except Exception:
            pass
    return entry


def get_pending(session_key: str) -> dict | None:
    """Return the oldest pending clarify request for this session, if any."""
    with _lock:
        queue = _gateway_queues.get(session_key) or []
        if queue:
            return dict(queue[0].data)
        pending = _pending.get(session_key)
        return dict(pending) if pending else None


def has_pending(session_key: str) -> bool:
    with _lock:
        return bool(_gateway_queues.get(session_key))


def pending_count(session_key: str) -> int:
    """Return the number of unresolved clarify prompts for a session."""
    with _lock:
        queue = _gateway_queues.get(session_key) or []
        if queue:
            return len(queue)
        return 1 if _pending.get(session_key) else 0


def resolve_clarify(session_key: str, response: Any, resolve_all: bool = False) -> int:
    """Resolve the oldest pending clarify request for a session."""
    with _lock:
        q = _gateway_queues.get(session_key)
        if not q:
            _pending.pop(session_key, None)
            return 0
        entries = list(q) if resolve_all else [q.pop(0)]
        if q:
            _pending[session_key] = q[0].data
            _clarify_sse_notify(session_key, dict(q[0].data), len(q))
        else:
            _clear_queue_locked(session_key)
            _clarify_sse_notify(session_key, None, 0)
    publish_session_list_changed("attention_resolved")
    count = 0
    for entry in entries:
        entry.result = response
        entry.event.set()
        count += 1
    return count


def resolve_clarify_by_id(session_key: str, clarify_id: str, response: Any) -> bool:
    """Resolve a specific pending clarify request by its stable id.

    Returns True if the id was found and resolved, False otherwise.
    """
    with _lock:
        q = _gateway_queues.get(session_key)
        if not q:
            _pending.pop(session_key, None)
            return False
        for i, entry in enumerate(q):
            if entry.clarify_id == clarify_id:
                q.pop(i)
                if q:
                    _pending[session_key] = q[0].data
                    _clarify_sse_notify(session_key, dict(q[0].data), len(q))
                else:
                    _clear_queue_locked(session_key)
                    _clarify_sse_notify(session_key, None, 0)
                # Safe to call while holding _lock: publish() only takes the
                # leaf _SESSION_EVENTS_LOCK and never re-acquires this lock.
                publish_session_list_changed("attention_resolved")
                entry.result = response
                entry.event.set()
                return True
        return False
