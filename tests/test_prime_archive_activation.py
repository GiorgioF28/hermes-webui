import json

import pytest

from api import prime_archive_activation as activation
from api.prime_history_archive import ArchiveError, restore_archived_messages
from api.prime_session_store import PrimeSessionStore
from api.codex_prime import prompt_history


def source_state():
    return {
        "session_id": "hermes-prime", "updated_at": 123, "pending_turn": None,
        "messages": [
            {"role": "user", "content": "old question", "created_at": "2026-10-03T12:00:00Z"},
            {"role": "assistant", "content": "old brief", "brief_id": "brief-old", "created_at": "2026-10-03T13:00:00Z"},
            {"role": "user", "content": "undated"},
            {"role": "assistant", "content": "recent", "created_at": "2026-10-03T22:00:00Z"},
        ],
        "delegations": [
            {"id": "old", "anchor_message_index": 0, "brief_status": "delivered", "status": "ok"},
            {"id": "new", "anchor_message_index": 3, "brief_status": "delivered", "status": "ok"},
            {"id": "pending", "anchor_message_index": 0, "brief_status": "pending", "status": "ok"},
        ],
    }


def stage(path):
    request_path = path.with_suffix(".archive-request.json")
    request_path.write_text(json.dumps({"schema": 1, "session_id": "hermes-prime",
                                       "cutoff": "2026-10-04T00:00:00+02:00", "summary": "Historical decisions; consult current sources."}), encoding="utf-8")
    return request_path


@pytest.fixture
def archived(tmp_path):
    path = tmp_path / "prime.json"
    path.write_text(json.dumps(source_state()), encoding="utf-8")
    stage(path)
    return PrimeSessionStore(path)


def test_startup_archive_filters_ui_and_model_but_keeps_absolute_cursors(archived):
    history = archived.history()
    assert history["total"] == history["message_count"] == 4
    assert [m["message_index"] for m in history["messages"]] == [2, 3]
    assert [m["content"] for m in history["messages"]] == ["undated", "recent"]
    assert [d["id"] for d in history["delegations"]] == ["new", "pending"]
    assert [d["id"] for d in archived.get_delegations()] == ["old", "new", "pending"]
    packet = prompt_history(history["messages"], total_messages=history["message_count"])
    assert [m["index"] for m in packet["messages"]] == [2, 3]
    assert "old question" not in json.dumps(packet)
    assert archived.history(3)["messages"][0]["message_index"] == 3
    assert archived.history(4)["messages"] == []
    assert archived.history_with_tool_events()["archive"]["archived_count"] == 2
    data = json.loads(archived.path.read_text())
    assert data["updated_at"] == 123
    assert archived.retrieve_history(0, 4)["messages"] == source_state()["messages"]


def test_new_turn_indices_brief_idempotence_restore_and_reload(archived):
    assert archived.inject_assistant_message("retry", {"brief_id": "brief-old"}) == 1
    stream = archived.begin_turn("new question")
    assert archived.finish_turn(stream, "new answer") == 5
    assert archived.live()["message_count"] == 6
    delta = archived.history(4)
    assert [m["message_index"] for m in delta["messages"]] == [4, 5]
    assert delta["messages"][-1]["reply_to_index"] == 4
    assert archived.retrieve_history(-2, 2)["messages"][-1]["content"] == "new answer"
    data = json.loads(archived.path.read_text())
    bundle = activation._read_bundle(data["_prime_archive"])
    restored = restore_archived_messages(data, bundle)
    assert restored["messages"][:4] == source_state()["messages"]
    assert restored["messages"][4:][-1]["reply_to_index"] == 4
    before = archived.path.read_bytes()
    stage(archived.path)
    loaded = PrimeSessionStore(archived.path)
    assert loaded.path.read_bytes() == before
    assert loaded.history()["message_count"] == 6


def test_active_turn_rejected_without_mutation(tmp_path):
    store = PrimeSessionStore(tmp_path / "prime.json")
    store.begin_turn("active")
    request_path = stage(store.path)
    before = store.path.read_bytes()
    with store._lock, pytest.raises(ArchiveError, match="active"):
        activation.activate_request(store, store._read_locked(), request_path)
    assert store.path.read_bytes() == before
    assert request_path.exists()


def test_failed_state_commit_preserves_original_and_request(tmp_path, monkeypatch):
    path = tmp_path / "prime.json"
    path.write_text(json.dumps(source_state()), encoding="utf-8")
    before = path.read_bytes()
    request_path = stage(path)
    real_write = activation.write_json_atomic

    def fail_state(target, payload):
        if target == path:
            raise OSError("simulated state commit failure")
        return real_write(target, payload)

    monkeypatch.setattr("api.prime_history_archive.write_json_atomic", fail_state)
    PrimeSessionStore(path)
    assert path.read_bytes() == before
    assert request_path.exists()
    backup = next((tmp_path / "_prime_archives").glob("*/original.json"))
    assert backup.read_bytes() == before


def test_tampered_archive_is_rejected(archived):
    data = json.loads(archived.path.read_text())
    bundle_path = __import__("pathlib").Path(data["_prime_archive"]["bundle_path"])
    bundle_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ArchiveError, match="checksum"):
        archived.retrieve_history(0, 1)


def test_pending_restart_output_is_preserved_before_archiving(tmp_path):
    path = tmp_path / "prime.json"
    state = source_state()
    state["pending_turn"] = {"stream_id": "old-active", "partial_output": "saved partial"}
    path.write_text(json.dumps(state), encoding="utf-8")
    stage(path)
    store = PrimeSessionStore(path)
    assert store.live()["active"] is False
    assert store.history()["messages"][-1]["content"] == "saved partial"
    assert store.history()["messages"][-1]["interrupted"] is True


def test_codex_prompt_includes_summary_and_retained_rows_only(archived, monkeypatch, tmp_path):
    from api import codex_prime, routes, memory_retrieval
    monkeypatch.setattr("api.prime_session_store.get_prime_session_store", lambda sid: archived)
    monkeypatch.setattr(routes, "_prime_system_prompt_for_user", lambda *args: "Prime instructions")
    monkeypatch.setattr(memory_retrieval, "build_prime_unlocked_memory_detail", lambda *args, **kwargs: "")
    monkeypatch.setattr(memory_retrieval, "build_prime_memory_context", lambda *args, **kwargs: "")
    prompt = codex_prime.build_prompt("current", tmp_path, session_id="hermes-prime", user="giorgio")
    assert "Historical decisions" in prompt
    assert "recent" in prompt and "undated" in prompt
    assert "old question" not in prompt and "old brief" not in prompt
    assert '"index": 3' in prompt


def test_archived_tool_events_do_not_reappear_in_ui(tmp_path):
    path = tmp_path / "prime.json"
    data = source_state()
    data["messages"][0]["stream_id"] = "archived-stream"
    data["journal"] = [{"event": "tool_call", "stream_id": "archived-stream", "tool": "old-tool"}]
    path.write_text(json.dumps(data), encoding="utf-8")
    stage(path)
    store = PrimeSessionStore(path)
    assert store.history_with_tool_events()["tool_events"] == []
    assert store.get_tool_events()[0]["tool"] == "old-tool"
