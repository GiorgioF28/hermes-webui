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
