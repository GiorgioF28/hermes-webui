"""Checkpoint memoria prima della compattazione di Prime (fail-closed).

La compattazione (/compact) riassume la conversazione: cio' che non e' gia'
in memoria si perde. Prima di compattare, i turni non ancora salvati vengono
passati al Memory Librarian, che scrive nel Vault (e Graphify/Notion se
previsto). Il segnalibro ``precompact_checkpoint_index`` nelle settings della
sessione Prime ricorda fino a dove si e' arrivati.

Fail-closed: se il checkpoint fallisce, chi chiama NON compatta (meglio un
contesto grande che una memoria bucata) e ritenta al turno successivo.

Il pass gira sul loop dell'agente (lo stesso delle deleghe) e dentro il lock
delle deleghe: nessuna scrittura in memoria in parallelo.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

ENV_ENABLED = "HERMES_PRIME_PRECOMPACT_CHECKPOINT"
ENV_TIMEOUT = "HERMES_PRIME_PRECOMPACT_TIMEOUT"
ENV_LOCK_WAIT = "HERMES_PRIME_PRECOMPACT_LOCK_WAIT"
DEFAULT_TIMEOUT_SECONDS = 150.0
# Attesa sul lock dei pass memoria: un pass Librarian dura fino a un paio di
# minuti. (Era 30s ed era tarata sul lock delle deleghe, tenuto per 10-15 min
# da ogni worker: il checkpoint falliva quasi sempre e la compattazione
# veniva rinviata finche' il contesto superava i 300k token.)
DEFAULT_LOCK_WAIT_SECONDS = 120.0
DEFAULT_MAX_CHARS = 40_000
_PER_MESSAGE_MAX_CHARS = 4_000
_MARKER_KEY = "precompact_checkpoint_index"

_CHECKPOINT_TASK = (
    "CHECKPOINT MEMORIA PRE-COMPATTAZIONE. La conversazione tra Giorgio e Hermes "
    "Prime sta per essere compattata: i turni qui sotto verranno riassunti e i "
    "dettagli andranno persi. Applica la skill sync-hermes-brain ed estrai SOLO "
    "cio' che e' memorizzabile (decisioni, cambi di stato, blocchi risolti, "
    "prossime azioni, asset/link, regole nuove), scrivendolo nel Vault canonico "
    "(poi Graphify; Notion solo se previsto). Niente trascrizioni della chat, "
    "niente segreti, tutto in italiano. Non toccare codice. Chiudi con il "
    "formato Memory Update; se davvero non c'e' nulla da salvare rispondi "
    "\"NIENTE DA MEMORIZZARE\"."
)


class CheckpointError(RuntimeError):
    pass


def enabled() -> bool:
    return os.getenv(ENV_ENABLED, "1").strip().lower() not in {"0", "false", "off", "no"}


def _env_float(name: str, default: float) -> float:
    try:
        return max(float(os.getenv(name, "") or default), 1.0)
    except ValueError:
        return default


# ── transcript ───────────────────────────────────────────────────────────────

def build_checkpoint_transcript(messages: list, since_index: int, max_chars: int = DEFAULT_MAX_CHARS):
    """(testo dei turni nuovi | None, nuovo indice). Solo i messaggi oltre ``since_index``."""
    total = len(messages or [])
    since = max(int(since_index or 0), 0)
    if since >= total:
        return None, total
    lines: list[str] = []
    for msg in messages[since:]:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "?").strip().lower()
        content = str(msg.get("content") or "").strip()
        if not content:
            continue
        if len(content) > _PER_MESSAGE_MAX_CHARS:
            content = content[:_PER_MESSAGE_MAX_CHARS].rstrip() + " […troncato]"
        lines.append(f"[{role}] {content}")
    text = "\n\n".join(lines)
    if len(text) > max_chars:
        marker = "\n\n[…troncato: turni piu' vecchi omessi per restare nel limite]"
        text = text[-(max_chars - len(marker)):].lstrip() + marker
    return (text or None), total


# ── store ────────────────────────────────────────────────────────────────────

def _store_for(session_id: str):
    from api.prime_session_store import get_prime_session_store

    return get_prime_session_store(session_id)


# ── runner sul loop dell'agente ──────────────────────────────────────────────

def librarian_runner(registry: Any, workspace: str, *, timeout: float | None = None) -> Callable[[str], str]:
    """Esegue il pass Librarian sul loop del registry, dentro il lock dei pass memoria.

    Non aspetta i worker delle deleghe (lock di esecuzione): la memory fence
    impedisce loro di scrivere la memoria, quindi il checkpoint puo' girare
    mentre una delega e' in corso. Aspetta solo un altro pass memoria."""
    total_timeout = timeout or _env_float(ENV_TIMEOUT, DEFAULT_TIMEOUT_SECONDS)
    lock_wait = _env_float(ENV_LOCK_WAIT, DEFAULT_LOCK_WAIT_SECONDS)

    async def _pass(prompt: str) -> str:
        from api import prime_delegation as pd

        lock = pd._MEMORY_PASS_LOCK
        try:
            await asyncio.wait_for(lock.acquire(), timeout=lock_wait)
        except asyncio.TimeoutError as exc:
            raise CheckpointError(
                f"pass memoria in corso: lock memoria non disponibile entro {lock_wait:.0f}s"
            ) from exc
        try:
            return await pd._run_worker(
                prompt,
                pd._LIBRARIAN_MODEL,
                workspace,
                agent_id=pd._LIBRARIAN_AGENT_ID,
                mcp_servers=pd._load_memory_mcp_servers(workspace),
                skills=["sync-hermes-brain"],
            )
        finally:
            lock.release()

    def run(prompt: str) -> str:
        loop = getattr(registry, "_loop", None)
        submit = getattr(loop, "submit", None)
        if not callable(submit):
            raise CheckpointError("registry senza loop: impossibile eseguire il Librarian")
        try:
            return str(submit(_pass(prompt), timeout=total_timeout) or "")
        except CheckpointError:
            raise
        except Exception as exc:
            raise CheckpointError(f"pass Librarian fallito: {type(exc).__name__}: {exc}") from exc

    return run


