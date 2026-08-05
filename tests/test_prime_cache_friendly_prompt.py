"""Tests per perf/prompt-cache-breakpoints — verifica che il prompt Prime
sia costruito in modo cache-friendly:

1. Il blocco memoria (variabile per turno) è SUFFISSO, non prefisso, del
   prompt utente inviato al CLI. Così il CLI claude può piazzare breakpoint
   cache_control sulla parte stabile (inizio messaggio = richiesta reale
   dell'utente) senza che contenuto variabile spezzi il prefisso cacheabile.

2. Il system prompt (_hermes_prime_system_prompt) NON viene ricalcolato quando
   la sessione Prime esiste già. Evita I/O a vuoto su project-inventory.csv,
   MEMORY.md e hermes-lean.md a ogni turno.

3. Corollario SDK: il cliente claude_agent_sdk non espone cache_control
   breakpoints espliciti (li gestisce internamente il CLI claude). La nostra
   ottimizzazione agisce sul LAYOUT del contenuto, non su parametri HTTP.
"""
from __future__ import annotations

import asyncio
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers condivisi
# ---------------------------------------------------------------------------

class _DoneFuture:
    def __init__(self, value=None):
        self._value = value

    def result(self, timeout=None):
        return self._value

    def cancel(self):
        return False

    def add_done_callback(self, cb):
        cb(self)


# ---------------------------------------------------------------------------
# Test 1: memoria è SUFFISSO del prompt utente
# ---------------------------------------------------------------------------

def test_memory_context_is_suffix_not_prefix(monkeypatch):
    """Il blocco memoria viene appeso DOPO il testo utente, non preposto."""
    from api import routes

    captured_prompt: list[str] = []

    class FakeClient:
        _hermes_sdk_session_id = "aaaaaaaa-0000-4000-8000-bbbbbbbbbbbb"

        async def query(self, message, session_id="default"):
            captured_prompt.append(message)

        async def receive_response(self):
            # Restituisce subito un ResultMessage minimale
            class _Result:
                pass
            _Result.__name__ = "ResultMessage"
            r = _Result()
            r.result = "ok"
            r.event = None
            r.usage = {}
            yield r

    class FakeRegistry:
        def get(self, session_id):
            return None  # nessuna sessione esistente

        def get_or_create(self, session_id, **kwargs):
            pass

        def submit_turn(self, session_id, drive):
            return _DoneFuture(asyncio.run(drive(FakeClient())))

        def close(self, session_id):
            pass

    FAKE_MEM_CTX = "## Memoria (indice)\n- nota-a: titolo A\n- nota-b: titolo B"

    monkeypatch.setattr(routes, "_get_claude_registry", lambda: FakeRegistry())
    monkeypatch.setattr("api.prime_delegation.get_background_tasks", lambda: [])
    monkeypatch.setattr(
        "api.memory_retrieval.build_prime_memory_context",
        lambda task, workspace, **kw: FAKE_MEM_CTX,
    )
    # Disabilita attachment note (non rilevante per questo test)
    monkeypatch.setattr(routes, "_claude_attachment_note", lambda *a, **kw: "")
    # prime_turn_started e normalize_prime_attachments (bridge_attachments)
    monkeypatch.setattr(
        "api.bridge_attachments.prime_turn_started", lambda: 1
    )
    monkeypatch.setattr(
        "api.bridge_attachments.normalize_prime_attachments", lambda att, bridge=None: att
    )
    monkeypatch.setattr(
        "api.bridge_attachments.record_prime_images", lambda att, turn=None: None
    )

    routes._hermes_prime_reply_claude(
        "che ora è",
        Path("."),
        on_token=lambda t: None,
        on_status=lambda s: None,
    )

    assert captured_prompt, "client.query non è stato chiamato"
    msg = captured_prompt[0]

    # La richiesta dell'utente PRECEDE la sezione memoria
    assert msg.startswith("che ora è"), (
        f"Il prompt non inizia con la richiesta utente. Inizio: {repr(msg[:80])}"
    )
    # Il blocco memoria è presente MA in fondo (suffisso)
    assert FAKE_MEM_CTX in msg, "Il blocco memoria non è nel prompt"
    user_pos = msg.index("che ora è")
    mem_pos = msg.index(FAKE_MEM_CTX)
    assert user_pos < mem_pos, (
        f"La memoria ({mem_pos}) è prima del testo utente ({user_pos}): "
        "deve essere suffisso!"
    )
    # Il separatore canonico è presente
    assert "## Memoria rilevante" in msg, "Manca il titolo sezione '## Memoria rilevante'"


# ---------------------------------------------------------------------------
# Test 2: system prompt NON ricalcolato se sessione esiste
# ---------------------------------------------------------------------------

