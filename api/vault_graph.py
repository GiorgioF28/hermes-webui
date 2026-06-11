"""Build a graph of the Obsidian vault for the Command Bridge "Vault Planet".

Pure filesystem walk (no MCP). Returns {nodes, edges} where the core node is
Hermes itself, first-level families are projects (01-Projects) and agents
(06-Agents), and deeper levels are folders -> notes. Optionally parses
``[[wikilink]]`` edges from note bodies (capped + cached).

Node:  {id, label, type, path, depth, childCount, mtime}
        type in {core, project, agent, folder, note}
Edge:  {source, target, kind}  kind in {hierarchy, wikilink}
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

# Folders whose direct children are a distinct visual family.
_PROJECT_DIRS = ("01-Projects",)
_AGENT_DIRS = ("06-Agents",)
# Never walk these.
_SKIP_DIRS = {".obsidian", ".git", ".trash", "node_modules", "__pycache__"}

_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
_MAX_WIKILINK_BYTES = 1_000_000  # skip parsing files larger than 1 MB

_CACHE_TTL = 15.0
_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, tuple, dict]] = {}  # vault_path -> (ts, signature, graph)


def _family_for(top_name: str) -> str | None:
    if top_name in _PROJECT_DIRS:
        return "project"
    if top_name in _AGENT_DIRS:
        return "agent"
    return None


def _node_id(rel: str) -> str:
    return rel.replace("\\", "/")


def _signature(vault: Path) -> tuple:
    """Cheap change signature: mtimes of the vault root and its top-level dirs.

    Catches structural changes (add/remove notes/folders). Pure file-body edits
    are picked up by the TTL refresh.
    """
    sig = []
    try:
        sig.append(("", vault.stat().st_mtime))
        for child in sorted(vault.iterdir(), key=lambda p: p.name):
            if child.name in _SKIP_DIRS:
                continue
            try:
                sig.append((child.name, child.stat().st_mtime))
            except OSError:
                continue
    except OSError:
        pass
    return tuple(sig)


def build_graph(vault_path, *, include_wikilinks: bool = True, max_files: int = 5000) -> dict:
    """Walk the vault and return {nodes, edges}. Pure function (no cache)."""
    vault = Path(str(vault_path)).expanduser()
    nodes: list[dict] = []
    edges: list[dict] = []
    if not vault.is_dir():
        return {"nodes": [], "edges": [], "vault": str(vault), "exists": False}

    core_id = "core"
    nodes.append({
        "id": core_id, "label": "Hermes", "type": "core",
        "path": "", "depth": 0, "childCount": 0, "mtime": 0,
    })

    stem_to_id: dict[str, str] = {}
    note_bodies: list[tuple[str, Path]] = []  # (node_id, path) for wikilink pass
    file_budget = [max_files]

    def walk(directory: Path, parent_id: str, depth: int, family: str | None) -> int:
        """Add children of ``directory``; return how many direct children added."""
        try:
            entries = sorted(
                directory.iterdir(),
                key=lambda p: (p.is_file(), p.name.lower()),
            )
        except OSError:
            return 0
        count = 0
        for entry in entries:
            if entry.name in _SKIP_DIRS or entry.name.startswith("."):
                continue
            if file_budget[0] <= 0:
                break
            rel = _node_id(str(entry.relative_to(vault)))
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                mtime = 0
            if entry.is_dir():
                # A top-level family dir colours its *children*, and the family
                # dir node itself is shown as that family's hub.
                this_family = family or _family_for(entry.name)
                node_type = this_family if (depth == 0 and this_family) else "folder"
                nid = rel
                nodes.append({
                    "id": nid, "label": entry.name, "type": node_type,
                    "path": rel, "depth": depth + 1, "childCount": 0, "mtime": mtime,
                })
                edges.append({"source": parent_id, "target": nid, "kind": "hierarchy"})
                file_budget[0] -= 1
                child_family = this_family if depth == 0 else family
                kids = walk(entry, nid, depth + 1, child_family)
                for n in nodes:  # set childCount
                    if n["id"] == nid:
                        n["childCount"] = kids
                        break
                count += 1
            elif entry.suffix.lower() == ".md":
                nid = rel
                node_type = family if (family in ("project", "agent")) else "note"
                label = entry.stem
                nodes.append({
                    "id": nid, "label": label, "type": node_type,
                    "path": rel, "depth": depth + 1, "childCount": 0, "mtime": mtime,
                })
                edges.append({"source": parent_id, "target": nid, "kind": "hierarchy"})
                stem_to_id.setdefault(label.lower(), nid)
                note_bodies.append((nid, entry))
                file_budget[0] -= 1
                count += 1
        return count

    top_children = walk(vault, core_id, 0, None)
    for n in nodes:
        if n["id"] == core_id:
            n["childCount"] = top_children
            break

    if include_wikilinks:
        for nid, path in note_bodies:
            try:
                if path.stat().st_size > _MAX_WIKILINK_BYTES:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            seen = set()
            for m in _WIKILINK_RE.finditer(text):
                target_stem = m.group(1).strip().lower()
                tid = stem_to_id.get(target_stem)
                if tid and tid != nid and tid not in seen:
                    seen.add(tid)
                    edges.append({"source": nid, "target": tid, "kind": "wikilink"})

    return {
        "nodes": nodes,
        "edges": edges,
        "vault": str(vault),
        "exists": True,
        "counts": {
            "nodes": len(nodes),
            "edges": len(edges),
            "projects": sum(1 for n in nodes if n["type"] == "project"),
            "agents": sum(1 for n in nodes if n["type"] == "agent"),
            "notes": sum(1 for n in nodes if n["type"] == "note"),
        },
    }


def get_vault_graph(vault_path, *, include_wikilinks: bool = True) -> dict:
    """Cached build_graph: rebuilds on TTL expiry or structural change."""
    vault = str(Path(str(vault_path)).expanduser())
    sig = _signature(Path(vault))
    now = time.time()
    with _cache_lock:
        cached = _cache.get(vault)
        if cached and (now - cached[0]) < _CACHE_TTL and cached[1] == sig:
            return cached[2]
    graph = build_graph(vault, include_wikilinks=include_wikilinks)
    with _cache_lock:
        _cache[vault] = (now, sig, graph)
    return graph
