import io
import json
import threading
import time
from pathlib import Path

from api import routes


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


def _events(handler):
    frames = handler.wfile.getvalue().decode("utf-8").strip().split("\n\n")
    result = []
    for frame in frames:
        event = None
        data = None
        for line in frame.splitlines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        result.append((event, data))
    return result


def test_prime_reply_emits_sdk_deltas_and_keeps_async_delegations(monkeypatch):
    class ThinkingMessage:
        event = {
            "type": "content_block_start",
            "content_block": {"type": "thinking"},
        }

    class ToolMessage:
        event = {
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "name": "Read"},
        }

    class StreamMessage:
        event = {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "Ciao "},
        }

    class StreamMessageTwo:
        event = {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "Giorgio"},
        }

    class FakeClient:
        async def query(self, message):
            assert message == "stato di oggi"

        async def receive_response(self):
            yield ThinkingMessage()
            yield ToolMessage()
            yield StreamMessage()
            yield StreamMessageTwo()

    class _DoneFuture:
        def __init__(self, value):
            self._value = value

        def result(self, timeout=None):
            return self._value

        def cancel(self):
            return False

    class FakeRegistry:
        def get_or_create(self, session_id, **kwargs):
            assert session_id == "hermes-prime"

        def submit_turn(self, session_id, drive):
            import asyncio

            assert session_id == "hermes-prime"
            return _DoneFuture(asyncio.run(drive(FakeClient())))

    monkeypatch.setattr(routes, "_get_claude_registry", lambda: FakeRegistry())
    monkeypatch.setattr(
        "api.prime_delegation.get_background_tasks",
        lambda: [{"id": "prime-1", "status": "in_corso"}],
    )
    deltas = []
    statuses = []

    result = routes._hermes_prime_reply(
        " stato   di oggi ",
        Path("."),
        on_token=deltas.append,
        on_status=statuses.append,
    )

    assert deltas == ["Ciao ", "Giorgio"]
    assert statuses == [
        {"state": "reasoning"},
        {"state": "tool", "tool": "Read"},
        {"state": "responding"},
    ]
    assert result == {
        "reply": "Ciao Giorgio",
        "delegations": [{"id": "prime-1", "status": "in_corso"}],
    }


def test_bridge_prime_post_streams_tokens_then_done(monkeypatch):
    handler = _Handler()
    monkeypatch.setattr(routes, "_sse_set_write_deadline", lambda _handler: None)

    def fake_reply(message, workspace, attachments=None, on_token=None, on_status=None):
        assert message == "brief"
        on_status({"state": "reasoning"})
        on_token("Prima ")
        on_token("parte")
        return {
            "reply": "Prima parte",
            "delegations": [{"id": "prime-2", "status": "in_corso"}],
        }

    monkeypatch.setattr(routes, "_hermes_prime_reply", fake_reply)

    assert routes._handle_bridge_prime(handler, {"message": "brief"}) is True
    assert handler.status == 200
    assert handler.headers["Content-Type"] == "text/event-stream; charset=utf-8"
    assert handler.headers["X-Accel-Buffering"] == "no"
    assert _events(handler) == [
        ("status", {"state": "reasoning"}),
        ("token", {"text": "Prima "}),
        ("token", {"text": "parte"}),
        ("status", {"state": "done"}),
        (
            "done",
            {
                "reply": "Prima parte",
                "delegations": [{"id": "prime-2", "status": "in_corso"}],
            },
        ),
    ]


def test_bridge_prime_forwards_image_attachments(monkeypatch):
    handler = _Handler()
    monkeypatch.setattr(routes, "_sse_set_write_deadline", lambda _handler: None)
    seen = {}

    def fake_reply(message, workspace, attachments=None, on_token=None, on_status=None):
        seen["message"] = message
        seen["attachments"] = attachments
        return {"reply": "ok", "delegations": []}

    monkeypatch.setattr(routes, "_hermes_prime_reply", fake_reply)

    # Solo foto, nessun testo: il messaggio diventa un placeholder e l'allegato
    # viene comunque inoltrato a Prime.
    body = {"message": "", "attachments": [{"name": "foto.png", "path": "C:/x/foto.png"}]}
    assert routes._handle_bridge_prime(handler, body) is True
    assert handler.status == 200
    assert seen["attachments"] == [{"name": "foto.png", "path": "C:/x/foto.png"}]
    assert seen["message"]  # placeholder non vuoto


