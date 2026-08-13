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

import re as _re
import time as _time
from datetime import datetime as _dt

# Rami stabili. Il valore stringa è parte del contratto (lo usa anche il frontend
# per decidere il messaggio) → non rinominare senza aggiornare la UI e i test.
CLAUDE_QUOTA = "claude_quota"
CLAUDE_PLAN_CREDITS = "claude_plan_credits"
CODEX_TIMEOUT = "codex_timeout"
CODEX_NOT_FOUND = "codex_not_found"
CODEX_AUTH = "codex_auth"
CODEX_EXIT = "codex_exit"
PRIME_IDLE_TIMEOUT = "prime_idle_timeout"
PRIME_HARDCAP = "prime_hardcap"
CLI_STARTUP = "cli_startup"
TRANSPORT_CUT = "transport_cut"
UNKNOWN = "unknown"

# ramo -> (messaggio utente, hint operativo)
_MESSAGES = {
    CLAUDE_QUOTA: (
        "Claude ha esaurito crediti/quota per questa finestra.",
        "Passa a Codex dalla UI (CODEX) o aspetta il reset della finestra.",
    ),
    CLAUDE_PLAN_CREDITS: (
        "Il modello scelto non è incluso nel tuo piano: richiede crediti extra.",
        "Non è quota finita e non si sblocca aspettando: scegli un modello incluso "
        "nell'abbonamento (es. Opus 5 o Sonnet 5) oppure attiva i crediti a consumo.",
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
    CLI_STARTUP: (
        "Il processo Claude CLI di Prime non è partito (morto all'avvio).",
        "Opzioni/config della sessione bridge rotte (es. --session-id non UUID, "
        "flag invalido, login CLI): guarda lo stderr nel log del server.",
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

    # 1-bis) CLI di Prime morto all'avvio (connect/initialize): ProcessError del
    # SDK ("Command failed with exit code N"), --session-id non UUID, flag rotti.
    if not codexy and (
        name in {"ProcessError", "CLIConnectionError"}
        or "command failed with exit code" in text
        or "invalid session id" in text
        or "failed to start claude" in text
    ):
        return CLI_STARTUP

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

    # 4-bis) Modello non incluso nel piano (richiede crediti a consumo). PRIMA
    # della quota: l'API risponde 429 e il messaggio del pin manuale contiene la
    # parola "quota", quindi senza questa precedenza finirebbe in CLAUDE_QUOTA e
    # manderebbe l'utente ad aspettare un reset che non arriva mai.
    if any(k in text for k in (
        "requires usage credits", "usage credits", "usage-credits",
        "extra usage", "not included in your plan",
    )):
        return CLAUDE_PLAN_CREDITS

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


# ── Quota reset parsing (timer Command Bridge) ────────────────────────────────
# Quando Claude esaurisce la quota, il CLI spesso dice ANCHE quando la finestra
# si resetta, ma in formati diversi a seconda della versione:
#   "Claude AI usage limit reached|1723554000"        (epoch dopo una pipe)
#   "Your limit will reset at 7pm"                    (orario locale am/pm)
#   "usage limit reached ... resets at 15:00"         (orario locale 24h)
#   "retry after 3600"                                (secondi)
#   timestamp ISO ("2026-08-13T18:00:00Z")
# Best-effort puro: se non troviamo nulla di plausibile torna None e la UI
# mostra il badge senza countdown. Sanity: il reset deve cadere tra "adesso" e
# +7 giorni, altrimenti e' rumore (id numerici, epoch passati, ecc.).
_QUOTA_EPOCH_PIPE = _re.compile(r"\|\s*(\d{10,13})\b")
_QUOTA_EPOCH_NEAR_RESET = _re.compile(r"reset\w*\D{0,12}(\d{10,13})\b", _re.I)
_QUOTA_ISO = _re.compile(
    r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)"
)
_QUOTA_RETRY_SECONDS = _re.compile(r"retry[- ]after[:\s]+(\d{1,6})\b", _re.I)
_QUOTA_CLOCK = _re.compile(
    r"reset\w*(?:\s+at)?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", _re.I
)


def quota_reset_epoch(text, now: float | None = None) -> float | None:
    """Estrae dall'errore di quota Claude QUANDO la finestra si resetta.

    Ritorna epoch (secondi, float) oppure None se il testo non contiene un
    orario di reset riconoscibile. ``now`` e' iniettabile per i test.
    """
    if not text:
        return None
    s = str(text)
    now_ts = float(now if now is not None else _time.time())

    def _ok(ts: float) -> float | None:
        return ts if now_ts < ts <= now_ts + 7 * 86400 else None

    # 1) Epoch esplicito (formato CLI "...|<epoch>" o vicino a "reset").
    for rx in (_QUOTA_EPOCH_PIPE, _QUOTA_EPOCH_NEAR_RESET):
        m = rx.search(s)
        if m:
            ts = float(m.group(1))
            if ts > 1e12:  # millisecondi
                ts /= 1000.0
            got = _ok(ts)
            if got is not None:
                return got

    # 2) Timestamp ISO (naive = ora locale del server).
    m = _QUOTA_ISO.search(s)
    if m:
        try:
            dt = _dt.fromisoformat(m.group(1).replace(" ", "T").replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.astimezone()
            got = _ok(dt.timestamp())
            if got is not None:
                return got
        except ValueError:
            pass

    # 3) "retry after N" (secondi).
    m = _QUOTA_RETRY_SECONDS.search(s)
    if m:
        got = _ok(now_ts + float(m.group(1)))
        if got is not None:
            return got

    # 4) Orario a muro ("resets at 7pm" / "reset at 15:00"): oggi, o domani se
    # l'ora e' gia' passata. Interpretato nella timezone locale del server.
    m = _QUOTA_CLOCK.search(s)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ampm = (m.group(3) or "").lower()
        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            local = _dt.fromtimestamp(now_ts)
            try:
                cand = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
                ts = cand.timestamp()
                if ts <= now_ts:
                    ts += 86400
                got = _ok(ts)
                if got is not None:
                    return got
            except (ValueError, OverflowError, OSError):
                pass

    return None
