"""Lead-brain failover — quando Claude (Anthropic) esaurisce i crediti, il brain
di Hermes Prime passa automaticamente a Codex, riprendendo da active-context +
handoff.

Lo stato vive in ``<workspace>/tasks/lead-brain.json`` cosi' flippare il capo NON
richiede un riavvio del codice: lo stato si legge ad ogni turno (vedi
``routes._hermes_prime_reply``). Il default e' sempre Claude.

Niente segreti qui dentro: lo stato contiene solo {lead, reason, since, manual}.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

LEAD_CLAUDE = "claude"
LEAD_CODEX = "codex"
_VALID_LEADS = frozenset({LEAD_CLAUDE, LEAD_CODEX})

# Marcatori che, trovati nel testo di un'eccezione del turno Claude, indicano
# crediti/quota finiti (non un blip transitorio). Volutamente conservativi:
# "overloaded" e gli errori di rete restano FUORI, cosi' un intoppo passeggero
# non sposta il capo in modo permanente. Una rate-limit per finestra su piano
# Pro/Max = "crediti finiti" per quella finestra, quindi e' incluso.
_QUOTA_ERROR_MARKERS = (
    "rate_limit",
    "rate limit",
    "429",
    "insufficient",
    "credit balance",
    "credit_balance",
    "billing",
    "quota",
    "usage limit",
    "usage_limit",
    "exceeded your",
    "out of credit",
)


def _state_path(workspace) -> Path:
    return Path(str(workspace)) / "tasks" / "lead-brain.json"


def get_lead_state(workspace) -> dict:
    """Stato completo del capo corrente. Default: Claude con failover automatico."""
    try:
        data = json.loads(_state_path(workspace).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            lead = str(data.get("lead") or "").strip().lower()
            if lead in _VALID_LEADS:
                data["lead"] = lead
                data["manual"] = bool(data.get("manual", False))
                return data
    except FileNotFoundError:
        pass
    except Exception:
        logger.debug("lead-brain state read failed", exc_info=True)
    return {"lead": LEAD_CLAUDE, "reason": "", "since": "", "manual": False}


def get_lead(workspace) -> str:
    """Chi e' il capo adesso: 'claude' (default) o 'codex'."""
    return get_lead_state(workspace).get("lead", LEAD_CLAUDE)


def set_lead(workspace, lead: str, reason: str = "", manual: bool = False) -> dict:
    """Persiste il capo corrente in modo atomico. Ritorna lo stato scritto."""
    norm = (lead or "").strip().lower()
    if norm not in _VALID_LEADS:
        norm = LEAD_CLAUDE
    state = {
        "lead": norm,
        "reason": str(reason or "")[:200],
        "since": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "manual": bool(manual),
    }
    p = _state_path(workspace)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        logger.warning("lead-brain state write failed", exc_info=True)
    return state


def set_auto_failover(workspace) -> dict:
    """Mantiene il capo corrente ma riabilita il failover automatico."""
    current = get_lead_state(workspace)
    return set_lead(
        workspace,
        current.get("lead", LEAD_CLAUDE),
        reason="failover automatico",
        manual=False,
    )


def is_claude_quota_error(exc: BaseException) -> bool:
    """True se l'eccezione del turno Claude segnala crediti/quota esauriti."""
    if exc is None:
        return False
    try:
        text = f"{type(exc).__name__}: {exc}".lower()
    except Exception:
        return False
    # Lo status 401/403 puro e' auth, non crediti: lo escludiamo per non flippare
    # su una sessione scaduta (che si risolve con re-login, non con Codex).
    return any(marker in text for marker in _QUOTA_ERROR_MARKERS)


