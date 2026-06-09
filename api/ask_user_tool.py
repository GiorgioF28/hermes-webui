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
            "items": {"type": "string"},
            "description": "2-4 opzioni concise tra cui scegliere",
        },
    },
    "required": ["question", "options"],
}


async def _run_ask_user(session_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Raw, unit-testable handler logic. Surfaces a clarify popup and blocks
    (off the event loop) until the user responds or the timeout elapses."""
    question = str(args.get("question") or "").strip() or "Quale opzione preferisci?"
    options = [str(o) for o in (args.get("options") or []) if str(o).strip()]
    entry = clarify.submit_pending(
        session_id,
        {
            "question": question,
            "choices_offered": options,
            "timeout_seconds": ASK_USER_TIMEOUT_SECONDS,
            "source": "claude-ask-user",
        },
    )
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
    return {"content": [{"type": "text", "text": str(entry.result)}]}


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
