"""Store Prime a buffer + card delega durevoli + storico incrementale.

Copre la spec docs/specs/fix-command-bridge-reload-sync-timeout.md:

- Bug C: `append_token` NON riscrive piu' il file di sessione una volta per
  token (con un transcript da ~2 MB saturava l'I/O e serializzava /live,
  /history e /todos dietro il lock, da cui il toast "Request timed out").
  I token si accumulano in RAM e vengono flushati a intervallo / a soglia /
  sempre a fine turno, restando comunque visibili a `live()`.
- Bug B: `_read_locked` su file corrotto mette il file in quarantena e logga,
  invece di restituire in silenzio una sessione vuota; `history(since_index)`
  permette di scaricare solo la coda del transcript.
- Bug A: lista `delegations` durevole (lancio -> fine -> brief delivered) con
  `delegations_rev` che cambia solo quando cambia davvero qualcosa.
"""

from __future__ import annotations

import json
import time

import pytest

from api.prime_session_store import (
    PARTIAL_FLUSH_MAX_TOKENS,
    PrimeSessionStore,
)


def _store(tmp_path) -> PrimeSessionStore:
    return PrimeSessionStore(tmp_path / "_bridge_prime_session.json", session_id="hermes-prime-test")


def _count_writes(store: PrimeSessionStore) -> list[int]:
    """Conta le scritture reali su disco senza cambiare il comportamento."""
    calls = [0]
    original = store._write_locked

    def counting(data):
        calls[0] += 1
        return original(data)

    store._write_locked = counting  # type: ignore[method-assign]
    return calls


# ── Bug C: buffering dei token ──────────────────────────────────────────────

def test_append_token_does_not_write_once_per_token(tmp_path):
    store = _store(tmp_path)
    stream_id = store.begin_turn("ciao")
    writes = _count_writes(store)

    for i in range(10):
        store.append_token(stream_id, f"tok{i} ")

    assert writes[0] == 0, "i token devono restare in RAM, non riscrivere il file"
    assert store.has_buffered_partial() is True


def test_live_sees_the_buffered_partial_before_any_flush(tmp_path):
    store = _store(tmp_path)
    stream_id = store.begin_turn("ciao")
    store.append_token(stream_id, "Ciao ")
    store.append_token(stream_id, "Giorgio")

    live = store.live()
    assert live["active"] is True
    assert live["pending_turn"]["partial_output"] == "Ciao Giorgio"
    # ...ma su disco il partial non e' ancora stato scritto token per token.
    on_disk = json.loads(store.path.read_text(encoding="utf-8"))
    assert on_disk["pending_turn"]["partial_output"] == ""


def test_token_flush_happens_after_max_tokens(tmp_path):
    store = _store(tmp_path)
    stream_id = store.begin_turn("ciao")
    writes = _count_writes(store)

    for i in range(PARTIAL_FLUSH_MAX_TOKENS):
        store.append_token(stream_id, "x")

    assert writes[0] == 1, "un solo flush, non uno per token"
    on_disk = json.loads(store.path.read_text(encoding="utf-8"))
    assert on_disk["pending_turn"]["partial_output"] == "x" * PARTIAL_FLUSH_MAX_TOKENS


def test_journal_has_no_entry_per_token(tmp_path):
    store = _store(tmp_path)
    stream_id = store.begin_turn("ciao")
    for i in range(PARTIAL_FLUSH_MAX_TOKENS):
        store.append_token(stream_id, "y")

    journal = json.loads(store.path.read_text(encoding="utf-8"))["journal"]
    assert [e for e in journal if e.get("event") == "token"] == []
    assert len([e for e in journal if e.get("event") == "token_flush"]) == 1


def test_flush_partial_forces_a_write(tmp_path):
    store = _store(tmp_path)
    stream_id = store.begin_turn("ciao")
    store.append_token(stream_id, "parziale")

    assert store.flush_partial(stream_id) is True
    on_disk = json.loads(store.path.read_text(encoding="utf-8"))
    assert on_disk["pending_turn"]["partial_output"] == "parziale"
    # Niente da flushare la seconda volta.
    assert store.flush_partial(stream_id) is False


def test_finish_turn_always_persists_the_buffered_partial(tmp_path):
    store = _store(tmp_path)
    stream_id = store.begin_turn("ciao")
    store.append_token(stream_id, "Ciao ")
    store.append_token(stream_id, "Giorgio")
    store.finish_turn(stream_id, "Ciao Giorgio")

    on_disk = json.loads(store.path.read_text(encoding="utf-8"))
    assert on_disk["pending_turn"] is None
    assert on_disk["messages"][-1]["content"] == "Ciao Giorgio"
    assert store.has_buffered_partial() is False


