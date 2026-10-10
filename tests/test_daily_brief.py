import io
import json
import os
import secrets
import threading
import urllib.error
import urllib.request
import pytest
from http.server import ThreadingHTTPServer
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from api import routes
from api.daily_brief import (
    ACCUMULATOR_FILENAME,
    accumulate_email_inbox,
    build_daily_brief_payload,
    handle_cron_daily_brief,
    DailyBriefProcessingError,
    daily_brief_window,
    ingest_email_digest,
    matches_noise,
    merge_noise_list,
    run_check_dm,
)


NOW = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _email(**overrides):
    row = {
        "account": "gmail-personale",
        "from": "person@example.test",
        "fromName": "Persona",
        "subject": "Aggiornamento progetto",
        "receivedAt": "2026-09-01T04:30:00Z",
    }
    row.update(overrides)
    return row


def test_payload_uses_new_contract_and_never_contains_delegations(tmp_path: Path):
    replies = tmp_path / "replies.json"
    _write(replies, {"replies": [{"handle": "Creator.One", "text": "Ci sono", "timestamp": "2026-09-01T07:40:00Z"}]})
    ingest_email_digest(tmp_path, {"accounts": [{"label": "gmail-personale"}], "emails": [_email()]}, now=NOW)

    payload = build_daily_brief_payload(tmp_path, replies_file=replies, now=NOW)

    assert set(payload) == {"ok", "generatedAt", "lastRun", "stale", "email", "ig"}
    assert payload["email"]["count"] == 1
    assert payload["ig"]["count"] == 1
    assert payload["ig"]["items"][0]["handle"] == "creator.one"
    assert "briefs" not in payload
    assert "outcome" not in json.dumps(payload)


def test_ig_replies_are_limited_to_last_24_hours_and_reject_invalid_or_future_dates(tmp_path: Path):
    replies = tmp_path / "replies.json"
    _write(replies, {"replies": [
        {"handle": "recent", "text": "ok", "timestamp": (NOW - timedelta(hours=23)).isoformat()},
        {"handle": "old", "text": "old", "timestamp": (NOW - timedelta(hours=24, seconds=1)).isoformat()},
        {"handle": "future", "text": "future", "timestamp": (NOW + timedelta(minutes=1)).isoformat()},
        {"handle": "invalid", "text": "bad", "timestamp": "not-a-date"},
    ]})

    payload = build_daily_brief_payload(tmp_path, replies_file=replies, now=NOW)

    assert payload["ig"]["count"] == 1
    assert payload["ig"]["items"][0]["handle"] == "recent"


def test_ig_reply_deduplication_keeps_latest_timestamp_inside_window(tmp_path: Path):
    replies = tmp_path / "replies.json"
    _write(replies, {"replies": [
        {"handle": "same", "text": "older", "timestamp": (NOW - timedelta(hours=12)).isoformat()},
        {"handle": "same", "text": "newer", "timestamp": (NOW - timedelta(hours=1)).isoformat()},
    ]})

    payload = build_daily_brief_payload(tmp_path, replies_file=replies, now=NOW)

    assert payload["ig"]["count"] == 1
    assert payload["ig"]["items"][0]["text"] == "newer"


def test_empty_digest_is_written_and_keeps_account_error_separate_from_zero_messages(tmp_path: Path):
    result = ingest_email_digest(tmp_path, {
        "accounts": [{"label": "gmail-personale", "error": "source unavailable"}],
        "emails": [],
    }, now=NOW)

    digest = json.loads((tmp_path / "daily-email-digest.json").read_text(encoding="utf-8"))
    payload = build_daily_brief_payload(tmp_path, replies_file=tmp_path / "missing.json", now=NOW)

    assert result["stored"] == 0
    assert digest["generatedAt"] == NOW.isoformat().replace("+00:00", "Z")
    assert payload["email"]["count"] == 0
    assert payload["email"]["accounts"] == [{"label": "gmail-personale", "count": 0, "error": "source unavailable"}]


def test_dm_success_does_not_clear_email_source_error(tmp_path: Path):
    from api.daily_brief import update_run_status

    update_run_status(tmp_path, now=NOW, source="digest", last_error="payload email non valido")
    update_run_status(tmp_path, now=NOW + timedelta(minutes=1), source="dm", last_error=None)

    payload = build_daily_brief_payload(tmp_path, replies_file=tmp_path / "missing.json", now=NOW + timedelta(minutes=2))

    assert payload["email"]["sourceStatus"] == "error"
    assert payload["email"]["lastError"] == "payload email non valido"


def test_failed_dm_check_is_reported_as_error_not_zero_replies(tmp_path: Path):
    from api.daily_brief import update_run_status

    update_run_status(tmp_path, now=NOW, source="dm", last_error="check-dm non completato")
    payload = build_daily_brief_payload(tmp_path, replies_file=tmp_path / "missing.json", now=NOW)
    assert payload["ig"]["count"] == 0
    assert payload["ig"]["sourceStatus"] == "error"
    assert payload["ig"]["lastError"] == "check-dm non completato"


def test_accumulator_error_is_separate_from_digest_status(tmp_path: Path):
    from api.daily_brief import update_run_status

    ingest_email_digest(tmp_path, {"accounts": [{"label": "gmail-personale", "count": 0, "error": None}], "emails": []}, now=NOW)
    update_run_status(tmp_path, now=NOW + timedelta(minutes=1), source="accumulate", last_error="payload email non valido")

    payload = build_daily_brief_payload(tmp_path, replies_file=tmp_path / "missing.json", now=NOW + timedelta(minutes=2))

    assert payload["email"]["sourceStatus"] == "current"
    assert payload["email"]["accumulatorStatus"] == "error"


def test_ingest_uses_completed_rome_07_window_and_preserves_late_backlog(tmp_path: Path):
    result = ingest_email_digest(tmp_path, {
        "accounts": [{"label": "gmail-personale"}],
        "emails": [
            _email(),
            _email(**{"from": "old@example.test", "receivedAt": "2026-08-31T04:00:00Z"}),
        ],
    }, now=NOW)

    stored = json.loads((tmp_path / "daily-email-digest.json").read_text(encoding="utf-8"))
    assert result == {"ok": True, "stored": 1, "skipped": 0, "analysed": 1}
    assert [row["from"] for row in stored["emails"]] == ["person@example.test"]
    from api.daily_brief_store import connect
    db = connect(tmp_path / "email-queue.sqlite3")
    assert db.execute("SELECT COUNT(*) FROM email_queue").fetchone()[0] == 1
    db.close()
    assert stored["windowStart"] == "2026-08-31T05:00:00Z"
    assert stored["windowEnd"] == "2026-09-01T05:00:00Z"


