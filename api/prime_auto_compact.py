"""Auto-compaction support for the Hermes Prime Claude SDK session.

The service turn is intentionally kept out of WebUI transcripts: it is sent
directly to the persistent SDK client and recorded only in this module's event
ledger for Insights.

Cantiere 1 (2026-07-08):
- Tetto duro sessione (HERMES_PRIME_SESSION_CAP_TOKENS, default 80k):
  forza il compact ignorando cooldown quando la sessione supera il cap.
- Cut a fine task: request_compact_after_task() / pop_compact_after_task()
  permettono a task_done (prime_delegation.py) di richiedere un compact
  extra alla fine del turno corrente, senza toccare il cooldown normale.
- Diagnostic logging per after_tokens=0: distingue "compact ok ma token
  non riportati" (atteso) da "compact fallito" (errore).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from api.paths import _platform_default_hermes_home

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD_TOKENS = 60_000
DEFAULT_COOLDOWN_TURNS = 10
DEFAULT_TIMEOUT_SECONDS = 180.0
DEFAULT_SESSION_CAP_TOKENS = 80_000
EVENT_FILE_NAME = "prime_auto_compact_events.jsonl"

# Flag set by task_done (prime_delegation) to request a post-task compact.
_COMPACT_AFTER_TASK_FLAG = threading.Event()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return max(int(float(value or 0)), 0)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return _safe_int(raw, default)


def auto_compact_enabled() -> bool:
    raw = os.getenv("PRIME_AUTO_COMPACT_ENABLED", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def compact_threshold_tokens() -> int:
    return _env_int("PRIME_COMPACT_THRESHOLD", DEFAULT_THRESHOLD_TOKENS)


def compact_threshold_for_model(model_state: dict | None = None) -> int:
    """Compact threshold scaled to real model context window.

    When the resolved model has a known context window, the threshold is scaled
    proportionally (default 60k / 200k claude-sonnet base ≈ 30%). This avoids
    compacting too early on large-context models or too late on small ones.

    Falls back to compact_threshold_tokens() (env-var or 60k default) when the
    model context length is unknown.
    """
    base = compact_threshold_tokens()
    if base <= 0:
        return base  # disabled
    try:
        cl = _prime_model_context_length(model_state)
        if cl > 0:
            # Scale: threshold = 30% of model context, clamped between 30k and 150k.
            scaled = max(30_000, min(150_000, int(cl * 0.30)))
            logger.debug(
                "prime compact threshold scaled: model_ctx=%d scaled=%d base=%d",
                cl, scaled, base,
            )
            return scaled
    except Exception:
        logger.debug("prime compact threshold model scaling failed", exc_info=True)
    return base


def _prime_model_context_length(model_state: dict | None = None) -> int:
    """Resolve the real context window for the Prime model from model metadata."""
    try:
        from api.routes import _resolve_prime_model_state, _resolve_context_length_for_session_model
        state = model_state if isinstance(model_state, dict) else _resolve_prime_model_state()
        model = str(state.get("model") or "").strip()
        provider = str(state.get("model_provider") or "").strip()
        if not model:
            return 0
        return _resolve_context_length_for_session_model(model, provider) or 0
    except Exception:
        logger.debug("prime model context length lookup failed", exc_info=True)
        return 0


def session_cap_tokens() -> int:
    """Tetto duro della sessione Prime (HERMES_PRIME_SESSION_CAP_TOKENS).

    Superato questo limite il compact viene forzato ignorando il cooldown.
    0 = disabilitato.
    """
    return _env_int("HERMES_PRIME_SESSION_CAP_TOKENS", DEFAULT_SESSION_CAP_TOKENS)


def compact_cooldown_turns() -> int:
    return _env_int("PRIME_COMPACT_COOLDOWN_TURNS", DEFAULT_COOLDOWN_TURNS)


def compact_timeout_seconds() -> float:
    try:
        value = float(os.getenv("PRIME_COMPACT_TIMEOUT", "") or DEFAULT_TIMEOUT_SECONDS)
        return value if value > 0 else DEFAULT_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS


def event_log_path() -> Path:
    override = os.getenv("PRIME_AUTO_COMPACT_EVENTS")
    if override:
        return Path(override).expanduser()
    state_override = os.getenv("HERMES_WEBUI_STATE_DIR")
    if state_override:
        return Path(state_override).expanduser() / EVENT_FILE_NAME
    return _platform_default_hermes_home() / "webui" / EVENT_FILE_NAME


def context_tokens_from_usage(usage: dict | None) -> int:
    usage = usage if isinstance(usage, dict) else {}
    return (
        _safe_int(usage.get("input_tokens"))
        + _safe_int(usage.get("cache_read_input_tokens") or usage.get("cache_read_tokens"))
        + _safe_int(usage.get("cache_creation_input_tokens") or usage.get("cache_write_tokens"))
    )


def usage_from_sdk_message(message: Any) -> dict:
    usage = getattr(message, "usage", None)
    if not isinstance(usage, dict):
        ev = getattr(message, "event", None)
        if isinstance(ev, dict) and isinstance(ev.get("usage"), dict):
            usage = ev.get("usage") or {}
        elif isinstance(ev, dict) and isinstance((ev.get("message") or {}).get("usage"), dict):
            usage = (ev.get("message") or {}).get("usage") or {}
        else:
            usage = {}
    return {
        "input_tokens": _safe_int(usage.get("input_tokens")),
        "output_tokens": _safe_int(usage.get("output_tokens")),
        "cache_read_input_tokens": _safe_int(usage.get("cache_read_input_tokens")),
        "cache_creation_input_tokens": _safe_int(usage.get("cache_creation_input_tokens")),
    }


# ── Cut-a-fine-task flag ──────────────────────────────────────────────────────

def request_compact_after_task() -> None:
    """Richiedi un compact forzato alla fine del turno corrente.

    Chiamato da ``task_done`` in prime_delegation.py: segnala che il task è
    concluso e la sessione va compattata appena il turno corrente termina.
    Il flag è thread-safe e si auto-resetta alla prima lettura.
    """
    _COMPACT_AFTER_TASK_FLAG.set()


def pop_compact_after_task() -> bool:
    """Legge e azzera il flag post-task.

    Ritorna True se task_done è stato chiamato nel turno appena concluso.
    Chiamato da routes._hermes_prime_reply_claude dopo ogni turno.
    """
    result = _COMPACT_AFTER_TASK_FLAG.is_set()
    _COMPACT_AFTER_TASK_FLAG.clear()
    return result


# ── Decision dataclass + state machine ───────────────────────────────────────

@dataclass
class PrimeAutoCompactDecision:
    should_compact: bool
    before_tokens: int = 0
    reason: str = ""
    forced: bool = False  # True quando il compact è forzato dal session cap


@dataclass
class PrimeAutoCompactState:
    threshold_tokens: int = DEFAULT_THRESHOLD_TOKENS
    cooldown_turns: int = DEFAULT_COOLDOWN_TURNS
    turns_since_compact: int = field(default=DEFAULT_COOLDOWN_TURNS)
    suppressed_until_below_threshold: bool = False

    def consider(
        self,
        usage: dict | None,
        *,
        idle: bool,
        cap_tokens: int = 0,
        force: bool = False,
    ) -> PrimeAutoCompactDecision:
        """Decide se compattare la sessione.

        ``cap_tokens`` è il tetto duro: se before_tokens >= cap_tokens il
        compact viene forzato ignorando cooldown e suppressed_flag.
        ``force`` è il flag cut-a-fine-task: ignora cooldown ma non il cap.
        """
        before = context_tokens_from_usage(usage)

        if self.threshold_tokens <= 0 and not force and cap_tokens <= 0:
            return PrimeAutoCompactDecision(False, before, "disabled")

        # Tetto duro: forza compact indipendentemente da cooldown/suppressed.
        if cap_tokens > 0 and before >= cap_tokens:
            return PrimeAutoCompactDecision(True, before, "session_cap_exceeded", forced=True)

        # Cut a fine task: forza compact ignorando cooldown.
        if force and before > 0:
            return PrimeAutoCompactDecision(True, before, "task_done_cut", forced=True)

        if self.threshold_tokens <= 0:
            return PrimeAutoCompactDecision(False, before, "disabled")

        if before <= self.threshold_tokens:
            self.turns_since_compact += 1
            self.suppressed_until_below_threshold = False
            return PrimeAutoCompactDecision(False, before, "below_threshold")
        if not idle:
            return PrimeAutoCompactDecision(False, before, "busy")
        if self.suppressed_until_below_threshold:
            return PrimeAutoCompactDecision(False, before, "post_compact_still_above_threshold")
        if self.turns_since_compact < self.cooldown_turns:
            return PrimeAutoCompactDecision(False, before, "cooldown")
        return PrimeAutoCompactDecision(True, before, "threshold_exceeded")

    def record_compact_result(self, *, after_tokens: int) -> None:
        self.turns_since_compact = 0
        self.suppressed_until_below_threshold = after_tokens >= self.threshold_tokens


_STATE = PrimeAutoCompactState()


def reset_state_for_tests(
    *,
    threshold_tokens: int = DEFAULT_THRESHOLD_TOKENS,
    cooldown_turns: int = DEFAULT_COOLDOWN_TURNS,
) -> None:
    global _STATE
    _STATE = PrimeAutoCompactState(
        threshold_tokens=threshold_tokens,
        cooldown_turns=cooldown_turns,
        turns_since_compact=cooldown_turns,
    )


def _sync_state_from_env() -> PrimeAutoCompactState:
    _STATE.threshold_tokens = compact_threshold_tokens()
    _STATE.cooldown_turns = compact_cooldown_turns()
    return _STATE


# ── Event logging ─────────────────────────────────────────────────────────────

def append_prime_auto_compact_event(event: dict) -> None:
    payload = {
        "event": "prime_auto_compact",
        "ts": time.time(),
        **event,
    }
    try:
        path = event_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        logger.debug("prime auto compact event write failed", exc_info=True)


def _extract_after_usage(result: Any) -> dict:
    if isinstance(result, dict):
        usage = result.get("usage")
        return usage if isinstance(usage, dict) else {}
    return {}


def _prune_prime_tool_results_after_compaction(registry: Any, session_id: str) -> bool:
    """Best-effort reuse of the standard post-compression tool-result prune.

    The Prime SDK client normally owns its context internally, so there may be
    no visible ``context_messages`` attribute to mutate. When tests or future
    client adapters expose one, apply the same helper used by chat streaming.
    """
    try:
        client = registry.get(session_id) if hasattr(registry, "get") else None
        context_messages = getattr(client, "context_messages", None)
        if not context_messages:
            return False
        from api.streaming import _prune_context_tool_results_after_compression

        pruned = _prune_context_tool_results_after_compression(client, context_messages)
        if pruned is context_messages:
            return False
        client.context_messages = pruned
        return True
    except Exception:
        logger.debug("prime manual compact tool-result prune failed", exc_info=True)
        return False


# ── Main entry points ──────────────────────────────────────────────────────────

def precompact_checkpoint_enabled() -> bool:
    from api import precompact_checkpoint

    return precompact_checkpoint.enabled()


def run_precompact_checkpoint(registry: Any, session_id: str) -> dict:
    """Hook verso api.precompact_checkpoint (sostituibile nei test)."""
    from api import precompact_checkpoint

    return precompact_checkpoint.run_for_registry(registry, session_id)


def _run_checkpoint_or_block(registry: Any, session_id: str, *, before_tokens: int, reason: str):
    """Checkpoint memoria prima di compattare. Ritorna (checkpoint, None) oppure
    (None, esito) quando il checkpoint fallisce: fail-closed, niente compact."""
    if not precompact_checkpoint_enabled():
        return None, None
    try:
        return run_precompact_checkpoint(registry, session_id), None
    except Exception as exc:
        event = {
            "session_id": session_id,
            "before_tokens": _safe_int(before_tokens),
            "after_tokens": 0,
            "after_tokens_unknown": True,
            "reason": "checkpoint_failed",
            "compact_reason": reason,
            "status": "checkpoint_failed",
            "error": str(exc) or type(exc).__name__,
        }
        append_prime_auto_compact_event(event)
        logger.warning("prime_auto_compact: checkpoint memoria fallito, compattazione rinviata: %s", exc)
        return None, {"compacted": False, **event}


def maybe_auto_compact_prime(
    registry: Any,
    *,
    session_id: str,
    usage: dict | None,
    idle: bool,
    run_service_turn: Callable[[Any, str, float], Any] | None = None,
    force: bool = False,
) -> dict:
    """Maybe send a hidden ``/compact`` service turn to the Prime SDK session.

    ``idle`` is supplied by the caller that owns Prime's outer turn lock. When
    false, this function is a no-op and does not mutate cooldown counters.

    ``force`` bypasses cooldown (used for session cap and cut-a-fine-task):
    il compact viene eseguito indipendentemente da threshold/cooldown.
    """
    if not auto_compact_enabled() and not force:
        return {"compacted": False, "reason": "disabled"}
    state = _sync_state_from_env()
    cap = session_cap_tokens()
    decision = state.consider(usage, idle=idle, cap_tokens=cap, force=force)
    if not decision.should_compact:
        return {"compacted": False, "reason": decision.reason, "before_tokens": decision.before_tokens}
    # Prima di riassumere la conversazione, cio' che non e' ancora in memoria
    # va salvato dal Librarian. Se fallisce, niente compact (fail-closed).
    checkpoint, blocked = _run_checkpoint_or_block(
        registry, session_id, before_tokens=decision.before_tokens, reason=decision.reason
    )
    if blocked is not None:
        return blocked

    try:
        if run_service_turn is None:
            result = _run_compact_service_turn(registry, session_id, compact_timeout_seconds())
        else:
            result = run_service_turn(registry, session_id, compact_timeout_seconds())
        after_usage = _extract_after_usage(result)
        after_tokens = context_tokens_from_usage(after_usage)
        state.record_compact_result(after_tokens=after_tokens)

        # Diagnostic: after_tokens=0 dopo un compact è atteso se il SDK non
        # riporta la dimensione del contesto nella risposta al /compact.
        # NON è un errore: il contesto sarà misurato nel prossimo turno.
        if after_tokens == 0:
            logger.info(
                "prime_auto_compact: after_tokens=0 (compact ok, context size "
                "unknown until next turn — this is expected behaviour)"
            )

        event = {
            "session_id": session_id,
            "before_tokens": decision.before_tokens,
            "after_tokens": after_tokens,
            "after_tokens_unknown": after_tokens == 0,
            "threshold_tokens": state.threshold_tokens,
            "cap_tokens": cap,
            "cooldown_turns": state.cooldown_turns,
            "reason": decision.reason,
            "forced": decision.forced,
            "checkpoint": checkpoint,
            "status": "ok",
        }
        append_prime_auto_compact_event(event)
        logger.info("prime_auto_compact %s", event)
        return {"compacted": True, **event}
    except Exception as exc:
        event = {
            "session_id": session_id,
            "before_tokens": decision.before_tokens,
            "after_tokens": 0,
            "after_tokens_unknown": True,
            "threshold_tokens": state.threshold_tokens,
            "cap_tokens": cap,
            "cooldown_turns": state.cooldown_turns,
            "reason": decision.reason,
            "forced": decision.forced,
            "status": "error",
            "error": type(exc).__name__,
        }
        append_prime_auto_compact_event(event)
        logger.warning("prime_auto_compact failed", exc_info=True)
        return {"compacted": False, **event}


def compact_prime_now(
    registry: Any,
    *,
    session_id: str,
    before_tokens: int = 0,
    run_service_turn: Callable[[Any, str, float], Any] | None = None,
    reason: str = "manual",
) -> dict:
    """Force a Prime ``/compact`` service turn and log it next to auto events."""
    before_tokens = _safe_int(before_tokens)
    checkpoint, blocked = _run_checkpoint_or_block(
        registry, session_id, before_tokens=before_tokens, reason=reason
    )
    if blocked is not None:
        return {"ok": False, **blocked}
    try:
        if run_service_turn is None:
            result = _run_compact_service_turn(registry, session_id, compact_timeout_seconds())
        else:
            result = run_service_turn(registry, session_id, compact_timeout_seconds())
        after_usage = _extract_after_usage(result)
        after_tokens = context_tokens_from_usage(after_usage)
        state = _sync_state_from_env()
        state.record_compact_result(after_tokens=after_tokens)
        pruned_tool_results = _prune_prime_tool_results_after_compaction(registry, session_id)
        event = {
            "session_id": session_id,
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
            "after_tokens_unknown": after_tokens == 0,
            "threshold_tokens": state.threshold_tokens,
            "cap_tokens": session_cap_tokens(),
            "cooldown_turns": state.cooldown_turns,
            "reason": reason,
            "forced": True,
            "manual": True,
            "pruned_tool_results": pruned_tool_results,
            "checkpoint": checkpoint,
            "status": "ok",
        }
        append_prime_auto_compact_event(event)
        logger.info("prime_manual_compact %s", event)
        return {"ok": True, "compacted": True, **event}
    except Exception as exc:
        event = {
            "session_id": session_id,
            "before_tokens": before_tokens,
            "after_tokens": 0,
            "after_tokens_unknown": True,
            "reason": reason,
            "forced": True,
            "manual": True,
            "status": "error",
            "error": type(exc).__name__,
        }
        append_prime_auto_compact_event(event)
        logger.warning("prime manual compact failed", exc_info=True)
        return {"ok": False, "compacted": False, **event}


def _run_compact_service_turn(registry: Any, session_id: str, timeout: float) -> dict:
    usage_holder: dict[str, dict] = {"usage": {}}

    async def _drive(client):
        await client.query("/compact")
        async for message in client.receive_response():
            usage = usage_from_sdk_message(message)
            if context_tokens_from_usage(usage) or _safe_int(usage.get("output_tokens")):
                usage_holder["usage"] = usage
        return usage_holder

    return registry.run_turn(session_id, _drive, timeout=timeout)


def read_prime_auto_compact_events(days: int = 30) -> list[dict]:
    cutoff = time.time() - (min(max(int(days or 30), 1), 365) * 86400)
    path = event_log_path()
    events: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return events
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict) or row.get("event") != "prime_auto_compact":
            continue
        try:
            ts = float(row.get("ts") or 0)
        except (TypeError, ValueError):
            ts = 0.0
        if ts >= cutoff:
            events.append(row)
    return events


def compute_compact_savings(events: list[dict]) -> dict:
    """Telemetria: before/after delta dai compact eventi.

    Ritorna un dict con:
    - total_compacts: numero di compact eseguiti con successo
    - total_saved_tokens: somma di (before - after) per eventi con after > 0
    - total_before: somma dei before_tokens
    - events_after_unknown: quanti eventi hanno after_tokens=0 (non misurato)
    """
    total_compacts = 0
    total_saved = 0
    total_before = 0
    after_unknown = 0
    for ev in events:
        if ev.get("status") != "ok":
            continue
        total_compacts += 1
        before = _safe_int(ev.get("before_tokens"))
        after = _safe_int(ev.get("after_tokens"))
        total_before += before
        if ev.get("after_tokens_unknown") or after == 0:
            after_unknown += 1
        else:
            total_saved += max(0, before - after)
    return {
        "total_compacts": total_compacts,
        "total_saved_tokens": total_saved,
        "total_before_tokens": total_before,
        "events_after_unknown": after_unknown,
    }
