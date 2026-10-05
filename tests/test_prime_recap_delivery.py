"""A brief is delivered only when its transcript row exists on disk."""
from unittest.mock import MagicMock

from api import delegation_store, prime_brief_queue, prime_session_store, routes


def test_failed_persistence_keeps_brief_retryable_then_delivers_once(monkeypatch, tmp_path):
    queue = prime_brief_queue.PrimeBriefQueue(tmp_path)
    store = prime_session_store.PrimeSessionStore(tmp_path / "prime.json")
    canonical = MagicMock()
    monkeypatch.setattr(prime_brief_queue, "get_brief_queue", lambda _: queue)
    monkeypatch.setattr(prime_session_store, "get_prime_session_store", lambda _: store)
    monkeypatch.setattr(delegation_store, "get_delegation_store", lambda _: canonical)
    monkeypatch.setattr(routes, "_hermes_prime_reply", lambda *a, **kw: {"reply": "Recap verificato"})
    monkeypatch.setattr(routes, "_BRIEF_JOBS", {})
    bid = queue.enqueue("retry", "programmatore", "codice", "fix", "done", "artefatto")
    inject = store.inject_assistant_message
    monkeypatch.setattr(store, "inject_assistant_message", MagicMock(side_effect=OSError("disk unavailable")))
    routes._run_prime_brief_job("retry", bid, "brief", tmp_path)
    assert not queue.is_delivered(bid)
    assert queue.get_pending()[0]["status"] == "failed_retryable"
    assert routes._brief_job_get("retry")["state"] == "failed_retryable"
    canonical.mark_brief_delivered.assert_not_called()
    assert store.history()["messages"] == []
    monkeypatch.setattr(store, "inject_assistant_message", inject)
    routes._run_prime_brief_job("retry", bid, "brief", tmp_path)
    assert queue.is_delivered(bid)
    assert routes._brief_job_get("retry")["state"] == "done"
    canonical.mark_brief_delivered.assert_called_once_with("retry")
    routes._run_prime_brief_job("retry", bid, "brief", tmp_path)
    assert len(store.history()["messages"]) == 1


def test_fallback_beyond_pending_page_is_saved_before_ack(monkeypatch, tmp_path):
    queue = prime_brief_queue.PrimeBriefQueue(tmp_path)
    store = prime_session_store.PrimeSessionStore(tmp_path / "prime.json")
    monkeypatch.setattr(prime_brief_queue, "get_brief_queue", lambda _: queue)
    monkeypatch.setattr(prime_session_store, "get_prime_session_store", lambda _: store)
    monkeypatch.setattr(delegation_store, "get_delegation_store", lambda _: MagicMock())
    monkeypatch.setattr(routes, "_hermes_prime_reply", lambda *a, **kw: {"reply": ""})
    monkeypatch.setattr(routes, "_BRIEF_JOBS", {})
    for n in range(55):
        bid = queue.enqueue(str(n), "programmatore", "codice", "fix", "done", "artefatto")
    routes._run_prime_brief_job("54", bid, "brief", tmp_path)
    assert queue.is_delivered(bid)
    assert len(queue.get_pending(limit=100)) == 54
    row, = store.history()["messages"]
    assert row["brief_id"] == bid
    assert row["brief_type"] == "fallback_no_llm"
    assert row["content"]


def test_failed_fallback_is_not_acknowledged(monkeypatch, tmp_path):
    queue = prime_brief_queue.PrimeBriefQueue(tmp_path)
    store = MagicMock()
    store.inject_assistant_message.side_effect = OSError("disk unavailable")
    canonical = MagicMock()
    monkeypatch.setattr(prime_brief_queue, "get_brief_queue", lambda _: queue)
    monkeypatch.setattr(prime_session_store, "get_prime_session_store", lambda _: store)
    monkeypatch.setattr(delegation_store, "get_delegation_store", lambda _: canonical)
    monkeypatch.setattr(routes, "_hermes_prime_reply", lambda *a, **kw: {"reply": ""})
    monkeypatch.setattr(routes, "_BRIEF_JOBS", {})
    bid = queue.enqueue("fallback", "programmatore", "codice", "fix", "done", "artefatto")
    routes._run_prime_brief_job("fallback", bid, "brief", tmp_path)
    assert not queue.is_delivered(bid)
    canonical.mark_brief_delivered.assert_not_called()
    assert routes._brief_job_get("fallback")["state"] == "failed_retryable"