def test_explicit_completed_window_reconciles_late_email_once(tmp_path: Path):
    from api.daily_brief_store import connect
    start, end = "2026-08-31T05:00:00Z", "2026-09-01T05:00:00Z"
    window = {"accounts": [], "emails": [], "windowStart": start, "windowEnd": end}
    first = ingest_email_digest(tmp_path, window, now=NOW)
    late = _email(messageId="late-in-window", receivedAt="2026-08-31T12:00:00Z", subject="Risposta tardiva")
    accumulate_email_inbox(tmp_path, {"emails": [late]}, now=NOW + timedelta(minutes=1))
    analysis = ([{"importance": "media", "summary": "Risposta recuperata", "why": "messaggio diretto"}], "prime", None)
    with patch("api.email_analysis.analyse_emails", return_value=analysis) as prime:
        recovered = ingest_email_digest(tmp_path, window, now=NOW + timedelta(minutes=2))
        replay = ingest_email_digest(tmp_path, window, now=NOW + timedelta(minutes=3))
    digest = json.loads((tmp_path / "daily-email-digest.json").read_text(encoding="utf-8"))
    db = connect(tmp_path / "email-queue.sqlite3")
    try:
        assert db.execute("SELECT COUNT(*) FROM email_queue WHERE payload LIKE '%late-in-window%'").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM email_receipts WHERE item_key LIKE '%late-in-window%'").fetchone()[0] == 1
    finally:
        db.close()
    assert first["stored"] == 0
    assert recovered["analysed"] == 1 and replay.get("recovered") is True
    assert prime.call_count == 1
    assert len(digest["emails"]) == 1 and digest["emails"][0]["subject"] == "Risposta tardiva"


def test_daily_window_boundaries_and_dst_lengths():
    from datetime import timedelta
    _before, end = daily_brief_window(datetime(2026, 9, 1, 4, 59, 59, tzinfo=timezone.utc))
    assert end == datetime(2026, 8, 31, 5, tzinfo=timezone.utc)
    exact_start, exact_end = daily_brief_window(datetime(2026, 9, 1, 5, tzinfo=timezone.utc))
    assert (exact_start, exact_end) == (datetime(2026, 8, 31, 5, tzinfo=timezone.utc), datetime(2026, 9, 1, 5, tzinfo=timezone.utc))
    spring_start, spring_end = daily_brief_window(datetime(2026, 3, 29, 5, tzinfo=timezone.utc))
    autumn_start, autumn_end = daily_brief_window(datetime(2026, 10, 25, 6, tzinfo=timezone.utc))
    assert spring_end.timestamp() - spring_start.timestamp() == timedelta(hours=23).total_seconds()
    assert autumn_end.timestamp() - autumn_start.timestamp() == timedelta(hours=25).total_seconds()


def test_queue_window_is_start_inclusive_and_end_exclusive(tmp_path: Path):
    from api.daily_brief_store import enqueue, start_or_recover_batch
    from api.daily_brief import _email_db_path
    start, end = "2026-08-31T05:00:00Z", "2026-09-01T05:00:00Z"
    rows = [
        _email(messageId="at-start", receivedAt=start),
        _email(messageId="before-end", receivedAt="2026-09-01T04:59:59Z"),
        _email(messageId="at-end", receivedAt=end),
    ]
    enqueue(_email_db_path(tmp_path), rows)
    _batch, selected, _prepared = start_or_recover_batch(_email_db_path(tmp_path), end, now="2026-09-01T05:00:00Z", start_after=start)
    assert {item["messageId"] for item in selected} == {"at-start", "before-end"}


def test_noise_list_filters_sender_after_three_hits(tmp_path: Path):
    additions = {"senders": ["person@example.test"], "domains": [], "subjectPatterns": []}
    for day in range(3):
        merge_noise_list(tmp_path, additions, now=NOW + timedelta(days=day))
    noise = json.loads((tmp_path / "email-noise-list.json").read_text(encoding="utf-8"))

    assert noise["senders"][0]["hits"] == 3
    assert matches_noise(_email(), noise) is True
    result = ingest_email_digest(tmp_path, {"accounts": [], "emails": [_email()], "windowStart": "2026-08-31T05:00:00Z", "windowEnd": "2026-09-01T05:00:00Z"}, now=NOW + timedelta(days=2))
    assert result["stored"] == 0
    assert result["skipped"] == 1
    assert result["analysed"] == 0


def test_full_acquired_body_is_kept_in_private_archive_and_used_for_analysis(tmp_path: Path):
    from api.daily_brief_store import connect
    body = "messaggio completo " * 300
    row = _email(messageId="full-body", bodyText=body)
    accumulate_email_inbox(tmp_path, {"emails": [row]}, now=NOW)
    db = connect(tmp_path / "email-queue.sqlite3")
    try:
        assert json.loads(db.execute("SELECT payload FROM email_queue").fetchone()[0])["bodyText"] == body
        assert db.execute("SELECT COUNT(*) FROM crm_outbox").fetchone()[0] == 1
    finally:
        db.close()


def test_newsletter_and_autoresponder_do_not_enter_crm_outbox(tmp_path: Path):
    from api.daily_brief_store import connect
    rows = [
        _email(messageId="newsletter", **{"from": "digest@example.test", "listUnsubscribe": True}),
        _email(messageId="autoreply", **{"from": "no-reply@example.test", "subject": "Automatic reply: received"}),
    ]
    accumulate_email_inbox(tmp_path, {"emails": rows}, now=NOW)
    db = connect(tmp_path / "email-queue.sqlite3")
    try:
        assert db.execute("SELECT COUNT(*) FROM email_queue").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM crm_outbox").fetchone()[0] == 0
    finally:
        db.close()


