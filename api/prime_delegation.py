"""Hermes Prime delegation (Phase 6 step 2).

Gives Hermes Prime an in-process SDK tool `delega(task_type, task)`: it spawns a
short-lived **sub-agent** (ephemeral — the hybrid model: chief persistent,
workers usa-e-getta), routes the model by task type, runs the task, returns the
result to Prime AND records a delegation event so the WebUI can show a card.

Model routing (auto, per task type) — v1 Claude-only:
    semplice  -> Sonnet (claude-sonnet-4-6)
    codice    -> Opus   (TODO: route to Codex CLI — vedi NOTE)
    altro     -> Opus   (claude-opus-4-8)
"""
from __future__ import annotations

import logging
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, tool, create_sdk_mcp_server

logger = logging.getLogger(__name__)

# session_id -> list[ {agent, task_type, task, status, output} ] for the current turn
_DELEGATIONS: dict[str, list] = {}

_WORKER_PERSONA = (
    "Sei un sotto-agente operativo di Hermes. Esegui il task assegnato in modo "
    "concreto e conciso. Rispondi SOLO con il risultato/esito, niente preamboli. "
    "Rispondi in italiano."
)


def get_and_clear_delegations(session_id: str) -> list:
    return _DELEGATIONS.pop(session_id, [])


def _model_for(task_type: str):
    t = (task_type or "").lower()
    if "sempl" in t or "simple" in t or "light" in t:
        return "claude-sonnet-4-6", "Sonnet"
    # NOTE: 'codice'/'code' dovrebbe andare a Codex CLI (subprocess) — v1 usa Opus.
    return "claude-opus-4-8", "Opus"


async def _run_worker(task: str, model: str, workspace: str) -> str:
    """Run one ephemeral sub-agent turn and return its text output."""
    opts = ClaudeAgentOptions(
        cwd=str(workspace),
        add_dirs=[str(workspace)],
        system_prompt=_WORKER_PERSONA,
        permission_mode="bypassPermissions",
        include_partial_messages=False,
        model=model,
        setting_sources=[],
        plugins=[],
        strict_mcp_config=True,
    )
    client = ClaudeSDKClient(options=opts)
    await client.connect()
    parts: list[str] = []
    final = ""
    try:
        await client.query(str(task))
        async for m in client.receive_response():
            cls = type(m).__name__
            if cls == "AssistantMessage":
                for b in (getattr(m, "content", None) or []):
                    if type(b).__name__ == "TextBlock":
                        parts.append(getattr(b, "text", "") or "")
            elif cls == "ResultMessage":
                r = getattr(m, "result", None)
                if r:
                    final = str(r)
    finally:
        try:
            await client.disconnect()
        except Exception:
            logger.debug("worker disconnect failed", exc_info=True)
    return ("".join(parts).strip() or final.strip())


def build_prime_delegation_server(session_id: str, workspace: str):
    """In-process MCP server exposing `delega` to the Hermes Prime session."""
    schema = {
        "type": "object",
        "properties": {
            "task_type": {"type": "string", "description": "tipo: codice | ricerca | ragionamento | semplice"},
            "task": {"type": "string", "description": "il task chiaro e completo da far eseguire al sotto-agente"},
        },
        "required": ["task_type", "task"],
    }

    @tool(
        "delega",
        "Delega un task a un sotto-agente operativo (sceglie il modello in base al tipo) "
        "e restituisce il risultato. Usa SOLO per lavoro concreto da ESEGUIRE, non per "
        "semplici domande o briefing.",
        schema,
    )
    async def delega(args):
        task_type = str(args.get("task_type") or "")
        task = str(args.get("task") or "").strip()
        if not task:
            return {"content": [{"type": "text", "text": "task vuoto"}], "is_error": True}
        model, label = _model_for(task_type)
        try:
            output = await _run_worker(task, model, workspace)
        except Exception as e:
            _DELEGATIONS.setdefault(session_id, []).append(
                {"agent": label, "task_type": task_type, "task": task, "status": "errore", "output": str(e)})
            return {"content": [{"type": "text", "text": "Delega fallita: " + str(e)}], "is_error": True}
        _DELEGATIONS.setdefault(session_id, []).append(
            {"agent": label, "task_type": task_type, "task": task, "status": "ok", "output": output})
        return {"content": [{"type": "text", "text": "Risultato dal sotto-agente (" + label + "):\n" + output}]}

    return create_sdk_mcp_server(name="team", version="1.0.0", tools=[delega])
