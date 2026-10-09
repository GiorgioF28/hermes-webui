import pytest

from api.verified_reply_sync import IdentityReview, ReplyLedger, ReplySyncError, sync_reply


class FakeNotion:
    def __init__(self, status="Contattato", page_id="page-1", **extra):
        self.calls = []
        self.page = {"id": page_id, "properties": {
            "Stato": {"type": "select", "select": {"name": status}},
            "Data risposta": {"type": "date", "date": None},
            "Note": {"type": "rich_text", "rich_text": []},
            "Fonte": {"type": "rich_text", "rich_text": [{"plain_text": "historic source"}]},
            "DM inviato": {"type": "rich_text", "rich_text": [{"plain_text": "sent DM"}]},
            "Owner": {"type": "select", "select": {"name": "Giorgio"}},
            "Canale risposta": {"type": "select", "select": None},
            "Prossima azione": {"type": "rich_text", "rich_text": []},
            **extra,
        }}

    def get_page(self, page_id):
        return self.page

    def patch_page(self, page_id, properties):
        self.calls.append(properties)
        for name, value in properties.items():
            prop_type = next(iter(value))
            raw = value[prop_type]
            if prop_type == "rich_text":
                self.page["properties"][name] = {"type": prop_type, prop_type: [
                    {"plain_text": item["text"]["content"]} for item in raw
                ]}
            else:
                self.page["properties"][name] = {"type": prop_type, prop_type: raw}
        return self.page


def event(account="ig-main", message_id="m1", channel="instagram", **extra):
    return {
        "account": account, "channel": channel, "message_id": message_id,
        "occurred_at": "2026-10-09T10:00:00Z", "summary": "Asked about the offer",
        "next_action": "Reply manually", "identity_key": "scoped-user-7",
        "crm_page_id": "page-1", "identity_verified": True, **extra,
    }


def test_accounts_have_independent_keys_and_replay_is_idempotent(tmp_path):
    db = tmp_path / "receipts.sqlite"
    notion = FakeNotion()
    ledger = ReplyLedger(db)
    assert sync_reply(event(account="ig-main"), notion=notion, ledger=ledger, identity_check=lambda *_: True)["action"] == "updated"
    second_notion = FakeNotion(page_id="page-2")
    assert sync_reply(event(account="ig-en", crm_page_id="page-2"), notion=second_notion, ledger=ledger, identity_check=lambda *_: True)["action"] == "updated"
    assert sync_reply(event(account="ig-main"), notion=notion, ledger=ledger, identity_check=lambda *_: True)["action"] == "duplicate"
    assert len(notion.calls) == 1
    assert len(second_notion.calls) == 1
    note = notion.page["properties"]["Note"]["rich_text"][0]["plain_text"]
    assert note.count("[reply:instagram:m1]") == 1


@pytest.mark.parametrize("status", ["In trattativa", "Chiuso", "Perso"])
def test_protected_status_is_preserved(tmp_path, status):
    notion = FakeNotion(status)
    sync_reply(event(), notion=notion, ledger=ReplyLedger(tmp_path / "r.db"), identity_check=lambda *_: True)
    assert notion.page["properties"]["Stato"]["select"]["name"] == status


def test_inconsistent_status_requires_documented_outbound(tmp_path):
    unproven = FakeNotion("Da contattare")
    with pytest.raises(IdentityReview, match="contacted_state_not_verified"):
        sync_reply(event(), notion=unproven, ledger=ReplyLedger(tmp_path / "a.db"), identity_check=lambda *_: True)
    assert unproven.calls == []

    proven = FakeNotion("Da contattare")
    proven.page["properties"]["DM inviato"] = {"type": "rich_text", "rich_text": [{"plain_text": "hello"}]}
    proven.page["properties"]["Data contatto"] = {"type": "date", "date": {"start": "2026-10-01"}}
    sync_reply(event(), notion=proven, ledger=ReplyLedger(tmp_path / "b.db"), identity_check=lambda *_: True)
    assert proven.page["properties"]["Stato"]["select"]["name"] == "Risposto"


def test_identity_conflict_is_review_only(tmp_path):
    notion = FakeNotion()
    with pytest.raises(IdentityReview, match="identity_mismatch"):
        sync_reply(event(), notion=notion, ledger=ReplyLedger(tmp_path / "r.db"), identity_check=lambda *_: False)
    assert notion.calls == []


def test_unverified_echo_and_autoresponder_never_write(tmp_path):
    notion = FakeNotion()
    ledger = ReplyLedger(tmp_path / "r.db")
    for payload in (event(identity_verified=False), event(echo=True), event(autoresponder=True)):
        with pytest.raises(ReplySyncError):
            sync_reply(payload, notion=notion, ledger=ledger, identity_check=lambda *_: True)
    assert notion.calls == []