def test_email_endpoint_to_sqlite_prime_crm_readback_and_cleanup_e2e(tmp_path: Path):
    from api.daily_brief_store import connect

    class NotionStub:
        database_id = "people-db"

        def __init__(self):
            self.page = {"id": "person-1", "parent": {"database_id": self.database_id},
                         "properties": {
                             "Email": {"type": "email", "email": "person@example.test"},
                             "Stato": {"type": "select", "select": {"name": "Da contattare"}},
                             "Data risposta": {"type": "date", "date": None},
                             "Canale risposta": {"type": "select", "select": None},
                             "Prossima azione": {"type": "rich_text", "rich_text": []},
                             "Note": {"type": "rich_text", "rich_text": []},
                         }}

        def query_identity_pages(self, prop, kind, value):
            assert (prop, kind, value) == ("Email", "email", "person@example.test")
            return [self.page]

        def get_page(self, page_id):
            return self.page

        def patch_page(self, page_id, properties):
            for name, value in properties.items():
                kind, raw = next(iter(value.items()))
                if kind == "rich_text":
                    raw = [{"plain_text": item["text"]["content"]} for item in raw]
                self.page["properties"][name] = {"type": kind, kind: raw}
            return self.page

    token = secrets.token_urlsafe(32)
    row = _email(messageId="e2e-1", bodyText="corpo privato sintetico", bodyExcerpt="Serve una risposta")
    with patch.dict(os.environ, {"HERMES_CRON_TOKEN": token}, clear=True):
        handler = FakeHandler({"emails": [row]}, token)
        handle_cron_daily_brief(handler, "/api/cron/daily-brief/email-accumulate", data_dir=tmp_path)
        assert handler.status == 200
        digest_handler = FakeHandler({"accounts": [], "emails": [],
                                      "windowStart": "2026-08-31T05:00:00Z",
                                      "windowEnd": "2026-09-01T05:00:00Z"}, token)
        with patch("api.email_analysis.analyse_emails", return_value=(
            [{"importance": "media", "summary": "Risposta da gestire", "why": "email diretta"}], "prime-stub", None
        )) as prime:
            handle_cron_daily_brief(digest_handler, "/api/cron/daily-brief/email", data_dir=tmp_path)
        assert digest_handler.status == 200 and prime.call_count == 1
    notion = NotionStub()
    crm_handler = FakeHandler({}, token)
    with patch.dict(os.environ, {"HERMES_CRON_TOKEN": token}, clear=True), patch(
        "api.email_crm_consumer.NotionCRM.from_env", return_value=notion,
    ):
        handle_cron_daily_brief(crm_handler, "/api/cron/daily-brief/crm-consume", data_dir=tmp_path)
    assert crm_handler.status == 200
    result = json.loads(crm_handler.wfile.getvalue())
    db = connect(tmp_path / "email-queue.sqlite3")
    try:
        pending_body_rows = db.execute("SELECT COUNT(*) FROM email_queue").fetchone()[0]
        crm_state = db.execute("SELECT status FROM crm_outbox").fetchone()[0]
    finally:
        db.close()
    assert result["updated"] == 1 and crm_state == "acked"
    assert pending_body_rows == 0
    assert notion.page["properties"]["Stato"]["select"]["name"] == "Risposto"
    assert "[reply:email:e2e-1]" in "".join(x["plain_text"] for x in notion.page["properties"]["Note"]["rich_text"])


def test_crm_consumer_route_uses_runtime_default_state_database(tmp_path: Path, monkeypatch):
    from api import config
    from api.daily_brief import DEFAULT_DATA_DIR, accumulate_email_inbox, handle_cron_daily_brief
    from api.daily_brief_store import connect

    class NotionStub:
        database_id = "people-db"

        def __init__(self):
            self.page = {"id": "person-1", "parent": {"database_id": self.database_id},
                         "properties": {"Email": {"type": "email", "email": "person@example.test"},
                                        "Stato": {"type": "select", "select": {"name": "Da contattare"}},
                                        "Data risposta": {"type": "date", "date": None},
                                        "Canale risposta": {"type": "select", "select": None},
                                        "Prossima azione": {"type": "rich_text", "rich_text": []},
                                        "Note": {"type": "rich_text", "rich_text": []}}}

        def query_identity_pages(self, prop, kind, value):
            assert (prop, kind, value) == ("Email", "email", "person@example.test")
            return [self.page]

        def get_page(self, page_id):
            return self.page

        def patch_page(self, page_id, properties):
            for name, value in properties.items():
                kind, raw = next(iter(value.items()))
                if kind == "rich_text":
                    raw = [{"plain_text": item["text"]["content"]} for item in raw]
                self.page["properties"][name] = {"type": kind, kind: raw}
            return self.page

    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "private")
    row = _email(messageId="default-path", bodyText="payload sintetico")
    accumulate_email_inbox(DEFAULT_DATA_DIR, {"emails": [row]}, now=NOW)
    expected = tmp_path / "private" / "daily-brief" / "email-queue.sqlite3"
    assert expected.is_file()

    token = secrets.token_urlsafe(32)
    handler = FakeHandler({}, token)
    notion = NotionStub()
    with patch.dict(os.environ, {"HERMES_CRON_TOKEN": token}, clear=True), patch(
        "api.email_crm_consumer.NotionCRM.from_env", return_value=notion,
    ):
        handle_cron_daily_brief(handler, "/api/cron/daily-brief/crm-consume")
    result = json.loads(handler.wfile.getvalue())
    db = connect(expected)
    try:
        assert db.execute("SELECT status FROM crm_outbox").fetchone()[0] == "acked"
        assert db.execute("SELECT COUNT(*) FROM crm_outbox WHERE status='pending'").fetchone()[0] == 0
    finally:
        db.close()
    assert result["updated"] == 1

    # The legacy checkout-relative location must never receive this event.
    assert not (DEFAULT_DATA_DIR / "email-queue.sqlite3").exists()