def test_restart_recovery_promotes_the_buffered_partial(tmp_path):
    """Abort/riavvio: il partial in RAM non deve andare perso."""
    store = _store(tmp_path)
    stream_id = store.begin_turn("ciao")
    store.append_token(stream_id, "meta' risposta")

    assert store.recover_stale_pending_turn("interrotto") is True
    on_disk = json.loads(store.path.read_text(encoding="utf-8"))
    assert on_disk["pending_turn"] is None
    assert on_disk["messages"][-1]["content"] == "meta' risposta"
    assert on_disk["messages"][-1]["interrupted"] is True


def test_append_token_ignores_an_unknown_stream_id(tmp_path):
    store = _store(tmp_path)
    store.begin_turn("ciao")
    store.append_token("stream-che-non-esiste", "rumore")
    assert store.live()["pending_turn"]["partial_output"] == ""


# ── Bug B: history incrementale e file corrotto ─────────────────────────────

def test_history_since_index_returns_only_the_tail(tmp_path):
    store = _store(tmp_path)
    for i in range(3):
        sid = store.begin_turn(f"domanda {i}")
        store.finish_turn(sid, f"risposta {i}")

    full = store.history()
    assert full["total"] == 6
    assert full["message_count"] == 6
    assert full["since_index"] == 0
    assert len(full["messages"]) == 6

    tail = store.history(4)
    assert tail["since_index"] == 4
    assert tail["total"] == 6
    assert len(tail["messages"]) == 2
    assert tail["messages"][0]["content"] == "domanda 2"


@pytest.mark.parametrize("value", [-5, "abc", None, 999])
def test_history_since_index_is_clamped(tmp_path, value):
    store = _store(tmp_path)
    sid = store.begin_turn("ciao")
    store.finish_turn(sid, "ok")

    hist = store.history(value)
    assert 0 <= hist["since_index"] <= hist["total"]
    assert len(hist["messages"]) == hist["total"] - hist["since_index"]


def test_history_with_tool_events_honours_since_index(tmp_path):
    store = _store(tmp_path)
    sid = store.begin_turn("ciao")
    store.append_tool_event(sid, "Read", "letto file")
    store.finish_turn(sid, "fatto")

    payload = store.history_with_tool_events(1)
    assert payload["since_index"] == 1
    assert payload["total"] == 2
    assert [e["tool"] for e in payload["tool_events"]] == ["Read"]
    assert "delegations" in payload


def test_corrupt_session_file_is_quarantined_not_silently_dropped(tmp_path, caplog):
    path = tmp_path / "_bridge_prime_session.json"
    path.write_text("{ questo non e' JSON", encoding="utf-8")

    with caplog.at_level("WARNING"):
        store = PrimeSessionStore(path, session_id="hermes-prime-test")
        hist = store.history()

    assert hist["messages"] == []
    quarantined = list(tmp_path.glob("_bridge_prime_session.json.corrupt-*"))
    assert len(quarantined) == 1, "il file corrotto deve essere rinominato, non perso"
    assert "corrupt" in caplog.text.lower()


def test_empty_orphan_tmp_files_are_cleaned_up_at_startup(tmp_path):
    path = tmp_path / "_bridge_prime_session.json"
    orphan = tmp_path / "_bridge_prime_session.json.tmp.123.456"
    orphan.write_text("", encoding="utf-8")
    keep = tmp_path / "_bridge_prime_session.json.tmp.999.999"
    keep.write_text("{}", encoding="utf-8")

    PrimeSessionStore(path, session_id="hermes-prime-test")

    assert not orphan.exists(), "i .tmp vuoti orfani vanno rimossi"
    assert keep.exists(), "i .tmp non vuoti non vanno toccati"


def test_inject_assistant_message_returns_its_index(tmp_path):
    store = _store(tmp_path)
    sid = store.begin_turn("ciao")
    store.finish_turn(sid, "ok")

    index = store.inject_assistant_message("brief della delega", meta={"task_id": "d99"})
    assert index == 2
    assert store.history()["messages"][index]["content"] == "brief della delega"


# ── Bug A: card delega durevoli ─────────────────────────────────────────────

