"""Preset lean per Hermes Prime — sostituisce claude_code (Punto 5, Fase 2).

HERMES_PRIME_USE_LEAN_PRESET=1 (default): usa prompt lean (<8k token totali).
HERMES_PRIME_USE_LEAN_PRESET=0: usa il vecchio claude_code preset (backward compat).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def lean_preset_enabled() -> bool:
    raw = os.getenv("HERMES_PRIME_USE_LEAN_PRESET", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _lean_persona_text() -> str:
    """Legge prompts/hermes-lean.md."""
    try:
        pf = Path(__file__).resolve().parent.parent / "prompts" / "hermes-lean.md"
        if pf.is_file():
            return pf.read_text(encoding="utf-8").strip()
    except Exception:
        logger.debug("hermes-lean.md read failed", exc_info=True)
    return ""


def estimate_tokens(text: str) -> int:
    """Stima token da caratteri (1 token ≈ 4 chars)."""
    return max(1, len(text) // 4)


def build_lean_system_prompt(append_parts: list[str]) -> str | dict:
    """Costruisce il system prompt lean per Prime.

    Se HERMES_PRIME_USE_LEAN_PRESET=0: ritorna dict con preset=claude_code (compat).
    Altrimenti: ritorna stringa plain-text (NO claude_code preset).

    append_parts: lista di sezioni testuali aggiuntive (brief, memoria, istruzioni).
    """
    if not lean_preset_enabled():
        # Compat: vecchio comportamento
        append = "\n\n".join(p for p in append_parts if p)
        return {"type": "preset", "preset": "claude_code", "append": append,
                "exclude_dynamic_sections": True}

    persona = _lean_persona_text()
    all_parts = ([persona] if persona else []) + [p for p in append_parts if p]
    full_text = "\n\n".join(all_parts)

    before_tokens = estimate_tokens(full_text)
    logger.info("prime_lean_preset: system prompt %d chars ≈ %d token (lean)",
                len(full_text), before_tokens)
    return full_text


# ── Toolset presets (P3-C, bridge-parity-p2) ─────────────────────────────

TOOLSET_LEAN = "lean"
TOOLSET_FULL = "full"
TOOLSET_READONLY = "readonly"
TOOLSET_DEFAULT = TOOLSET_LEAN
VALID_TOOLSETS = frozenset({TOOLSET_LEAN, TOOLSET_FULL, TOOLSET_READONLY})


def resolve_prime_toolset(settings: dict | None = None) -> str:
    """Return the active toolset name from settings, defaulting to lean."""
    ts = str((settings or {}).get("toolset") or TOOLSET_DEFAULT).strip().lower()
    return ts if ts in VALID_TOOLSETS else TOOLSET_DEFAULT


def build_lean_system_prompt_with_toolset(
    append_parts: list[str],
    toolset: str = TOOLSET_DEFAULT,
) -> str | dict:
    """Like build_lean_system_prompt but respects the configured toolset.

    lean (default): current lean preset — no change from P1 behaviour.
    full: lean persona + append note that all tools are available.
    readonly: lean persona + append note restricting to read-only tools.

    The actual tool list sent to the model is controlled by the caller
    (routes._hermes_prime_reply); this function only adjusts the system prompt
    to set the right expectation for the model.
    """
    toolset = toolset if toolset in VALID_TOOLSETS else TOOLSET_DEFAULT
    extra_note = ""
    if toolset == TOOLSET_FULL:
        extra_note = "\n\n[Toolset attivo: FULL — tutti gli strumenti disponibili sono abilitati.]"
    elif toolset == TOOLSET_READONLY:
        extra_note = "\n\n[Toolset attivo: READONLY — usa solo strumenti di lettura (Read, Grep, Glob, WebFetch, WebSearch). Non usare strumenti che scrivono o modificano file o chiamate esterne.]"
    if extra_note:
        append_parts = list(append_parts) + [extra_note]
    return build_lean_system_prompt(append_parts)
