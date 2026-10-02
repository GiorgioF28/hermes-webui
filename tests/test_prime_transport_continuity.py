"""A browser is an observer, not the owner of the durable Prime turn."""
import io
import json
import threading
from unittest.mock import MagicMock

import pytest

from api import prime_session_store, routes


def test_background_brief_cannot_split_a_durable_user_reply(monkeypatch, tmp_path):
    from api import prime_brief_queue, delegation_store
    store = prime_session_store.PrimeSessionStore(tmp_path / "prime.json")
    sid = "brief-order-regression"
    entered = threading.Event()
    monkeypatch.setattr(prime_session_store, "get_prime_session_store", lambda _: store)
    monkeypatch.setattr(prime_brief_queue, "get_brief_queue", lambda _: MagicMock())
    monkeypatch.setattr(delegation_store, "get_delegation_store", lambda _: MagicMock())
    def reply(*args, **kwargs):
        entered.set()
        return {"reply": "esito delega"}
    monkeypatch.setattr(routes, "_hermes_prime_reply", reply)
    worker = threading.Thread(target=routes._run_prime_brief_job,
        args=("order-test", "brief-order-test", "brief", tmp_path, sid))
    with routes._prime_bridge_turn_lock(sid):
        stream_id = store.begin_turn("domanda")
        worker.start()
        assert not entered.wait(0.1)
        store.finish_turn(stream_id, "risposta")
    worker.join(2)
    assert not worker.is_alive()
    assert [m["content"] for m in store.history()["messages"]] == ["domanda", "risposta", "esito delega"]


def test_cancelled_reply_keeps_its_stream_owner(tmp_path):
    store = prime_session_store.PrimeSessionStore(tmp_path / "prime.json")
    stream_id = store.begin_turn("domanda")
    store.append_token(stream_id, "parziale")
    store.cancel_turn(stream_id)
    reply = store.history()["messages"][-1]
    assert reply["stream_id"] == stream_id
    assert reply["reply_to_index"] == 0


class Handler:
    def __init__(self):
        self.wfile = io.BytesIO()

    def send_response(self, status):
        assert status == 200

    def send_header(self, name, value):
        pass

    def end_headers(self):
        pass


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    store = prime_session_store.PrimeSessionStore(tmp_path / "prime.json")
    monkeypatch.setattr(prime_session_store, "get_prime_session_store", lambda sid: store)
    monkeypatch.setattr(routes, "_sse_set_write_deadline", lambda handler: None)
    monkeypatch.setattr(routes, "_bridge_prime_start_attention_relays", lambda *args: lambda: None)
    return store, Handler()


@pytest.mark.parametrize("failure", [BrokenPipeError, ConnectionResetError, TimeoutError])
@pytest.mark.parametrize("failed_event", ["status", "token"])
def test_disconnect_keeps_provider_running_and_persists_full_reply(bridge, monkeypatch, failure, failed_event):
    store, handler = bridge
    writes = []

    def disconnected_sse(handler, event, payload):
        writes.append(event)
        if event == failed_event:
            raise failure("observer gone")

    monkeypatch.setattr("api.streaming._sse", disconnected_sse)

    def reply(*args, on_token, on_status, **kwargs):
        on_status({"state": "reasoning"})
        on_token("Prima ")
        assert store.live()["active"]
        on_token("seconda parte")
        return {"reply": "Prima seconda parte", "usage": {"output_tokens": 4}}

    monkeypatch.setattr(routes, "_call_hermes_prime_reply_for_bridge", reply)
    assert routes._handle_bridge_prime(handler, {"message": "continua"})
    expected = ["started"] + (["status"] if failed_event == "status" else ["status", "token"])
    assert writes == expected
    history = prime_session_store.PrimeSessionStore(store.path).history()
    assert [m["content"] for m in history["messages"]] == ["continua", "Prima seconda parte"]
    assert history["messages"][-1]["usage"] == {"output_tokens": 4}
    assert not store.live()["active"]
    journal = json.loads(store.path.read_text(encoding="utf-8"))["journal"]
    assert not any(e["event"] == "turn_error" for e in journal)


