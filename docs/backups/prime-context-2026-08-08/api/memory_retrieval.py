"""Retrieval selettivo della memoria per-richiesta — Cantiere 2 (2026-07-08).

Strategia:
- Indice (MEMORY.md) sempre presente nel contesto iniziale: poche centinaia
  di token, statico e cacheable.
- Corpi delle note: caricati on-demand solo se rilevanti per il task corrente,
  entro un budget fisso (HERMES_MEMORY_BUDGET_TOKENS, default 2000 token).
- Selezione v1: keyword match tra task e title+description dell'indice.
- Nessuna memoria fuori scope: se il match è vuoto, si restituisce solo
  l'indice, non un dump di fallback.
- Selezione v2 (Fase 2 Punto 4): filtro per scope delle note prima del
  keyword match. Le note con scope "global" (o senza scope) sono sempre
  incluse. Le note con scope specifico compaiono solo se il task_scope corrente
  corrisponde.

Assunzione sulla struttura della memoria:
    <mem_dir>/
        MEMORY.md           ← indice con one-liner per ogni nota
        <slug>.md           ← corpo di ogni nota (link da MEMORY.md)
                              Il frontmatter YAML può contenere scope: <valore>

La directory si risolve in quest'ordine:
  1. Env HERMES_PRIME_MEMORY_DIR
  2. Prima directory con memory/MEMORY.md trovata in ~/.claude/projects/
     (la Claude Code project memory del workspace Hermes setup)
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_BUDGET_TOKENS = 2_000
# Stima conservativa: 1 token ≈ 4 caratteri
_CHARS_PER_TOKEN = 4.0

# Stopwords italiane/inglesi frequenti che non aggiungono segnale
_STOPWORDS = frozenset({
    "questa", "questo", "questi", "queste", "tutte", "tutti", "quale", "quali",
    "come", "cosa", "dove", "quando", "perché", "perche", "farlo", "fare",
    "fatto", "file", "alla", "agli", "delle", "degli", "nella", "nelle",
    "negli", "sono", "siamo", "hanno", "deve", "vuole", "vuoi", "puoi",
    "posso", "dobbiamo", "bisogna", "that", "this", "with", "from", "have",
    "been", "will", "would", "could", "should", "about", "also", "then",
    "when", "what", "which", "where", "their", "there", "here", "more",
    "very", "just", "only", "also", "into", "over", "some", "such",
})


# ── Config ────────────────────────────────────────────────────────────────────

def memory_budget_tokens() -> int:
    """Token budget per la memoria selettiva (HERMES_MEMORY_BUDGET_TOKENS)."""
    raw = os.getenv("HERMES_MEMORY_BUDGET_TOKENS", "").strip()
    if raw:
        try:
            v = int(float(raw))
            return max(v, 0)
        except (TypeError, ValueError):
            pass
    return DEFAULT_MEMORY_BUDGET_TOKENS


# ── Directory discovery ───────────────────────────────────────────────────────

def find_prime_memory_dir() -> Path | None:
    """Trova la directory delle note memoria per Prime.

    Cerca (in ordine):
    1. HERMES_PRIME_MEMORY_DIR env var
    2. Prima sottocartella di ~/.claude/projects/ con memory/MEMORY.md
    """
    override = os.getenv("HERMES_PRIME_MEMORY_DIR", "").strip()
    if override:
        p = Path(override).expanduser()
        if p.is_dir():
            return p
        logger.warning("HERMES_PRIME_MEMORY_DIR='%s' non esiste o non è una dir", override)
        return None

    claude_projects = Path.home() / ".claude" / "projects"
    if not claude_projects.is_dir():
        return None
    try:
        candidates = sorted(claude_projects.iterdir())
    except OSError:
        return None
    for project_dir in candidates:
        if not project_dir.is_dir():
            continue
        mem_dir = project_dir / "memory"
        if mem_dir.is_dir() and (mem_dir / "MEMORY.md").is_file():
            return mem_dir
    return None


# ── Scope management ─────────────────────────────────────────────────────────

VALID_SCOPES = frozenset({"hermes", "visionbuilts", "rap", "global"})

_SCOPE_RE = re.compile(r"^scope:\s*(\w+)", re.MULTILINE)


def normalize_scope(scope: str) -> str:
    """Normalizza uno scope: lowercase, solo valori noti. Default 'global'."""
    s = (scope or "").strip().lower()
    return s if s in VALID_SCOPES else "global"


def parse_note_scope_fast(mem_dir: Path, filename: str) -> str:
    """Legge le prime 15 righe del file e ritorna il valore di scope.

    Cerca ``scope: <value>`` nel frontmatter YAML.
    Ritorna il valore in lowercase (normalizzato tramite normalize_scope),
    o "global" se assente o in caso di errore.

    Path safety: stesse regole di load_memory_body (no traversal, no slash).
    """
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        return "global"
    path = mem_dir / filename
    if not path.is_file():
        return "global"
    try:
        lines: list[str] = []
        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= 15:
                    break
                lines.append(line)
        head = "".join(lines)
        m = _SCOPE_RE.search(head)
        if m:
            return normalize_scope(m.group(1))
    except Exception:
        logger.debug("parse_note_scope_fast: error reading '%s'", filename, exc_info=True)
    return "global"


# ── Index parsing ──────────────────────────────────────────────────────────────

_INDEX_LINE_RE = re.compile(
    r"^\s*-\s*\[(?P<title>[^\]]+)\]\((?P<filename>[^)]+\.md)\)"
    r"(?:\s*[—–-]\s*(?P<description>.+))?$"
)


def parse_memory_index(mem_dir: Path) -> list[dict]:
    """Parsa MEMORY.md e ritorna la lista di entry dell'indice.

    Ogni entry: {"title": str, "filename": str, "description": str, "scope": str}
    """
    mem_file = mem_dir / "MEMORY.md"
    if not mem_file.is_file():
        return []
    entries: list[dict] = []
    try:
        text = mem_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        m = _INDEX_LINE_RE.match(line)
        if not m:
            continue
        filename = m.group("filename").strip()
        scope = parse_note_scope_fast(mem_dir, filename)
        entries.append({
            "title": m.group("title").strip(),
            "filename": filename,
            "description": (m.group("description") or "").strip(),
            "scope": scope,
        })
    return entries


def format_memory_index(entries: list[dict]) -> str:
    """Formatta l'indice come lista Markdown one-liner (per il system prompt)."""
    if not entries:
        return ""
    lines = ["## Memoria (indice)"]
    for e in entries:
        title = e.get("title", "")
        filename = e.get("filename", "")
        desc = e.get("description", "")
        if desc:
            lines.append(f"- [{title}]({filename}) — {desc}")
        else:
            lines.append(f"- [{title}]({filename})")
    return "\n".join(lines)


