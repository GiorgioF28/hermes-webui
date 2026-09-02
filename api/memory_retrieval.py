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
import math
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_BUDGET_TOKENS = 2_000
DEFAULT_UNLOCKED_MEMORY_TOP_K = 6
DEFAULT_UNLOCKED_MEMORY_MAX_CHARS = 12_000
DEFAULT_UNLOCKED_MEMORY_MIN_SCORE = 1.0
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


def _env_int(name: str, default: int, *, minimum: int = 0, maximum: int = 1_000_000) -> int:
    try:
        value = int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default
    return min(max(value, minimum), maximum)


def unlocked_memory_top_k() -> int:
    return _env_int("HERMES_PRIME_MEMORY_TOP_K", DEFAULT_UNLOCKED_MEMORY_TOP_K, maximum=50)


def unlocked_memory_max_chars() -> int:
    return _env_int(
        "HERMES_PRIME_MEMORY_MAX_CHARS",
        DEFAULT_UNLOCKED_MEMORY_MAX_CHARS,
        maximum=200_000,
    )


def unlocked_memory_min_score() -> float:
    try:
        return max(float(os.getenv("HERMES_PRIME_MEMORY_MIN_SCORE", "") or DEFAULT_UNLOCKED_MEMORY_MIN_SCORE), 0.0)
    except (TypeError, ValueError):
        return DEFAULT_UNLOCKED_MEMORY_MIN_SCORE


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

VALID_SCOPES = frozenset({"hermes", "visionbuilts", "libricino", "rap", "global"})

_SCOPE_RE = re.compile(r"^scope:\s*(\w+)", re.MULTILINE)


def _frontmatter_list_value(frontmatter: str, key: str) -> list[str]:
    """Parser YAML minimale per alias/tags, senza dipendenze aggiuntive."""
    lines = frontmatter.splitlines()
    values: list[str] = []
    collecting = False
    for line in lines:
        direct = re.match(rf"^{re.escape(key)}\s*:\s*(.*)$", line, re.IGNORECASE)
        if direct:
            collecting = True
            raw = direct.group(1).strip()
            if raw.startswith("[") and raw.endswith("]"):
                values.extend(part.strip(" \t'\"") for part in raw[1:-1].split(","))
            elif raw:
                values.append(raw.strip("'\""))
            continue
        if collecting:
            item = re.match(r"^\s*-\s*(.+?)\s*$", line)
            if item:
                values.append(item.group(1).strip("'\""))
                continue
            if line.strip():
                break
    return [value for value in values if value]


def parse_note_metadata_fast(mem_dir: Path, filename: str) -> dict:
    """Legge solo il front-matter utile al retrieval: scope, aliases e tags."""
    default = {"scope": "global", "aliases": [], "tags": [], "always_active": False}
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        return default
    path = mem_dir / filename
    if not path.is_file():
        return default
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            lines = []
            for index, line in enumerate(fh):
                if index >= 120:
                    break
                lines.append(line)
                if index > 0 and line.strip() == "---":
                    break
        head = "".join(lines)
    except OSError:
        return default
    scope_match = _SCOPE_RE.search(head)
    aliases = _frontmatter_list_value(head, "aliases")
    if not aliases:
        aliases = _frontmatter_list_value(head, "alias")
    tags = _frontmatter_list_value(head, "tags")
    active_match = re.search(
        r"^(?:always_active|always-active|sempre_attiva)\s*:\s*(true|yes|1|on)\s*$",
        head,
        re.IGNORECASE | re.MULTILINE,
    )
    active_tags = {tag.casefold().lstrip("#") for tag in tags}
    return {
        "scope": normalize_scope(scope_match.group(1)) if scope_match else "global",
        "aliases": aliases,
        "tags": tags,
        "always_active": bool(active_match) or bool(
            active_tags & {"always-active", "always_active", "regole-operative", "regole/operative"}
        ),
    }


def normalize_scope(scope: str) -> str:
    """Normalizza uno scope: lowercase, solo valori noti. Default 'global'."""
    s = (scope or "").strip().lower()
    return s if s in VALID_SCOPES else "global"