def test_meta_relay_igsid_is_durable_but_never_guessed_as_notion_handle(tmp_path: Path):
    from api.daily_brief import handle_cron_daily_brief
    from api.daily_brief_store import connect, pending_crm_events

    token = secrets.token_urlsafe(32)
    handler = FakeHandler({"events": [{
        "message_id": "meta-mid-1", "instagram_scoped_user_id": "person@example.test",
        "account_aziendale_destinatario": "business-1", "occurred_at": "2026-10-10T12:00:00Z",
        "verified_handle": "", "text": "Ciao",
    }]}, token)
    with patch.dict(os.environ, {"HERMES_CRON_TOKEN": token}, clear=True):
        handle_cron_daily_brief(handler, "/api/cron/daily-brief/meta-relay-ingest", data_dir=tmp_path)
    assert handler.status == 200
    event, = pending_crm_events(tmp_path / "email-queue.sqlite3")
    assert event["identity_key"] == "igsid:person@example.test"

    class MustNotQueryNotion:
        def query_identity_pages(self, *args):
            raise AssertionError("IGSID must be reviewed before any Notion handle lookup")

    from api.email_crm_consumer import consume_email_crm
    assert consume_email_crm(tmp_path, MustNotQueryNotion())["review"] == 1
    db = connect(tmp_path / "email-queue.sqlite3")
    try:
        row = db.execute("SELECT status,last_error FROM crm_outbox").fetchone()
        assert tuple(row) == ("review", "instagram_igsid_without_verified_handle")
    finally:
        db.close()


def test_pending_archive_recovers_a_missing_queue_without_reprocessing_completed_items(tmp_path: Path):
    from api.daily_brief_store import connect
    row = _email(messageId="recover-me", bodyText="contenuto acquisito")
    accumulate_email_inbox(tmp_path, {"emails": [row]}, now=NOW)
    with patch("api.email_analysis.analyse_emails", return_value=([{"importance": "media", "summary": "Recuperata", "why": "informa"}], "prime", None)):
        first = ingest_email_digest(tmp_path, {"accounts": [], "emails": []}, now=NOW)
        second = ingest_email_digest(tmp_path, {"accounts": [], "emails": []}, now=NOW + timedelta(minutes=1))
    db = connect(tmp_path / "email-queue.sqlite3")
    receipts = db.execute("SELECT account,item_key FROM email_receipts").fetchall()
    pending = db.execute("SELECT COUNT(*) FROM email_queue").fetchone()[0]
    db.close()
    assert first["stored"] == 1
    assert second["stored"] == 1 and second.get("recovered") is True
    assert pending == 0
    assert len(receipts) == 1


def test_account_error_marks_digest_incomplete_not_successful_zero(tmp_path: Path):
    ingest_email_digest(tmp_path, {
        "accounts": [{"label": "gmail-personale", "error": "provider unavailable"}],
        "emails": [],
    }, now=NOW)
    payload = build_daily_brief_payload(tmp_path, replies_file=tmp_path / "missing.json", now=NOW)
    assert payload["email"]["count"] == 0
    assert payload["email"]["sourceStatus"] == "error"
    assert payload["email"]["lastError"] == "sorgente email incompleta: gmail-personale"


def test_accumulator_dedupes_without_cap_or_age_pruning(tmp_path: Path):
    rows = []
    for index in range(405):
        rows.append(_email(
            messageId=f"message-{index}",
            receivedAt=(NOW - timedelta(seconds=index)).isoformat(),
            bodyExcerpt="A\x00  body\n\t" + ("x" * 2200),
        ))
    rows.append(_email(messageId="expired", receivedAt=(NOW - timedelta(hours=49)).isoformat()))

    result = accumulate_email_inbox(tmp_path, {"emails": rows}, now=NOW)
    duplicate = accumulate_email_inbox(tmp_path, {"emails": [rows[0]]}, now=NOW)
    from api.daily_brief_store import connect
    db = connect(tmp_path / "email-queue.sqlite3")
    stored = [json.loads(row[0]) for row in db.execute("SELECT payload FROM email_queue ORDER BY received_at DESC")]
    db.close()

    assert result == {"ok": True, "added": 406, "total": 406, "skipped": 0}
    assert duplicate == {"ok": True, "added": 0, "total": 406, "skipped": 1}
    assert len(stored) == 406
    assert any(row["messageId"] == "expired" for row in stored)
    first = next(row for row in stored if row["messageId"] == "message-0")
    assert "\x00" not in first["bodyExcerpt"]
    assert "\n" not in first["bodyExcerpt"]
    assert len(first["bodyExcerpt"]) <= 2000
    assert first["bodyExcerpt"].startswith("A body")


def test_ingestion_archives_durably_without_calling_model(tmp_path: Path):
    from unittest.mock import patch

    rows = [_email(messageId=f"arrival-{index}") for index in range(3)]
    with patch("api.email_analysis.analyse_emails", side_effect=AssertionError("model called during ingest")):
        accumulate_email_inbox(tmp_path, {"emails": rows}, now=NOW)
    from api.daily_brief_store import connect
    db = connect(tmp_path / "email-queue.sqlite3")
    before_restart = db.execute("SELECT COUNT(*) FROM email_queue").fetchone()[0]
    db.close()
    # A fresh read models process restart; replaying the same provider items is idempotent.
    replay = accumulate_email_inbox(tmp_path, {"emails": rows}, now=NOW + timedelta(days=3))
    db = connect(tmp_path / "email-queue.sqlite3")
    after_restart = db.execute("SELECT COUNT(*) FROM email_queue").fetchone()[0]
    db.close()
    assert replay["added"] == 0
    assert before_restart == after_restart == 3


def test_digest_merges_accumulator_and_flushes_only_consumed_items(tmp_path: Path):
    consumed = _email(
        account="yahoo-personale",
        messageId="consumed",
        receivedAt="2026-09-01T04:00:00Z",
        bodyExcerpt="Dettaglio riservato alla sola analisi",
    )
    accumulate_email_inbox(tmp_path, {"emails": [consumed]}, now=NOW)

    def analyse_with_concurrent_arrival(emails, **_kwargs):
        future = _email(
            account="gmail-secondario",
            messageId="future",
            receivedAt="2026-09-01T09:00:00Z",
            bodyExcerpt="Arrivata dopo il cutoff",
        )
        accumulate_email_inbox(tmp_path, {"emails": [future]}, now=NOW + timedelta(hours=1))
        return ([{"importance": "alta", "summary": "Serve una decisione.", "why": "richiede risposta"}], "prime", None)

    with patch("api.email_analysis.analyse_emails", side_effect=analyse_with_concurrent_arrival):
        result = ingest_email_digest(tmp_path, {"accounts": [], "emails": []}, now=NOW)

    digest = json.loads((tmp_path / "daily-email-digest.json").read_text(encoding="utf-8"))
    from api.daily_brief_store import connect
    db = connect(tmp_path / "email-queue.sqlite3")
    accumulator = [json.loads(row[0]) for row in db.execute("SELECT payload FROM email_queue")]
    db.close()
    assert result == {"ok": True, "stored": 1, "skipped": 0, "analysed": 1}
    assert digest["version"] == 2
    assert digest["analysisEngine"] == "prime"
    assert digest["emails"][0]["summary"] == "Serve una decisione."
    assert digest["emails"][0]["why"] == "richiede risposta"
    assert "bodyExcerpt" not in json.dumps(digest)
    assert [row["messageId"] for row in accumulator] == ["future"]
    db = connect(tmp_path / "email-queue.sqlite3")
    assert db.execute("SELECT COUNT(*) FROM email_receipts").fetchone()[0] == 1
    db.close()


