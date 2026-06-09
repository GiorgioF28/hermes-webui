import queue
import sys
import types

from api import models, streaming
from api.models import Session


def test_exception_interruption_persists_partial_before_marker(tmp_path, monkeypatch):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(streaming, "SESSION_DIR", session_dir)
    models.SESSIONS.clear()
    streaming.SESSIONS.clear()
    streaming.STREAMS.clear()
    streaming.AGENT_INSTANCES.clear()
    streaming.SESSION_AGENT_LOCKS.clear()

    session_id = "interrupted-partial-session"
    stream_id = "interrupted-partial-stream"
    session = Session(
        session_id=session_id,
        title="Interrupted partial",
        workspace=str(tmp_path),
        model="gpt-4o",
        messages=[],
        context_messages=[],
    )
    session.active_stream_id = stream_id
    session.pending_user_message = "Inspect and fix the project."
    session.pending_started_at = 1.0
    session.save()
    models.SESSIONS[session_id] = session
    streaming.SESSIONS[session_id] = session
    event_queue = queue.Queue()
    streaming.STREAMS[stream_id] = event_queue

    class FakeAgent:
        def __init__(
            self,
            session_id=None,
            stream_delta_callback=None,
            reasoning_callback=None,
            **_kwargs,
        ):
            self.session_id = session_id
            self.stream_delta_callback = stream_delta_callback
            self.reasoning_callback = reasoning_callback
            self.context_compressor = None
            self.session_prompt_tokens = 0
            self.session_completion_tokens = 0
            self.session_estimated_cost_usd = None
            self.session_cache_read_tokens = 0
            self.session_cache_write_tokens = 0
            self.reasoning_config = None
            self.ephemeral_system_prompt = None
            self._last_error = None

        def run_conversation(self, **_kwargs):
            self.reasoning_callback("I checked the relevant files.")
            self.stream_delta_callback("Completed fifteen diagnostic steps.")
            raise RuntimeError("Response interrupted")

        def interrupt(self, _message):
            return None

    fake_hermes_state = types.ModuleType("hermes_state")
    fake_hermes_state.SessionDB = lambda *_args, **_kwargs: object()

    with monkeypatch.context() as patch:
        patch.setattr(streaming, "get_session", lambda _sid: session)
        patch.setattr(streaming, "_get_ai_agent", lambda: FakeAgent)
        patch.setattr(
            streaming,
            "resolve_model_provider",
            lambda *_args, **_kwargs: ("gpt-4o", "openai", None),
        )
        patch.setattr("api.config.get_config", lambda *_args, **_kwargs: {})
        patch.setattr("api.config._resolve_cli_toolsets", lambda *_args, **_kwargs: [])
        patch.setattr(streaming, "redact_session_data", lambda data: data)
        patch.setitem(sys.modules, "hermes_state", fake_hermes_state)
        streaming._run_agent_streaming(
            session_id=session_id,
            msg_text="Inspect and fix the project.",
            model="gpt-4o",
            workspace=str(tmp_path),
            stream_id=stream_id,
        )

    partial_idx = next(i for i, msg in enumerate(session.messages) if msg.get("_partial"))
    marker_idx = next(
        i
        for i, msg in enumerate(session.messages)
        if msg.get("_error") and "Response interrupted" in str(msg.get("content"))
    )
    partial = session.messages[partial_idx]

    assert partial_idx < marker_idx
    assert partial["content"] == "Completed fifteen diagnostic steps."
    assert partial["reasoning"] == "I checked the relevant files."

    events = []
    while not event_queue.empty():
        events.append(event_queue.get_nowait())
    payload = next(payload for event, payload in events if event == "apperror")
    assert payload["type"] == "interrupted"
    assert payload["session"]["messages"] == session.messages
