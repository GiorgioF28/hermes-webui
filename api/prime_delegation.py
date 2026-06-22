"""Hermes Prime delegation (Phase 6 step 2).

Gives Hermes Prime an in-process SDK tool `delega(task_type, task)`: it spawns a
short-lived **sub-agent** (ephemeral — the hybrid model: chief persistent,
workers usa-e-getta), routes the model by task type, runs the task, returns the
result to Prime AND records a delegation event so the WebUI can show a card.

Model routing (auto, per task type):
    semplice  -> Sonnet (claude-sonnet-4-6)
    codice    -> Codex CLI (subprocess) — vedi _run_codex_worker
    altro     -> Opus   (claude-opus-4-8)
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import shutil
import subprocess
import time

from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, tool, create_sdk_mcp_server

logger = logging.getLogger(__name__)

# Marker model per instradare a Codex CLI invece che al client Claude.
_CODEX_MODEL = "__codex_cli__"
# Timeout ragionevole per un turno Codex effimero (secondi).
_CODEX_TIMEOUT = 600

# session_id -> list[ {agent, task_type, task, status, output} ] for the current turn
_DELEGATIONS: dict[str, list] = {}

# Background (async) delegations — parli con Prime MENTRE i sotto-agenti lavorano.
_BG_TASKS: dict[str, dict] = {}
_BG_REFS: set = set()
_TASK_SEQ = itertools.count(1)

_WORKER_PERSONA = (
    "Sei un sotto-agente operativo di Hermes. Esegui il task assegnato in modo "
    "concreto e conciso. Rispondi SOLO con il risultato/esito, niente preamboli. "
    "Rispondi in italiano.\n"
    "\n"
    "REGOLE DI SICUREZZA (vincolanti):\n"
    "1) NON rompere il sistema in esecuzione. Hermes gira live sulla 8788 mentre "
    "l'utente lo usa: non modificare il codice in modo da romperlo.\n"
    "2) Le modifiche al codice devono essere COMPLETE e COERENTI, mai a metà. Se "
    "tocchi un endpoint backend e il suo consumo frontend, vanno fatti INSIEME.\n"
    "3) Non dare per scontato che una modifica sia 'live': i .py richiedono RIAVVIO "
    "del server, i .js/.css richiedono Ctrl+F5. NON riavviare tu: SEGNALA che serve "
    "un riavvio e lascia decidere all'utente.\n"
    "4) Non toccare le parti che gia' funzionano (Command Bridge: pianeta 3D, voce, "
    "delega asincrona). Se un task tocca file condivisi, segnala il rischio.\n"
    "5) Scritture/azioni distruttive: niente senza necessita' chiara. Mai salvare "
    "segreti/credenziali (token, password, API key, OAuth, auth.json)."
)


def get_and_clear_delegations(session_id: str) -> list:
    return _DELEGATIONS.pop(session_id, [])


async def _run_and_store(task_id, task_type, task, model, label, workspace):
    """Esegue il sotto-agente in background e salva il risultato nel registro."""
    t = _BG_TASKS.get(task_id)
    if t is None:
        return
    try:
        if model == _CODEX_MODEL:
            output = await _run_codex_worker(task, workspace)
        else:
            output = await _run_worker(task, model, workspace)
        t.update(status="ok", output=output, finished=time.time())
    except Exception as e:
        t.update(status="errore", output=str(e), finished=time.time())


def get_background_tasks(max_age: float = 600.0) -> list:
    """Snapshot JSON-safe delle deleghe in background (in corso + completate recenti)."""
    now = time.time()
    out = []
    for t in list(_BG_TASKS.values()):
        if t.get("status") != "in_corso" and t.get("finished") and (now - t["finished"]) > max_age:
            continue
        out.append({
            "id": t["id"], "agent": t["agent"], "task_type": t["task_type"],
            "task": t["task"], "status": t["status"], "output": t.get("output", ""),
            "finished": t.get("finished"),
        })
    out.sort(key=lambda x: x["id"])
    return out


def get_background_task(task_id: str) -> dict | None:
    """Snapshot JSON-safe di una singola delega (per il brief automatico)."""
    t = _BG_TASKS.get(task_id)
    if not t:
        return None
    return {
        "id": t["id"], "agent": t["agent"], "task_type": t["task_type"],
        "task": t["task"], "status": t["status"], "output": t.get("output", ""),
        "finished": t.get("finished"),
    }


def _model_for(task_type: str):
    t = (task_type or "").lower()
    if "codic" in t or "code" in t or "dev" in t:
        return _CODEX_MODEL, "Codex"
    if "sempl" in t or "simple" in t or "light" in t:
        return "claude-sonnet-4-6", "Sonnet"
    return "claude-opus-4-8", "Opus"


def _codex_exec_blocking(task: str, workspace: str) -> str:
    """Run one Codex CLI exec turn (blocking) and return stdout (or raise)."""
    exe = shutil.which("codex.cmd") or shutil.which("codex") or "codex.cmd"
    cmd = [
        exe, "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "-C", str(workspace),
        str(task),
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(workspace),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_CODEX_TIMEOUT,
        shell=False,
    )
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        detail = err or out or f"exit code {proc.returncode}"
        raise RuntimeError(f"Codex CLI exit {proc.returncode}: {detail}")
    return out or err


async def _run_codex_worker(task: str, workspace: str) -> str:
    """Run a Codex CLI exec turn off the event loop (non-blocking)."""
    try:
        return await asyncio.to_thread(_codex_exec_blocking, task, workspace)
    except FileNotFoundError as e:
        raise RuntimeError("Codex CLI non trovato (codex.cmd non nel PATH)") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Codex CLI timeout dopo {_CODEX_TIMEOUT}s") from e


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
        task_id = "d" + str(next(_TASK_SEQ))
        _BG_TASKS[task_id] = {
            "id": task_id, "session_id": session_id, "agent": label, "task_type": task_type,
            "task": task, "status": "in_corso", "output": "", "started": time.time(), "finished": None,
        }
        # Avvia in background: Prime torna subito a parlare con l'utente.
        fut = asyncio.ensure_future(_run_and_store(task_id, task_type, task, model, label, workspace))
        _BG_REFS.add(fut)
        fut.add_done_callback(lambda f: _BG_REFS.discard(f))
        return {"content": [{"type": "text", "text":
            "Delega avviata (id " + task_id + ") al sotto-agente " + label + ". "
            "Continua pure a parlarmi: porto il risultato appena pronto."}]}

    return create_sdk_mcp_server(name="team", version="1.0.0", tools=[delega])
