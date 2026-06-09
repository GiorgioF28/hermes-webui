from api import routes


def test_system_prompt_is_preset_with_append(tmp_path):
    sp = routes._claude_session_system_prompt(str(tmp_path))
    assert sp["type"] == "preset" and sp["preset"] == "claude_code"
    assert "ask_user" in sp["append"]


def test_consume_stream_event_routes_to_parser():
    state = routes.ClaudeStreamState()
    events = []
    class FakeStreamEvent:
        event = {"type": "content_block_delta", "index": 0,
                 "delta": {"type": "text_delta", "text": "hi"}}
    routes._consume_claude_sdk_message(FakeStreamEvent(), state, events.append, session_id="s", clean_msg="m")
    assert ("token", {"text": "hi"}) in events


def test_consume_result_message_emits_completion():
    state = routes.ClaudeStreamState()
    events = []
    class FakeResult:  # name must be ResultMessage for the isinstance-by-name check
        pass
    FakeResult.__name__ = "ResultMessage"
    fr = FakeResult(); fr.usage = {"input_tokens": 1, "output_tokens": 2}; fr.result = "done text"
    routes._consume_claude_sdk_message(fr, state, events.append, session_id="s", clean_msg="m")
    kinds = [k for k, _ in events]
    assert "metering" in kinds and "reasoning" in kinds


def test_flag_off_delegates_to_legacy(monkeypatch):
    called = {}
    monkeypatch.setattr(routes, "persistent_cli_bridge_enabled", lambda *a, **k: False)
    monkeypatch.setattr(routes, "_run_claude_code_streaming_legacy",
                        lambda *a, **k: called.setdefault("legacy", True))
    routes._run_claude_code_streaming("sid", "msg", "claude-code", "/ws", "stream-x")
    assert called.get("legacy") is True
