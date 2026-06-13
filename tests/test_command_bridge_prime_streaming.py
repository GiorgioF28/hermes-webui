import io
import json
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
            yield StreamMessage()
            yield StreamMessageTwo()

    class FakeRegistry:
        def get_or_create(self, session_id, **kwargs):
            assert session_id == "hermes-prime"

        def run_turn(self, session_id, drive, timeout):
            import asyncio

            assert session_id == "hermes-prime"
            assert timeout == 120
            return asyncio.run(drive(FakeClient()))

    monkeypatch.setattr(routes, "_get_claude_registry", lambda: FakeRegistry())
    monkeypatch.setattr(
        "api.prime_delegation.get_background_tasks",
        lambda: [{"id": "prime-1", "status": "in_corso"}],
    )
    deltas = []

    result = routes._hermes_prime_reply(
        " stato   di oggi ",
        Path("."),
        on_token=deltas.append,
    )

    assert deltas == ["Ciao ", "Giorgio"]
    assert result == {
        "reply": "Ciao Giorgio",
        "delegations": [{"id": "prime-1", "status": "in_corso"}],
    }


def test_bridge_prime_post_streams_tokens_then_done(monkeypatch):
    handler = _Handler()
    monkeypatch.setattr(routes, "_sse_set_write_deadline", lambda _handler: None)

    def fake_reply(message, workspace, on_token):
        assert message == "brief"
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
        ("token", {"text": "Prima "}),
        ("token", {"text": "parte"}),
        (
            "done",
            {
                "reply": "Prima parte",
                "delegations": [{"id": "prime-2", "status": "in_corso"}],
            },
        ),
    ]


def test_prime_reply_finalizes_gracefully_on_turn_timeout(monkeypatch):
    # Un turno che sfora i 120s solleva TimeoutError da fut.result(): NON deve
    # propagarsi (verrebbe scambiato per client disconnesso), ma restituire ciò
    # che è stato prodotto. Qui non è stato prodotto nulla -> messaggio chiaro.
    class FakeRegistry:
        def get_or_create(self, session_id, **kwargs):
            assert session_id == "hermes-prime"

        def run_turn(self, session_id, drive, timeout):
            assert timeout == 120
            raise TimeoutError("turn exceeded 120s")

    monkeypatch.setattr(routes, "_get_claude_registry", lambda: FakeRegistry())
    monkeypatch.setattr("api.prime_delegation.get_background_tasks", lambda: [])
    deltas = []

    result = routes._hermes_prime_reply("brief", Path("."), on_token=deltas.append)

    assert result["reply"]  # non vuoto: l'utente riceve un messaggio leggibile
    assert deltas and deltas[0] == result["reply"]  # ed è stato anche streamato
    assert result["delegations"] == []


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
    assert "token: function (d) { showToken(d && d.text); }" in source
    assert "done: function (d)" in source
    assert "if (!reply && ph && ph.parentNode)" in source
    assert "api('api/bridge/tasks')" in source
    assert "setInterval(pollTasks, 3000)" in source
