"""Quale Claude Code usa Hermes.

Il Python SDK (claude_agent_sdk) porta con se' un ``claude.exe`` incorporato in
``_bundled/`` e lo preferisce a qualsiasi CLI di sistema: aggiornare l'app,
``npm i -g @anthropic-ai/claude-code`` o ``claude update`` non lo tocca. Prime
restava sul CLI incorporato (2.1.169) mentre tutto il resto era al 2.1.259, e
i modelli nuovi rispondevano "version 2.1.251 or newer is required".

Qui si risolve il CLI che Giorgio aggiorna davvero e lo si passa come
``cli_path`` a ogni ``ClaudeAgentOptions``. Ordine:

1. ``HERMES_CLAUDE_CLI`` (path esplicito, se esiste);
2. il ``claude`` sul PATH. Su Windows e' uno shim ``claude.cmd`` che lancia
   ``node_modules/@anthropic-ai/claude-code/bin/claude.exe``: si usa
   direttamente l'exe nativo, perche' un ``.cmd`` passerebbe da ``cmd.exe`` che
   ri-parsa la riga di comando (system prompt e config MCP sono argomenti);
3. altrimenti ``None``: l'SDK usa il suo incorporato, come prima.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

ENV_VAR = "HERMES_CLAUDE_CLI"
_NPM_NATIVE_EXE = Path("node_modules") / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
_UNRESOLVED = object()
_cache: object = _UNRESOLVED


def reset_cache_for_tests() -> None:
    global _cache
    _cache = _UNRESOLVED


def _from_env() -> str | None:
    raw = os.getenv(ENV_VAR, "").strip().strip('"')
    if not raw:
        return None
    path = Path(raw).expanduser()
    if path.is_file():
        return str(path)
    logger.warning("%s=%r non esiste: ignorato", ENV_VAR, raw)
    return None


def _from_path() -> str | None:
    is_windows = platform.system() == "Windows"
    found = shutil.which("claude") or (shutil.which("claude.cmd") if is_windows else None)
    if not found:
        return None
    candidate = Path(found)
    if not is_windows:
        return str(candidate)
    if candidate.suffix.lower() == ".exe":
        return str(candidate)
    # Shim npm (claude / claude.cmd / claude.ps1): usa l'exe nativo del pacchetto.
    native = candidate.parent / _NPM_NATIVE_EXE
    if native.is_file():
        return str(native)
    logger.warning(
        "claude sul PATH e' uno shim (%s) senza exe nativo in %s: uso il CLI incorporato nell'SDK",
        candidate, native,
    )
    return None


def resolve_claude_cli_path() -> str | None:
    """Path del CLI Claude Code da passare come ``cli_path``; ``None`` = default SDK."""
    global _cache
    if _cache is not _UNRESOLVED:
        return _cache  # type: ignore[return-value]
    resolved = _from_env() or _from_path()
    _cache = resolved
    if resolved:
        logger.info("Claude Code CLI per Hermes: %s", resolved)
    else:
        logger.info("Claude Code CLI per Hermes: incorporato nell'SDK (nessun claude di sistema trovato)")
    return resolved
