"""Checkpoint memoria prima della compattazione di Prime (fail-closed).

La compattazione riassume la conversazione: cio' che non e' gia' in memoria
viene perso. Prima di compattare, il Librarian riceve i turni non ancora
salvati e li scrive nel Vault. Se il checkpoint fallisce, la compattazione
NON parte (fail-closed): meglio un contesto grande che una memoria bucata;
si ritenta al turno successivo.
"""

from __future__ import annotations

import json

import pytest

from api import precompact_checkpoint as pc
from api import prime_auto_compact as pac
from api import prime_session_store


def _store(tmp_path, messages, settings=None):
    path = tmp_path / "prime-session.json"
    path.write_text(json.dumps({"messages": messages, "settings": settings or {}}), encoding="utf-8")
    return prime_session_store.PrimeSessionStore(path)


MSGS = [
    {"role": "user", "content": "decidiamo: il Ricercatore carica lui il CRM Notion"},
    {"role": "assistant", "content": "ok, aggiorno il playbook e il Librarian registra"},
    {"role": "user", "content": "e il prossimo passo e' il checkpoint pre-compattazione"},
]


# ── transcript ───────────────────────────────────────────────────────────────

def test_transcript_renders_only_new_messages():
    text, index = pc.build_checkpoint_transcript(MSGS, since_index=1)
    assert index == 3
    assert "Ricercatore carica" not in text
    assert "aggiorno il playbook" in text and "checkpoint pre-compattazione" in text
    assert text.startswith("[assistant]") or "[assistant]" in text.splitlines()[0]


def test_transcript_is_none_when_nothing_new():
    text, index = pc.build_checkpoint_transcript(MSGS, since_index=3)
    assert text is None and index == 3


def test_transcript_is_capped():
    big = [{"role": "user", "content": "x" * 50_000} for _ in range(3)]
    text, _ = pc.build_checkpoint_transcript(big, since_index=0, max_chars=10_000)
    assert len(text) <= 10_500
    assert "tronc" in text.lower()


# ── checkpoint ───────────────────────────────────────────────────────────────

def test_checkpoint_sends_new_turns_to_librarian_and_advances_marker(tmp_path, monkeypatch):
    store = _store(tmp_path, MSGS, {"precompact_checkpoint_index": 1})
    monkeypatch.setattr(pc, "_store_for", lambda session_id: store)
    prompts = []

    def runner(prompt: str) -> str:
        prompts.append(prompt)
        return "## Memory Update\n- Vault: aggiornato playbook"

    result = pc.run_precompact_checkpoint("hermes-prime", str(tmp_path), runner=runner)

    assert result["saved"] is True
    assert result["messages"] == 2
    assert "aggiorno il playbook" in prompts[0]
    assert "Ricercatore carica" not in prompts[0], "gia' salvato al checkpoint precedente"
    assert "CHECKPOINT" in prompts[0].upper()
    assert store.get_settings()["precompact_checkpoint_index"] == 3


def test_checkpoint_with_nothing_new_is_a_noop(tmp_path, monkeypatch):
    store = _store(tmp_path, MSGS, {"precompact_checkpoint_index": 3})
    monkeypatch.setattr(pc, "_store_for", lambda session_id: store)
    calls = []
    result = pc.run_precompact_checkpoint("hermes-prime", str(tmp_path), runner=lambda p: calls.append(p) or "")
    assert result == {"saved": False, "reason": "nothing_new", "messages": 0}
    assert calls == []


def test_niente_da_memorizzare_is_still_a_success(tmp_path, monkeypatch):
    store = _store(tmp_path, MSGS)
    monkeypatch.setattr(pc, "_store_for", lambda session_id: store)
    result = pc.run_precompact_checkpoint("hermes-prime", str(tmp_path), runner=lambda p: "NIENTE DA MEMORIZZARE")
    assert result["saved"] is True
    assert store.get_settings()["precompact_checkpoint_index"] == 3


def test_checkpoint_failure_does_not_advance_marker(tmp_path, monkeypatch):
    store = _store(tmp_path, MSGS, {"precompact_checkpoint_index": 1})
    monkeypatch.setattr(pc, "_store_for", lambda session_id: store)

    def runner(prompt):
        raise RuntimeError("librarian offline")

    with pytest.raises(pc.CheckpointError):
        pc.run_precompact_checkpoint("hermes-prime", str(tmp_path), runner=runner)
    assert store.get_settings()["precompact_checkpoint_index"] == 1


# ── aggancio alla compattazione: fail-closed ─────────────────────────────────

@pytest.fixture
def compact_env(monkeypatch):
    monkeypatch.delenv("HERMES_PRIME_PRECOMPACT_CHECKPOINT", raising=False)
    monkeypatch.setattr(pac, "append_prime_auto_compact_event", lambda event: None)
    # maybe_auto_compact_prime rilegge soglia e cooldown dall'env a ogni chiamata:
    # forziamo uno stato "sopra soglia, fuori cooldown" indipendente dall'ambiente.
    monkeypatch.setattr(
        pac, "_sync_state_from_env",
        lambda: pac.PrimeAutoCompactState(threshold_tokens=100, cooldown_turns=0, turns_since_compact=0),
    )
    return {"input_tokens": 500}


def test_checkpoint_runs_before_the_compact_turn(compact_env, monkeypatch):
    order = []
    monkeypatch.setattr(pac, "run_precompact_checkpoint", lambda registry, session_id: order.append("checkpoint") or {"saved": True, "messages": 4})

    def service_turn(registry, session_id, timeout):
        order.append("compact")
        return {"usage": {"input_tokens": 10}}

    result = pac.maybe_auto_compact_prime(object(), session_id="hermes-prime", usage=compact_env, idle=True, run_service_turn=service_turn)
    assert order == ["checkpoint", "compact"]
    assert result["compacted"] is True
    assert result["checkpoint"] == {"saved": True, "messages": 4}


def test_checkpoint_failure_blocks_compaction(compact_env, monkeypatch):
    def failing(registry, session_id):
        raise pc.CheckpointError("librarian offline")

    monkeypatch.setattr(pac, "run_precompact_checkpoint", failing)
    calls = []
    result = pac.maybe_auto_compact_prime(object(), session_id="hermes-prime", usage=compact_env, idle=True,
                                          run_service_turn=lambda r, s, t: calls.append(1))
    assert calls == [], "fail-closed: senza checkpoint non si compatta"
    assert result["compacted"] is False
    assert result["reason"] == "checkpoint_failed"
    assert "librarian offline" in result["error"]


def test_checkpoint_can_be_disabled(compact_env, monkeypatch):
    monkeypatch.setenv("HERMES_PRIME_PRECOMPACT_CHECKPOINT", "0")
    monkeypatch.setattr(pac, "run_precompact_checkpoint", lambda *a, **k: (_ for _ in ()).throw(AssertionError("non doveva girare")))
    result = pac.maybe_auto_compact_prime(object(), session_id="hermes-prime", usage=compact_env, idle=True,
                                          run_service_turn=lambda r, s, t: {"usage": {"input_tokens": 10}})
    assert result["compacted"] is True


def test_manual_compact_is_fail_closed_too(compact_env, monkeypatch):
    def failing(registry, session_id):
        raise pc.CheckpointError("librarian offline")

    monkeypatch.setattr(pac, "run_precompact_checkpoint", failing)
    calls = []
    result = pac.compact_prime_now(object(), session_id="hermes-prime", run_service_turn=lambda r, s, t: calls.append(1))
    assert calls == []
    assert result["ok"] is False and result["reason"] == "checkpoint_failed"
