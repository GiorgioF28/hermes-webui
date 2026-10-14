import io
import json
import uuid

import pytest

from api import sicilia_crm as crm


class Handler:
    def __init__(self):
        self.headers = {}
        self.status = None
        self.response_headers = {}
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.response_headers[key] = value

    def end_headers(self):
        pass

    def result(self):
        return json.loads(self.wfile.getvalue())


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(crm, "DB_PATH", tmp_path / "sicilia-crm.sqlite3")
    monkeypatch.setattr(crm, "_identity", lambda _handler: "giorgio")
    monkeypatch.setattr(crm, "_csrf_ok", lambda _handler: True)


def record_payload(**overrides):
    value = {
        "client_request_id": str(uuid.uuid4()),
        "name": "Contatto di prova",
        "outcome": "potenziale",
        "location_source": "device",
        "latitude": 37.5,
        "longitude": 14.2,
        "accuracy_m": 12.0,
        "altitude_m": None,
        "altitude_accuracy_m": None,
        "captured_at": "2026-10-14T09:00:00Z",
    }
    value.update(overrides)
    return value


def test_save_is_persistent_idempotent_and_uses_server_author():
    first = crm.save_record(record_payload(), "giorgio")
    retry = crm.save_record(record_payload(client_request_id=first["client_request_id"], name="ignored retry"), "tom")
    assert first["id"] == retry["id"]
    assert retry["name"] == "Contatto di prova"
    assert retry["author"] == "giorgio"
    assert crm.list_records()[0]["id"] == first["id"]
    assert first["altitude_m"] is None


@pytest.mark.parametrize("overrides", [
    {"latitude": float("nan")}, {"latitude": 91}, {"longitude": -181},
    {"accuracy_m": -1}, {"name": ""}, {"appointment_at": "2026-10-14T12:30"},
    {"appointment_at": "2026-10-14T12:30:00+00:00"}, {"captured_at": "2026-10-14T09:00:00"},
    {"declared_latitude": 37.5},
])
def test_rejects_invalid_or_unpaired_values(overrides):
    with pytest.raises(ValueError):
        crm.validate_record(record_payload(**overrides))


def test_corrected_pin_keeps_device_fix_and_maps_declared_coordinate():
    saved = crm.save_record(record_payload(declared_latitude=37.51, declared_longitude=14.21,
                                          location_source="pin_correction"), "giorgio")
    assert (saved["latitude"], saved["longitude"]) == (37.5, 14.2)
    assert (saved["pin_latitude"], saved["pin_longitude"]) == (37.51, 14.21)
    assert crm.list_records()[0]["pin_latitude"] == 37.51


def test_ai_returns_validated_draft_without_writing():
    answer = json.dumps({"name": "Giulia", "outcome": "potenziale", "floor": "",
                         "appointment_at": "", "clarification": ""})
    draft = crm.extract_draft("Ho incontrato Giulia, contatto potenziale.", lambda *_: answer)
    assert draft["name"] == "Giulia"
    assert crm.list_records() == []


def test_ai_rejects_unknown_fields_and_does_not_write():
    with pytest.raises(ValueError, match="unknown_fields"):
        crm.extract_draft("testo", lambda *_: '{"name":"inventato","secret":"x"}')
    assert crm.list_records() == []


def test_draft_endpoint_does_not_save_and_post_routes_require_csrf(monkeypatch):
    handler = Handler()
    monkeypatch.setattr(crm, "_csrf_ok", lambda _handler: False)
    assert crm.handle_post(handler, type("URL", (), {"path": "/api/sicilia-crm/records"})(), record_payload()) is True
    assert handler.status == 403
    assert crm.list_records() == []


def test_post_save_and_get_readback_keep_one_record_on_retry():
    parsed = type("URL", (), {"path": "/api/sicilia-crm/records"})()
    payload = record_payload()
    first_handler = Handler()
    assert crm.handle_post(first_handler, parsed, payload) is True
    first = first_handler.result()["record"]
    assert first_handler.status == 201
    retry_handler = Handler()
    assert crm.handle_post(retry_handler, parsed, payload) is True
    assert retry_handler.result()["record"]["id"] == first["id"]
    get_handler = Handler()
    assert crm.handle_get(get_handler, parsed) is True
    assert [row["id"] for row in get_handler.result()["records"]] == [first["id"]]


def test_unknown_paths_fall_through():
    handler = Handler()
    parsed = type("URL", (), {"path": "/api/unrelated"})()
    assert crm.handle_get(handler, parsed) is False
    assert crm.handle_post(handler, parsed, {}) is False


def test_personal_mvp_rejects_other_hermes_identity(monkeypatch):
    monkeypatch.setattr(crm, "_identity", lambda _handler: "tom")
    handler = Handler()
    parsed = type("URL", (), {"path": "/api/sicilia-crm/records"})()
    assert crm.handle_get(handler, parsed) is True
    assert handler.status == 403
    assert handler.result()["error"] == "not_authorized"
