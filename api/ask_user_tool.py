# api/ask_user_tool.py
"""In-process SDK tool `ask_user` that surfaces a choice in the Hermes WebUI
via the existing clarify popup and blocks until the user picks an option.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from claude_agent_sdk import tool, create_sdk_mcp_server
from api import clarify

logger = logging.getLogger(__name__)

ASK_USER_TIMEOUT_SECONDS = 600

_ASK_USER_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string", "description": "La domanda da porre all'utente"},
        "options": {
            "type": "array",
            "items": {
                "oneOf": [
                    {"type": "string"},
                    {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "description": {"type": "string"},
                        },
                        "required": ["label"],
                    },
                ]
            },
            "description": "2-4 opzioni concise tra cui scegliere",
        },
        "questions": {
            "type": "array",
            "description": "AskUserQuestion payload con domande multiple.",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "header": {"type": "string"},
                    "question": {"type": "string"},
                    "options": {"type": "array"},
                    "multiSelect": {"type": "boolean"},
                },
                "required": ["question"],
            },
        },
        "multiSelect": {
            "type": "boolean",
            "description": "Permette piu scelte per la domanda principale.",
        },
    },
}


async def _run_ask_user(session_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Raw, unit-testable handler logic. Surfaces a clarify popup and blocks
    (off the event loop) until the user responds or the timeout elapses."""
    raw = dict(args or {})
    payload = clarify.normalize_prompt_payload(
        raw,
        session_id=session_id,
        timeout_seconds=ASK_USER_TIMEOUT_SECONDS,
    )
    payload["source"] = "claude-ask-user"
    if not clarify.is_valid_ask_user_payload(payload):
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Errore: ask_user richiede una domanda non vuota e almeno due "
                        "opzioni valide per ogni domanda. Richiama il tool con un payload completo."
                    ),
                }
            ],
            "is_error": True,
        }
    entry = clarify.submit_pending(session_id, payload)
    if session_id in {"hermes-prime", "hermes-prime-tom"}:
        try:
            from api.prime_session_store import get_prime_session_store

            get_prime_session_store(session_id).append_clarify_request(entry.clarify_id, entry.data)
        except Exception:
            logger.debug("Prime ask_user request persistence failed", exc_info=True)
    # entry.event is a threading.Event resolved from the HTTP thread; wait off
    # the asyncio loop so we never block the loop the SDK runs on.
    resolved = await asyncio.to_thread(entry.event.wait, ASK_USER_TIMEOUT_SECONDS)
    if not resolved or entry.result is None:
        return {
            "content": [
                {
                    "type": "text",
                    "text": "L'utente non ha risposto in tempo; procedi con la tua scelta migliore e spiegala.",
                }
            ]
        }
    if session_id in {"hermes-prime", "hermes-prime-tom"}:
        try:
            from api.prime_session_store import get_prime_session_store

            get_prime_session_store(session_id).append_clarify_response(entry.clarify_id, entry.result)
        except Exception:
            logger.debug("Prime ask_user response persistence failed", exc_info=True)
    return {"content": [{"type": "text", "text": clarify.format_response_for_agent(entry.data, entry.result)}]}


def _ask_user_handler_for(session_id: str):
    """Return an SdkMcpTool bound to this session_id."""

    @tool(
        "ask_user",
        "Chiedi all'utente di scegliere tra piu approcci/opzioni e attendi la risposta. "
        "Usa per scelte di design o preferenza, non per chiedere permessi.",
        _ASK_USER_SCHEMA,
    )
    async def ask_user(args: dict[str, Any]) -> dict[str, Any]:
        return await _run_ask_user(session_id, args)

    return ask_user


def build_ask_user_server(session_id: str):
    """Build an in-process MCP server exposing `ask_user` for one session."""
    return create_sdk_mcp_server(
        name="hermes",
        version="1.0.0",
        tools=[_ask_user_handler_for(session_id)],
    )
