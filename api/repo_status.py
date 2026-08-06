"""Read-only repository status snapshot for the Command Bridge sidebar."""

from __future__ import annotations

import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable


REPO_STATUS_CONFIG = {
    "repos": (
        ("Console VisionBuilts", Path(r"C:\Users\giorg\Documents\Hermes setup\visionbuilts-console")),
        ("Hermes WebUI", Path(r"C:\Users\giorg\Documents\Hermes setup\hermes-webui")),
        ("Hermes setup", Path(r"C:\Users\giorg\Documents\Hermes setup")),
        ("Creator Earning Engine", Path(r"C:\Users\giorg\Documents\Hermes setup\tomasvisionbuilts\creator-earning-engine")),
    ),
    "workflow": {
        "id": "vt67KEU7MY0vd9vJ",
        "label": "aggiornato via API",
    },
    "cache_ttl_seconds": 30.0,
    "command_timeout_seconds": 4.0,
}

_AHEAD_RE = re.compile(r"\bahead\s+(\d+)\b")
_BEHIND_RE = re.compile(r"\bbehind\s+(\d+)\b")
_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"expires_at": 0.0, "payload": None}


def parse_porcelain_status(output: str) -> dict[str, Any]:
    """Parse ``git status -sb --porcelain`` without requiring an upstream."""
    lines = output.splitlines()
    header = lines[0][3:].strip() if lines and lines[0].startswith("## ") else ""
    tracking = header.split(" [", 1)[0]
    branch_part, separator, upstream = tracking.partition("...")
    branch = branch_part.strip() or "HEAD"
    if branch.startswith("No commits yet on "):
        branch = branch.removeprefix("No commits yet on ").strip()
    if branch == "HEAD (no branch)":
        branch = "detached"

    ahead_match = _AHEAD_RE.search(header)
    behind_match = _BEHIND_RE.search(header)
    return {
        "branch": branch,
        "upstream": upstream.strip() if separator and upstream.strip() else None,
        "ahead": int(ahead_match.group(1)) if ahead_match else 0,
        "behind": int(behind_match.group(1)) if behind_match else 0,
        "dirty": sum(1 for line in lines[1:] if line.strip()),
    }


def _run_git(repo: Path, args: list[str], timeout: float) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=True,
    )
    return completed.stdout.strip()


def read_repo_status(
    name: str,
    repo: Path,
    *,
    runner: Callable[[Path, list[str], float], str] = _run_git,
) -> dict[str, Any]:
    timeout = float(REPO_STATUS_CONFIG["command_timeout_seconds"])
    try:
        status = parse_porcelain_status(
            runner(repo, ["status", "-sb", "--porcelain"], timeout)
        )
        head_line = runner(repo, ["log", "-1", "--format=%h %s"], timeout)
        head_hash, _, subject = head_line.partition(" ")
        if not head_hash:
            raise ValueError("HEAD non disponibile")
        return {
            "name": name,
            "status": "ok",
            **status,
            "head": {"hash": head_hash, "subject": subject},
        }
    except (OSError, subprocess.SubprocessError, ValueError):
        return {
            "name": name,
            "status": "errore",
            "branch": None,
            "upstream": None,
            "ahead": 0,
            "behind": 0,
            "dirty": 0,
            "head": None,
        }


def get_repo_status(*, force: bool = False) -> dict[str, Any]:
    """Return a cached snapshot; one broken repository never breaks the payload."""
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get("payload")
        if not force and cached is not None and now < float(_CACHE["expires_at"]):
            return cached

        repos = [read_repo_status(name, path) for name, path in REPO_STATUS_CONFIG["repos"]]
        payload = {
            "ok": True,
            "repos": repos,
            "workflow": dict(REPO_STATUS_CONFIG["workflow"]),
            "cache_ttl_seconds": REPO_STATUS_CONFIG["cache_ttl_seconds"],
        }
        _CACHE["payload"] = payload
        _CACHE["expires_at"] = now + float(REPO_STATUS_CONFIG["cache_ttl_seconds"])
        return payload
