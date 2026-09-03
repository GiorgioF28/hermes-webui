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
_DELEGATION_ANCHORS: dict[str, dict[str, Any]] = {}
_DELEGATION_EXECUTION_LOCK = asyncio.Lock()
_MEMORY_MCP_SERVER_NAMES = ("hermes-memory", "notion")
_LIBRARIAN_AGENT_ID = "memory-librarian"
_LIBRARIAN_MODEL = "claude-sonnet-4-6"
_CODEX_FALLBACK_MODEL_ENV = "HERMES_CODEX_FALLBACK_MODEL"
_CODEX_FALLBACK_COOLDOWN_ENV = "HERMES_CODEX_FALLBACK_COOLDOWN_SECONDS"
_DEFAULT_CODEX_FALLBACK_MODEL = "claude-sonnet-4-6"
_DEFAULT_CODEX_FALLBACK_COOLDOWN_SECONDS = 3600.0
_CODEX_FALLBACK_STATE = {
    "until": 0.0,
    "reason": "",
    "last_failure": 0.0,
}


class DelegationRuntimeError(RuntimeError):
    """Runtime failure with machine-readable outcome metadata."""

    def __init__(
        self,
        message: str,
        *,
        category: str = "runtime_error",
        provider: str = "unknown",
        partial_output: str = "",
        exit_code: int | None = None,
        duration_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.provider = provider
        self.partial_output = str(partial_output or "").strip()
        self.exit_code = exit_code
        self.duration_seconds = duration_seconds


class CodexTimeoutError(DelegationRuntimeError):
    def __init__(self, duration_seconds: float, partial_output: str = "") -> None:
        duration = max(float(duration_seconds or 0), 0.0)
        super().__init__(
            f"Codex CLI timeout dopo {duration:g}s (durata runtime: {duration:g}s)",
            category="timeout",
            provider="codex",
            partial_output=partial_output,
            duration_seconds=duration,
        )


class CodexStartError(DelegationRuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(message, category="provider_unavailable", provider="codex")


class CodexProcessError(DelegationRuntimeError):
    def __init__(self, exit_code: int, detail: str) -> None:
        super().__init__(
            f"Codex CLI exit {exit_code}: {detail}",
            category="process_exit",
            provider="codex",
            partial_output=detail,
            exit_code=exit_code,
        )


class DelegationOutputError(DelegationRuntimeError):
    pass

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
    "logga su Sync Log. Scrivi ogni artefatto di memoria esclusivamente in italiano, "
    "anche quando la richiesta o la chat di origine sono in un'altra lingua. Non "
    "copiare trascrizioni chat nella memoria. Non salvare segreti. Riporta cosa hai "
    "cambiato nel formato "
    "Memory Update."
)

_TOM_MEMORY_RULES = (
    "ORIGINE SESSIONE TOM (vincolante): l'eventuale Agent Result e ogni scrittura "
    "in Vault, Graphify, MEMORY.md, run o issue devono essere redatti esclusivamente "
    "in italiano. Non copiare nella memoria la chat o la history ceca di Tom. La "
    "traduzione in ceco avviene solo nella chat Prime-Tom."
)


def _task_with_session_rules(task: str, session_id: str) -> str:
    if str(session_id or "hermes-prime") != "hermes-prime-tom":
        return task
    return f"{_TOM_MEMORY_RULES}\n\n{task}"


def get_and_clear_delegations(session_id: str) -> list:
    return _DELEGATIONS.pop(session_id, [])


# --- Persistenza durevole delle deleghe -------------------------------------
# Gli esiti vivevano SOLO in RAM (_BG_TASKS): se Prime sbatteva sul session-limit
# o il server riavviava, il risultato del sotto-agente evaporava. Ora ogni cambio
# di stato viene appeso a tasks/delegations.jsonl e ricaricato all'avvio.
_DELEGATIONS_LOADED = False
_PERSIST_FIELDS = (
    "id", "session_id", "agent", "agent_id", "task_type", "task", "status", "output",
    "started", "finished", "librarian_status", "librarian_output",
    "runtime", "fallback_runtime", "fallback_model", "fallback_reason",
    "failure_reason", "error_category", "error_code", "result_partial",
    "anchor_session_id", "anchor_message_index", "anchor_created_at", "summary",
)


def _delegations_log_path(workspace: str) -> Path:
    return Path(workspace) / "tasks" / "delegations.jsonl"


def set_delegation_anchor_context(
    session_id: str,
    *,
    message_index: int | None = None,
    created_at: float | None = None,
) -> None:
    """Set the chat message anchor used by newly-created delegations.

    The Command Bridge creates background cards from a polling endpoint, so the
    anchor must live in the durable delegation record rather than only in the DOM.
    """
    sid = str(session_id or "hermes-prime")
    _DELEGATION_ANCHORS[sid] = {
        "anchor_session_id": sid,
        "anchor_message_index": message_index,
        "anchor_created_at": created_at,
    }


def _delegation_anchor(session_id: str) -> dict[str, Any]:
    return dict(_DELEGATION_ANCHORS.get(str(session_id or "hermes-prime")) or {
        "anchor_session_id": str(session_id or "hermes-prime"),
        "anchor_message_index": None,
        "anchor_created_at": None,
    })


def _brief_summary(t: dict) -> str:
    tid = str(t.get("id") or "").strip()
    task = re.sub(r"\s+", " ", str(t.get("task") or "").strip())
    output = re.sub(r"\s+", " ", str(t.get("output") or "").strip())
    status = str(t.get("status") or "")
    if status == "in_corso":
        state = "in corso"
    elif status in ("ok", "parziale", "done"):
        state = "completato"
    elif status == "interrotta":
        state = "interrotta"
    else:
        state = "fallita"
    subject = task or str(t.get("task_type") or t.get("agent") or "delega")
    commit = ""
    match = re.search(r"\b(?:commit\s+)?([0-9a-f]{7,12})\b", output, flags=re.I)
    if match:
        commit = ", commit " + match.group(1)
    text = f"{tid} - {subject[:72]}: {state}{commit}".strip()
    return text[:140]


# Mappa status CANONICO -> LEGACY per la normalizzazione al caricamento.
# Serve perche' DelegationStore.upsert() appende record in schema canonico allo
# stesso delegations.jsonl; se l'ultima riga per un id e' quella canonica,
# _BG_TASKS finisce con status="done", finished=None, output="" -> le card
# riappaiono dopo il riavvio (bug diagnosticato in d153).
_CANONICAL_STATUS_TO_LEGACY: dict[str, str] = {
    "done": "ok",
    "failed": "errore",
    "running": "in_corso",
    "pending": "in_corso",
}


def _canonical_to_legacy(rec: dict) -> dict:
    """Normalizza un record in schema canonico al formato legacy atteso da _BG_TASKS.

    Non-distruttivo: opera su una copia. Converte:
    - status:    done->ok, failed->errore, running/pending->in_corso
    - output:    usa result.text se output e' assente/vuoto
    - started:   usa started_at come fallback
    - finished:  usa finished_at come fallback
    """
    rec = dict(rec)
    raw_status = str(rec.get("status") or "")
    if raw_status in _CANONICAL_STATUS_TO_LEGACY:
        rec["status"] = _CANONICAL_STATUS_TO_LEGACY[raw_status]
    # output: il campo canonico e' result.text
    result = rec.get("result") or {}
    if not rec.get("output"):
        rec["output"] = str(result.get("text") or "")
    error = rec.get("error") or {}
    if not rec.get("failure_reason"):
        rec["failure_reason"] = str(error.get("message") or "")
    if not rec.get("error_category"):
        rec["error_category"] = str(error.get("category") or "")
    if not rec.get("error_code"):
        rec["error_code"] = str(error.get("code") or "")
    if "result_partial" not in rec:
        rec["result_partial"] = bool(result.get("partial", False))
    # timestamps: il campo canonico usa il suffisso _at
    if not rec.get("started") and rec.get("started_at"):
        rec["started"] = rec["started_at"]
    if not rec.get("finished") and rec.get("finished_at"):
        rec["finished"] = rec["finished_at"]
    ui = rec.get("ui") or {}
    if not rec.get("anchor_session_id"):
        rec["anchor_session_id"] = ui.get("anchor_session_id") or rec.get("session_id")
    if rec.get("anchor_message_index") is None and ui.get("anchor_message_index") is not None:
        rec["anchor_message_index"] = ui.get("anchor_message_index")
    if not rec.get("anchor_created_at"):
        rec["anchor_created_at"] = ui.get("anchor_created_at")
    if not rec.get("summary"):
        rec["summary"] = ui.get("summary") or _brief_summary(rec)
    return rec


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
    # Fase 1: sync to canonical store (delegations-state.json)
    try:
        from api.delegation_store import get_delegation_store, bg_task_to_canonical
        get_delegation_store(workspace).upsert(bg_task_to_canonical(t))
    except Exception:
        logger.debug("delegation_store sync failed for %s", task_id, exc_info=True)


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
                # Punto 1 fix d153: normalizza record canonici (appesi da
                # DelegationStore.upsert) al formato legacy prima di metterli
                # in _BG_TASKS. Senza questa conversione l'ultima riga per
                # ogni id e' quella canonica (status=done, finished=None,
                # output="") e get_background_tasks() non la filtra per eta',
                # facendo riapparire le card dopo il riavvio.
                latest[tid] = _canonical_to_legacy(rec)
        for tid, rec in latest.items():
            if tid in _BG_TASKS:
                continue
            changed = False
            # Una delega rimasta "in_corso" prima del riavvio non gira piu':
            # marcala interrotta cosi' si vede che si era bloccata (token/crash).
            if rec.get("status") == "in_corso":
                rec["status"] = "interrotta"
                # NB: la riga persistita ha "finished": null (chiave PRESENTE),
                # quindi setdefault non la sostituirebbe mai e la delega
                # resterebbe visibile per sempre come card fantasma (bug d53/d81).
                if not rec.get("finished"):
                    rec["finished"] = rec.get("started") or time.time()
                changed = True
            if rec.get("librarian_status") == "in_corso":
                rec["librarian_status"] = "interrotta"
                rec["librarian_output"] = (
                    str(rec.get("librarian_output") or "").strip()
                    or "[librarian interrotto dal riavvio - non ri-eseguire automaticamente]"
                )
                changed = True
            _BG_TASKS[tid] = dict(rec)
            if changed:
                _persist_bg_task(tid, workspace)
        # Riallinea il contatore id per non riusare un "dN" gia' presente.
        used = [int(str(k)[1:]) for k in _BG_TASKS if str(k).startswith("d") and str(k)[1:].isdigit()]
        if used:
            _TASK_SEQ = itertools.count(max(used) + 1)
    except Exception:
        logger.debug("delegation reload failed", exc_info=True)
    # Fase 1: recover crashed delegations in canonical store
    try:
        from api.delegation_store import get_delegation_store
        _store = get_delegation_store(workspace)
        _live_ids = set(_BG_TASKS.keys())
        _recovered = _store.recover_crashed(_live_ids)
        if _recovered:
            logger.info(
                "delegation_store: recovered %d crashed delegation(s): %s",
                len(_recovered), _recovered,
            )
    except Exception:
        logger.debug("delegation_store crash recovery failed", exc_info=True)


async def _run_and_store(task_id, task_type, task, model, label, workspace):
    """Execute Giorgio and Tom delegations through one fair shared queue."""
    async with _DELEGATION_EXECUTION_LOCK:
        await _run_and_store_serial(task_id, task_type, task, model, label, workspace)


async def _run_and_store_serial(task_id, task_type, task, model, label, workspace):
    """Esegue il sotto-agente in background e salva il risultato nel registro."""
    t = _BG_TASKS.get(task_id)
    if t is None:
        return
    session_id = str(t.get("session_id") or t.get("anchor_session_id") or "hermes-prime")
    worker_task = _task_with_session_rules(task, session_id)
    try:
        if model == _CODEX_MODEL:
            output = await _run_codex_worker_with_fallback(
                worker_task, workspace, agent_id=t.get("agent_id"), progress=t
            )
        else:
            output = await _run_worker(worker_task, model, workspace, agent_id=t.get("agent_id"), progress=t)
        output = _validate_delegation_output(output)
        t.update(
            status="ok",
            output=output,
            finished=time.time(),
            failure_reason="",
            error_category="",
            error_code="",
            result_partial=False,
        )
        _persist_bg_task(task_id, workspace)
        # Fase 1: enqueue brief for delivery (idempotent)
        try:
            from api.prime_brief_queue import get_brief_queue
            get_brief_queue(workspace).enqueue(
                task_id,
                agent=str(t.get("agent") or ""),
                task_type=task_type,
                task=task,
                status="done",
                output=output,
                session_id=str(t.get("session_id") or t.get("anchor_session_id") or "hermes-prime"),
            )
        except Exception:
            logger.debug("brief queue enqueue failed for %s", task_id, exc_info=True)
        try:
            import inspect

            enqueue_kwargs = {}
            try:
                params = inspect.signature(_enqueue_librarian_pass).parameters
                if "session_id" in params:
                    enqueue_kwargs["session_id"] = session_id
            except (TypeError, ValueError):
                pass
            _enqueue_librarian_pass(
                task_id, task_type, task, output, workspace, **enqueue_kwargs
            )
        except Exception:
            logger.debug("librarian hook enqueue failed", exc_info=True)
    except Exception as e:
        # Conserva l'eventuale output PARZIALE accumulato prima del blocco
        # (es. token finiti a meta' risposta): non deve andare perso.
        from api.delegation_store import classify_error as _clf_err

        err = _clf_err(e)
        msg = str(e).strip() or "runtime failure"
        partials = []
        current = str(t.get("output") or "").strip()
        if current and not current.startswith("Codex esaurito -> fallback Sonnet"):
            partials.append(current)
        exc_partial = str(getattr(e, "partial_output", "") or "").strip()
        if exc_partial and exc_partial not in partials:
            partials.append(exc_partial)
        partial = "\n\n".join(partials).strip()
        error_code = str(getattr(e, "exit_code", "") or type(e).__name__)
        category = str(err.get("category") or "unknown")
        t.update(
            status="errore",
            output=partial,
            finished=time.time(),
            failure_reason=msg,
            error_category=category,
            error_code=error_code,
            result_partial=bool(partial) and category in {"timeout", "process_exit", "truncated_output"},
        )
        _persist_bg_task(task_id, workspace)
        # Fase 1: enqueue brief for failed delegation (high priority)
        try:
            from api.prime_brief_queue import get_brief_queue
            brief_output = partial
            if msg and msg not in brief_output:
                brief_output = (brief_output + "\n\n[errore: " + msg + "]").strip()
            get_brief_queue(workspace).enqueue(
                task_id,
                agent=str(t.get("agent") or ""),
                task_type=task_type,
                task=task,
                status="failed",
                output=brief_output,
                error_category=category,
                priority="high",
                session_id=str(t.get("session_id") or t.get("anchor_session_id") or "hermes-prime"),
            )
        except Exception:
            logger.debug("brief queue enqueue (error) failed for %s", task_id, exc_info=True)


def get_background_tasks(max_age: float = 600.0, *, session_id: str | None = None) -> list:
    """Snapshot JSON-safe delle deleghe in background (in corso + completate recenti).

    Punto 2 fix d153: usa finished_at come fallback di finished e result.text
    come fallback di output, cosi' i record con schema canonico gia' in memoria
    vengono filtrati correttamente per max_age anche senza la normalizzazione
    al load (safety-net in caso di record inseriti direttamente in _BG_TASKS).
    """
    now = time.time()
    # Status che indicano una delega ancora in esecuzione (legacy + canonico).
    _RUNNING_STATUSES = frozenset({"in_corso", "running", "pending"})
    out = []
    for t in list(_BG_TASKS.values()):
        task_session_id = str(t.get("anchor_session_id") or t.get("session_id") or "hermes-prime")
        if session_id is not None and task_session_id != session_id:
            continue
        # Fallback: finished_at usato se finished e' assente (record canonico).
        finished = t.get("finished") or t.get("finished_at")
        is_running = t.get("status") in _RUNNING_STATUSES
        if not is_running and finished and (now - finished) > max_age:
            continue
        # Fallback: result.text usato se output e' assente (record canonico).
        output = t.get("output") or (t.get("result") or {}).get("text", "")
        view = dict(t)
        view["output"] = output
        view["finished"] = finished
        out.append({
            "id": t["id"], "agent": t["agent"], "task_type": t["task_type"],
            "task": t["task"], "status": t["status"], "output": output,
            # started: serve alla UI per il timer "in corso da / durata" sulle card.
            "started": t.get("started") or t.get("started_at") or t.get("created_at"),
            "finished": finished,
            "anchor_session_id": t.get("anchor_session_id") or t.get("session_id") or "hermes-prime",
            "anchor_message_index": t.get("anchor_message_index"),
            "anchor_created_at": t.get("anchor_created_at"),
            "summary": t.get("summary") or _brief_summary(view),
            "librarian_status": t.get("librarian_status"),
            "librarian_output": t.get("librarian_output", ""),
            "runtime": t.get("runtime"),
            "fallback_runtime": t.get("fallback_runtime"),
            "fallback_model": t.get("fallback_model"),
            "fallback_reason": t.get("fallback_reason"),
            "failure_reason": t.get("failure_reason") or (t.get("error") or {}).get("message", ""),
            "error_category": t.get("error_category") or (t.get("error") or {}).get("category", ""),
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
        "anchor_session_id": t.get("anchor_session_id") or t.get("session_id") or "hermes-prime",
        "anchor_message_index": t.get("anchor_message_index"),
        "anchor_created_at": t.get("anchor_created_at"),
        "summary": t.get("summary") or _brief_summary(t),
        "librarian_status": t.get("librarian_status"),
        "librarian_output": t.get("librarian_output", ""),
        "runtime": t.get("runtime"),
        "fallback_runtime": t.get("fallback_runtime"),
        "fallback_model": t.get("fallback_model"),
        "fallback_reason": t.get("fallback_reason"),
        "failure_reason": t.get("failure_reason") or (t.get("error") or {}).get("message", ""),
        "error_category": t.get("error_category") or (t.get("error") or {}).get("category", ""),
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
    # Override manuale dal pannello AGENTI: vince su tutto il routing sotto,
    # compresa la regola "programmatore -> sempre Codex" (e' una scelta esplicita).
    if agent_id:
        from api import agent_models

        override = agent_models.get_overrides().get(_AGENT_NOTE_ALIASES.get(a, a))
        if override == "codex":
            return _CODEX_MODEL, "Codex"
        if override:
            return override, agent_models.label_for(override)
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


# Nomi con cui Prime chiama gli agenti -> slug della nota in 06-Agents.
# Senza questa tabella `agent="programmatore"` non trovava
# "Programmatore Project Engineer.md" (match solo esatto) e il sotto-agente
# partiva con la persona generica, perdendo il suo contratto operativo.
_AGENT_NOTE_ALIASES = {
    "programmatore": "programmatore-project-engineer",
    "programmer": "programmatore-project-engineer",
    "sviluppatore": "programmatore-project-engineer",
    "developer": "programmatore-project-engineer",
    "dev": "programmatore-project-engineer",
    "coder": "programmatore-project-engineer",
    "codex": "programmatore-project-engineer",
    "ricercatore": "research-analyst",
    "researcher": "research-analyst",
    "research": "research-analyst",
    "analista": "research-analyst",
    "social": "social-client-contact",
    "outreach": "social-client-contact",
    "orchestratore": "orchestratore",
    "orchestrator": "orchestratore",
    "librarian": "memory-librarian",
    "memory-librarian": "memory-librarian",
    "memoria": "memory-librarian",
    "qa": "qa-reviewer",
    "reviewer": "qa-reviewer",
    "n8n": "n8n-workflow-engineer",
    "pdf": "pdf-ebook-designer",
    "ebook": "pdf-ebook-designer",
    "business": "business-strategist",
    "strategist": "business-strategist",
    "ops": "ops-automation-engineer",
}


def live_agent_slugs() -> dict[str, str]:
    """Slug canonico -> etichetta del task per ogni delega ancora in corso.

    E' il segnale che accende i pianeti nel Command Bridge; il pannello AGENTI
    lo usa per dire "attivo" con la stessa verita'. Il Librarian automatico
    post-delega non ha una card propria ma conta come attivo mentre aggiorna
    la memoria.
    """
    live: dict[str, str] = {}
    for t in list(_BG_TASKS.values()):
        if t.get("status") in ("in_corso", "running", "pending"):
            raw = _agent_slug(str(t.get("agent_id") or t.get("agent") or ""))
            slug = _AGENT_NOTE_ALIASES.get(raw, raw)
            label = (str(t.get("agent") or "") + " " + str(t.get("task_type") or "")).strip()
            live[slug] = label or slug
        if t.get("librarian_status") == "in_corso":
            live["memory-librarian"] = "librarian memoria"
    return live


def _agent_note_candidates(agents_dir: Path) -> list[tuple[str, Path]]:
    """(slug, path) per ogni nota agente: slug del file e slug del titolo H1."""
    out: list[tuple[str, Path]] = []
    for path in sorted(agents_dir.glob("*.md"), key=lambda p: p.name.lower()):
        slugs = {_agent_slug(path.stem)}
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        match = re.search(r"(?m)^#\s+(.+?)\s*$", text)
        if match:
            title = re.sub(r"^Agent:\s*", "", match.group(1).strip(), flags=re.I)
            slugs.add(_agent_slug(title))
        for slug in slugs:
            if slug:
                out.append((slug, path))
    return out


def _agent_note_path(agent_id: str | None, workspace: str) -> Path | None:
    if not agent_id:
        return None
    agents_dir = Path(workspace) / "obsidian-vault" / "06-Agents"
    if not agents_dir.is_dir():
        return None
    wanted = _agent_slug(agent_id)
    candidates = _agent_note_candidates(agents_dir)
    # 1) alias esplicito (deterministico)
    alias = _AGENT_NOTE_ALIASES.get(wanted)
    if alias:
        for slug, path in candidates:
            if slug == alias:
                return path
    # 2) match esatto su nome file o titolo
    for slug, path in candidates:
        if slug == wanted:
            return path
    # 3) prefisso: "programmatore" -> "programmatore-project-engineer"
    for slug, path in candidates:
        if slug.startswith(wanted + "-"):
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


def _enqueue_librarian_pass(
    task_id: str,
    task_type: str,
    task: str,
    output: str,
    workspace: str,
    *,
    session_id: str = "hermes-prime",
) -> None:
    """Fire-and-forget: route an Agent Result through the Memory Librarian."""
    t = _BG_TASKS.get(task_id)
    if t is not None:
        t["librarian_status"] = "in_corso"
        t["librarian_output"] = ""
    fut = asyncio.ensure_future(
        _run_librarian(
            task_id, task_type, task, output, workspace, session_id=session_id
        )
    )
    _BG_REFS.add(fut)
    fut.add_done_callback(lambda f: _BG_REFS.discard(f))


async def _run_librarian(
    task_id: str,
    task_type: str,
    task: str,
    output: str,
    workspace: str,
    *,
    session_id: str = "hermes-prime",
) -> None:
    """Best-effort memory sync pass. It must never change the delegation outcome."""
    async with _DELEGATION_EXECUTION_LOCK:
        await _run_librarian_serial(
            task_id, task_type, task, output, workspace, session_id=session_id
        )


async def _run_librarian_serial(
    task_id: str,
    task_type: str,
    task: str,
    output: str,
    workspace: str,
    *,
    session_id: str = "hermes-prime",
) -> None:
    """Run one memory pass inside the shared delegation execution queue."""
    t = _BG_TASKS.get(task_id)
    origin_rules = f"\n\n{_TOM_MEMORY_RULES}" if session_id == "hermes-prime-tom" else ""
    prompt = (
        f"{_LIBRARIAN_TASK}{origin_rules}\n\n"
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


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, "") or default)
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def codex_fallback_model() -> str:
    return os.getenv(_CODEX_FALLBACK_MODEL_ENV, _DEFAULT_CODEX_FALLBACK_MODEL).strip() or _DEFAULT_CODEX_FALLBACK_MODEL


def codex_fallback_cooldown_seconds() -> float:
    return _env_float(_CODEX_FALLBACK_COOLDOWN_ENV, _DEFAULT_CODEX_FALLBACK_COOLDOWN_SECONDS)


# Frasi inequivocabili che il CLI Codex/OpenAI emette SOLO a crediti esauriti.
_CODEX_QUOTA_STRICT_MARKERS = (
    "usage_limit_exceeded",
    "usage_limit_reached",
    "usage limit exceeded",
    "hit your usage limit",
    "plan limit reached",
    "limit of messages per 5 hours",
    "used up your usage",
    "out of credit",
    "credit balance",
    "credit_balance",
    "insufficient_quota",
    "session limit",
    "hit your session limit",
)

# Marker generici: sicuri solo su errori "corti" (exit code + stderr), NON su
# testi che incorporano l'output del task.
_CODEX_QUOTA_BROAD_MARKERS = _CODEX_QUOTA_STRICT_MARKERS + (
    "usage limit",
    "quota",
    "rate limit",
    "rate_limit",
)


def is_codex_quota_error(exc: Any) -> bool:
    """True when Codex CLI failed because account credits/usage are exhausted."""
    if isinstance(exc, CodexTimeoutError):
        return False
    text = f"{type(exc).__name__}: {exc}".lower()
    if "codex" not in text:
        return False
    if "codex cli timeout dopo" in text:
        # Timeout e quota sono cause diverse: anche se il parziale contiene
        # marker di quota, un hard-timeout non deve mai consumare Sonnet.
        return False
    if any(marker in text for marker in _CODEX_QUOTA_BROAD_MARKERS):
        return True
    return "http 429" in text and any(marker in text for marker in ("limit", "usage", "quota", "credit"))


def is_codex_start_error(exc: Any) -> bool:
    return isinstance(exc, CodexStartError)


_RUNTIME_QUOTA_MARKERS = _CODEX_QUOTA_BROAD_MARKERS + (
    "session limit",
    "hit your session limit",
    "credit",
)
_TRUNCATED_OUTPUT_MARKERS = (
    "[truncated]",
    "output truncated",
    "response truncated",
    "truncated output",
    "max output length",
    "maximum output length",
)


def _looks_like_runtime_quota_output(output: str) -> bool:
    text = str(output or "").strip().lower()
    if not text or len(text) > 2000:
        return False
    return any(marker in text for marker in _RUNTIME_QUOTA_MARKERS)


def _validate_delegation_output(output: Any) -> str:
    """Reject terminal runtime/error placeholders that are not real results."""
    text = str(output or "").strip()
    if not text:
        raise DelegationOutputError(
            "Runtime terminato senza output finale utile",
            category="empty_output",
            partial_output="",
        )
    if _looks_like_runtime_quota_output(text):
        raise DelegationOutputError(
            "Runtime ha restituito un errore di quota/limite: " + re.sub(r"\s+", " ", text)[:500],
            category="quota_exhausted",
            partial_output=text,
        )
    lowered = text.lower()
    if any(marker in lowered for marker in _TRUNCATED_OUTPUT_MARKERS):
        residue = lowered
        for marker in _TRUNCATED_OUTPUT_MARKERS:
            residue = residue.replace(marker, " ")
        if len(re.sub(r"[\W_]+", "", residue)) < 16:
            raise DelegationOutputError(
                "Runtime terminato con output troncato senza contenuto utile",
                category="truncated_output",
                partial_output=text,
            )
    return text


def _codex_fallback_active(now: float | None = None) -> bool:
    now = time.time() if now is None else float(now)
    return float(_CODEX_FALLBACK_STATE.get("until") or 0.0) > now


def _codex_fallback_status(now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else float(now)
    until = float(_CODEX_FALLBACK_STATE.get("until") or 0.0)
    return {
        "active": until > now,
        "until": until,
        "remaining": max(until - now, 0.0),
        "reason": str(_CODEX_FALLBACK_STATE.get("reason") or ""),
        "model": codex_fallback_model(),
    }


def _mark_codex_exhausted(reason: str, *, now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else float(now)
    clean_reason = re.sub(r"\s+", " ", str(reason or "codex quota exhausted")).strip()[:240]
    _CODEX_FALLBACK_STATE.update({
        "until": now + codex_fallback_cooldown_seconds(),
        "reason": clean_reason,
        "last_failure": now,
    })
    return _codex_fallback_status(now)


def _clear_codex_fallback() -> None:
    _CODEX_FALLBACK_STATE.update({"until": 0.0, "reason": "", "last_failure": 0.0})


def reset_codex_fallback_for_tests() -> None:
    _clear_codex_fallback()


def _set_progress_fallback(progress: dict | None, reason: str) -> None:
    if progress is None:
        return
    progress["runtime"] = "sonnet-fallback"
    progress["fallback_runtime"] = "sonnet"
    progress["fallback_model"] = codex_fallback_model()
    progress["fallback_reason"] = reason
    progress["output"] = (
        "Codex esaurito -> fallback Sonnet 4.6 temporaneo. "
        "Esecuzione in corso..."
    )


async def _run_codex_worker_with_fallback(
    task: str,
    workspace: str,
    *,
    agent_id: str | None = None,
    progress: dict | None = None,
) -> str:
    """Run a Codex sub-agent, falling back to Sonnet 4.6 only for quota exhaustion."""
    status = _codex_fallback_status()
    if status["active"]:
        reason = status.get("reason") or "cooldown quota Codex"
        _set_progress_fallback(progress, reason)
        logger.warning("Codex subagent fallback active -> %s (%.0fs remaining)", status["model"], status["remaining"])
        return await _run_worker(
            task, codex_fallback_model(), workspace, agent_id=agent_id, progress=progress
        )

    if progress is not None:
        progress["runtime"] = "codex"
    try:
        output = await _run_codex_worker(task, workspace, agent_id=agent_id)
        _clear_codex_fallback()
        return output
    except Exception as exc:
        if isinstance(exc, CodexTimeoutError):
            raise
        if is_codex_start_error(exc):
            reason = str(exc)
            _set_progress_fallback(progress, reason)
            logger.warning("Codex subagent start failed -> fallback %s", codex_fallback_model())
            return await _run_worker(
                task, codex_fallback_model(), workspace, agent_id=agent_id, progress=progress
            )
        if not is_codex_quota_error(exc):
            raise
        status = _mark_codex_exhausted(str(exc))
        _set_progress_fallback(progress, status["reason"])
        logger.warning(
            "Codex subagent quota exhausted -> fallback %s for %.0fs",
            status["model"],
            status["remaining"],
        )
        return await _run_worker(
            task, codex_fallback_model(), workspace, agent_id=agent_id, progress=progress
        )


def _codex_exec_blocking(task: str, workspace: str) -> str:
    """Run one Codex CLI exec turn (blocking) and return stdout (or raise).

    Il prompt viaggia su STDIN (`codex exec -`), MAI come argomento.
    Su Windows l'eseguibile e' `codex.cmd`: un batch file. CreateProcess lo
    lancia tramite `cmd.exe /c`, che RI-PARSA la riga di comando, quindi i
    metacaratteri del task (`&`, `|`, `(`, `)`, `^`, `<`, `>`, newline)
    troncano l'argomento e possono persino far eseguire pezzi di testo come
    comandi. Sintomo osservato: il sotto-agente riceve solo l'inizio del task
    e risponde con un generico "Ricevuto, dimmi cosa verificare".
    Riproduzione: un task con `... (1) righe & (2) prime 10.` fa uscire il
    wrapper con exit 255 e l'errore cmd "prime non atteso".
    """
    exe = _resolve_codex_executable()
    cmd = [
        exe, "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "-C", str(workspace),
        "-",  # prompt da stdin: nessun parsing di cmd.exe sul testo del task
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(workspace),
        input=str(task),
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
        raise CodexProcessError(proc.returncode, detail)
    return out or err


def _codex_worker_prompt(task: str, agent_id: str | None, workspace: str) -> str:
    """Prompt completo per Codex: istruzioni di sistema + task.

    Il Codex CLI non ha un flag di system prompt (`codex exec --help`: solo
    prompt posizionale/stdin), quindi la persona viaggia in testa allo stesso
    messaggio, delimitata. Fino a oggi il path Codex mandava il task NUDO: il
    sotto-agente non vedeva ne' la persona, ne' le regole di sicurezza, ne' la
    sua nota agente in obsidian-vault/06-Agents — a differenza del path Claude,
    che le passa via ClaudeAgentOptions.system_prompt. Per migliorare un agente
    si edita la sua nota nel Vault: da qui in poi vale per entrambi i runtime.
    """
    system = _worker_system_prompt(agent_id, workspace).strip()
    return (
        "# ISTRUZIONI DI SISTEMA (vincolanti, non sono il task)\n"
        f"{system}\n\n"
        "---\n\n"
        "# TASK DA ESEGUIRE ORA\n"
        f"{str(task).strip()}\n"
    )


async def _run_codex_worker(task: str, workspace: str, *, agent_id: str | None = None) -> str:
    """Run a Codex CLI exec turn off the event loop (non-blocking)."""
    prompt = _codex_worker_prompt(task, agent_id, workspace)
    try:
        return await asyncio.to_thread(_codex_exec_blocking, prompt, workspace)
    except subprocess.TimeoutExpired as e:
        # Conserva l'output parziale prodotto da Codex prima del timeout.
        # Include anche stderr: a quota esaurita il CLI stampa il messaggio di
        # usage limit e poi resta appeso fino al timeout — senza stderr nel
        # testo dell'errore is_codex_quota_error non scatterebbe mai.
        chunks = []
        for raw in (e.stdout or e.output, e.stderr):
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
            raw = str(raw or "").strip()
            if raw:
                chunks.append(raw)
        partial = "\n".join(chunks)
        raise CodexTimeoutError(float(getattr(e, "timeout", None) or _CODEX_TIMEOUT), partial) from e
    except (FileNotFoundError, PermissionError, OSError) as e:
        raise CodexStartError(f"Codex CLI non avviabile: {e}") from e


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
                if bool(getattr(m, "is_error", False)):
                    partial = ("".join(parts).strip() or final.strip())
                    category = "quota_exhausted" if _looks_like_runtime_quota_output(partial) else "runtime_error"
                    raise DelegationRuntimeError(
                        partial or "Claude runtime terminato senza risultato utile",
                        category=category,
                        provider="claude",
                        partial_output=partial,
                    )
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
            "runtime": "codex" if model == _CODEX_MODEL else "claude",
            "fallback_runtime": "",
            "fallback_model": "",
            "fallback_reason": "",
            **_delegation_anchor(session_id),
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
            **_delegation_anchor(session_id),
        }
        _persist_bg_task(task_id, workspace)
        # Salvataggio in memoria (Vault -> Graphify -> Notion) via Librarian, async.
        try:
            _enqueue_librarian_pass(
                task_id,
                "memoria",
                "Task concluso: " + nome,
                riassunto,
                workspace,
                session_id=session_id,
            )
        except Exception:
            logger.debug("task_done librarian enqueue failed", exc_info=True)
        # Cantiere 1 — cut a fine task: segnala che la sessione va compattata
        # a fine turno (routes._hermes_prime_reply_claude legge il flag).
        try:
            from api import prime_auto_compact
            prime_auto_compact.request_compact_after_task()
        except Exception:
            logger.debug("task_done: compact request failed", exc_info=True)
        return {"content": [{"type": "text", "text":
            "Segnato '" + nome + "' come " + stato + ". Salvo l'essenziale in memoria "
            "(Librarian) e tengo il contesto pulito."}]}

    return create_sdk_mcp_server(name="team", version="1.0.0", tools=[delega, task_done])
