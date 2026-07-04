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
    "MANDATO OPERATIVO: il tuo compito e' ESEGUIRE il task fino in fondo e "
    "restituire l'esito concreto (cosa hai fatto, trovato o cambiato). Le regole "
    "sotto sono vincoli su COME lavorare in sicurezza, NON un permesso per "
    "fermarti a un semplice 'ricevuto' o a un piano senza agire. Se qualcosa ti "
    "blocca davvero, fai il massimo possibile in sicurezza e segnala con "
    "precisione cosa resta e perche'.\n"
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
    "MANDATO OPERATIVO: ESEGUI il task fino in fondo e restituisci l'esito "
    "concreto. Le regole sotto sono vincoli su COME lavorare in sicurezza, NON "
    "un permesso per fermarti a un 'ricevuto' o a un piano senza agire.\n"
    "\n"
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


# --- Persistenza durevole delle deleghe -------------------------------------
# Gli esiti vivevano SOLO in RAM (_BG_TASKS): se Prime sbatteva sul session-limit
# o il server riavviava, il risultato del sotto-agente evaporava. Ora ogni cambio
# di stato viene appeso a tasks/delegations.jsonl e ricaricato all'avvio.
_DELEGATIONS_LOADED = False
_PERSIST_FIELDS = (
    "id", "agent", "agent_id", "task_type", "task", "status", "output",
    "started", "finished", "librarian_status", "librarian_output",
)


def _delegations_log_path(workspace: str) -> Path:
    return Path(workspace) / "tasks" / "delegations.jsonl"


def _persist_bg_task(task_id: str, workspace: str) -> None:
    """Appende lo stato corrente di una delega al log durevole (crash-safe)."""
    t = _BG_TASKS.get(task_id)
    if t is None or not workspace:
        return
    try:
        path = _delegations_log_path(workspace)
        path.parent.mkdir(parents=True, exist_ok=True)
        snapshot = {k: t.get(k) for k in _PERSIST_FIELDS}
        with path.open("a", encoding="utf-8", errors="replace") as fh:
            fh.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
    except Exception:
        logger.debug("delegation persist failed for %s", task_id, exc_info=True)


def _load_bg_tasks(workspace: str) -> None:
    """Ricarica l'ultimo stato noto delle deleghe dopo un riavvio."""
    global _DELEGATIONS_LOADED, _TASK_SEQ
    if _DELEGATIONS_LOADED or not workspace:
        return
    _DELEGATIONS_LOADED = True
    path = _delegations_log_path(workspace)
    if not path.is_file():
        return
    try:
        latest: dict[str, dict] = {}
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            tid = rec.get("id")
            if tid:
                latest[tid] = rec
        for tid, rec in latest.items():
            if tid in _BG_TASKS:
                continue
            # Una delega rimasta "in_corso" prima del riavvio non gira piu':
            # marcala interrotta cosi' si vede che si era bloccata (token/crash).
            if rec.get("status") == "in_corso":
                rec["status"] = "interrotta"
                rec.setdefault("finished", rec.get("started"))
            _BG_TASKS[tid] = dict(rec)
        # Riallinea il contatore id per non riusare un "dN" gia' presente.
        used = [int(str(k)[1:]) for k in _BG_TASKS if str(k).startswith("d") and str(k)[1:].isdigit()]
        if used:
            _TASK_SEQ = itertools.count(max(used) + 1)
    except Exception:
        logger.debug("delegation reload failed", exc_info=True)


async def _run_and_store(task_id, task_type, task, model, label, workspace):
    """Esegue il sotto-agente in background e salva il risultato nel registro."""
    t = _BG_TASKS.get(task_id)
    if t is None:
        return
    try:
        if model == _CODEX_MODEL:
            output = await _run_codex_worker(task, workspace)
        else:
            output = await _run_worker(task, model, workspace, agent_id=t.get("agent_id"), progress=t)
        t.update(status="ok", output=output, finished=time.time())
        _persist_bg_task(task_id, workspace)
        try:
            _enqueue_librarian_pass(task_id, task_type, task, output, workspace)
        except Exception:
            logger.debug("librarian hook enqueue failed", exc_info=True)
    except Exception as e:
        # Conserva l'eventuale output PARZIALE accumulato prima del blocco
        # (es. token finiti a meta' risposta): non deve andare perso.
        partial = str(t.get("output") or "").strip()
        msg = str(e)
        combined = (partial + "\n\n[interrotta: " + msg + "]").strip() if partial else msg
        t.update(status="errore", output=combined, finished=time.time())
        _persist_bg_task(task_id, workspace)


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