# ── checkpoint ───────────────────────────────────────────────────────────────

def run_precompact_checkpoint(session_id: str, workspace: str, *, runner: Callable[[str], str], timeout: float | None = None) -> dict:
    """Manda al Librarian i turni non ancora salvati; avanza il segnalibro solo a successo."""
    store = _store_for(session_id)
    history = store.history()
    messages = history.get("messages") or []
    settings = history.get("settings") or {}
    since = int(settings.get(_MARKER_KEY) or 0)
    transcript, new_index = build_checkpoint_transcript(messages, since)
    if transcript is None:
        return {"saved": False, "reason": "nothing_new", "messages": 0}
    prompt = f"{_CHECKPOINT_TASK}\n\n## Sessione\n- id: {session_id}\n- turni: {new_index - since}\n\n## Turni da salvare\n{transcript}"
    started = time.time()
    try:
        output = runner(prompt)
    except CheckpointError:
        raise
    except Exception as exc:
        raise CheckpointError(f"{type(exc).__name__}: {exc}") from exc
    store.update_settings(**{_MARKER_KEY: new_index, "precompact_checkpoint_at": time.time()})
    try:
        from api.agent_registry import record_agent_usage
        from api import prime_delegation as pd

        record_agent_usage(workspace, pd._LIBRARIAN_AGENT_ID, "checkpoint", f"ckpt-{int(started)}")
    except Exception:
        logger.debug("checkpoint usage ledger append failed", exc_info=True)
    summary = str(output or "").strip()
    return {
        "saved": True,
        "messages": new_index - since,
        "index": new_index,
        "seconds": round(time.time() - started, 1),
        "nothing_to_store": "niente da memorizzare" in summary.lower(),
        "output_chars": len(summary),
    }


def run_for_registry(registry: Any, session_id: str) -> dict:
    """Entry point per prime_auto_compact: risolve workspace e runner reali."""
    from api.config import DEFAULT_WORKSPACE

    settings = _store_for(session_id).get_settings()
    workspace = str(settings.get("workspace") or DEFAULT_WORKSPACE)
    return run_precompact_checkpoint(session_id, workspace, runner=librarian_runner(registry, workspace))