def test_delegation_lifecycle_launch_finish_brief_delivered(tmp_path):
    store = _store(tmp_path)
    assert store.live()["delegations_rev"] == 0

    store.upsert_delegation(
        "d16",
        agent="librarian",
        task_excerpt="cerca gli appunti",
        status="in_corso",
        started_at=1000.0,
        anchor_message_index=4,
    )
    rev_launch = store.get_delegations_rev()
    assert rev_launch > 0

    store.upsert_delegation("d16", status="ok", finished_at=1200.0)
    rev_finish = store.get_delegations_rev()
    assert rev_finish > rev_launch

    store.mark_delegation_brief("d16", "delivered", 7)
    rev_brief = store.get_delegations_rev()
    assert rev_brief > rev_finish

    (record,) = store.get_delegations()
    assert record["id"] == "d16"
    assert record["agent"] == "librarian"
    assert record["task_excerpt"] == "cerca gli appunti"
    assert record["status"] == "ok"
    assert record["anchor_message_index"] == 4
    assert record["finished_at"] == 1200.0
    assert record["brief_status"] == "delivered"
    assert record["brief_message_index"] == 7


def test_delegation_upsert_never_downgrades_known_fields(tmp_path):
    """Il poll a 3 s sa meno del lancio: non deve azzerare agent/anchor."""
    store = _store(tmp_path)
    store.upsert_delegation("d1", agent="librarian", anchor_message_index=3, status="in_corso")
    store.upsert_delegation("d1", agent="", anchor_message_index=None, status="ok")

    (record,) = store.get_delegations()
    assert record["agent"] == "librarian"
    assert record["anchor_message_index"] == 3
    assert record["status"] == "ok"


def test_delegation_upsert_without_changes_does_not_write_or_bump_rev(tmp_path):
    """Il poller /api/bridge/tasks non deve riscrivere il file ogni 3 s."""
    store = _store(tmp_path)
    store.upsert_delegation("d1", agent="librarian", status="ok")
    rev_before = store.get_delegations_rev()

    writes = _count_writes(store)
    store.upsert_delegation("d1", agent="librarian", status="ok")

    assert writes[0] == 0
    assert store.get_delegations_rev() == rev_before


def test_delegations_list_is_capped(tmp_path):
    from api.prime_session_store import DELEGATIONS_CAP

    store = _store(tmp_path)
    for i in range(DELEGATIONS_CAP + 10):
        store.upsert_delegation(f"d{i}", status="ok")

    records = store.get_delegations()
    assert len(records) == DELEGATIONS_CAP
    assert records[-1]["id"] == f"d{DELEGATIONS_CAP + 9}"


def test_delegation_ignores_an_empty_task_id(tmp_path):
    store = _store(tmp_path)
    assert store.upsert_delegation("") is None
    assert store.upsert_delegation("   ") is None
    assert store.get_delegations() == []


def test_history_and_live_expose_delegations_for_the_reload_replay(tmp_path):
    store = _store(tmp_path)
    sid = store.begin_turn("delega a librarian")
    store.finish_turn(sid, "ok, delegato")
    store.upsert_delegation("d16", agent="librarian", status="ok", anchor_message_index=0)

    hist = store.history()
    assert hist["message_count"] == 2
    assert [d["id"] for d in hist["delegations"]] == ["d16"]
    assert hist["delegations_rev"] == store.live()["delegations_rev"]

    live = store.live()
    assert live["message_count"] == 2
    assert live["delegations_rev"] > 0


def test_delegations_survive_a_store_reopen(tmp_path):
    path = tmp_path / "_bridge_prime_session.json"
    first = PrimeSessionStore(path, session_id="hermes-prime-test")
    first.upsert_delegation("d16", agent="librarian", status="ok", brief_status="delivered")

    second = PrimeSessionStore(path, session_id="hermes-prime-test")
    (record,) = second.get_delegations()
    assert record["id"] == "d16"
    assert record["brief_status"] == "delivered"


def test_legacy_session_file_without_delegations_still_loads(tmp_path):
    """Retro-compatibilita': file scritto dal backend vecchio."""
    path = tmp_path / "_bridge_prime_session.json"
    path.write_text(
        json.dumps(
            {
                "session_id": "hermes-prime",
                "created_at": time.time(),
                "updated_at": time.time(),
                "messages": [{"role": "user", "content": "ciao"}],
                "pending_turn": None,
                "journal": [],
                "settings": {},
            }
        ),
        encoding="utf-8",
    )

    store = PrimeSessionStore(path, session_id="hermes-prime-test")
    hist = store.history()
    assert hist["delegations"] == []
    assert hist["delegations_rev"] == 0
    assert hist["message_count"] == 1