# Alias che identificano l'agente di sviluppo: deve SEMPRE girare su Codex,
# qualunque task_type scelga Prime (basta che dica "passa al programmatore").
_CODEX_AGENT_ALIASES = (
    "programmatore", "programmer", "codex", "sviluppatore", "developer", "dev", "coder",
)


def _model_for(task_type: str, agent_id: str = ""):
    """Instrada la delega al brain giusto.

    DEFAULT: Codex. I sotto-agenti (librarian, ricercatore, social, dev, …) girano
    su Codex per NON bruciare i crediti cloud di Claude — che è la risorsa scarsa e
    che serve a Hermes Prime. Escape hatch: se il task_type chiede esplicitamente
    ragionamento pesante ("opus"/"ragiona"/"reason"/"claude") si usa Opus. Overridabile
    con HERMES_SUBAGENT_BRAIN=claude per tornare al vecchio routing Sonnet/Opus.
    """
    t = (task_type or "").lower()
    a = _agent_slug(agent_id)
    if a in _CODEX_AGENT_ALIASES or "codic" in t or "code" in t or "dev" in t:
        return _CODEX_MODEL, "Codex"
    # Richiesta esplicita di ragionamento cloud (rara): resta su Opus.
    if any(k in t for k in ("opus", "ragiona", "reason", "claude")):
        return "claude-opus-4-8", "Opus"
    if os.getenv("HERMES_SUBAGENT_BRAIN", "codex").strip().lower() == "claude":
        if "sempl" in t or "simple" in t or "light" in t:
            return "claude-sonnet-4-6", "Sonnet"
        return "claude-opus-4-8", "Opus"
    # Default: Codex per tutti i sotto-agenti (risparmia i crediti cloud).
    return _CODEX_MODEL, "Codex"


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
        # Il Librarian gira in automatico dopo ogni delega ma finora non lasciava
        # traccia nel registro uso: la sua card nel Command Bridge restava sempre
        # "non vivo". Registra l'uso cosi' il pannello agenti riflette che ha
        # girato davvero (best-effort: non deve mai far fallire il pass memoria).
        try:
            from api.agent_registry import record_agent_usage
            record_agent_usage(workspace, _LIBRARIAN_AGENT_ID, "memoria", task_id)
        except Exception:
            logger.debug("librarian usage ledger append failed", exc_info=True)
        if t is not None:
            t.update(librarian_status="ok", librarian_output=result)
            _persist_bg_task(task_id, workspace)
    except Exception as exc:
        logger.debug("librarian pass failed for %s", task_id, exc_info=True)
        if t is not None:
            t.update(librarian_status="errore", librarian_output=str(exc))
            _persist_bg_task(task_id, workspace)


def _resolve_codex_executable() -> str:
    """Trova Codex anche quando il server Hermes parte con un PATH minimale."""
    configured = str(os.getenv("HERMES_CODEX_CLI") or "").strip().strip('"')
    candidates = [
        configured,
        shutil.which("codex.cmd"),
        shutil.which("codex"),
    ]
    local_app_data = str(os.getenv("LOCALAPPDATA") or "").strip()
    app_data = str(os.getenv("APPDATA") or "").strip()
    if local_app_data:
        node_dir = Path(local_app_data) / "hermes" / "node"
        candidates.extend([
            str(node_dir / "codex.cmd"),
            str(node_dir / "codex.exe"),
            str(node_dir / "codex"),
        ])
    if app_data:
        candidates.append(str(Path(app_data) / "npm" / "codex.cmd"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate))
    checked = [c for c in candidates if c]
    raise FileNotFoundError(
        "Codex CLI non trovato. Imposta HERMES_CODEX_CLI oppure installa codex "
        f"in uno dei percorsi attesi ({len(checked)} controllati)."
    )