def result_message_quota_reason(msg) -> str:
    """Crediti/quota finiti segnalati da un ``ResultMessage`` del Claude SDK.

    Caso critico: quando i crediti/usage di Claude finiscono il CLI **non solleva
    un'eccezione** — emette un ResultMessage con ``is_error=True`` e
    ``api_error_status`` valorizzato (es. 429), spesso con ``subtype="success"``.
    Senza questo controllo il turno "completa" senza errori e lo switch a Codex
    non scatta mai (l'utente vede l'errore grezzo come risposta).

    Ritorna una stringa-motivo NON vuota se va fatto l'handoff, altrimenti "".
    Conservativo: 429 = limite di finestra (usage/rate) → handoff; 5xx (500/529
    overloaded) = intoppo transitorio → NON flippa il capo.
    """
    if msg is None:
        return ""
    status = getattr(msg, "api_error_status", None)
    is_err = bool(getattr(msg, "is_error", False))
    if status is None and not is_err:
        return ""
    # 429 = rate/usage limit per la finestra corrente → crediti finiti per ora.
    # Il testo del result va portato dietro: lo stesso 429 copre sia la quota
    # esaurita sia "modello non incluso nel piano" (Fable 5 su Pro), e senza il
    # dettaglio i due casi diventano indistinguibili per l'utente.
    if status == 429:
        detail = str(getattr(msg, "result", "") or "").strip()
        return f"api_error_status=429: {detail}"[:300] if detail else "api_error_status=429"
    # 5xx (incluso 529 overloaded): transitorio, non e' quota → non flippare.
    if isinstance(status, int) and 500 <= status < 600:
        return ""
    if is_err:
        blobs: list[str] = []
        for attr in ("result", "subtype", "stop_reason"):
            v = getattr(msg, attr, None)
            if v and str(v).lower() != "success":
                blobs.append(str(v))
        errs = getattr(msg, "errors", None)
        if isinstance(errs, (list, tuple)):
            blobs.extend(str(e) for e in errs)
        text = " ".join(blobs).lower()
        if any(marker in text for marker in _QUOTA_ERROR_MARKERS):
            return text[:120]
    return ""


def parse_brain_command(message) -> str | None:
    """Comando manuale del capo dalla chat di Prime. Ritorna 'claude'/'codex'
    (set), 'auto' (riabilita failover), 'status' o None se non è un comando.

    Rete di sicurezza se l'auto-failover non scatta: l'utente scrive '/brain codex'
    e forza il capo senza dover toccare endpoint o riavviare.
    """
    text = " ".join(str(message or "").split()).lower().strip()
    if not text:
        return None
    if text in ("/brain", "/capo"):
        return "status"
    if text in ("/brain auto", "/capo auto"):
        return "auto"
    m = re.match(r"^/(?:brain|capo)\s+(claude|codex)\b", text)
    if m:
        return m.group(1)
    # Forme naturali solo se il messaggio è breve e dedicato (no falsi positivi
    # in una frase di conversazione che cita 'codex'/'claude').
    if len(text.split()) <= 5:
        if re.search(r"\b(passa|switch|metti|vai|forza)\b.*\bcodex\b", text):
            return LEAD_CODEX
        if re.search(r"\b(passa|torna|switch|metti|vai|forza)\b.*\bclaude\b", text):
            return LEAD_CLAUDE
    return None


def _read_resume_section(workspace) -> str:
    """Estrae la sezione '▶ RIPRENDI DA QUI' da tasks/active-context.md."""
    try:
        text = (Path(str(workspace)) / "tasks" / "active-context.md").read_text(encoding="utf-8")
    except Exception:
        return ""
    m = re.search(r"RIPRENDI DA QUI(.*?)(?:\n---\n|\Z)", text, re.S)
    if not m:
        return ""
    return m.group(1).strip()[:4000]


def build_handoff_packet(workspace, user_message: str = "", partial_reply: str = "") -> str:
    """Pacchetto di consegna per Codex: stato di ripresa + parziale + messaggio."""
    parts: list[str] = []
    resume = _read_resume_section(workspace)
    if resume:
        parts.append("## Stato di ripresa (tasks/active-context.md → RIPRENDI DA QUI)\n" + resume)
    partial = str(partial_reply or "").strip()
    if partial:
        parts.append("## Risposta che Claude stava dando prima di esaurirsi (parziale)\n" + partial[:2000])
    msg = str(user_message or "").strip()
    if msg:
        parts.append("## Ultimo messaggio dell'utente a cui rispondere\n" + msg[:2000])
    return "\n\n".join(parts)
