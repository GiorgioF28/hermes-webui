"""Stall guard per i worker Claude: spezza la chiamata identica ripetuta.

Ispirato agli "runtime stall guards" di hermes-agent upstream. Quando un
sotto-agente rifa' la stessa identica chiamata a uno strumento (stesso nome,
stessi argomenti) per la N-esima volta, l'hook PreToolUse dell'SDK la nega con
un messaggio che gli ricorda che il risultato lo ha gia' e gli chiede di
cambiare approccio o consegnare. Evita che una delega giri a vuoto fino al run
budget. Vale per i worker Claude: Codex e' un processo one-shot senza hook.

HERMES_STALL_GUARD_REPEATS: soglia (default 3); 0 disattiva.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

ENV_REPEATS = "HERMES_STALL_GUARD_REPEATS"
DEFAULT_REPEATS = 3


def _repeats_from_env() -> int:
    raw = os.getenv(ENV_REPEATS, "").strip()
    try:
        return max(int(raw), 0) if raw else DEFAULT_REPEATS
    except ValueError:
        return DEFAULT_REPEATS


class StallGuard:
    def __init__(self, repeats: int | None = None) -> None:
        self.repeats = _repeats_from_env() if repeats is None else max(int(repeats), 0)
        self._counts: dict[str, int] = {}
        self._repeated: dict[str, int] = {}
        self.denied = 0

    @staticmethod
    def _key(tool_name: str, tool_input: Any) -> str:
        try:
            body = json.dumps(tool_input or {}, sort_keys=True, ensure_ascii=False, default=str)
        except Exception:
            body = repr(tool_input)
        return f"{tool_name}\n{body}"

    def check(self, tool_name: str, tool_input: Any) -> str | None:
        """Ritorna il motivo del rifiuto se la chiamata e' la N-esima identica, altrimenti None."""
        if self.repeats <= 0:
            return None
        key = self._key(str(tool_name or ""), tool_input)
        count = self._counts.get(key, 0) + 1
        self._counts[key] = count
        if count < self.repeats:
            return None
        self.denied += 1
        self._repeated[str(tool_name or "")] = count
        logger.warning("stall guard: chiamata identica n.%d a %s negata", count, tool_name)
        return (
            f"STALL GUARD: e' la chiamata n.{count} IDENTICA a `{tool_name}` con gli stessi "
            "argomenti. Il risultato lo hai gia' ottenuto le volte precedenti: non ripeterla. "
            "Cambia approccio, usa cio' che hai gia' letto, oppure consegna l'esito con cio' "
            "che sai e segnala cosa manca."
        )

    def summary(self) -> dict:
        return {"denied": self.denied, "repeated_calls": dict(self._repeated)}

    def callback(self):
        """Callback PreToolUse nella forma attesa dall'SDK (HookCallback)."""

        async def _pre_tool_use(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
            reason = self.check(
                str((input_data or {}).get("tool_name") or ""),
                (input_data or {}).get("tool_input"),
            )
            if not reason:
                return {}
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }

        return _pre_tool_use

    def hooks(self) -> dict | None:
        """Valore per ClaudeAgentOptions(hooks=...); None se disattivato."""
        if self.repeats <= 0:
            return None
        from claude_agent_sdk import HookMatcher

        return {"PreToolUse": [HookMatcher(matcher=None, hooks=[self.callback()])]}