# Vocabolario per progetto: le parole con cui Giorgio nomina di fatto ciascun
# progetto. Un task che ne tocca due (es. estrarre ricette dai reel dei creator
# per VisionBuilts) resta volutamente senza scope, cosi' entrambi i contesti
# restano visibili.
_SCOPE_VOCABULARY: dict[str, tuple[str, ...]] = {
    "hermes": ("hermes", "command bridge", "prime", "8788", "webui", "gateway"),
    "visionbuilts": (
        "visionbuilts", "vision builds", "console", "n8n", "crm",
        "influencer", "outreach", "instagram", "reel", "creator",
    ),
    "libricino": ("libricino", "ricetta", "ricette"),
}

_SCOPE_MARKERS: dict[str, tuple[re.Pattern[str], ...]] = {
    scope: tuple(re.compile(r"\b" + re.escape(m) + r"\b") for m in markers)
    for scope, markers in _SCOPE_VOCABULARY.items()
}


def classify_task_scope(task: str) -> str:
    """Deduce lo scope di progetto dal testo del task.

    Ritorna "" se il task non tocca nessun progetto noto oppure se ne tocca
    piu' di uno: in entrambi i casi il filtro resta disattivato e Prime vede
    tutte le note, che e' il comportamento storico.
    """
    text = str(task or "").casefold()
    matched = {
        scope
        for scope, patterns in _SCOPE_MARKERS.items()
        if any(pattern.search(text) for pattern in patterns)
    }
    return matched.pop() if len(matched) == 1 else ""