# ── Keyword extraction and scoring ────────────────────────────────────────────

def _extract_keywords(task: str, *, min_len: int = 4) -> list[str]:
    """Estrae keyword significative dal task, rimuovendo le stopwords."""
    raw = str(task or "").lower()
    words = re.findall(r"[a-zA-ZÀ-ÿà-ÿ\-]{" + str(min_len) + r",}", raw)
    seen: set[str] = set()
    result: list[str] = []
    for w in words:
        if w not in _STOPWORDS and w not in seen:
            seen.add(w)
            result.append(w)
    return result


def score_entry_relevance(entry: dict, keywords: list[str]) -> int:
    """Score di rilevanza di una entry rispetto alle keyword del task.

    Match case-insensitive su title + description. Ritorna il numero di
    keyword trovate (0 = nessuna rilevanza).
    """
    if not keywords:
        return 0
    haystack = f"{entry.get('title', '')} {entry.get('description', '')}".lower()
    return sum(1 for kw in keywords if kw and kw in haystack)


# ── Body loading ──────────────────────────────────────────────────────────────

def _chars_to_tokens(n_chars: int) -> int:
    """Stima il numero di token da una lunghezza in caratteri."""
    return max(1, int(n_chars / _CHARS_PER_TOKEN))


def load_memory_body(mem_dir: Path, filename: str) -> str:
    """Carica il corpo di una nota memoria."""
    # Sanity check: only allow simple .md filenames (no path traversal)
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        return ""
    path = mem_dir / filename
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


# ── Selection ─────────────────────────────────────────────────────────────────