def test_quiet_turn_has_heartbeat_and_thread_stops(bridge, monkeypatch):
    store, handler = bridge
    beat = threading.Event()

    class ObservedWriter(io.BytesIO):
        def write(self, value):
            if value == b": keepalive\n\n":
                beat.set()
            return super().write(value)

    handler.wfile = ObservedWriter()
    monkeypatch.setattr(routes, "_SSE_HEARTBEAT_INTERVAL_SECONDS", 0.01)

    def quiet_reply(*args, **kwargs):
        assert beat.wait(1), "quiet provider must not leave the HTTP stream idle"
        return {"reply": "completato"}

    monkeypatch.setattr(routes, "_call_hermes_prime_reply_for_bridge", quiet_reply)
    routes._handle_bridge_prime(handler, {"message": "attendi"})
    assert b": keepalive\n\n" in handler.wfile.getvalue()
    assert not any(t.name == "bridge-prime-heartbeat" for t in threading.enumerate())
    assert store.history()["messages"][-1]["content"] == "completato"


def test_provider_error_after_disconnect_remains_a_real_turn_error(bridge, monkeypatch):
    store, handler = bridge

    def disconnected(*args):
        raise BrokenPipeError("browser gone")

    monkeypatch.setattr("api.streaming._sse", disconnected)

    def failed_reply(*args, on_token, **kwargs):
        on_token("parziale")
        raise RuntimeError("provider failed")

    monkeypatch.setattr(routes, "_call_hermes_prime_reply_for_bridge", failed_reply)
    routes._handle_bridge_prime(handler, {"message": "prova"})
    journal = json.loads(store.path.read_text(encoding="utf-8"))["journal"]
    errors = [e for e in journal if e["event"] == "turn_error"]
    assert errors[-1]["error"] == "provider failed"


def test_concurrent_posts_queue_before_beginning_a_second_durable_turn(bridge, monkeypatch):
    store, _ = bridge
    first_started = threading.Event()
    release_first = threading.Event()
    second_queued = threading.Event()
    handlers = [Handler(), Handler()]
    original_sse = __import__("api.streaming", fromlist=["_sse"])._sse

    def observe_sse(handler, event, payload):
        if event == "status" and payload.get("state") == "queued":
            second_queued.set()
        return original_sse(handler, event, payload)

    monkeypatch.setattr("api.streaming._sse", observe_sse)

    def fake_reply(message, workspace, *, on_token, **kwargs):
        if message == "uno":
            first_started.set()
            assert release_first.wait(2)
        on_token(message)
        return {"reply": "risposta " + message, "usage": {}}

    monkeypatch.setattr(routes, "_call_hermes_prime_reply_for_bridge", fake_reply)
    errors = []

    def handle(index, message):
        try:
            routes._handle_bridge_prime(handlers[index], {"message": message})
        except Exception as exc:  # surface thread failures in the assertion thread
            errors.append(exc)

    first = threading.Thread(target=handle, args=(0, "uno"))
    second = threading.Thread(target=handle, args=(1, "due"))
    first.start()
    assert first_started.wait(1)
    second.start()
    assert second_queued.wait(1)
    assert [m["content"] for m in store.history()["messages"]] == ["uno"]
    release_first.set()
    first.join(2)
    second.join(2)
    assert not first.is_alive() and not second.is_alive()
    assert errors == []

    messages = store.history()["messages"]
    assert [m["content"] for m in messages] == ["uno", "risposta uno", "due", "risposta due"]
    assert messages[0]["stream_id"] == messages[1]["stream_id"]
    assert messages[2]["stream_id"] == messages[3]["stream_id"]
    assert messages[1]["reply_to_index"] == 0
    assert messages[3]["reply_to_index"] == 2