def parse_note_scope_fast(mem_dir: Path, filename: str) -> str:
    """Legge le prime 15 righe del file e ritorna il valore di scope.

    Cerca ``scope: <value>`` nel frontmatter YAML.
    Ritorna il valore in lowercase (normalizzato tramite normalize_scope),
    o "global" se assente o in caso di errore.

    Path safety: stesse regole di load_memory_body (no traversal, no slash).
    """
    return parse_note_metadata_fast(mem_dir, filename)["scope"]


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
        metadata = parse_note_metadata_fast(mem_dir, filename)
        title = m.group("title").strip()
        always_active = metadata["always_active"] or (
            "regole operative sempre attive" in title.casefold()
        )
        entries.append({
            "title": title,
            "filename": filename,
            "description": (m.group("description") or "").strip(),
            "scope": metadata["scope"],
            "aliases": metadata["aliases"],
            "tags": metadata["tags"],
            "always_active": always_active,
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
    words = re.findall(r"[a-zA-ZÀ-ÿà-ÿ0-9\-]{" + str(min_len) + r",}", raw)
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


def _weighted_entry_score(entry: dict, keywords: list[str], idf: dict[str, float]) -> float:
    fields = (
        (str(entry.get("title", "")).casefold(), 5.0),
        (" ".join(entry.get("aliases") or []).casefold(), 4.0),
        (" ".join(entry.get("tags") or []).casefold(), 3.0),
        (str(entry.get("description", "")).casefold(), 2.0),
        (str(entry.get("body", "")).casefold(), 1.0),
    )
    score = 0.0
    for keyword in keywords:
        term_score = sum(
            weight
            for haystack, weight in fields
            if keyword in set(re.findall(r"[a-zA-ZÀ-ÿà-ÿ0-9_-]+", haystack))
        )
        score += term_score * idf.get(keyword, 1.0)
    return round(score, 3)


def select_memories_unlocked(
    task: str,
    mem_dir: Path,
    *,
    top_k: int | None = None,
    max_chars: int | None = None,
    min_score: float | None = None,
    task_scope: str = "",
) -> list[dict]:
    """Retrieval lessicale pesato per il profilo unlocked.

    Titolo, alias, tag e descrizione pesano più del corpo. Le regole operative
    sempre attive entrano prima del top-K e indipendentemente dalla query.
    """
    top_k = unlocked_memory_top_k() if top_k is None else max(int(top_k), 0)
    max_chars = unlocked_memory_max_chars() if max_chars is None else max(int(max_chars), 0)
    min_score = unlocked_memory_min_score() if min_score is None else max(float(min_score), 0.0)
    if max_chars <= 0:
        return []
    entries = parse_memory_index(mem_dir)
    if task_scope:
        entries = [
            entry for entry in entries
            if entry.get("scope", "") in {task_scope, "global", ""}
            or entry.get("always_active")
        ]
    hydrated = []
    for entry in entries:
        body = load_memory_body(mem_dir, entry["filename"])
        if body:
            hydrated.append({**entry, "body": body})
    keywords = _extract_keywords(task)
    doc_count = max(len(hydrated), 1)
    idf = {}
    for keyword in keywords:
        frequency = sum(
            1 for entry in hydrated
            if keyword in set(re.findall(
                r"[a-zA-ZÀ-ÿà-ÿ0-9_-]+",
                " ".join([
                    str(entry.get("title", "")),
                    str(entry.get("description", "")),
                    " ".join(entry.get("aliases") or []),
                    " ".join(entry.get("tags") or []),
                    str(entry.get("body", "")),
                ]).casefold(),
            ))
        )
        idf[keyword] = 1.0 + math.log((doc_count + 1.0) / (frequency + 1.0))

    always = []
    scored = []
    for entry in hydrated:
        score = _weighted_entry_score(entry, keywords, idf)
        enriched = {**entry, "score": score}
        if entry.get("always_active"):
            always.append(enriched)
        elif score >= min_score:
            scored.append(enriched)
    scored.sort(key=lambda entry: (-entry["score"], entry["title"].casefold()))
    candidates = always + scored[:top_k]

    selected = []
    used = 0
    for entry in candidates:
        remaining = max_chars - used
        if remaining <= 0:
            break
        body = entry["body"]
        if len(body) > remaining:
            if not entry.get("always_active"):
                continue
            suffix = "\n[…nota ridotta al budget caratteri]"
            body = body[:max(remaining - len(suffix), 0)].rstrip() + suffix[:remaining]
        selected.append({**entry, "body": body, "body_chars": len(body)})
        used += len(body)

    logger.info(
        "prime_memory_retrieval: selected=%s used_chars=%d/%d top_k=%d query_terms=%s",
        [
            {"file": entry["filename"], "score": entry["score"], "always": bool(entry.get("always_active"))}
            for entry in selected
        ],
        used,
        max_chars,
        top_k,
        keywords,
    )
    return selected


def build_unlocked_memory_detail(
    task: str,
    mem_dir: Path,
    *,
    top_k: int | None = None,
    max_chars: int | None = None,
    min_score: float | None = None,
    task_scope: str = "",
) -> str:
    """Solo dettaglio dinamico, già limitato al budget caratteri."""
    budget = unlocked_memory_max_chars() if max_chars is None else max(int(max_chars), 0)
    selected = select_memories_unlocked(
        task,
        mem_dir,
        top_k=top_k,
        max_chars=budget,
        min_score=min_score,
        task_scope=task_scope,
    )
    if not selected or budget <= 0:
        return ""
    parts = ["## Memoria (dettaglio rilevante per il task corrente)"]
    for entry in selected:
        marker = " [sempre attiva]" if entry.get("always_active") else ""
        parts.append(f"### {entry['title']}{marker}\n{entry['body']}")
    text = "\n\n".join(parts)
    return text if len(text) <= budget else text[:budget].rstrip()


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


def build_prime_unlocked_memory_detail(
    task: str,
    workspace: Path | None = None,
    *,
    task_scope: str = "",
) -> str:
    """Entry point per il dettaglio unlocked; l'indice resta nel system prompt."""
    mem_dir = find_prime_memory_dir()
    if mem_dir is None:
        logger.debug("memory_retrieval: nessuna mem_dir trovata, dettaglio unlocked omesso")
        return ""
    return build_unlocked_memory_detail(task, mem_dir, task_scope=task_scope)
