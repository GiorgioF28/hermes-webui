import io
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from api.intake_email import IntakeService, handle_intake_email, validate_event
from api.intake_store import IntakeStore


def event():
    return {
        "schema_version": 1,
        "event_id": "email:gmail:abc",
        "channel": "email",
        "event_type": "inbound_message",
        "occurred_at": "2026-08-15T10:20:30Z",
        "received_at": "2026-08-15T10:20:35Z",
        "source": {
            "provider": "gmail", "account": "admin@visionbuilts.net",
            "external_message_id": "abc", "external_thread_id": "thread",
            "workflow": "visionbuilts-email-intake-v1",
        },
        "sender": {
            "external_id": "email:creator@example.com", "name": "Creator",
            "email": "creator@example.com", "handle": "creator", "profile_url": "",
        },
        "message": {
            "subject": "Application", "text": "Hello", "envelope_from": "noreply@visionbuilts.net",
            "reply_to": "creator@example.com", "to": ["admin@visionbuilts.net"],
            "content_sha256": "a" * 64, "attachments": [],
        },
        "hints": {"visionbuilts_form": True, "language": "en"},
    }


class Handler:
    def __init__(self, payload, token="secret"):
        raw = json.dumps(payload).encode()
        self.headers = {"Authorization": f"Bearer {token}", "Content-Length": str(len(raw))}
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.client_address = ("127.0.0.77", 1234)
        self.response_status = None
        self.response_headers = {}
        self.close_connection = False

    def send_response(self, status):
        self.response_status = status

    def send_header(self, name, value):
        self.response_headers[name] = value

    def end_headers(self):
        pass


@pytest.fixture(autouse=True)
def configured_token(monkeypatch):
    monkeypatch.setenv("HERMES_INTAKE_TOKEN", "secret")


def test_strict_schema_rejects_unknown_field():
    payload = event()
    payload["surprise"] = "drift"
    with pytest.raises(Exception) as exc:
        validate_event(payload)
    assert exc.value.errors == [{"field": "surprise", "code": "unknown"}]


def test_auth_is_checked_before_body(monkeypatch, tmp_path):
    handler = Handler(event(), token="wrong")
    original_pos = handler.rfile.tell()
    service = IntakeService(IntakeStore(tmp_path / "db.sqlite3"), start_workers=False)
    handle_intake_email(handler, service)
    assert handler.response_status == 401
    assert handler.rfile.tell() == original_pos


def test_missing_runtime_secret_is_503_before_body(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_INTAKE_TOKEN")
    handler = Handler(event())
    service = IntakeService(IntakeStore(tmp_path / "db.sqlite3"), start_workers=False)
    handle_intake_email(handler, service)
    assert handler.response_status == 503
    assert handler.rfile.tell() == 0


def test_new_event_202_then_duplicate_200(tmp_path):
    service = IntakeService(IntakeStore(tmp_path / "db.sqlite3"), start_workers=False)
    first = Handler(event())
    handle_intake_email(first, service)
    second = Handler(event())
    second.client_address = ("127.0.0.78", 1234)
    handle_intake_email(second, service)
    assert first.response_status == 202
    assert second.response_status == 200
    body = json.loads(second.wfile.getvalue())
    assert body["duplicate"] is True


def test_oversize_body_is_413_without_read(tmp_path):
    handler = Handler(event())
    handler.headers["Content-Length"] = str(64 * 1024 + 1)
    service = IntakeService(IntakeStore(tmp_path / "db.sqlite3"), start_workers=False)
    handle_intake_email(handler, service)
    assert handler.response_status == 413
    assert handler.rfile.tell() == 0


def test_worker_relevant_event_calls_notion_once(tmp_path):
    calls = []
    extraction = {
        "classification": "relevant", "confidence": 1.0,
        "lead": {"email": "creator@example.com", "instagram_handle": "creator"},
    }
    notion = SimpleNamespace(
        upsert=lambda event, result: calls.append((event, result)) or SimpleNamespace(
            page_id="page", action="created"
        )
    )
    store = IntakeStore(tmp_path / "db.sqlite3")
    service = IntakeService(
        store, extractor=lambda _: extraction, notion_factory=lambda: notion, start_workers=False
    )
    service.enqueue(event())
    assert service.process_one() is True
    assert len(calls) == 1
    assert store.get_by_event_id(event()["event_id"])["status"] == "done"


def test_public_and_csrf_carveouts_are_exact():
    from api.auth import PUBLIC_PATHS
    from api.routes import _csrf_exempt_path

    assert "/api/intake/email" in PUBLIC_PATHS
    assert _csrf_exempt_path("/api/intake/email") is True
    assert "/api/intake" not in PUBLIC_PATHS
    assert _csrf_exempt_path("/api/intake/email/other") is False


def test_real_http_handler_on_alternate_port(monkeypatch, tmp_path):
    import api.intake_email as intake_module
    from server import Handler as WebUIHandler

    service = IntakeService(IntakeStore(tmp_path / "db.sqlite3"), start_workers=False)
    monkeypatch.setattr(intake_module, "_SERVICE", service)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), WebUIHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        raw = json.dumps(event()).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_port}/api/intake/email",
            data=raw,
            method="POST",
            headers={
                "Authorization": "Bearer secret",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            body = json.loads(response.read())
        assert response.status == 202
        assert body["event_id"] == event()["event_id"]
        assert body["duplicate"] is False
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
