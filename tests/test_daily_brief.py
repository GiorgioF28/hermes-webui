from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from api.daily_brief import build_daily_brief_payload
from api.delegation_store import DelegationStore
import api.routes as routes


def _terminal_record(task_id: str, finished_at: float) -> dict:
    return {
        "id": task_id,
        "agent": "programmatore",
        "task_type": "codice",
        "task": "Implementa vista Daily Brief",
        "status": "done",
        "created_at": finished_at - 60,
        "finished_at": finished_at,
        "result": {"text": "Patch verificata"},
    }


def test_payload_groups_recent_completed_delegations_and_reads_replies(tmp_path: Path):
    now = datetime(2026, 8, 28, 12, tzinfo=timezone.utc).timestamp()
    store = DelegationStore(tmp_path)
    store.upsert(_terminal_record("d1", now - 60))
    store.upsert({**_terminal_record("old", now - 9 * 86_400), "status": "failed"})
    store.upsert({**_terminal_record("running", now - 30), "status": "running"})
    replies_file = tmp_path / "replies.json"
    replies_file.write_text(json.dumps({"version": 1, "replies": [
        {
            "handle": "Creator.One",
            "text": "Sono interessata",
            "timestamp": "2026-08-28T09:00:00.000Z",
            "notionPageId": "page-1",
            "detectedAt": "2026-08-28T09:01:00.000Z",
        },
        {
            "handle": "creator.one",
            "text": "Messaggio precedente",
            "timestamp": "2026-08-27T09:00:00.000Z",
        },
    ]}), encoding="utf-8")

    payload = build_daily_brief_payload(tmp_path, replies_file=replies_file, now=now)

    assert payload["ok"] is True
    assert [row["id"] for group in payload["briefs"] for row in group["items"]] == ["d1"]
    assert payload["briefs"][0]["items"][0]["outcome"] == "Patch verificata"
    assert payload["ig_replies"][0]["notionPageId"] == "page-1"
    assert len(payload["ig_replies"]) == 1
    assert payload["checkDmNotInitialized"] is False
    assert payload["checkDmMalformed"] is False


def test_payload_missing_replies_file_is_empty_not_initialized(tmp_path: Path):
    payload = build_daily_brief_payload(tmp_path, replies_file=tmp_path / "missing.json")
    assert payload["ig_replies"] == []
    assert payload["checkDmNotInitialized"] is True
    assert payload["checkDmMalformed"] is False


def test_payload_malformed_replies_file_is_empty_without_500(tmp_path: Path):
    replies_file = tmp_path / "replies.json"
    replies_file.write_text("{not-json", encoding="utf-8")
    payload = build_daily_brief_payload(tmp_path, replies_file=replies_file)
    assert payload["ig_replies"] == []
    assert payload["checkDmNotInitialized"] is False
    assert payload["checkDmMalformed"] is True


def test_endpoint_returns_payload_with_no_store_cache(tmp_path: Path):
    captured = []
    fake_payload = {"ok": True, "briefs": [], "ig_replies": [], "checkDmNotInitialized": True, "checkDmMalformed": False}

    def capture(_handler, payload, **kwargs):
        captured.append((payload, kwargs))
        return True

    with patch.object(routes, "j", capture), patch.object(routes, "_prime_workspace_from_settings", return_value=tmp_path), patch(
        "api.daily_brief.build_daily_brief_payload", return_value=fake_payload
    ):
        assert routes._handle_bridge_prime_daily_brief(object()) is True

    assert captured == [(fake_payload, {"extra_headers": {"Cache-Control": "no-store"}})]