def test_bridge_prime_rejects_empty_message_without_attachments(monkeypatch):
    handler = _Handler()
    monkeypatch.setattr(routes, "_sse_set_write_deadline", lambda _handler: None)
    routes._handle_bridge_prime(handler, {"message": "  "})
    assert handler.status == 400


def test_second_prime_turn_queues_without_interrupting_first(monkeypatch):
    first_started = threading.Event()
    release_first = threading.Event()
    second_submitted = threading.Event()
    statuses = {"first": [], "second": []}

    class _Future:
        def __init__(self, name):
            self.name = name

        def result(self, timeout=None):
            if self.name == "first":
                first_started.set()
                if not release_first.wait(timeout=2):
                    raise AssertionError("first turn was not released")
            else:
                second_submitted.set()

        def cancel(self):
            return False

    class FakeRegistry:
        def __init__(self):
            self.calls = 0
            self.closed = []

        def get_or_create(self, session_id, **kwargs):
            assert session_id == "hermes-prime"

        def submit_turn(self, session_id, drive):
            self.calls += 1
            return _Future("first" if self.calls == 1 else "second")

        def close(self, session_id):
            self.closed.append(session_id)

    reg = FakeRegistry()
    monkeypatch.setattr(routes, "_get_claude_registry", lambda: reg)
    monkeypatch.setattr("api.prime_delegation.get_background_tasks", lambda: [])

    threads = [
        threading.Thread(
            target=routes._hermes_prime_reply,
            args=("one", Path(".")),
            kwargs={"on_status": statuses["first"].append},
        ),
        threading.Thread(
            target=routes._hermes_prime_reply,
            args=("two", Path(".")),
            kwargs={"on_status": statuses["second"].append},
        ),
    ]
    threads[0].start()
    assert first_started.wait(timeout=1)
    threads[1].start()
    time.sleep(0.05)

    assert {"state": "queued"} in statuses["second"]
    assert not second_submitted.is_set()
    assert reg.closed == []

    release_first.set()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()

    assert second_submitted.is_set()
    assert statuses["second"][-1] == {"state": "reasoning"}
    assert reg.closed == []


def test_prime_reply_finalizes_gracefully_on_stalled_turn(monkeypatch):
    # Un turno che si blocca (nessun progresso) deve essere interrotto dal
    # watchdog anti-blocco senza propagare TimeoutError (verrebbe scambiato per
    # client disconnesso). Qui la future non completa mai e non c'è attività:
    # con cap minimo il watchdog stacca e restituisce un messaggio leggibile.
    import concurrent.futures as _futures

    monkeypatch.setenv("HERMES_PRIME_IDLE_TIMEOUT", "0.05")
    monkeypatch.setenv("HERMES_PRIME_HARD_CAP", "0.2")
    monkeypatch.setenv("HERMES_PRIME_POLL", "0.01")

    class _StuckFuture:
        def __init__(self):
            self.cancelled = False

        def result(self, timeout=None):
            raise _futures.TimeoutError()

        def cancel(self):
            self.cancelled = True
            return True

        def add_done_callback(self, cb):
            pass

    stuck = _StuckFuture()

    class FakeRegistry:
        def __init__(self):
            self.closed = []

        def get_or_create(self, session_id, **kwargs):
            assert session_id == "hermes-prime"

        def submit_turn(self, session_id, drive):
            return stuck

        def close(self, session_id):
            self.closed.append(session_id)

    reg = FakeRegistry()
    monkeypatch.setattr(routes, "_get_claude_registry", lambda: reg)
    monkeypatch.setattr("api.prime_delegation.get_background_tasks", lambda: [])
    deltas = []

    result = routes._hermes_prime_reply("brief", Path("."), on_token=deltas.append)

    assert result["reply"]  # non vuoto: l'utente riceve un messaggio leggibile
    assert deltas and deltas[0] == result["reply"]  # ed è stato anche streamato
    assert result["delegations"] == []
    assert stuck.cancelled  # il watchdog ha provato a cancellare il turno bloccato
    # ...e la sessione persistente è stata scartata, così il turno successivo
    # non eredita lo stream a metà (niente desync off-by-one).
    assert reg.closed == ["hermes-prime"]