def test_system_prompt_not_recomputed_when_session_exists(monkeypatch):
    """Se la sessione hermes-prime è già attiva, _hermes_prime_system_prompt
    NON deve essere chiamato (evita I/O su project-inventory.csv, MEMORY.md
    e hermes-lean.md a ogni turno)."""
    from api import routes

    compute_calls: list[int] = []

    def fake_system_prompt(workspace):
        compute_calls.append(1)
        return "system_prompt_calcolato"

    class _ExistingClient:
        _hermes_sdk_session_id = None

    existing_client = _ExistingClient()

    class FakeRegistry:
        def get(self, session_id):
            # Simula sessione già esistente → get_or_create non chiama factory
            return existing_client

        def get_or_create(self, session_id, **kwargs):
            # Se session esiste, get_or_create NON chiama il factory;
            # il system_prompt passato viene ignorato.
            return existing_client

        def submit_turn(self, session_id, drive):
            class _Fut:
                def result(self, timeout=None):
                    return asyncio.run(drive(existing_client))
                def cancel(self):
                    return False
                def add_done_callback(self, cb):
                    cb(self)
            return _Fut()

        def close(self, session_id):
            pass

    class _FakeClient:
        _hermes_sdk_session_id = None

        async def query(self, message, session_id="default"):
            pass

        async def receive_response(self):
            class _Result:
                pass
            _Result.__name__ = "ResultMessage"
            r = _Result(); r.result = "ok"; r.event = None; r.usage = {}
            yield r

    existing_client.__class__ = _FakeClient

    monkeypatch.setattr(routes, "_get_claude_registry", lambda: FakeRegistry())
    monkeypatch.setattr(routes, "_hermes_prime_system_prompt", fake_system_prompt)
    monkeypatch.setattr("api.prime_delegation.get_background_tasks", lambda: [])
    monkeypatch.setattr(
        "api.memory_retrieval.build_prime_memory_context",
        lambda task, workspace, **kw: "",
    )
    monkeypatch.setattr(routes, "_claude_attachment_note", lambda *a, **kw: "")
    monkeypatch.setattr("api.bridge_attachments.prime_turn_started", lambda: 1)
    monkeypatch.setattr(
        "api.bridge_attachments.normalize_prime_attachments", lambda att, bridge=None: att
    )
    monkeypatch.setattr(
        "api.bridge_attachments.record_prime_images", lambda att, turn=None: None
    )

    routes._hermes_prime_reply_claude(
        "ciao",
        Path("."),
        on_token=lambda t: None,
        on_status=lambda s: None,
    )

    assert compute_calls == [], (
        f"_hermes_prime_system_prompt è stato chiamato {len(compute_calls)} volta/e "
        "anche se la sessione esisteva già: I/O a vuoto evitabile."
    )


# ---------------------------------------------------------------------------
# Test 3: sistema prompt calcolato quando sessione NON esiste
# ---------------------------------------------------------------------------

def test_system_prompt_computed_when_session_is_new(monkeypatch):
    """Se la sessione hermes-prime NON esiste, _hermes_prime_system_prompt
    DEVE essere chiamato (la factory ne ha bisogno per avviare il CLI)."""
    from api import routes

    compute_calls: list[int] = []

    def fake_system_prompt(workspace):
        compute_calls.append(1)
        return "system_prompt_calcolato"

    class FakeRegistry:
        def get(self, session_id):
            return None  # nessuna sessione

        def get_or_create(self, session_id, **kwargs):
            pass

        def submit_turn(self, session_id, drive):
            class _FakeClient:
                _hermes_sdk_session_id = None
                async def query(self, message, session_id="default"):
                    pass
                async def receive_response(self):
                    class _Result:
                        pass
                    _Result.__name__ = "ResultMessage"
                    r = _Result(); r.result = "ok"; r.event = None; r.usage = {}
                    yield r

            class _Fut:
                def result(self, timeout=None):
                    return asyncio.run(drive(_FakeClient()))
                def cancel(self):
                    return False
                def add_done_callback(self, cb):
                    cb(self)
            return _Fut()

        def close(self, session_id):
            pass

    monkeypatch.setattr(routes, "_get_claude_registry", lambda: FakeRegistry())
    monkeypatch.setattr(routes, "_hermes_prime_system_prompt", fake_system_prompt)
    monkeypatch.setattr("api.prime_delegation.get_background_tasks", lambda: [])
    monkeypatch.setattr(
        "api.memory_retrieval.build_prime_memory_context",
        lambda task, workspace, **kw: "",
    )
    monkeypatch.setattr(routes, "_claude_attachment_note", lambda *a, **kw: "")
    monkeypatch.setattr("api.bridge_attachments.prime_turn_started", lambda: 1)
    monkeypatch.setattr(
        "api.bridge_attachments.normalize_prime_attachments", lambda att, bridge=None: att
    )
    monkeypatch.setattr(
        "api.bridge_attachments.record_prime_images", lambda att, turn=None: None
    )

    routes._hermes_prime_reply_claude(
        "ciao",
        Path("."),
        on_token=lambda t: None,
        on_status=lambda s: None,
    )

    assert compute_calls == [1], (
        "_hermes_prime_system_prompt deve essere chiamato esattamente una volta "
        f"quando la sessione è nuova, ma compute_calls={compute_calls}"
    )
