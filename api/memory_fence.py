"""Recinto di scrittura sulla memoria per i sotto-agenti.

"Solo il Librarian scrive la memoria" era una regola di prompt: i worker
Claude girano con bypassPermissions e quelli Codex con
--dangerously-bypass-approvals-and-sandbox, quindi possono scrivere il Vault.
Il recinto fotografa le aree protette prima della delega, ripristina cio' che
e' stato toccato e restituisce un rapporto da appendere all'esito, cosi'
Prime e il Librarian vedono che cosa l'agente voleva scrivere.

Gira dentro il lock delle deleghe: snapshot e ripristino non possono
sovrapporsi a un pass del Librarian.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from api.config import STATE_DIR

# Relativi al workspace. obsidian-vault e MEMORY.md sono la memoria canonica.
PROTECTED = ("obsidian-vault", "MEMORY.md")
# Derivati (519 MB): troppo grandi da copiare, si segnala soltanto.
REPORT_ONLY = ("graphify-out",)
SNAPSHOT_ROOT = STATE_DIR / "memory-fence"
_COPY_MAX_BYTES = 2_000_000
_SKIP_DIRS = {".git", "node_modules", ".obsidian"}
_EXEMPT_AGENTS = {"memory-librarian", "hermes-prime-chief-of-staff"}


def fence_applies(agent_id: str | None) -> bool:
    """Tutti tranne Librarian e Prime; disattivabile con HERMES_MEMORY_FENCE=0."""
    if os.getenv("HERMES_MEMORY_FENCE", "1").strip().lower() in {"0", "false", "off", "no"}:
        return False
    from api.agent_registry import canonical_agent_slug

    return canonical_agent_slug(str(agent_id or "")) not in _EXEMPT_AGENTS


def _walk(root: Path):
    if root.is_file():
        yield root
        return
    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            yield Path(dirpath) / name


def _stamp(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


class MemoryFence:
    def __init__(
        self,
        workspace: str | Path,
        *,
        protected: tuple[str, ...] | None = None,
        report_only: tuple[str, ...] | None = None,
        extra_protected: list[Path] | None = None,
        snapshot_root: Path | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.protected = tuple(protected or PROTECTED)
        self.report_only = tuple(report_only or REPORT_ONLY)
        self.extra_protected = [Path(p) for p in (extra_protected or [])]
        self.snapshot_root = Path(snapshot_root or SNAPSHOT_ROOT)
        self._dir: Path | None = None
        self._stamps: dict[str, tuple[int, int]] = {}
        self._copies: dict[str, Path] = {}
        self._report_stamps: dict[str, tuple[int, int]] = {}
        self._armed = False

    # ── aree ───────────────────────────────────────────────────────────────
    def _protected_roots(self) -> list[tuple[Path, str]]:
        roots = [(self.workspace / rel, "") for rel in self.protected]
        roots.extend((p, f"ext{i}:") for i, p in enumerate(self.extra_protected))
        return roots

    def _key(self, path: Path, root: Path, prefix: str) -> str:
        if prefix:
            return prefix + path.relative_to(root).as_posix()
        return path.relative_to(self.workspace).as_posix()

    def _path_for_key(self, key: str) -> Path:
        if key.startswith("ext"):
            idx, rel = key.split(":", 1)
            return self.extra_protected[int(idx[3:])] / rel
        return self.workspace / key

    # ── ciclo ──────────────────────────────────────────────────────────────
    def arm(self) -> None:
        self._dir = self.snapshot_root / f"{os.getpid()}-{time.time_ns()}"
        self._dir.mkdir(parents=True, exist_ok=True)
        for root, prefix in self._protected_roots():
            for path in _walk(root):
                stamp = _stamp(path)
                if stamp is None:
                    continue
                key = self._key(path, root, prefix)
                self._stamps[key] = stamp
                if stamp[1] <= _COPY_MAX_BYTES:
                    copy = self._dir / key.replace(":", "_")
                    copy.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copy2(path, copy)
                        self._copies[key] = copy
                    except OSError:
                        pass
        for rel in self.report_only:
            root = self.workspace / rel
            for path in _walk(root):
                stamp = _stamp(path)
                if stamp is not None:
                    self._report_stamps[path.relative_to(self.workspace).as_posix()] = stamp
        self._armed = True

    def enforce(self) -> dict:
        report = {"restored": [], "removed": [], "reported": [], "violations": 0}
        if not self._armed:
            return report
        self._armed = False
        try:
            for root, prefix in self._protected_roots():
                seen: set[str] = set()
                for path in _walk(root):
                    key = self._key(path, root, prefix)
                    seen.add(key)
                    before = self._stamps.get(key)
                    if before is None:
                        try:
                            path.unlink()
                            report["removed"].append(key)
                        except OSError:
                            report["reported"].append(key)
                        continue
                    if _stamp(path) != before:
                        copy = self._copies.get(key)
                        if copy is not None and copy.is_file():
                            shutil.copy2(copy, path)
                            report["restored"].append(key)
                        else:
                            report["reported"].append(key)
                # File protetti cancellati dall'agente: rimettili.
                for key, copy in self._copies.items():
                    if key.startswith(prefix if prefix else "") and key not in seen:
                        target = self._path_for_key(key)
                        if key in self._stamps and not target.exists() and self._belongs(key, root, prefix):
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(copy, target)
                            report["restored"].append(key)
            for rel in self.report_only:
                root = self.workspace / rel
                for path in _walk(root):
                    key = path.relative_to(self.workspace).as_posix()
                    if self._report_stamps.get(key) != _stamp(path):
                        report["reported"].append(key)
        finally:
            if self._dir is not None:
                shutil.rmtree(self._dir, ignore_errors=True)
                self._dir = None
        for name in ("restored", "removed", "reported"):
            report[name] = sorted(set(report[name]))
        report["violations"] = len(report["restored"]) + len(report["removed"]) + len(report["reported"])
        return report

    def _belongs(self, key: str, root: Path, prefix: str) -> bool:
        if prefix:
            return key.startswith(prefix)
        try:
            return self._path_for_key(key).resolve().is_relative_to(root.resolve())
        except (OSError, ValueError):
            return False


def render_report(report: dict | None) -> str:
    if not report or not report.get("violations"):
        return ""
    lines = ["⚠️ RECINTO MEMORIA: il sotto-agente ha provato a scrivere la memoria condivisa. "
             "Solo il Librarian scrive: le modifiche sono state annullate e vanno passate come Agent Result."]
    for label, items in (("ripristinati", report.get("restored")), ("rimossi", report.get("removed")),
                         ("segnalati (non ripristinabili)", report.get("reported"))):
        if items:
            lines.append(f"- {label}: " + ", ".join(items))
    return "\n".join(lines)