def test_prime_reply_resets_session_when_turn_disconnects(monkeypatch):
    # Ctrl+F5 a metà stream -> on_token scrive su un socket morto e il turno
    # solleva un errore di disconnessione. La sessione persistente DEVE essere
    # scartata (così il turno dopo non eredita lo stream a metà) e l'errore
    # deve comunque propagare a _handle_bridge_prime per l'evento terminale.
    import pytest

    class _BrokenFuture:
        def result(self, timeout=None):
            raise BrokenPipeError("client gone (Ctrl+F5)")

        def cancel(self):
            return False

        def add_done_callback(self, cb):
            pass

    class FakeRegistry:
        def __init__(self):
            self.closed = []

        def get_or_create(self, session_id, **kwargs):
            assert session_id == "hermes-prime"

        def submit_turn(self, session_id, drive):
            return _BrokenFuture()

        def close(self, session_id):
            self.closed.append(session_id)

    reg = FakeRegistry()
    monkeypatch.setattr(routes, "_get_claude_registry", lambda: reg)
    monkeypatch.setattr("api.prime_delegation.get_background_tasks", lambda: [])

    with pytest.raises(BrokenPipeError):
        routes._hermes_prime_reply("brief", Path("."))

    assert reg.closed == ["hermes-prime"]  # sessione scartata: niente off-by-one


def test_bridge_prime_post_emits_terminal_event_on_disconnect_error(monkeypatch):
    # Se il reply solleva un errore "tipo disconnessione" (es. TimeoutError o
    # pipe del bridge che cade) il handler non deve restare in silenzio: prova
    # a chiudere lo stream con un evento terminale.
    handler = _Handler()
    monkeypatch.setattr(routes, "_sse_set_write_deadline", lambda _handler: None)
    monkeypatch.setattr(
        routes,
        "_hermes_prime_reply",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("bridge pipe")),
    )

    assert routes._handle_bridge_prime(handler, {"message": "brief"}) is True
    events = _events(handler)
    assert events and events[-1][0] == "error"  # mai silenzio: c'è un terminale


def test_bridge_prime_post_reports_failures_as_sse(monkeypatch):
    handler = _Handler()
    monkeypatch.setattr(routes, "_sse_set_write_deadline", lambda _handler: None)
    monkeypatch.setattr(
        routes,
        "_hermes_prime_reply",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("provider down")),
    )

    assert routes._handle_bridge_prime(handler, {"message": "brief"}) is True
    assert handler.status == 200
    assert _events(handler) == [("error", {"error": "provider down"})]


def test_command_bridge_frontend_consumes_post_sse_without_touching_task_polling():
    source = Path("static/command_bridge.js").read_text(encoding="utf-8")

    assert "function streamPrimeResponse(response, handlers)" in source
    assert "response.body.getReader()" in source
    assert "status: function (d) { showStatus(d && d.state, d && d.tool); }" in source
    assert "token: function (d) { showToken(d && d.text); }" in source
    assert "done: function (d)" in source
    assert "if (!settled && reply) finish('\\u2713 risposta ricevuta');" in source
    assert "if (!reply && ph && ph.parentNode)" in source
    assert "api('api/bridge/tasks')" in source
    assert "setInterval(pollTasks, 3000)" in source
    assert 'id="cbBrainClaude"' in source
    assert 'id="cbBrainCodex"' in source
    assert "api/bridge/prime/lead" in source
    assert "action: 'auto'" in source