def _codex_exec_blocking(task: str, workspace: str) -> str:
    """Run one Codex CLI exec turn (blocking) and return stdout (or raise)."""
    exe = _resolve_codex_executable()
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
        # Conserva l'output parziale prodotto da Codex prima del timeout.
        partial = e.stdout or e.output or ""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", "replace")
        partial = str(partial).strip()
        suffix = f"\nParziale prima del timeout:\n{partial}" if partial else ""
        raise RuntimeError(f"Codex CLI timeout dopo {_CODEX_TIMEOUT}s.{suffix}") from e


async def _run_worker(
    task: str,
    model: str,
    workspace: str,
    *,
    agent_id: str | None = None,
    mcp_servers: dict[str, dict[str, Any]] | None = None,
    skills: list[str] | None = None,
    progress: dict | None = None,
) -> str:
    """Run one ephemeral sub-agent turn and return its text output.

    Se ``progress`` e' fornito, il testo parziale viene scritto live in
    ``progress["output"]`` cosi' che, se il turno si interrompe (token finiti),
    l'esito accumulato fin li' non vada perso."""
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
                if progress is not None:
                    progress["output"] = "".join(parts).strip()
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
    # Ricarica l'ultimo stato noto delle deleghe (sopravvive a riavvio/crash).
    _load_bg_tasks(workspace)
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
        model, label = _model_for(task_type, agent_id)
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
        # Persisti subito il dispatch: anche se Prime muore prima della fine,
        # resta traccia durevole che la delega era partita.
        _persist_bg_task(task_id, workspace)
        # Avvia in background: Prime torna subito a parlare con l'utente.
        fut = asyncio.ensure_future(_run_and_store(task_id, task_type, task, model, label, workspace))
        _BG_REFS.add(fut)
        fut.add_done_callback(lambda f: _BG_REFS.discard(f))
        return {"content": [{"type": "text", "text":
            "Delega avviata (id " + task_id + ") al sotto-agente " + label + ". "
            "Continua pure a parlarmi: porto il risultato appena pronto."}]}

    done_schema = {
        "type": "object",
        "properties": {
            "nome": {"type": "string", "description": "nome/argomento del (sotto-)task concluso"},
            "riassunto": {"type": "string", "description": "1 riga: cosa e' stato fatto/deciso (per la memoria)"},
            "stato": {"type": "string", "description": "chiuso | parziale (default: chiuso)"},
        },
        "required": ["nome", "riassunto"],
    }

    @tool(
        "task_done",
        "Segnala che un (sotto-)task e' concluso (chiuso o parziale). Registra l'esito "
        "in memoria tramite il Librarian, SENZA resettare la sessione. Chiamalo quando "
        "chiudi un ramo di lavoro: cosi' l'essenziale finisce in memoria e il contesto "
        "resta pulito. NON usarlo per una semplice risposta: solo a lavoro concluso.",
        done_schema,
    )
    async def task_done(args):
        nome = str(args.get("nome") or "").strip()
        riassunto = str(args.get("riassunto") or "").strip()
        stato = (str(args.get("stato") or "chiuso").strip().lower() or "chiuso")
        if not nome or not riassunto:
            return {"content": [{"type": "text", "text": "task_done richiede nome e riassunto"}], "is_error": True}
        task_id = "done-" + str(next(_TASK_SEQ))
        _BG_TASKS[task_id] = {
            "id": task_id, "session_id": session_id, "agent": "task_done", "agent_id": "",
            "task_type": "memoria", "task": "Task concluso: " + nome,
            "status": ("ok" if stato != "parziale" else "parziale"),
            "output": riassunto, "started": time.time(), "finished": time.time(),
        }
        _persist_bg_task(task_id, workspace)
        # Salvataggio in memoria (Vault -> Graphify -> Notion) via Librarian, async.
        try:
            _enqueue_librarian_pass(task_id, "memoria", "Task concluso: " + nome, riassunto, workspace)
        except Exception:
            logger.debug("task_done librarian enqueue failed", exc_info=True)
        return {"content": [{"type": "text", "text":
            "Segnato '" + nome + "' come " + stato + ". Salvo l'essenziale in memoria "
            "(Librarian) e tengo il contesto pulito."}]}

    return create_sdk_mcp_server(name="team", version="1.0.0", tools=[delega, task_done])
