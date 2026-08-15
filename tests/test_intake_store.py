import threading

from api.intake_store import IntakeQueueFullError, IntakeStore


def event(event_id="email:gmail:abc", digest="a" * 64):
    return {
        "schema_version": 1,
        "event_id": event_id,
        "channel": "email",
        "event_type": "inbound_message",
        "occurred_at": "2026-08-15T10:20:30Z",
        "received_at": "2026-08-15T10:20:35Z",
        "source": {"account": "admin@visionbuilts.net"},
        "sender": {"external_id": "email:test@example.com"},
        "message": {"text": "hello", "content_sha256": digest, "attachments": [], "to": []},
        "hints": {},
    }


def test_insert_is_idempotent(tmp_path):
    store = IntakeStore(tmp_path / "intake.sqlite3")
    first, first_duplicate = store.insert_or_get(event())
    second, second_duplicate = store.insert_or_get(event())
    assert first_duplicate is False
    assert second_duplicate is True
    assert first["intake_id"] == second["intake_id"]
    assert store.active_count() == 1


def test_secondary_dedupe_within_ten_minutes(tmp_path):
    store = IntakeStore(tmp_path / "intake.sqlite3")
    first, _ = store.insert_or_get(event("email:gmail:first"))
    second_event = event("email:gmail:second")
    second_event["occurred_at"] = "2026-08-15T10:29:30Z"
    second, duplicate = store.insert_or_get(second_event)
    assert duplicate is True
    assert second["intake_id"] == first["intake_id"]


def test_concurrent_claim_has_single_winner(tmp_path):
    store = IntakeStore(tmp_path / "intake.sqlite3")
    store.insert_or_get(event())
    claimed = []

    def claim():
        claimed.append(store.claim_next())

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(item is not None for item in claimed) == 1


def test_finish_minimizes_payload_and_retry_is_bounded(tmp_path):
    store = IntakeStore(tmp_path / "intake.sqlite3")
    long_event = event()
    long_event["message"]["text"] = "x" * 1000
    store.insert_or_get(long_event)
    row = store.claim_next()
    assert store.retry_or_fail(row["intake_id"], "temporary") == "queued"
    row = store.claim_next()
    assert store.retry_or_fail(row["intake_id"], "temporary") == "queued"
    row = store.claim_next()
    assert store.retry_or_fail(row["intake_id"], "temporary") == "failed"
    final = store.get_by_event_id(long_event["event_id"])
    assert final["status"] == "failed"
    assert len(__import__("json").loads(final["event_json"])["message"]["text"]) == 500


def test_queue_limit_still_allows_duplicate_receipt(tmp_path):
    store = IntakeStore(tmp_path / "intake.sqlite3")
    first, _ = store.insert_or_get(event(), max_active=1)
    duplicate, is_duplicate = store.insert_or_get(event(), max_active=1)
    assert is_duplicate is True
    assert duplicate["intake_id"] == first["intake_id"]
    with __import__("pytest").raises(IntakeQueueFullError):
        store.insert_or_get(event("email:gmail:other", "b" * 64), max_active=1)
