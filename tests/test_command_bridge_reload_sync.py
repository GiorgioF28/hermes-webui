"""Endpoint del Command Bridge dopo il fix reload/sync multi-device.

Spec: docs/specs/fix-command-bridge-reload-sync-timeout.md

- GET /api/bridge/prime/history onora `?since_index=N`, usa
  `history_with_tool_events()` (cosi' il replay delle tool card ha davvero i
  dati) e porta `delegations` + `message_count` per ricostruire le card delega
  dopo un reload (Bug A/B).
- GET /api/bridge/tasks resta invariato nella lista `tasks` (solo running /
  brief non consegnati) ma porta in piu' `prime_live` per il sync
  telefono<->PC senza aprire nuovi socket (Bug D).
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from api import prime_session_store as pss
from api import routes
from api.prime_session_store import PrimeSessionStore


class _Handler:
    """Handler minimo: gli endpoint usano solo l'identita' e `j()`."""


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    store = PrimeSessionStore(tmp_path / "_bridge_prime_session.json", session_id="hermes-prime")
    captured: list[dict] = []

    monkeypatch.setattr(pss, "get_prime_session_store", lambda session_id="hermes-prime": store)
    monkeypatch.setattr(routes, "_request_prime_session_id", lambda handler: "hermes-prime")
    monkeypatch.setattr(routes, "get_clarify_pending", lambda session_id: None)
    monkeypatch.setattr(
        routes,
        "j",
        lambda handler, payload, **kw: captured.append(payload) or True,
    )
    return store, captured


def _get_history(query: str = ""):
    parsed = urlsplit("/api/bridge/prime/history" + (("?" + query) if query else ""))
    routes._handle_bridge_prime_history(_Handler(), parsed)


# ── GET /api/bridge/prime/history ───────────────────────────────────────────

def test_history_endpoint_returns_message_count_and_delegations(bridge):
    store, captured = bridge
    sid = store.begin_turn("delega a librarian")
    store.finish_turn(sid, "ok, delegato")
    store.upsert_delegation(
        "d16", agent="librarian", status="ok", anchor_message_index=0, brief_status="delivered"
    )

    _get_history()

    payload = captured[-1]
    assert payload["message_count"] == 2
    assert payload["total"] == 2
    assert payload["since_index"] == 0
    assert len(payload["messages"]) == 2
    assert [d["id"] for d in payload["delegations"]] == ["d16"]
    assert payload["delegations"][0]["brief_status"] == "delivered"
    assert payload["delegations_rev"] > 0


def test_history_endpoint_honours_since_index(bridge):
    store, captured = bridge
    for i in range(3):
        sid = store.begin_turn(f"domanda {i}")
        store.finish_turn(sid, f"risposta {i}")

    _get_history("since_index=4")

    payload = captured[-1]
    assert payload["since_index"] == 4
    assert payload["total"] == 6
    assert len(payload["messages"]) == 2
    assert payload["messages"][0]["content"] == "domanda 2"


@pytest.mark.parametrize("query", ["", "since_index=", "since_index=abc", "since_index=-4"])
def test_history_endpoint_falls_back_to_full_transcript(bridge, query):
    store, captured = bridge
    sid = store.begin_turn("ciao")
    store.finish_turn(sid, "ok")

    _get_history(query)

    payload = captured[-1]
    assert payload["since_index"] == 0
    assert len(payload["messages"]) == 2


def test_history_endpoint_without_parsed_still_works(bridge):
    """Retro-compatibilita' del chiamante interno (parsed opzionale)."""
    store, captured = bridge
    sid = store.begin_turn("ciao")
    store.finish_turn(sid, "ok")

    routes._handle_bridge_prime_history(_Handler())

    assert captured[-1]["since_index"] == 0
    assert len(captured[-1]["messages"]) == 2


def test_history_endpoint_includes_tool_events(bridge):
    """Prima chiamava history(): data.tool_events era sempre undefined."""
    store, captured = bridge
    sid = store.begin_turn("leggi il file")
    store.append_tool_event(sid, "Read", "letto note.md")
    store.finish_turn(sid, "fatto")

    _get_history()

    assert [e["tool"] for e in captured[-1]["tool_events"]] == ["Read"]


