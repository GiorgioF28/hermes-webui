from __future__ import annotations

import json

import pytest

from api import prime_history_archive as archive


def _state():
    return {
        "session_id": "hermes-prime",
        "messages": [
            {"role": "user", "content": "old", "created_at": "2026-10-03T20:00:00Z"},
            {"role": "assistant", "content": "undated"},
            {"role": "assistant", "content": "at cutoff", "created_at": "2026-10-03T22:00:00Z"},
            {"role": "user", "content": "new", "created_at": "2026-10-04T00:00:00+02:00"},
        ],
        "pending_turn": None,
        "journal": [{"event": "tool_call", "stream_id": "s1"}],
        "settings": {"todo_snapshot": {"items": ["preserve"]}},
        "delegations": [{"id": "d1", "anchor_message_index": 3, "brief_message_index": 2}],
    }


def test_cutoff_timezone_absolute_slots_and_round_trip():
    source = json.dumps(_state(), ensure_ascii=False).encode()
    bundle, candidate = archive.build_archive_bundle(source, "2026-10-04T00:00:00+02:00")
    assert [row["index"] for row in bundle["archived_entries"]] == [0]
    assert candidate["messages"][0][archive.TOMBSTONE_KEY]["original_index"] == 0
    assert candidate["messages"][1] == _state()["messages"][1]  # no timestamp: retain
    assert candidate["messages"][2] == _state()["messages"][2]  # exact cutoff: retain
    assert candidate["messages"][3] == _state()["messages"][3]
    assert candidate["delegations"] == _state()["delegations"]
    assert candidate["journal"] == _state()["journal"]
    assert archive.retrieve_archived_message(bundle, 0) == _state()["messages"][0]
    assert archive.restore_archived_messages(candidate, bundle) == _state()


def test_idempotent_selection_and_timezone_required():
    _, candidate = archive.build_archive_bundle(json.dumps(_state()).encode(), "2026-10-04T00:00:00+02:00")
    assert archive.select_archive_entries(candidate["messages"], "2026-10-04T00:00:00+02:00") == []
    with pytest.raises(archive.ArchiveError, match="timezone"):
        archive.parse_cutoff("2026-10-04T00:00:00")


def test_active_turn_rejected():
    state = _state()
    state["pending_turn"] = {"stream_id": "active"}
    _, candidate = archive.build_archive_bundle(json.dumps(state).encode(), "2026-10-04T00:00:00+02:00")
    path = __import__("pathlib").Path("not-read-when-active")
    with pytest.raises(archive.ArchiveError, match="active"):
        archive.apply_archive_atomically(path, "irrelevant", candidate, active_turn=True)


def test_apply_refuses_changed_source(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"old":true}', encoding="utf-8")
    _, candidate = archive.build_archive_bundle(json.dumps(_state()).encode(), "2026-10-04T00:00:00+02:00")
    with pytest.raises(archive.ArchiveError, match="changed"):
        archive.apply_archive_atomically(path, "wrong-hash", candidate, active_turn=False)


def test_restore_rejects_changed_anchor_slot():
    bundle, candidate = archive.build_archive_bundle(json.dumps(_state()).encode(), "2026-10-04T00:00:00+02:00")
    candidate["messages"][0][archive.TOMBSTONE_KEY]["archive_id"] = "other"
    with pytest.raises(archive.ArchiveError, match="identity"):
        archive.restore_archived_messages(candidate, bundle)


def test_failed_atomic_write_preserves_existing_file(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    target.write_text('{"original":true}', encoding="utf-8")

    def fail_replace(*_args):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(archive.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        archive.write_json_atomic(target, {"replacement": True})
    assert target.read_text(encoding="utf-8") == '{"original":true}'
    assert list(tmp_path.glob("state.json.tmp.*")) == []
