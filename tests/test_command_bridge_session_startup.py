"""Regressione bug 2026-07-18: Prime morto all'avvio per --session-id non-UUID.

Il CLI Claude valida `--session-id` ("Invalid session ID. Must be a valid UUID.")
ed esce con exit 1 -> il client SDK di Prime non si connette MAI e ogni messaggio
del Command Bridge fallisce all'istante con [unknown]. Questi test bloccano:
1) il contratto "l'id sessione SDK di Prime E' un UUID valido";
2) la classificazione dell'errore (mai piu' [unknown] per un crash di startup).
"""
import uuid

from api import bridge_errors as be
from api import routes


# --- 1) contratto: session id SDK di Prime = UUID valido ---------------------

def test_prime_sdk_session_id_is_valid_uuid():
    sid = routes._fresh_prime_sdk_session_id()
    # uuid.UUID solleva ValueError se il formato non e' un UUID: il vecchio
    # "hermes-prime-<pid>-<hex>" faceva morire il CLI all'avvio.
    parsed = uuid.UUID(str(sid))
    assert str(parsed) == str(sid).lower()


def test_prime_sdk_session_id_is_fresh_per_connect():
    # Bug 2026-07-18 #3: con un UUID fisso per processo, ogni ricreazione della
    # sessione rilanciava il CLI con lo stesso --session-id -> "Session ID ...
    # is already in use" (exit 1, ramo cli_startup) fino al riavvio. L'id deve
    # essere NUOVO ad ogni chiamata.
    ids = {routes._fresh_prime_sdk_session_id() for _ in range(5)}
    assert len(ids) == 5


# --- 2) classificazione: crash di startup del CLI != unknown -----------------

class ProcessError(Exception):
    """Stesso nome dell'eccezione del claude_agent_sdk (classifica per nome)."""


def test_sdk_process_error_is_cli_startup():
    exc = ProcessError(
        "Command failed with exit code 1 (exit code: 1)\n"
        "Error output: Check stderr output for details"
    )
    assert be.classify_branch(exc) == be.CLI_STARTUP


def test_invalid_session_id_text_is_cli_startup():
    exc = RuntimeError("Error: Invalid session ID. Must be a valid UUID.")
    assert be.classify_branch(exc) == be.CLI_STARTUP


def test_cli_startup_has_message_and_hint():
    info = be.classify(ProcessError("Command failed with exit code 1"))
    assert info["branch"] == be.CLI_STARTUP
    assert "CLI" in info["message"]
    assert info["hint"]


def test_codex_exit_still_wins_for_codex_errors():
    # Un errore del *Codex* CLI non deve finire nel ramo cli_startup di Prime.
    exc = RuntimeError("Codex CLI exit 1: boom")
    assert be.classify_branch(exc) == be.CODEX_EXIT