def test_analysis_failure_keeps_batch_and_does_not_publish_recap(tmp_path: Path):
    from api.email_analysis import analyse_emails

    row = _email(messageId="fallback", subject="Pagamento urgente", bodyExcerpt="testo")
    accumulate_email_inbox(tmp_path, {"emails": [row]}, now=NOW)

    def failed_analysis(emails, **kwargs):
        return analyse_emails(
            emails,
            vip_senders=kwargs.get("vip_senders"),
            client_factory=lambda: (_ for _ in ()).throw(TimeoutError()),
        )

    with patch("api.email_analysis.analyse_emails", side_effect=failed_analysis):
        with pytest.raises(DailyBriefProcessingError):
            ingest_email_digest(tmp_path, {"accounts": [], "emails": []}, now=NOW)

    from api.daily_brief_store import connect
    db = connect(tmp_path / "email-queue.sqlite3")
    pending = [json.loads(row[0]) for row in db.execute("SELECT payload FROM email_queue")]
    batch = db.execute("SELECT status FROM digest_batch").fetchone()[0]
    db.close()
    assert [item["messageId"] for item in pending] == ["fallback"]
    assert batch == "analyzing"
    assert not (tmp_path / "daily-email-digest.json").exists()


def test_json_migration_backups_preserves_pending_and_processed_receipts_once(tmp_path: Path):
    from api.daily_brief_store import connect, migrate_legacy

    pending = _email(messageId="pending", bodyText="pending-private")
    processed = dict(_email(messageId="done", bodyText="processed-private"), processingStatus="processed")
    _write(tmp_path / "email-archive.json", {"version": 1, "items": [pending, processed]})
    _write(tmp_path / ACCUMULATOR_FILENAME, {"version": 2, "items": [pending, _email(messageId="done"), _email(messageId="queue-only")]})
    archive_before = (tmp_path / "email-archive.json").read_bytes()
    accumulator_before = (tmp_path / ACCUMULATOR_FILENAME).read_bytes()
    db_path = tmp_path / "state" / "daily-brief" / "email-queue.sqlite3"

    assert migrate_legacy(db_path, tmp_path, now="2026-09-01T08:00:00Z")["imported"] == 2
    assert migrate_legacy(db_path, tmp_path, now="2026-09-02T08:00:00Z")["alreadyMigrated"] == 1
    db = connect(db_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM email_queue").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM email_receipts").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM email_queue WHERE payload LIKE '%private%'").fetchone()[0] == 1
    finally:
        db.close()
    backup_dir = db_path.parent / "migration-backups"
    assert (backup_dir / "email-archive.json").read_bytes() == archive_before
    assert (backup_dir / ACCUMULATOR_FILENAME).read_bytes() == accumulator_before
    assert (tmp_path / "email-archive.json").read_bytes() == archive_before
    assert (tmp_path / ACCUMULATOR_FILENAME).read_bytes() == accumulator_before


