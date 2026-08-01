"""Test per la classificazione degli errori del Command Bridge.

Obiettivo: quando Hermes Prime o un sotto-agente falliscono, l'utente deve vedere
un RAMO diagnostico chiaro (perché è fallito) invece del generico "non riesco a
contattare Hermes Bridge". Questi test bloccano quel contratto.
"""
import io
import subprocess

from api import bridge_errors as be
from api import routes


# ── 1) Classificatore puro ──────────────────────────────────────────────────

def test_codex_timeout_from_runtimeerror():
    exc = RuntimeError("Codex CLI timeout dopo 600s.\nParziale prima del timeout: ...")
    assert be.classify_branch(exc) == be.CODEX_TIMEOUT


def test_codex_timeout_from_subprocess_timeoutexpired():
    exc = subprocess.TimeoutExpired(cmd=["codex", "exec"], timeout=600)
    assert be.classify_branch(exc) == be.CODEX_TIMEOUT


def test_codex_not_found():
    exc = RuntimeError("Codex CLI non trovato (codex.cmd non nel PATH)")
    assert be.classify_branch(exc) == be.CODEX_NOT_FOUND
    assert be.classify_branch(FileNotFoundError("codex.cmd")) == be.CODEX_NOT_FOUND


def test_codex_auth():
    exc = RuntimeError("Codex CLI exit 1: not logged in, please login")
    assert be.classify_branch(exc) == be.CODEX_AUTH


def test_codex_exit_generic():
    exc = RuntimeError("Codex CLI exit 2: some build failure")
    assert be.classify_branch(exc) == be.CODEX_EXIT


def test_claude_quota_session_limit():
    # È esattamente il testo che appare nella card quando Claude finisce la finestra.
    exc = RuntimeError("You've hit your session limit - resets 2:50pm")
    assert be.classify_branch(exc) == be.CLAUDE_QUOTA


def test_claude_quota_named_exception():
    class _ClaudeExhausted(Exception):
        pass
    assert be.classify_branch(_ClaudeExhausted("api_error_status=429")) == be.CLAUDE_QUOTA


def test_claude_plan_credits_not_confused_with_quota():
    # Caso reale (2026-08-01): Fable 5 non e' incluso nel piano Pro e l'API
    # risponde 429 -> il bridge diceva "quota esaurita" e mandava l'utente ad
    # aspettare un reset che non sarebbe mai arrivato. E' un problema di PIANO.
    exc = RuntimeError(
        "api_error_status=429: Fable 5 requires usage credits. "
        "Run /usage-credits to continue or switch models with /model."
    )
    assert be.classify_branch(exc) == be.CLAUDE_PLAN_CREDITS


def test_claude_plan_credits_wins_over_quota_wording():
    # Il messaggio del pin manuale contiene la parola "quota": il ramo piano
    # deve comunque vincere, altrimenti la causa vera resta nascosta.
    exc = RuntimeError(
        "Claude e' pinnato manualmente e ha rifiutato il turno per quota "
        "(api_error_status=429: Fable 5 requires usage credits)."
    )
    assert be.classify_branch(exc) == be.CLAUDE_PLAN_CREDITS


def test_transport_cut():
    assert be.classify_branch(BrokenPipeError("[Errno 32] Broken pipe")) == be.TRANSPORT_CUT
    assert be.classify_branch(ConnectionResetError("WinError 10054")) == be.TRANSPORT_CUT


def test_unknown_fallback():
    assert be.classify_branch(ValueError("qualcosa di strano")) == be.UNKNOWN


def test_classify_returns_message_and_hint():
    info = be.classify(RuntimeError("Codex CLI timeout dopo 600s."))
    assert info["branch"] == be.CODEX_TIMEOUT
    assert info["message"] and info["hint"]


# ── 2) Wiring: il bridge emette un evento SSE 'error' con il ramo ────────────

class _Handler:
    def __init__(self):
        self.wfile = io.BytesIO()
        self.status = None
        self.headers = {}

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.headers[name] = value

    def end_headers(self):
        pass


def _events(handler):
    import json
    raw = handler.wfile.getvalue().decode("utf-8").strip()
    frames = [f for f in raw.split("\n\n") if f.strip()]
    out = []
    for frame in frames:
        event = data = None
        for line in frame.splitlines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        out.append((event, data))
    return out


def test_bridge_prime_emits_error_event_with_branch(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("Codex CLI timeout dopo 600s.")

    monkeypatch.setattr(routes, "_hermes_prime_reply", _boom)
    handler = _Handler()
    routes._handle_bridge_prime(handler, {"message": "ciao"})

    events = _events(handler)
    errs = [d for (e, d) in events if e == "error"]
    assert errs, f"nessun evento error negli SSE: {events}"
    assert errs[-1].get("branch") == be.CODEX_TIMEOUT
    assert errs[-1].get("error")  # messaggio umano presente
