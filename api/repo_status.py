"""Read-only repository status snapshot for the Command Bridge sidebar."""

from __future__ import annotations

import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable


LIVE_REPO_PATH = Path(r"C:\Users\giorg\Documents\Hermes setup\hermes-webui")

REPO_STATUS_CONFIG = {
    "repos": (
        ("Console VisionBuilts", Path(r"C:\Users\giorg\Documents\Hermes setup\visionbuilts-console")),
        ("Hermes WebUI", LIVE_REPO_PATH),
        ("Hermes setup", Path(r"C:\Users\giorg\Documents\Hermes setup")),
        ("Creator Earning Engine", Path(r"C:\Users\giorg\Documents\Hermes setup\tomasvisionbuilts\creator-earning-engine")),
    ),
    "workflow": {
        "id": "vt67KEU7MY0vd9vJ",
        "label": "aggiornato via API",
    },
    # Branch upstream/community che non sono "lavoro nostro rimasto indietro".
    "ignored_branches": {
        "Hermes WebUI": ("master",),
    },
    "max_stranded": 6,
    # Repo il cui codice e' effettivamente in esecuzione sulla 8788.
    "live_repo": "Hermes WebUI",
    # Pattern usati per capire se il codice su disco e' cambiato dopo l'avvio.
    "live_watch_globs": ("api/*.py", "static/*.js", "static/*.css", "*.py"),
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


def compute_live_behind(repo: dict[str, Any]) -> dict[str, Any]:
    """Calcola live_behind e live_behind_reasons per un repo snapshot (funzione pura).

    Un repo e' 'live_behind' quando c'e' lavoro non ancora riflesso nel build live:
      - dirty:    modifiche non committate su disco
      - ahead:    commit locali non pushati sul remote
      - stranded: branch locali con commit non inclusi in HEAD
      - behind:   il remote e' avanti del locale (serve pull)
    Il campo e' addizionale e retrocompatibile: chi non lo legge ignora i nuovi campi.
    """
    reasons: list[str] = []
    if repo.get("status") != "ok":
        return {"live_behind": False, "live_behind_reasons": reasons}
    if int(repo.get("dirty", 0)) > 0:
        reasons.append("dirty")
    if int(repo.get("ahead", 0)) > 0:
        reasons.append("ahead")
    if repo.get("stranded"):
        reasons.append("stranded")
    if int(repo.get("behind", 0)) > 0:
        reasons.append("behind")
    return {"live_behind": bool(reasons), "live_behind_reasons": reasons}


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


def read_stranded_branches(
    repo: Path,
    *,
    runner: Callable[[Path, list[str], float], str] = _run_git,
    timeout: float = 4.0,
    ignored: tuple[str, ...] = (),
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Branch locali con commit NON contenuti in HEAD (lavoro fatto e mai integrato).

    E' il segnale che mancava: un fix committato su un branch che non e' quello
    checkout-ato non arrivera' mai in produzione, per quanti riavvii si facciano.
    """
    raw = runner(
        repo,
        [
            "for-each-ref",
            "--no-merged",
            "HEAD",
            "--sort=-committerdate",
            "--format=%(refname:short)\t%(objectname:short)\t%(contents:subject)",
            "refs/heads/",
        ],
        timeout,
    )
    out: list[dict[str, Any]] = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        branch, short_hash = parts[0].strip(), parts[1].strip()
        subject = parts[2].strip() if len(parts) > 2 else ""
        if not branch or branch in ignored:
            continue
        try:
            commits = int((runner(repo, ["rev-list", "--count", f"HEAD..{branch}"], timeout) or "0").strip())
        except (OSError, subprocess.SubprocessError, ValueError):
            continue
        if commits <= 0:
            continue
        out.append({"branch": branch, "commits": commits, "hash": short_hash, "subject": subject})
        if len(out) >= limit:
            break
    return out


def read_repo_status(
    name: str,
    repo: Path,
    *,
    runner: Callable[[Path, list[str], float], str] = _run_git,
    ignored_branches: tuple[str, ...] = (),
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
        try:
            stranded = read_stranded_branches(
                repo,
                runner=runner,
                timeout=timeout,
                ignored=ignored_branches,
                limit=int(REPO_STATUS_CONFIG["max_stranded"]),
            )
            stranded_error = False
        except (OSError, subprocess.SubprocessError, ValueError):
            stranded, stranded_error = [], True
        base: dict[str, Any] = {
            "name": name,
            "status": "ok",
            **status,
            "head": {"hash": head_hash, "subject": subject},
            "stranded": stranded,
            "stranded_error": stranded_error,
        }
        return {**base, **compute_live_behind(base)}
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
            "stranded": [],
            "stranded_error": True,
            "live_behind": False,
            "live_behind_reasons": [],
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
