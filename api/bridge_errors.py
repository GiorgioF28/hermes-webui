"""Classificazione degli errori del Command Bridge (Hermes Prime + sotto-agenti).

Scopo: trasformare un'eccezione (o un testo d'errore) in un RAMO diagnostico
stabile con messaggio umano + hint, così l'utente vede PERCHÉ è fallito invece
del generico "non riesco a contattare Hermes Bridge".

Il modulo è PURO e senza dipendenze interne (niente import circolari, facile da
testare): classifica per NOME del tipo di eccezione + testo, non per classe
importata. I messaggi non contengono segreti/paths (già ripuliti a monte da
``_sanitize_error``).
"""
from __future__ import annotations

# Rami stabili. Il valore stringa è parte del contratto (lo usa anche il frontend
# per decidere il messaggio) → non rinominare senza aggiornare la UI e i test.
CLAUDE_QUOTA = "claude_quota"
CODEX_TIMEOUT = "codex_timeout"
CODEX_NOT_FOUND = "codex_not_found"
CODEX_AUTH = "codex_auth"
CODEX_EXIT = "codex_exit"
PRIME_IDLE_TIMEOUT = "prime_idle_timeout"
PRIME_HARDCAP = "prime_hardcap"
TRANSPORT_CUT = "transport_cut"
UNKNOWN = "unknown"

# ramo -> (messaggio utente, hint operativo)
_MESSAGES = {
    CLAUDE_QUOTA: (
        "Claude ha esaurito crediti/quota per questa finestra.",
        "Passa a Codex dalla UI (CODEX) o aspetta il reset della finestra.",
    ),
    CODEX_TIMEOUT: (
        "Codex ha superato il tempo massimo per un singolo turno.",
        "Task troppo grosso per una sola exec: spezzalo in sotto-task più piccoli.",
    ),
    CODEX_NOT_FOUND: (
        "Codex CLI non raggiungibile (eseguibile non trovato nel PATH del server).",
        "Verifica che 'codex.cmd' sia nel PATH del processo WebUI, poi riavvia.",
    ),
    CODEX_AUTH: (
        "Codex non è autenticato (login scaduto o mancante).",
        "Rifai il login di Codex (codex login) e riprova.",
    ),
    CODEX_EXIT: (
        "Codex è uscito con errore durante l'esecuzione del turno.",
        "Guarda l'output del task per il dettaglio dell'errore Codex.",
    ),
    PRIME_IDLE_TIMEOUT: (
        "Hermes Prime è rimasto fermo (nessun progresso) oltre il limite di attesa.",
        "Probabile stallo nel ragionamento: richiedi il brief o spezza la richiesta.",
    ),
    PRIME_HARDCAP: (
        "Il turno di Hermes Prime ha superato il tetto massimo di durata.",
        "Richiesta troppo lunga: spezzala o delega il lavoro pesante.",
    ),
    TRANSPORT_CUT: (
        "La connessione con Hermes Prime si è interrotta a metà risposta.",
        "Rete/stream caduti: riprova; se ricorre, controlla i log del server.",
    ),
    UNKNOWN: (
        "Hermes Prime non ha completato la risposta per un errore non classificato.",
        "Controlla i log del server ('hermes prime reply failed') per il dettaglio.",
    ),
}


def _text_of(exc) -> str:
    if exc is None:
        return ""
    if isinstance(exc, str):
        return exc
    try:
        return f"{type(exc).__name__}: {exc}"
    except Exception:
        return ""


def classify_branch(exc) -> str:
    """Ritorna solo il ramo (stringa). ``exc`` può essere Exception o testo."""
    name = "" if isinstance(exc, (str, type(None))) else type(exc).__name__
    text = _text_of(exc).lower()

    # 1) Trasporto / disconnessioni socket (prima di tutto: sono inequivocabili).
    if name in {"BrokenPipeError", "ConnectionResetError", "ConnectionAbortedError"} \
       or "broken pipe" in text or "connection reset" in text \
       or "connection aborted" in text or "winerror 10053" in text \
       or "winerror 10054" in text:
        return TRANSPORT_CUT

    codexy = "codex" in text

    # 2) Timeout (Codex exec vs stallo di Prime).
    if "timeoutexpired" in text or "timeout dopo" in text or "timed out" in text \
       or name == "TimeoutError":
        if codexy or "timeout dopo" in text:
            return CODEX_TIMEOUT
        return PRIME_IDLE_TIMEOUT

    # 3) Eseguibile non trovato / PATH.
    if name == "FileNotFoundError" or "non trovato" in text or "not found" in text \
       or "codex.cmd" in text or "no such file" in text:
        return CODEX_NOT_FOUND

    # 4) Auth Codex (login scaduto/mancante) — specifico di Codex.
    if codexy and any(k in text for k in (
        "unauthorized", "not logged in", "please login", "please log in",
        "auth.json", "authentication", "credential",
    )):
        return CODEX_AUTH

    # 5) Quota/crediti Claude finiti (include il limite di sessione Pro/Max).
    if name == "_ClaudeExhausted" or any(k in text for k in (
        "rate_limit", "rate limit", "quota", "credit balance", "credit_balance",
        "usage limit", "usage_limit", "exceeded your", "out of credit",
        "session limit", "hit your session", "429", "insufficient", "billing",
    )):
        return CLAUDE_QUOTA

    # 6) Codex uscito con exit code != 0.
    if "codex cli exit" in text or (codexy and "exit" in text):
        return CODEX_EXIT

    return UNKNOWN


def classify(exc) -> dict:
    """Ritorna ``{branch, message, hint}`` pronto per l'utente/SSE."""
    branch = classify_branch(exc)
    message, hint = _MESSAGES.get(branch, _MESSAGES[UNKNOWN])
    return {"branch": branch, "message": message, "hint": hint}