def test_migration_marker_skips_moved_or_corrupt_legacy_files(tmp_path: Path):
    from api.daily_brief_store import connect, migrate_legacy

    _write(tmp_path / "email-archive.json", {"items": [_email(messageId="once")]})
    db_path = tmp_path / "state" / "email-queue.sqlite3"
    assert migrate_legacy(db_path, tmp_path, now="2026-09-01T08:00:00Z")["imported"] == 1
    (tmp_path / "email-archive.json").write_text("corrupt", encoding="utf-8")
    (tmp_path / ACCUMULATOR_FILENAME).write_text("corrupt", encoding="utf-8")
    assert migrate_legacy(db_path, tmp_path, now="2026-09-02T08:00:00Z")["alreadyMigrated"] == 1
    db = connect(db_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM email_queue").fetchone()[0] == 1
    finally:
        db.close()


def test_replay_after_cleanup_and_while_prepared_never_recreates_pending(tmp_path: Path):
    from api.daily_brief_store import connect

    row = _email(messageId="replay-after-cleanup", bodyText="synthetic")
    accumulate_email_inbox(tmp_path, {"emails": [row]}, now=NOW)
    analysis = ([{"importance": "media", "summary": "Ricevuta", "why": "Info"}], "prime", None)
    with patch("api.email_analysis.analyse_emails", return_value=analysis):
        ingest_email_digest(tmp_path, {"accounts": [], "emails": []}, now=NOW)
    replay = accumulate_email_inbox(tmp_path, {"emails": [row]}, now=NOW + timedelta(minutes=1))
    assert replay["added"] == 0 and replay["skipped"] == 1 and replay["total"] == 0

    second = _email(messageId="replay-prepared", bodyText="synthetic", receivedAt="2026-09-01T08:00:00Z")
    accumulate_email_inbox(tmp_path, {"emails": [second]}, now=NOW)
    real_write = __import__("api.daily_brief", fromlist=["_atomic_write_json"])._atomic_write_json

    def fail_publish(path, payload):
        if Path(path).name == "daily-email-digest.json":
            raise OSError("simulated publish interruption")
        return real_write(path, payload)

    with patch("api.email_analysis.analyse_emails", return_value=analysis), patch(
        "api.daily_brief._atomic_write_json", side_effect=fail_publish,
    ), pytest.raises(OSError):
        ingest_email_digest(
            tmp_path,
            {"accounts": [], "emails": [], "windowStart": "2026-09-01T05:00:00Z", "windowEnd": "2026-09-02T05:00:00Z"},
            now=NOW,
        )
    replay_prepared = accumulate_email_inbox(tmp_path, {"emails": [second]}, now=NOW + timedelta(minutes=1))
    db = connect(tmp_path / "email-queue.sqlite3")
    try:
        assert replay_prepared["added"] == 0 and replay_prepared["total"] == 1
        assert db.execute("SELECT status FROM digest_batch").fetchone()[0] == "prepared"
        assert db.execute("SELECT COUNT(*) FROM email_queue WHERE payload LIKE '%replay-prepared%'").fetchone()[0] == 1
    finally:
        db.close()


def test_rollback_receipt_overrides_pending_archive_for_message_id_and_hash_fallback(tmp_path: Path):
    from api.daily_brief_store import connect, enqueue, export_legacy, finish_batch, prepare_batch, start_or_recover_batch

    legacy = tmp_path / "legacy"
    legacy.mkdir()
    identified = _email(messageId="rollback-processed", bodyText="sensitive body")
    fallback = _email(subject="fallback subject", bodyText="fallback body")
    # Keep stale pending copies in JSON to reproduce the rollback collision.
    _write(legacy / "email-archive.json", {"items": [identified, fallback]})
    db_path = tmp_path / "db" / "email-queue.sqlite3"
    enqueue(db_path, [identified, fallback])
    batch_id, _selected, _ = start_or_recover_batch(db_path, "2026-09-02T00:00:00Z", now="2026-09-01T08:00:00Z")
    digest = {"digestId": batch_id, "emails": []}
    prepare_batch(db_path, batch_id, digest)
    finish_batch(db_path, batch_id, processed_at="2026-09-01T08:01:00Z")

    export_legacy(db_path, legacy, now="2026-09-01T08:02:00Z")
    exported = json.loads((legacy / "email-archive.json").read_text(encoding="utf-8"))
    assert all(item["processingStatus"] == "processed" for item in exported["items"])
    assert all("bodyText" not in item for item in exported["items"])

    restored = tmp_path / "restored.sqlite3"
    from api.daily_brief_store import migrate_legacy
    migrate_legacy(restored, legacy, now="2026-09-01T08:03:00Z")
    restored_db = connect(restored)
    try:
        assert restored_db.execute("SELECT COUNT(*) FROM email_queue").fetchone()[0] == 0
        assert restored_db.execute("SELECT COUNT(*) FROM email_receipts").fetchone()[0] == 2
    finally:
        restored_db.close()
    replay = enqueue(restored, [identified, fallback])
    assert replay == {"added": 0, "skipped": 2}
    assert connect(restored).execute("SELECT COUNT(*) FROM email_queue").fetchone()[0] == 0


def test_crash_after_digest_publish_is_replayed_idempotently_before_cleanup(tmp_path: Path):
    from api.daily_brief_store import connect

    row = _email(messageId="crash-window")
    accumulate_email_inbox(tmp_path, {"emails": [row]}, now=NOW)
    analysis = ([{"importance": "alta", "summary": "Azione", "why": "scadenza"}], "prime", None)
    real_write = __import__("api.daily_brief", fromlist=["_atomic_write_json"])._atomic_write_json

    def fail_digest(path, payload):
        if Path(path).name == "daily-email-digest.json":
            raise OSError("simulated publish interruption")
        return real_write(path, payload)

    with patch("api.email_analysis.analyse_emails", return_value=analysis), patch(
        "api.daily_brief._atomic_write_json", side_effect=fail_digest,
    ), pytest.raises(OSError):
        ingest_email_digest(tmp_path, {"accounts": [], "emails": []}, now=NOW)
    db = connect(tmp_path / "email-queue.sqlite3")
    prepared = db.execute("SELECT batch_id,status,digest_json FROM digest_batch").fetchone()
    assert prepared["status"] == "prepared"
    batch_id = prepared["batch_id"]
    db.close()
    with patch("api.email_analysis.analyse_emails", side_effect=AssertionError("must reuse prepared recap")):
        recovered = ingest_email_digest(tmp_path, {"accounts": [], "emails": []}, now=NOW + timedelta(minutes=1))
    digest = json.loads((tmp_path / "daily-email-digest.json").read_text(encoding="utf-8"))
    assert recovered["recovered"] is True
    assert digest["digestId"] == batch_id
    db = connect(tmp_path / "email-queue.sqlite3")
    assert db.execute("SELECT COUNT(*) FROM email_queue").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM email_receipts").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM digest_batch").fetchone()[0] == 0
    db.close()
    replay = accumulate_email_inbox(tmp_path, {"emails": [row]}, now=NOW + timedelta(minutes=2))
    assert replay["added"] == 0


def test_rollback_export_preserves_pending_data_and_leaves_sqlite_intact(tmp_path: Path):
    from api.daily_brief_store import connect, export_legacy

    row = _email(messageId="rollback-pending", bodyText="corpo sintetico")
    accumulate_email_inbox(tmp_path / "legacy", {"emails": [row]}, now=NOW)
    db_path = tmp_path / "legacy" / "email-queue.sqlite3"
    legacy = tmp_path / "legacy"
    before = connect(db_path)
    count_before = before.execute("SELECT COUNT(*) FROM email_queue").fetchone()[0]
    before.close()
    _write(legacy / ACCUMULATOR_FILENAME, {"version": 2, "items": [{"messageId": "stale-rollback-copy"}]})
    result = export_legacy(db_path, legacy, now="2026-09-01T08:00:00Z")
    exported = json.loads((legacy / ACCUMULATOR_FILENAME).read_text(encoding="utf-8"))
    backup = db_path.parent / "rollback-backups" / (ACCUMULATOR_FILENAME + ".before-export")
    after = connect(db_path)
    count_after = after.execute("SELECT COUNT(*) FROM email_queue").fetchone()[0]
    after.close()
    assert result["pending"] == 1
    assert exported["items"][0]["messageId"] == "rollback-pending"
    assert exported["items"][0]["bodyText"] == "corpo sintetico"
    assert count_before == count_after == 1
    assert backup.is_file()


def test_concurrent_duplicate_intake_commits_once(tmp_path: Path):
    from concurrent.futures import ThreadPoolExecutor
    from api.daily_brief_store import connect

    row = _email(messageId="parallel-duplicate")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _n: accumulate_email_inbox(tmp_path, {"emails": [row]}, now=NOW), range(8)))
    assert sum(result["added"] for result in results) == 1
    db = connect(tmp_path / "email-queue.sqlite3")
    assert db.execute("SELECT COUNT(*) FROM email_queue").fetchone()[0] == 1
    db.close()