def test_out_of_order_reply_does_not_overwrite_newer_reply(tmp_path):
    notion = FakeNotion()
    ledger = ReplyLedger(tmp_path / "r.db")
    sync_reply(event(message_id="new", occurred_at="2026-10-09T11:00:00Z"), notion=notion, ledger=ledger, identity_check=lambda *_: True)
    stale = event(message_id="old", occurred_at="2026-10-09T09:00:00Z")
    assert sync_reply(stale, notion=notion, ledger=ledger, identity_check=lambda *_: True)["action"] == "stale"
    assert len(notion.calls) == 1
    assert not ledger.get(__import__("api.verified_reply_sync", fromlist=["ReplyEvent"]).ReplyEvent.parse(stale).key)


def test_notion_failure_or_readback_mismatch_is_not_acknowledged(tmp_path):
    ledger = ReplyLedger(tmp_path / "r.db")

    class Fails(FakeNotion):
        def patch_page(self, *_):
            raise RuntimeError("notion unavailable")

    with pytest.raises(RuntimeError):
        sync_reply(event(), notion=Fails(), ledger=ledger, identity_check=lambda *_: True)
    assert not ledger.get(__import__("api.verified_reply_sync", fromlist=["ReplyEvent"]).ReplyEvent.parse(event()).key)


def test_retry_after_applied_patch_reuses_marker_and_acknowledges(tmp_path):
    class ReadbackInterrupted(FakeNotion):
        fail_once = True

        def get_page(self, page_id):
            if self.fail_once and self.calls:
                self.fail_once = False
                raise RuntimeError("temporary readback failure")
            return self.page

    notion = ReadbackInterrupted()
    ledger = ReplyLedger(tmp_path / "r.db")
    with pytest.raises(RuntimeError):
        sync_reply(event(), notion=notion, ledger=ledger, identity_check=lambda *_: True)
    assert not ledger.get(__import__("api.verified_reply_sync", fromlist=["ReplyEvent"]).ReplyEvent.parse(event()).key)
    assert sync_reply(event(), notion=notion, ledger=ledger, identity_check=lambda *_: True)["action"] == "updated"
    note = "".join(x["plain_text"] for x in notion.page["properties"]["Note"]["rich_text"])
    assert note.count("[reply:instagram:m1]") == 1


def test_preserves_long_notes_and_semantic_notion_timestamp(tmp_path):
    note = "x" * 2600
    notion = FakeNotion()
    notion.page["properties"]["Note"] = {"type": "rich_text", "rich_text": [
        {"plain_text": note[:2000]}, {"plain_text": note[2000:]}
    ]}
    original_patch = notion.patch_page

    def normalized_patch(page_id, props):
        result = original_patch(page_id, props)
        date = result["properties"]["Data risposta"]["date"]["start"]
        result["properties"]["Data risposta"]["date"]["start"] = "2026-10-09T10:00:00.000+00:00"
        return result

    notion.patch_page = normalized_patch
    sync_reply(event(), notion=notion, ledger=ReplyLedger(tmp_path / "r.db"), identity_check=lambda *_: True)
    saved = "".join(x["plain_text"] for x in notion.page["properties"]["Note"]["rich_text"])
    assert saved.startswith(note)
    assert "Prossima azione: Reply manually" in saved
    assert len(notion.calls[0]["Note"]["rich_text"]) == 2


def test_equal_timestamp_distinct_events_are_not_dropped(tmp_path):
    notion = FakeNotion()
    ledger = ReplyLedger(tmp_path / "r.db")
    sync_reply(event(message_id="first"), notion=notion, ledger=ledger, identity_check=lambda *_: True)
    result = sync_reply(event(message_id="second"), notion=notion, ledger=ledger, identity_check=lambda *_: True)
    assert result["action"] == "updated"
    note = "".join(x["plain_text"] for x in notion.page["properties"]["Note"]["rich_text"])
    assert "[reply:instagram:first]" in note and "[reply:instagram:second]" in note


def test_ledger_connections_close_and_database_reopens(tmp_path):
    path = tmp_path / "r.db"
    ledger = ReplyLedger(path)
    ledger.get("missing")
    db = ledger._connect()
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.close()
    ledger2 = ReplyLedger(path)
    assert ledger2.get("missing") is None

    class BadReadback(FakeNotion):
        def patch_page(self, *_):
            return self.page

    with pytest.raises(ReplySyncError, match="readback"):
        sync_reply(event(), notion=BadReadback(), ledger=ledger, identity_check=lambda *_: True)
    assert not ledger.get(__import__("api.verified_reply_sync", fromlist=["ReplyEvent"]).ReplyEvent.parse(event()).key)


def test_email_thread_event_uses_same_verified_contract(tmp_path):
    notion = FakeNotion()
    result = sync_reply(event(account="admin@visionbuilts.net", channel="email", message_id="gmail-msg-1"),
                        notion=notion, ledger=ReplyLedger(tmp_path / "r.db"), identity_check=lambda e, _: e.identity_key == "scoped-user-7")
    assert result["action"] == "updated"
    assert notion.page["properties"]["Stato"]["select"]["name"] == "Risposto"
