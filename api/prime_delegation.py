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
import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, tool, create_sdk_mcp_server

logger = logging.getLogger(__name__)

# Marker model per instradare a Codex CLI invece che al client Claude.
_CODEX_MODEL = "__codex_cli__"
# Timeout per un turno Codex effimero (secondi). 1000s: i task di sviluppo lunghi
# (implementare una spec + test) sforavano i 600s precedenti. Overridabile con
# HERMES_CODEX_TIMEOUT. Alzalo ancora se ricompaiono errori 'codex_timeout'.
_CODEX_TIMEOUT = int(os.getenv("HERMES_CODEX_TIMEOUT", "1000") or "1000")

# session_id -> list[ {agent, task_type, task, status, output} ] for the current turn
_DELEGATIONS: dict[str, list] = {}

# Background (async) delegations — parli con Prime MENTRE i sotto-agenti lavorano.
_BG_TASKS: dict[str, dict] = {}
_BG_REFS: set = set()
_TASK_SEQ = itertools.count(1)
_MEMORY_MCP_SERVER_NAMES = ("hermes-memory", "notion")
_LIBRARIAN_AGENT_ID = "memory-librarian"
_LIBRARIAN_MODEL = "claude-sonnet-4-6"

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

_WORKER_SAFETY_RULES = (
    "REGOLE DI SICUREZZA (vincolanti):\n"
    "1) NON rompere il sistema in esecuzione. Hermes gira live sulla 8788 mentre "
    "l'utente lo usa: non modificare il codice in modo da romperlo.\n"
    "2) Le modifiche al codice devono essere COMPLETE e COERENTI, mai a meta'. Se "
    "tocchi un endpoint backend e il suo consumo frontend, vanno fatti INSIEME.\n"
    "3) Non dare per scontato che una modifica sia 'live': i .py richiedono RIAVVIO "
    "del server, i .js/.css richiedono Ctrl+F5. NON riavviare tu: SEGNALA che serve "
    "un riavvio e lascia decidere all'utente.\n"
    "4) Non toccare le parti che gia' funzionano (Command Bridge: pianeta 3D, voce, "
    "delega asincrona). Se un task tocca file condivisi, segnala il rischio.\n"
    "5) Scritture/azioni distruttive: niente senza necessita' chiara. Mai salvare "
    "segreti/credenziali (token, password, API key, OAuth, auth.json)."
)

_LIBRARIAN_TASK = (
    "Hai ricevuto un Agent Result da una delega. Applica la skill sync-hermes-brain "
    "e aggiorna SOLO la memoria: Vault canonico -> Graphify -> Notion. Non scrivere "
    "codice di sistema. Ordine: classifica, deduplica, scrivi canonico nel Vault, "
    "reindicizza Graphify se il corpus cambia, pubblica su Notion nel DB giusto, "
    "logga su Sync Log. Non salvare segreti. Riporta cosa hai cambiato nel formato "
    "Memory Update."
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
            output = await _run_worker(task, model, workspace, agent_id=t.get("agent_id"))
        t.update(status="ok", output=output, finished=time.time())
        try:
            _enqueue_librarian_pass(task_id, task_type, task, output, workspace)
        except Exception:
            logger.debug("librarian hook enqueue failed", exc_info=True)
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
            "librarian_status": t.get("librarian_status"),
            "librarian_output": t.get("librarian_output", ""),
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
        "librarian_status": t.get("librarian_status"),
        "librarian_output": t.get("librarian_output", ""),
    }


def _model_for(task_type: str):
    t = (task_type or "").lower()
    if "codic" in t or "code" in t or "dev" in t:
        return _CODEX_MODEL, "Codex"
    if "sempl" in t or "simple" in t or "light" in t:
        return "claude-sonnet-4-6", "Sonnet"
    return "claude-opus-4-8", "Opus"


def _agent_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return slug or "agent"


def _agent_note_path(agent_id: str | None, workspace: str) -> Path | None:
    if not agent_id:
        return None
    agents_dir = Path(workspace) / "obsidian-vault" / "06-Agents"
    if not agents_dir.is_dir():
        return None
    wanted = _agent_slug(agent_id)
    for path in sorted(agents_dir.glob("*.md"), key=lambda p: p.name.lower()):
        if _agent_slug(path.stem) == wanted:
            return path
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = re.search(r"(?m)^#\s+(.+?)\s*$", text)
        title = re.sub(r"^Agent:\s*", "", match.group(1).strip(), flags=re.I) if match else ""
        if _agent_slug(title) == wanted:
            return path
    return None


def _agent_note_text(agent_id: str | None, workspace: str) -> str:
    path = _agent_note_path(agent_id, workspace)
    if not path:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        logger.debug("agent note read failed: %s", path, exc_info=True)
        return ""


