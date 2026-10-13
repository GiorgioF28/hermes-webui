"""Stop owns one request; progress contains metadata, never tool payloads."""
import threading
import time
from unittest.mock import MagicMock

import pytest
from api import codex_prime as cp, routes, prime_session_store
from api.prime_turn_queue import PrimeTurnQueue
from tests.test_prime_codex_capacity import runner, FINAL  # noqa: F401
from tests.test_prime_transport_continuity import bridge  # noqa: F401


def test_native_activity_and_retry_status_never_include_tool_payload(runner):
    responses, commands, processes, registered, revoked, statuses, tokens, cancel, run = runner
    responses.append(([
        {"type": "item.started", "item": {"type": "command_execution", "command": "secret value"}},
        {"type": "item.completed", "item": {"type": "command_execution", "aggregated_output": "secret value"}},
        {"type": "item.started", "item": {"type": "mcp_tool_call", "tool": "team_status", "arguments": {"secret": "value"}}},
        {"type": "prime.connection_retry"}, *FINAL], 0))
    run()
    assert {"state": "tool", "tool": "comando locale"} in statuses
    assert {"state": "tool", "tool": "team_status"} in statuses
    assert {"state": "connection_retry"} in statuses
    assert "secret" not in str(statuses)


def test_cancel_waiting_request_does_not_start_or_displace_owner():
    gate = PrimeTurnQueue()
    gate.acquire()
    ticket = gate.reserve()
    cancel = threading.Event()
    result = []
    worker = threading.Thread(target=lambda: result.append(gate.acquire_reserved(ticket, cancel_event=cancel)))
    worker.start()
    cancel.set()
    worker.join(1)
    assert result == [False]
    assert not gate.acquire(blocking=False)
    gate.release()
    assert gate.acquire(blocking=False)
    gate.release()


def test_stop_before_provider_registration_reuses_cancel_event(monkeypatch):
    sid = "early-stop"
    event = threading.Event()
    routes._prime_active_set(sid, stream_id="owned", cancel_event=event)
    assert routes._request_prime_cancel("owned", sid)
    assert routes._prime_cancel_event(sid, "owned") is event
    assert event.is_set()
    assert routes._prime_cancel_event(sid, "other") is not event
    routes._prime_active_clear("owned", sid)


def test_queued_stop_does_not_cancel_another_active_turn(monkeypatch, tmp_path):
    store = prime_session_store.PrimeSessionStore(tmp_path / "prime.json")
    sid = "queued-stop"
    active_id = store.begin_turn("active")
    active = threading.Event()
    waiting = threading.Event()
    routes._prime_active_set(sid, stream_id=active_id, cancel_event=active)
    routes._PRIME_BROWSER_REQUESTS[(sid, "queued")] = {"cancel_event": waiting, "stream_id": None}
    monkeypatch.setattr(routes, "_request_prime_session_id", lambda _: sid)
    monkeypatch.setattr(prime_session_store, "get_prime_session_store", lambda _: store)
    monkeypatch.setattr(routes, "j", lambda _, payload: payload)
    try:
        result = routes._handle_bridge_prime_cancel(object(), {"request_id": "queued"})
        assert result["queued"] and result["cancelled"]
        assert waiting.is_set() and not active.is_set()
        assert store.live()["pending_turn"]["stream_id"] == active_id
    finally:
        routes._PRIME_BROWSER_REQUESTS.pop((sid, "queued"), None)
        routes._prime_active_clear(active_id, sid)


def test_stop_during_started_callback_prevents_provider_launch(bridge, monkeypatch):
    store, handler = bridge
    def sse(_, kind, payload):
        if kind == "started":
            routes._request_prime_cancel(payload["stream_id"])
    monkeypatch.setattr("api.streaming._sse", sse)
    observed = []
    def reply(*args, stream_id, **kwargs):
        event = routes._prime_cancel_event("hermes-prime", stream_id)
        observed.append(event.is_set())
        return {"reply": "", "cancelled": event.is_set()}
    monkeypatch.setattr(routes, "_call_hermes_prime_reply_for_bridge", reply)
    routes._handle_bridge_prime(handler, {"message": "Stop", "request_id": "early"})
    assert observed == [True]
    assert not store.live()["active"]
    assert not routes._PRIME_BROWSER_REQUESTS
    assert not routes._prime_active_snapshot()


def test_stop_before_http_post_prevents_late_request_launch(bridge, monkeypatch):
    store, handler = bridge
    monkeypatch.setattr(routes, "_request_prime_session_id", lambda _: "hermes-prime")
    monkeypatch.setattr(routes, "j", lambda _, payload: payload)
    stopped = routes._handle_bridge_prime_cancel(object(), {"request_id": "late-post"})
    assert stopped["pending_start"]
    provider = MagicMock(side_effect=AssertionError("cancelled request launched"))
    monkeypatch.setattr(routes, "_call_hermes_prime_reply_for_bridge", provider)
    routes._handle_bridge_prime(handler, {"message": "late", "request_id": "late-post"})
    provider.assert_not_called()
    assert store.live()["message_count"] == 0
    assert not routes._PRIME_BROWSER_REQUESTS
    assert routes._prime_bridge_turn_lock("hermes-prime").acquire(blocking=False)
    routes._prime_bridge_turn_lock("hermes-prime").release()


def test_blocked_prompt_pipe_does_not_block_stop(monkeypatch, tmp_path):
    from api import prime_delegation
    entered = threading.Event()
    released = threading.Event()
    cancelled = threading.Event()
    stopped = []
    class Input:
        def write(self, text):
            entered.set()
            released.wait(3)
        def close(self): pass
    class Proc:
        stdin = Input()
        stderr = iter(())
        @property
        def stdout(self):
            released.wait(3)
            return iter(())
    class Runtime:
        url = "http://127.0.0.1:1/mcp"
        def register(self, *args): return "isolated"
        def revoke(self, token): pass
    monkeypatch.setattr(cp, "get_runtime", lambda: Runtime())
    monkeypatch.setattr(cp, "build_tools", lambda *args: [])
    monkeypatch.setattr(cp.subprocess, "Popen", lambda *args, **kwargs: Proc())
    monkeypatch.setattr(prime_delegation, "_resolve_codex_executable", lambda: "isolated")
    monkeypatch.setattr(routes, "get_clarify_pending_count", lambda _: 0)
    def stop(proc):
        stopped.append(proc)
        released.set()
    monkeypatch.setattr(cp, "stop_process", stop)
    results = []
    worker = threading.Thread(target=lambda: results.append(cp.run_prime("prompt", tmp_path,
        session_id="pipe-test", cancel=cancelled)))
    worker.start()
    assert entered.wait(1)
    cancelled.set()
    worker.join(1)
    assert not worker.is_alive()
    assert results[0]["cancelled"]
    assert len(stopped) == 1


def test_progress_is_owned_and_available_for_recovery(bridge, monkeypatch):
    store, handler=bridge
    def reply(*args,on_status,stream_id,**kwargs):
        on_status({"state":"tool","tool":"team_status","idle_seconds":4})
        assert routes._prime_active_snapshot()["progress"]=={"state":"tool","tool":"team_status","idle_seconds":4}
        routes._prime_progress_set("hermes-prime","stale",{"state":"reasoning"})
        assert routes._prime_active_snapshot()["progress"]["state"]=="tool"
        return {"reply":"Complete"}
    monkeypatch.setattr(routes,"_call_hermes_prime_reply_for_bridge",reply)
    routes._handle_bridge_prime(handler,{"message":"Check"})
    assert not routes._prime_active_snapshot()
