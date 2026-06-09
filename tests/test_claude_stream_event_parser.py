# tests/test_claude_stream_event_parser.py
from api import routes


def test_parse_text_delta_emits_token():
    events = []
    state = routes.ClaudeStreamState()
    inner = {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": "ciao"}}
    routes._handle_claude_stream_event(inner, state, events.append, session_id="s1", clean_msg="x")
    assert ("token", {"text": "ciao"}) in events
    assert "ciao" in "".join(state.streamed_answer_parts)


def test_parse_tool_use_start_and_stop():
    events = []
    state = routes.ClaudeStreamState()
    start = {"type": "content_block_start", "index": 0,
             "content_block": {"type": "tool_use", "id": "tool-1", "name": "Read", "input": {}}}
    routes._handle_claude_stream_event(start, state, events.append, session_id="s1", clean_msg="x")
    stop = {"type": "content_block_stop", "index": 0}
    routes._handle_claude_stream_event(stop, state, events.append, session_id="s1", clean_msg="x")
    kinds = [e[0] for e in events]
    assert "tool" in kinds
    assert "tool_complete" in kinds


def test_message_delta_emits_metering():
    events = []
    state = routes.ClaudeStreamState()
    inner = {"type": "message_delta", "usage": {"input_tokens": 5, "output_tokens": 7}}
    routes._handle_claude_stream_event(inner, state, events.append, session_id="s1", clean_msg="x")
    assert any(k == "metering" for k, _ in events)
