"""Ciclo di memoria di Hermes Prime — logica di decisione (pura, testabile).

Questo modulo NON tocca la sessione né la rete: contiene solo le decisioni
"quando potare / comprimere", così sono verificabili con test deterministici.
Il wiring nel loop di Prime (misurare i token reali, resettare la sessione,
accodare il Librarian) vive in routes.py e usa queste funzioni.

Vedi docs/hermes-memory-lifecycle-design.md (Fase A: core token-management).
"""
from __future__ import annotations

import re

# Soglia di default: Prime non deve superare il 60% della finestra di contesto.
DEFAULT_COMPACT_THRESHOLD = 0.60


def context_pressure(prompt_tokens, context_length) -> float:
    """Frazione di contesto usata dall'ultimo turno: prompt_tokens / finestra.

    ``prompt_tokens`` = token in ingresso dell'ultimo ResultMessage (input +
    cache-read: è ciò che occupa la finestra). Ritorna 0.0 se i dati non sono
    validi/insufficienti; clampa a [0, 1].
    """
    try:
        pt = max(0, int(prompt_tokens or 0))
        cl = int(context_length or 0)
    except (TypeError, ValueError):
        return 0.0
    if cl <= 0:
        return 0.0
    return min(1.0, pt / cl)


def should_compact(pressure, threshold: float = DEFAULT_COMPACT_THRESHOLD) -> bool:
    """True se la pressione di contesto ha raggiunto la soglia (auto-compact)."""
    try:
        return float(pressure) >= float(threshold)
    except (TypeError, ValueError):
        return False


def head_changed(prev_head, curr_head) -> bool:
    """True se l'HEAD git è cambiato (nuovo commit) rispetto all'ultimo visto.

    Primitiva del rilevatore commit (trigger L1 SALVA). ``curr_head`` vuoto →
    False (non sappiamo nulla, non scattare).
    """
    curr = str(curr_head or "").strip()
    if not curr:
        return False
    return curr != str(prev_head or "").strip()


# --- Rilevamento "nuovo argomento" (trigger L2 RESET) ------------------------
# CONSERVATIVO: in dubbio NON è nuovo topic (falso negativo < falso positivo).
# Serve un apri-argomento esplicito E l'assenza di marcatori di prosecuzione.

_NEW_TOPIC_OPENERS = tuple(re.compile(p) for p in (
    r"\bora (?:occupiamoci|passiamo|facciamo|vediamo)\b",
    r"\bpassiamo a\b",
    r"\bcambiamo (?:argomento|discorso|progetto)\b",
    r"\bnuov[ao] (?:task|progetto|cosa|argomento)\b",
    r"\baltr[ao] (?:cosa|task|progetto)\b",
    r"\badesso (?:facciamo|occupiamoci|vediamo)\b",
))

_CONTINUATION_MARKERS = (
    "continu", "prosegu", "sotto-task", "sottotask", "questi altri", "questi due",
    "stesso", "rimang", "abbiamo finito", "resta ", "restano", "manca",
)


def is_new_topic(user_message, current_task: str = "") -> bool:
    """True SOLO se il messaggio apre chiaramente un argomento nuovo.

    Conservativo: (1) se ci sono marcatori di prosecuzione ("continuiamo",
    "questi altri due", "abbiamo finito A, ora B") → False; (2) serve un
    apri-argomento esplicito, altrimenti False. ``current_task`` è accettato per
    usi futuri (distanza semantica) ma non richiesto ora.
    """
    text = " ".join(str(user_message or "").lower().split())
    if not text:
        return False
    if any(marker in text for marker in _CONTINUATION_MARKERS):
        return False
    return any(pat.search(text) for pat in _NEW_TOPIC_OPENERS)
