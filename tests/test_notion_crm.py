import pytest

from api.notion_crm import NotionCRM, NotionIdentityConflict, NotionSchemaError


def schema():
    from api.notion_crm import EXPECTED_PROPERTIES

    return {"properties": {name: {"type": kind} for name, kind in EXPECTED_PROPERTIES.items()}}


def event():
    return {
        "event_id": "email:gmail:abc",
        "occurred_at": "2026-08-15T10:20:30Z",
        "sender": {"external_id": "email:creator@example.com", "email": "creator@example.com"},
        "message": {"subject": "Application"},
    }


def extraction():
    return {
        "lead": {
            "name": "Creator", "email": "creator@example.com", "instagram_handle": "creator",
            "profile_urls": ["https://instagram.com/creator"], "niches": ["Food"],
            "followers": 1000, "language": "EN", "message_summary": "Interested",
            "next_action": "Review", "suggested_status": "Da contattare",
            "source_label": "VisionBuilts form",
        }
    }


def test_schema_validation_fails_closed():
    client = NotionCRM("token", "db", transport=lambda *_: (200, {}, {"properties": {}}))
    with pytest.raises(NotionSchemaError, match="schema_incompatible"):
        client.validate_schema()


def test_create_maps_additive_fields_and_auto_owner():
    calls = []

    def transport(method, path, payload):
        calls.append((method, path, payload))
        if method == "GET":
            return 200, {}, schema()
        if path.endswith("/query"):
            return 200, {}, {"results": []}
        return 200, {}, {"id": "page-created"}

    result = NotionCRM("token", "db", transport=transport).upsert(event(), extraction())
    assert result.action == "created"
    create = next(call for call in calls if call[1] == "/pages")
    props = create[2]["properties"]
    assert props["External ID"]["rich_text"][0]["text"]["content"] == "email:creator@example.com"
    assert props["Owner"]["select"]["name"] == "Auto"
    assert props["Stato"]["status"]["name"] == "Da contattare"


def test_protected_status_is_not_downgraded():
    existing = {
        "id": "page-existing",
        "properties": {"Stato": {"type": "status", "status": {"name": "In trattativa"}}},
    }

    def transport(method, path, payload):
        if method == "GET":
            return 200, {}, schema()
        if path.endswith("/query"):
            value = next(iter(payload["filter"].values()))
            return 200, {}, {"results": [existing] if value != "External ID" else []}
        return 200, {}, {"id": "page-existing"}

    client = NotionCRM("token", "db", transport=transport)
    client.validate_schema()
    props = client._properties(event(), extraction(), existing)
    assert props["Stato"]["status"]["name"] == "In trattativa"


def test_agent_cannot_promote_response_without_verified_thread_link():
    output = extraction()
    output["lead"]["suggested_status"] = "Risposto"
    client = NotionCRM("token", "db", transport=lambda *_: (200, {}, {}))
    assert client._properties(event(), output, None)["Stato"]["status"]["name"] == "Da contattare"


def test_conflicting_identity_matches_stop_write():
    counter = 0

    def transport(method, path, payload):
        nonlocal counter
        if method == "GET":
            return 200, {}, schema()
        if path.endswith("/query"):
            counter += 1
            return 200, {}, {"results": [{"id": f"page-{counter}"}] if counter < 3 else []}
        raise AssertionError("write should not happen")

    with pytest.raises(NotionIdentityConflict):
        NotionCRM("token", "db", transport=transport).upsert(event(), extraction())


def test_retry_only_429_and_5xx():
    attempts = []

    def transport(method, path, payload):
        attempts.append(1)
        return (503 if len(attempts) < 3 else 200), {}, schema()

    client = NotionCRM("token", "db", transport=transport, sleep=lambda *_: None)
    client.validate_schema()
    assert len(attempts) == 3

    attempts.clear()
    client = NotionCRM("token", "db", transport=lambda *_: (400, {}, {}), sleep=lambda *_: attempts.append(1))
    with pytest.raises(Exception, match="notion_http_400"):
        client.validate_schema()
    assert attempts == []
