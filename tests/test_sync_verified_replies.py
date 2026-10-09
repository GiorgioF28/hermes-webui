import json

from scripts.sync_verified_replies import consume


class Notion:
    def __init__(self):
        self.page = {"id": "p1", "properties": {
            "Email": {"type": "email", "email": "a@example.com"},
            "Stato": {"type": "select", "select": {"name": "Contattato"}},
            "Data risposta": {"type": "date", "date": None},
            "Note": {"type": "rich_text", "rich_text": []},
        }}
        self.calls = []

    def get_page(self, page_id):
        return self.page

    def patch_page(self, page_id, properties):
        self.calls.append(properties)
        for name, prop in properties.items():
            kind, value = next(iter(prop.items()))
            if kind == "rich_text":
                self.page["properties"][name] = {"type": kind, kind: [
                    {"plain_text": item["text"]["content"]} for item in value
                ]}
            else:
                self.page["properties"][name] = {"type": kind, kind: value}
        return self.page


def test_consumer_uses_reviewed_mapping_not_payload_page_or_verified_flag(tmp_path):
    event = {"channel": "email", "account": "primary", "message_id": "m1",
             "occurred_at": "2026-10-09T10:00:00Z", "summary": "Asked a question",
             "next_action": "Review manually", "identity_key": "sender-a",
             "sender_email": "a@example.com", "thread_id": "thread-1", "in_reply_to": "out-1",
             "crm_page_id": "attacker-page", "identity_verified": False}
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps(event) + "\n", encoding="utf-8")
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps({"primary|email|sender-a": {
        "page_id": "p1", "notion_property": "Email", "identity_value": "a@example.com",
        "thread_id": "thread-1", "outbound_message_ids": ["out-1"]
    }}), encoding="utf-8")
    notion = Notion()
    notion.database_id = "db1"
    notion.page["parent"] = {"database_id": "db1"}
    result = consume(events, mapping, tmp_path / "ledger.sqlite", notion)
    assert result["updated"] == 1
    assert len(notion.calls) == 1


def test_consumer_keeps_unmapped_events_in_review(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps({"channel": "instagram", "account": "ig", "message_id": "m"}) + "\n", encoding="utf-8")
    mapping = tmp_path / "mapping.json"
    mapping.write_text("{}", encoding="utf-8")
    notion = Notion()
    result = consume(events, mapping, tmp_path / "ledger.sqlite", notion)
    assert result["review"] == 1
    assert notion.calls == []
