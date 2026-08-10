import asyncio
import io
import json
import threading
import time
from pathlib import Path

from api import ask_user_tool, clarify, routes


class _Handler:
    def __init__(self):
        self.wfile = io.BytesIO()
        self.status = None
        self.headers = {}

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.headers[name] = value

    def end_headers(self):
        pass


def _payload(handler):
    return json.loads(handler.wfile.getvalue().decode("utf-8"))


def test_prime_ask_user_question_waits_unblocks_and_persists(monkeypatch, tmp_path):
    from api import prime_session_store

    store = prime_session_store.PrimeSessionStore(tmp_path / "prime-session.json")
    monkeypatch.setattr(prime_session_store, "_STORE", store)
    clarify.clear_pending("hermes-prime")
    result_box = {}

    def _runner():
        result_box["result"] = asyncio.run(
            ask_user_tool._run_ask_user(
                "hermes-prime",
                {
                    "questions": [
                        {
                            "id": "scope",
                            "header": "Scope",
                            "question": "Cosa porto nel bridge?",
                            "options": [
                                {"label": "Backend", "description": "Sblocca il turno"},
                                {"label": "Frontend", "description": "Card cliccabile"},
                            ],
                        },
                        {
                            "id": "tests",
                            "question": "Quali test lancio?",
                            "multiSelect": True,
                            "options": [{"label": "Unit"}, {"label": "Bridge"}],
                        },
                    ]
                },
            )
        )

    thread = threading.Thread(target=_runner)
    thread.start()
    try:
        pending = None
        for _ in range(200):
            pending = clarify.get_pending("hermes-prime")
            if pending:
                break
            time.sleep(0.02)
        assert pending is not None
        assert pending["kind"] == "ask_user_question"
        assert pending["questions"][0]["options"][0]["description"] == "Sblocca il turno"
        assert pending["questions"][1]["multiSelect"] is True

        handler = _Handler()
        routes._handle_clarify_respond(
            handler,
            {
                "session_id": "hermes-prime",
                "clarify_id": pending["clarify_id"],
                "response": {
                    "answers": {
                        "Cosa porto nel bridge?": "Frontend",
                        "Quali test lancio?": ["Unit", "Bridge"],
                    }
                },
            },
        )
        assert handler.status == 200
        assert _payload(handler)["ok"] is True

        thread.join(timeout=5)
        assert not thread.is_alive()
        result_text = result_box["result"]["content"][0]["text"]
        assert json.loads(result_text) == {
            "Cosa porto nel bridge?": "Frontend",
            "Quali test lancio?": ["Unit", "Bridge"],
        }

        history = store.history()
        assert [m["role"] for m in history["messages"]] == ["assistant", "user"]
        assert history["messages"][0]["_bridge_clarify_event"] == "request"
        assert history["messages"][0]["_bridge_clarify_payload"]["questions"][0]["id"] == "scope"
        assert history["messages"][0]["_bridge_clarify_resolved"] is True
        assert history["messages"][0]["_bridge_clarify_response"]["answers"]["Cosa porto nel bridge?"] == "Frontend"
        assert history["messages"][1]["_bridge_clarify_event"] == "response"
        assert "Cosa porto nel bridge?: Frontend" in history["messages"][1]["content"]
    finally:
        clarify.clear_pending("hermes-prime")


def test_bridge_prime_history_exposes_pending_clarify(monkeypatch, tmp_path):
    from api import prime_session_store

    store = prime_session_store.PrimeSessionStore(tmp_path / "prime-session.json")
    monkeypatch.setattr(prime_session_store, "_STORE", store)
    clarify.clear_pending("hermes-prime")
    try:
        entry = clarify.submit_pending(
            "hermes-prime",
            {
                "kind": "ask_user_question",
                "question": "Scegli?",
                "questions": [{"id": "q1", "question": "Scegli?", "options": [{"label": "A"}]}],
            },
        )
        handler = _Handler()
        assert routes._handle_bridge_prime_history(handler) is True
        data = _payload(handler)
        assert data["pending_clarify"]["clarify_id"] == entry.clarify_id
        assert data["pending_clarify"]["questions"][0]["id"] == "q1"
    finally:
        clarify.clear_pending("hermes-prime")


def test_command_bridge_frontend_renders_structured_clarify_cards():
    source = Path("static/command_bridge.js").read_text(encoding="utf-8")

    assert "function renderBridgeStructuredQuestions" in source
    assert "bridgeNormalizeOptions" in source
    assert "multiSelect" in source
    assert "cb-option-description" in source
    assert "bridgeStructuredEcho" in source
    assert "pending_clarify" in source
    assert "apiPost('/api/clarify/respond'" in source
    assert "function resolveBridgeClarifyCard" in source
    assert "cb-resolved" in source
    assert "data-option-label" in source
    assert "bridgeValidAskUserPending" in source
    assert "options.onResolved" in source
    assert "log.appendChild(ph)" in source


def test_invalid_ask_user_payload_does_not_emit_zombie_card():
    sid = "invalid-ask-user"
    clarify.clear_pending(sid)
    try:
        result = asyncio.run(ask_user_tool._run_ask_user(sid, {}))
        assert result["is_error"] is True
        assert clarify.get_pending(sid) is None
    finally:
        clarify.clear_pending(sid)