def select_memories_for_task(
    task: str,
    mem_dir: Path,
    *,
    budget_tokens: int,
    task_scope: str = "",
) -> list[dict]:
    """Seleziona le note rilevanti per il task entro il budget token.

    Ritorna una lista di entry arricchite con il campo "body".
    Lista vuota se nessuna entry ha score > 0, o se budget <= 0.

    ``task_scope`` — se non-vuoto, filtra le entry per scope prima del
    keyword match. Sono incluse le entry con scope == task_scope, oppure
    scope == "global", oppure scope == "" (legacy, senza scope).
    Se task_scope è vuota stringa, nessun filtro scope (backward compatible).
    """
    if budget_tokens <= 0:
        return []
    entries = parse_memory_index(mem_dir)
    if not entries:
        return []
    keywords = _extract_keywords(task)
    if not keywords:
        return []

    # Filtro scope (Fase 2 Punto 4)
    if task_scope:
        entries = [
            e for e in entries
            if e.get("scope", "") == task_scope
            or e.get("scope", "") in {"global", ""}
        ]

    scored = [
        (e, score_entry_relevance(e, keywords))
        for e in entries
    ]
    # Ordina per score decrescente, a parità mantieni l'ordine originale
    scored.sort(key=lambda x: -x[1])
    # Filtra solo le entry con score > 0
    scored = [(e, s) for e, s in scored if s > 0]

    results: list[dict] = []
    tokens_used = 0
    for entry, score in scored:
        body = load_memory_body(mem_dir, entry["filename"])
        if not body:
            continue
        body_tokens = _chars_to_tokens(len(body))
        if tokens_used + body_tokens > budget_tokens:
            logger.debug(
                "memory_retrieval: skip '%s' (body %d tok > remaining budget %d tok)",
                entry["title"], body_tokens, budget_tokens - tokens_used,
            )
            break
        results.append({
            **entry,
            "body": body,
            "score": score,
            "body_tokens": body_tokens,
        })
        tokens_used += body_tokens

    return results


# ── Public builder ────────────────────────────────────────────────────────────

def build_memory_context(
    task: str,
    mem_dir: Path,
    *,
    budget_tokens: int | None = None,
    index_only: bool = False,
    task_scope: str = "",
) -> str:
    """Costruisce il blocco di contesto memoria per un turno Prime.

    ``task`` — testo della richiesta corrente (per il keyword match).
    ``mem_dir`` — directory contenente MEMORY.md e le note individuali.
    ``budget_tokens`` — budget per i corpi (default: HERMES_MEMORY_BUDGET_TOKENS).
    ``index_only`` — se True, restituisce solo l'indice (per il system prompt).
    ``task_scope`` — scope del task corrente (filtro per-note, Fase 2 Punto 4).

    Ritorna una stringa vuota se mem_dir non esiste o MEMORY.md è assente.
    """
    if budget_tokens is None:
        budget_tokens = memory_budget_tokens()

    entries = parse_memory_index(mem_dir)
    if not entries:
        return ""

    index_text = format_memory_index(entries)

    if index_only or budget_tokens <= 0:
        return index_text

    relevant = select_memories_for_task(
        task, mem_dir, budget_tokens=budget_tokens, task_scope=task_scope
    )
    if not relevant:
        return index_text

    body_parts = ["## Memoria (dettaglio rilevante per il task corrente)"]
    for entry in relevant:
        body_parts.append(f"\n### {entry['title']}\n{entry['body']}")

    detail_text = "\n".join(body_parts)
    return index_text + "\n\n" + detail_text


def build_prime_memory_context(
    task: str,
    workspace: Path | None = None,
    *,
    task_scope: str = "",
) -> str:
    """Entry point per routes.py: seleziona la mem_dir e costruisce il contesto.

    Ritorna stringa vuota se la mem_dir non è trovata o non ha MEMORY.md.

    ``task_scope`` — scope del task corrente per il filtro per-nota (Fase 2 Punto 4).
    """
    mem_dir = find_prime_memory_dir()
    if mem_dir is None:
        logger.debug("memory_retrieval: nessuna mem_dir trovata, contesto memoria omesso")
        return ""
    return build_memory_context(task, mem_dir, task_scope=task_scope)