def test_history_endpoint_keeps_pending_briefs_count(bridge):
    """Nessuna regressione sul campo di Fase 1."""
    store, captured = bridge
    sid = store.begin_turn("ciao")
    store.finish_turn(sid, "ok")

    _get_history()

    assert "pending_briefs_count" in captured[-1]


# ── GET /api/bridge/tasks ───────────────────────────────────────────────────

@pytest.fixture
def tasks_endpoint(bridge, monkeypatch):
    store, captured = bridge
    from api import delegation_store, prime_delegation

    class _EmptyDelegationStore:
        def get_all(self):
            return []

    monkeypatch.setattr(delegation_store, "get_delegation_store", lambda workspace: _EmptyDelegationStore())
    monkeypatch.setattr(prime_delegation, "get_background_tasks", lambda: list(_BACKGROUND[0]))
    return store, captured


_BACKGROUND: list[list[dict]] = [[]]


def _call_tasks():
    routes._handle_bridge_tasks(_Handler(), urlsplit("/api/bridge/tasks"))


def test_tasks_endpoint_shape_is_unchanged(tasks_endpoint):
    store, captured = tasks_endpoint
    _BACKGROUND[0] = [
        {
            "id": "d16",
            "agent": "librarian",
            "task": "cerca gli appunti",
            "status": "in_corso",
            "anchor_session_id": "hermes-prime",
            "anchor_message_index": 2,
        }
    ]

    _call_tasks()

    payload = captured[-1]
    assert payload["ok"] is True
    assert [t["id"] for t in payload["tasks"]] == ["d16"]
    assert payload["tasks"][0]["agent"] == "librarian"


def test_tasks_endpoint_exposes_prime_live_for_multi_device_sync(tasks_endpoint):
    store, captured = tasks_endpoint
    _BACKGROUND[0] = []
    sid = store.begin_turn("ciao")
    store.finish_turn(sid, "ok")

    _call_tasks()

    live = captured[-1]["prime_live"]
    assert live["message_count"] == 2
    assert live["streaming"] is False
    assert live["delegations_rev"] == store.live()["delegations_rev"]
    assert "updated_at" in live


def test_tasks_endpoint_reports_streaming_while_a_turn_is_open(tasks_endpoint):
    store, captured = tasks_endpoint
    _BACKGROUND[0] = []
    store.begin_turn("ciao")

    _call_tasks()

    assert captured[-1]["prime_live"]["streaming"] is True


def test_tasks_endpoint_persists_delegation_records_for_the_reload_replay(tasks_endpoint):
    """Il poll a 3 s alimenta le card durevoli usate dopo un reload."""
    store, captured = tasks_endpoint
    _BACKGROUND[0] = [
        {
            "id": "d16",
            "agent": "librarian",
            "task": "cerca gli appunti",
            "status": "in_corso",
            "anchor_session_id": "hermes-prime",
            "anchor_message_index": 2,
        }
    ]

    _call_tasks()

    (record,) = store.get_delegations()
    assert record["id"] == "d16"
    assert record["agent"] == "librarian"
    assert record["task_excerpt"] == "cerca gli appunti"
    assert record["status"] == "in_corso"
    assert record["anchor_message_index"] == 2

    # La delega finisce: il record durevole si aggiorna e rev cambia.
    rev_before = store.get_delegations_rev()
    _BACKGROUND[0] = [
        {
            "id": "d16",
            "agent": "librarian",
            "task": "cerca gli appunti",
            "status": "ok",
            "anchor_session_id": "hermes-prime",
            "anchor_message_index": 2,
            "finished": 1200.0,
        }
    ]
    _call_tasks()

    (record,) = store.get_delegations()
    assert record["status"] == "ok"
    assert record["finished_at"] == 1200.0
    assert store.get_delegations_rev() > rev_before


def test_tasks_endpoint_ignores_other_sessions(tasks_endpoint):
    store, captured = tasks_endpoint
    _BACKGROUND[0] = [
        {"id": "d99", "agent": "librarian", "status": "ok", "anchor_session_id": "hermes-prime-tom"}
    ]

    _call_tasks()

    assert captured[-1]["tasks"] == []
    assert store.get_delegations() == []