def _worker_system_prompt(agent_id: str | None, workspace: str) -> str:
    note = _agent_note_text(agent_id, workspace)
    if not note:
        return _WORKER_PERSONA
    return (
        "Sei un sotto-agente operativo di Hermes. Usa la seguente nota agente come "
        "persona e contratto operativo. Rispondi in italiano, concreto e conciso.\n\n"
        "## Nota agente\n"
        f"{note}\n\n"
        f"{_WORKER_SAFETY_RULES}"
    )


def _load_memory_mcp_servers(workspace: str) -> dict[str, dict[str, Any]]:
    """Load only memory MCP servers from workspace .mcp.json, without secrets in code."""
    path = Path(workspace) / ".mcp.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.debug("memory mcp config read failed: %s", path, exc_info=True)
        return {}
    servers = data.get("mcpServers") or data.get("mcp_servers") or {}
    selected = {}
    for name in _MEMORY_MCP_SERVER_NAMES:
        cfg = servers.get(name)
        if isinstance(cfg, dict):
            selected[name] = dict(cfg)
    return selected


def _enqueue_librarian_pass(task_id: str, task_type: str, task: str, output: str, workspace: str) -> None:
    """Fire-and-forget: route an Agent Result through the Memory Librarian."""
    t = _BG_TASKS.get(task_id)
    if t is not None:
        t["librarian_status"] = "in_corso"
        t["librarian_output"] = ""
    fut = asyncio.ensure_future(_run_librarian(task_id, task_type, task, output, workspace))
    _BG_REFS.add(fut)
    fut.add_done_callback(lambda f: _BG_REFS.discard(f))


async def _run_librarian(task_id: str, task_type: str, task: str, output: str, workspace: str) -> None:
    """Best-effort memory sync pass. It must never change the delegation outcome."""
    t = _BG_TASKS.get(task_id)
    prompt = (
        f"{_LIBRARIAN_TASK}\n\n"
        f"## Delega\n- id: {task_id}\n- tipo: {task_type}\n- task: {task}\n\n"
        f"## Agent Result\n{output}"
    )
    try:
        result = await _run_worker(
            prompt,
            _LIBRARIAN_MODEL,
            workspace,
            agent_id=_LIBRARIAN_AGENT_ID,
            mcp_servers=_load_memory_mcp_servers(workspace),
            skills=["sync-hermes-brain"],
        )
        if t is not None:
            t.update(librarian_status="ok", librarian_output=result)
    except Exception as exc:
        logger.debug("librarian pass failed for %s", task_id, exc_info=True)
        if t is not None:
            t.update(librarian_status="errore", librarian_output=str(exc))


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


async def _run_worker(
    task: str,
    model: str,
    workspace: str,
    *,
    agent_id: str | None = None,
    mcp_servers: dict[str, dict[str, Any]] | None = None,
    skills: list[str] | None = None,
) -> str:
    """Run one ephemeral sub-agent turn and return its text output."""
    opts = ClaudeAgentOptions(
        cwd=str(workspace),
        add_dirs=[str(workspace)],
        system_prompt=_worker_system_prompt(agent_id, workspace),
        permission_mode="bypassPermissions",
        include_partial_messages=False,
        model=model,
        mcp_servers=mcp_servers or {},
        setting_sources=[],
        plugins=[],
        strict_mcp_config=True,
        skills=skills,
        env={k: v for k, v in {"NOTION_TOKEN": os.getenv("NOTION_TOKEN")}.items() if v},
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
            "agent": {"type": "string", "description": "opzionale: id agente da obsidian-vault/06-Agents"},
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
        agent_id = str(args.get("agent") or "").strip()
        if not task:
            return {"content": [{"type": "text", "text": "task vuoto"}], "is_error": True}
        model, label = _model_for(task_type)
        if agent_id:
            label = agent_id
        task_id = "d" + str(next(_TASK_SEQ))
        _BG_TASKS[task_id] = {
            "id": task_id, "session_id": session_id, "agent": label, "agent_id": agent_id,
            "task_type": task_type,
            "task": task, "status": "in_corso", "output": "", "started": time.time(), "finished": None,
        }
        # Logga SEMPRE l'uso: se non c'e' un agente esplicito, usa il label/modello.
        try:
            from api.agent_registry import record_agent_usage
            record_agent_usage(workspace, agent_id or label, task_type, task_id)
        except Exception:
            logger.debug("agent usage ledger append failed", exc_info=True)
        # Avvia in background: Prime torna subito a parlare con l'utente.
        fut = asyncio.ensure_future(_run_and_store(task_id, task_type, task, model, label, workspace))
        _BG_REFS.add(fut)
        fut.add_done_callback(lambda f: _BG_REFS.discard(f))
        return {"content": [{"type": "text", "text":
            "Delega avviata (id " + task_id + ") al sotto-agente " + label + ". "
            "Continua pure a parlarmi: porto il risultato appena pronto."}]}

    return create_sdk_mcp_server(name="team", version="1.0.0", tools=[delega])
