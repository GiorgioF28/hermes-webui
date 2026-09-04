import io
import json
import os
import secrets
import threading
import urllib.error
import urllib.request
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
        "receivedAt": "2026-09-01T07:30:00Z",
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


def test_ingest_filters_out_email_outside_current_rome_day(tmp_path: Path):
    result = ingest_email_digest(tmp_path, {
        "accounts": [{"label": "gmail-personale"}],
        "emails": [
            _email(),
            _email(**{"from": "old@example.test", "receivedAt": "2026-08-31T20:00:00Z"}),
        ],
    }, now=NOW)

    stored = json.loads((tmp_path / "daily-email-digest.json").read_text(encoding="utf-8"))
    assert result == {"ok": True, "stored": 2, "skipped": 0, "analysed": 2}
    assert [row["from"] for row in stored["emails"]] == ["person@example.test", "old@example.test"]


def test_noise_list_filters_sender_after_three_hits(tmp_path: Path):
    additions = {"senders": ["person@example.test"], "domains": [], "subjectPatterns": []}
    for day in range(3):
        merge_noise_list(tmp_path, additions, now=NOW + timedelta(days=day))
    noise = json.loads((tmp_path / "email-noise-list.json").read_text(encoding="utf-8"))

    assert noise["senders"][0]["hits"] == 3
    assert matches_noise(_email(), noise) is True
    result = ingest_email_digest(tmp_path, {"accounts": [], "emails": [_email()]}, now=NOW + timedelta(days=2))
    assert result == {"ok": True, "stored": 0, "skipped": 1, "analysed": 0}


def test_accumulator_dedupes_prunes_caps_and_cleans_body(tmp_path: Path):
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
    stored = json.loads((tmp_path / ACCUMULATOR_FILENAME).read_text(encoding="utf-8"))

    assert result == {"ok": True, "added": 400, "total": 400, "skipped": 6}
    assert duplicate == {"ok": True, "added": 0, "total": 400, "skipped": 1}
    assert len(stored["items"]) == 400
    assert stored["items"][0]["messageId"] == "message-0"
    assert "\x00" not in stored["items"][0]["bodyExcerpt"]
    assert "\n" not in stored["items"][0]["bodyExcerpt"]
    assert len(stored["items"][0]["bodyExcerpt"]) <= 2000
    assert stored["items"][0]["bodyExcerpt"].startswith("A body")


def test_digest_merges_accumulator_and_flushes_only_consumed_items(tmp_path: Path):
    consumed = _email(
        account="yahoo-personale",
        messageId="consumed",
        receivedAt="2026-09-01T07:00:00Z",
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
    accumulator = json.loads((tmp_path / ACCUMULATOR_FILENAME).read_text(encoding="utf-8"))
    assert result == {"ok": True, "stored": 1, "skipped": 0, "analysed": 1}
    assert digest["version"] == 2
    assert digest["analysisEngine"] == "prime"
    assert digest["emails"][0]["summary"] == "Serve una decisione."
    assert digest["emails"][0]["why"] == "richiede risposta"
    assert "bodyExcerpt" not in json.dumps(digest)
    assert [row["messageId"] for row in accumulator["items"]] == ["future"]


def test_analysis_failure_uses_rules_writes_digest_and_flushes(tmp_path: Path):
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
        result = ingest_email_digest(tmp_path, {"accounts": [], "emails": []}, now=NOW)

    digest = json.loads((tmp_path / "daily-email-digest.json").read_text(encoding="utf-8"))
    accumulator = json.loads((tmp_path / ACCUMULATOR_FILENAME).read_text(encoding="utf-8"))
    assert result["analysed"] == 1
    assert digest["analysisEngine"] == "rules"
    assert "TimeoutError" in digest["analysisError"]
    assert digest["emails"][0]["importance"] == "alta"
    assert digest["emails"][0]["summary"] == ""
    assert digest["emails"][0]["why"] == ""
    assert accumulator["items"] == []


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
    for endpoint in ("email", "email-accumulate", "noise", "check-dm"):
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
    with patch.dict(os.environ, {"HERMES_CRON_TOKEN": runtime_secret}, clear=True):
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
        assert (tmp_path / ACCUMULATOR_FILENAME).is_file()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