def test_noise_merge_is_incremental_atomic_and_prunes_old_entries(tmp_path: Path):
    _write(tmp_path / "email-noise-list.json", {
        "version": 1,
        "updatedAt": "2026-01-01T00:00:00Z",
        "senders": [{"pattern": "expired@example.test", "hits": 9, "lastSeen": "2026-01-01T00:00:00Z"}],
        "domains": [],
        "subjectPatterns": [],
    })
    payload = {"senders": ["fresh@example.test", "fresh@example.test"], "domains": ["bulk.example.test"], "subjectPatterns": ["unsubscribe"]}

    merged = merge_noise_list(tmp_path, payload, now=NOW)

    assert [row["pattern"] for row in merged["senders"]] == ["fresh@example.test"]
    assert merged["senders"][0]["hits"] == 1
    assert not (tmp_path / "email-noise-list.json.tmp").exists()


def test_payload_is_fail_soft_for_missing_and_corrupt_files(tmp_path: Path):
    missing = build_daily_brief_payload(tmp_path, replies_file=tmp_path / "missing.json", now=NOW)
    assert missing["email"]["count"] == 0
    assert missing["ig"]["notInitialized"] is True
    assert missing["stale"] is True

    (tmp_path / "daily-email-digest.json").write_text("{broken", encoding="utf-8")
    (tmp_path / "daily-brief-run.json").write_text("[]", encoding="utf-8")
    replies = tmp_path / "replies.json"
    replies.write_text("{broken", encoding="utf-8")
    corrupt = build_daily_brief_payload(tmp_path, replies_file=replies, now=NOW)
    assert corrupt["ok"] is True
    assert corrupt["email"]["count"] == 0
    assert corrupt["stale"] is True
    assert corrupt["ig"]["malformed"] is True

    _write(tmp_path / "daily-email-digest.json", {"accounts": [], "emails": [], "noiseSkipped": "not-a-count"})
    tolerant = build_daily_brief_payload(tmp_path, replies_file=tmp_path / "missing.json", now=NOW)
    assert tolerant["email"]["noiseSkipped"] == 0


def test_stale_threshold_is_strictly_more_than_26_hours(tmp_path: Path):
    replies = tmp_path / "replies.json"
    _write(replies, {"replies": []})
    _write(tmp_path / "daily-email-digest.json", {"accounts": [], "emails": [], "noiseSkipped": 0})
    _write(tmp_path / "daily-brief-run.json", {"lastRun": "2026-08-31T06:00:00Z"})
    assert build_daily_brief_payload(tmp_path, replies_file=replies, now=NOW)["stale"] is False
    _write(tmp_path / "daily-brief-run.json", {"lastRun": "2026-08-31T05:59:59Z"})
    assert build_daily_brief_payload(tmp_path, replies_file=replies, now=NOW)["stale"] is True


class FakeHandler:
    def __init__(self, body=None, token=None):
        raw = json.dumps(body or {}).encode("utf-8")
        self.headers = {"Content-Length": str(len(raw))}
        if token is not None:
            self.headers["X-Hermes-Cron-Token"] = token
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.status = None
        self.response_headers = {}

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.response_headers[key] = value

    def end_headers(self):
        pass


def test_all_cron_endpoints_are_403_when_server_token_is_not_configured(tmp_path: Path):
    for endpoint in ("email", "email-accumulate", "crm-consume", "noise", "check-dm"):
        handler = FakeHandler({"emails": []})
        with patch.dict(os.environ, {}, clear=True):
            assert handle_cron_daily_brief(handler, f"/api/cron/daily-brief/{endpoint}", data_dir=tmp_path) is True
        assert handler.status == 403


def test_cron_endpoint_is_403_when_header_is_missing_or_wrong(tmp_path: Path):
    expected = secrets.token_urlsafe(32)
    for provided in (None, secrets.token_urlsafe(32)):
        handler = FakeHandler({"emails": []}, provided)
        with patch.dict(os.environ, {"HERMES_CRON_TOKEN": expected}, clear=True):
            handle_cron_daily_brief(handler, "/api/cron/daily-brief/email", data_dir=tmp_path)
        assert handler.status == 403


def test_cron_email_endpoint_accepts_matching_runtime_token_and_writes_atomically(tmp_path: Path):
    runtime_secret = secrets.token_urlsafe(32)
    handler = FakeHandler({"accounts": [], "emails": [_email()]}, runtime_secret)
    analysis = ([{"importance": "media", "summary": "Aggiornamento", "why": "informazione"}], "prime", None)
    with patch.dict(os.environ, {"HERMES_CRON_TOKEN": runtime_secret}, clear=True), patch(
        "api.email_analysis.analyse_emails", return_value=analysis,
    ):
        handle_cron_daily_brief(handler, "/api/cron/daily-brief/email", data_dir=tmp_path)
    assert handler.status == 200
    assert (tmp_path / "daily-email-digest.json").is_file()
    assert (tmp_path / "daily-brief-run.json").is_file()
    assert not list(tmp_path.glob("*.tmp"))


def test_cron_accumulator_endpoint_requires_token_and_returns_422_for_bad_date(tmp_path: Path):
    runtime_secret = secrets.token_urlsafe(32)
    handler = FakeHandler({"emails": [_email(receivedAt="not-a-date")]}, runtime_secret)
    with patch.dict(os.environ, {"HERMES_CRON_TOKEN": runtime_secret}, clear=True):
        handle_cron_daily_brief(handler, "/api/cron/daily-brief/email-accumulate", data_dir=tmp_path)
    assert handler.status == 422
    assert not (tmp_path / ACCUMULATOR_FILENAME).exists()


def test_sqlite_status_endpoint_is_authenticated_and_confirms_protocol(tmp_path: Path):
    runtime_secret = secrets.token_urlsafe(32)
    handler = FakeHandler({}, runtime_secret)
    with patch.dict(os.environ, {"HERMES_CRON_TOKEN": runtime_secret}, clear=True):
        handle_cron_daily_brief(handler, "/api/cron/daily-brief/status", data_dir=tmp_path)
    assert handler.status == 200
    response = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert response == {"ok": True, "storage": "sqlite", "schemaVersion": 2, "recapProtocol": "outbox-v1", "pending": 0, "crmPending": 0}


def test_accumulation_storage_failure_never_returns_http_200(tmp_path: Path):
    import sqlite3

    runtime_secret = secrets.token_urlsafe(32)
    handler = FakeHandler({"emails": [_email(messageId="commit-required")]}, runtime_secret)
    with patch.dict(os.environ, {"HERMES_CRON_TOKEN": runtime_secret}, clear=True), patch(
        "api.daily_brief_store.enqueue", side_effect=sqlite3.OperationalError("simulated storage failure"),
    ):
        handle_cron_daily_brief(handler, "/api/cron/daily-brief/email-accumulate", data_dir=tmp_path)
    assert handler.status == 503
    response = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert response["error"] == "daily_brief_storage_error"


def test_cron_noise_endpoint_accepts_matching_runtime_token_and_merges(tmp_path: Path):
    runtime_secret = secrets.token_urlsafe(32)
    handler = FakeHandler({"senders": ["bulk@example.test"], "domains": [], "subjectPatterns": []}, runtime_secret)
    with patch.dict(os.environ, {"HERMES_CRON_TOKEN": runtime_secret}, clear=True):
        handle_cron_daily_brief(handler, "/api/cron/daily-brief/noise", data_dir=tmp_path)
    stored = json.loads((tmp_path / "email-noise-list.json").read_text(encoding="utf-8"))
    assert handler.status == 200
    assert stored["senders"][0]["hits"] == 1


def test_cron_check_dm_endpoint_returns_200_even_when_cli_fails(tmp_path: Path):
    runtime_secret = secrets.token_urlsafe(32)
    handler = FakeHandler({}, runtime_secret)
    with patch.dict(os.environ, {"HERMES_CRON_TOKEN": runtime_secret}, clear=True), patch(
        "api.daily_brief.run_check_dm", return_value={"ok": False, "exitCode": 10, "repliesFound": None, "reason": "check-dm non completato"}
    ):
        handle_cron_daily_brief(handler, "/api/cron/daily-brief/check-dm", data_dir=tmp_path)
    assert handler.status == 200
    response = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert response["ok"] is False


def test_check_dm_returns_200_style_payload_without_exposing_process_output(tmp_path: Path):
    class Completed:
        returncode = 0
        stdout = "Risposte nuove: 2\nprivate diagnostic"
        stderr = ""

    result = run_check_dm(tmp_path, outreach_dir=tmp_path, now=NOW, runner=lambda *args, **kwargs: Completed())
    assert result == {"ok": True, "exitCode": 0, "repliesFound": 2}
    assert "stdout" not in result and "stderr" not in result


def test_bridge_endpoint_returns_payload_with_no_store_cache():
    captured = []
    fake_payload = {"ok": True, "generatedAt": "2026-09-01T08:00:00Z", "lastRun": None, "stale": True, "email": {}, "ig": {}}

    def capture(_handler, payload, **kwargs):
        captured.append((payload, kwargs))
        return True

    with patch.object(routes, "j", capture), patch("api.daily_brief.build_daily_brief_payload", return_value=fake_payload):
        assert routes._handle_bridge_daily_brief(object()) is True
    assert captured == [(fake_payload, {"extra_headers": {"Cache-Control": "no-store"}})]


def test_check_auth_lets_cron_routes_through_to_their_own_token_gate(monkeypatch):
    """Regression: with a WebUI password set, check_auth answered 401 on
    /api/cron/daily-brief/* before handle_post could reach the cron token
    gate, so every n8n execution failed with 'Authentication required'."""
    from types import SimpleNamespace
    from api.auth import check_auth

    monkeypatch.setenv("HERMES_WEBUI_PASSWORD", "test-password")
    handler = FakeHandler({"emails": []})
    handler.command = "POST"
    for endpoint in ("email", "email-accumulate", "noise", "check-dm"):
        assert check_auth(handler, SimpleNamespace(path=f"/api/cron/daily-brief/{endpoint}")) is True
    # The carve-out is a strict prefix: siblings still need a browser session.
    assert check_auth(handler, SimpleNamespace(path="/api/cron/daily-brief")) is False
    assert handler.status == 401
    handler = FakeHandler({"emails": []})
    assert check_auth(handler, SimpleNamespace(path="/api/crons")) is False
    assert handler.status == 401


def test_real_http_server_cron_accumulate_uses_token_not_cookie(monkeypatch, tmp_path):
    """End-to-end through server.Handler with auth enabled: no token -> 403
    from the cron gate (not 401 from the cookie gate); right token -> 200."""
    import api.daily_brief as daily_brief_module
    from server import Handler as WebUIHandler

    runtime_secret = secrets.token_urlsafe(32)
    monkeypatch.setenv("HERMES_WEBUI_PASSWORD", "test-password")
    monkeypatch.setenv("HERMES_CRON_TOKEN", runtime_secret)
    # handle_post imports handle_cron_daily_brief lazily, so patching the
    # module attribute redirects the real server into tmp_path (the default
    # data_dir is bound at def-time; patching DEFAULT_DATA_DIR would not).
    real_handler = daily_brief_module.handle_cron_daily_brief
    monkeypatch.setattr(
        daily_brief_module,
        "handle_cron_daily_brief",
        lambda handler, path: real_handler(handler, path, data_dir=tmp_path),
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), WebUIHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{httpd.server_port}/api/cron/daily-brief/email-accumulate"
        raw = json.dumps({"accounts": [], "emails": [_email()]}).encode()

        def post(headers):
            request = urllib.request.Request(url, data=raw, method="POST", headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read())

        status, body = post({"Content-Type": "application/json"})
        assert (status, body["error"]) == (403, "forbidden")
        status, body = post({"Content-Type": "application/json", "X-Hermes-Cron-Token": "wrong"})
        assert (status, body["error"]) == (403, "forbidden")
        status, body = post({"Content-Type": "application/json", "X-Hermes-Cron-Token": runtime_secret})
        assert status == 200, body
        assert body["ok"] is True
        from api.daily_brief_store import connect
        db = connect(tmp_path / "email-queue.sqlite3")
        assert db.execute("SELECT COUNT(*) FROM email_queue").fetchone()[0] == 1
        db.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
