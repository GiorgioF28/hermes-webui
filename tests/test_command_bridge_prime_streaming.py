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
        # Il factory reale aggancia al client l'id SDK fresco della sessione
        # (bug "Session ID already in use"): il turno deve usare QUELLO.
        _hermes_sdk_session_id = "11111111-2222-4333-8444-555555555555"

        async def query(self, message, session_id="default"):
            assert message.endswith("stato di oggi")
            assert session_id == "11111111-2222-4333-8444-555555555555"

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
        "usage": {},
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
            "usage": {
                "input_tokens": 1200,
                "output_tokens": 34,
                "cache_read_input_tokens": 800,
                "cache_creation_input_tokens": 40,
            },
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
            "usage",
            {
                "usage": {
                    "input_tokens": 1200,
                    "output_tokens": 34,
                    "cache_read_input_tokens": 800,
                    "cache_creation_input_tokens": 40,
                },
            },
        ),
        (
            "done",
            {
                "reply": "Prima parte",
                "delegations": [{"id": "prime-2", "status": "in_corso"}],
                "usage": {
                    "input_tokens": 1200,
                    "output_tokens": 34,
                    "cache_read_input_tokens": 800,
                    "cache_creation_input_tokens": 40,
                },
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
    events = _events(handler)
    assert events[0][0] == "error"
    assert events[0][1]["branch"] == "unknown"
    assert events[0][1]["detail"] == "provider down"


def test_bridge_prime_history_persists_successful_turn(monkeypatch, tmp_path):
    from api import prime_session_store

    monkeypatch.setattr(
        prime_session_store,
        "_STORE",
        prime_session_store.PrimeSessionStore(tmp_path / "prime-session.json"),
    )
    handler = _Handler()
    monkeypatch.setattr(routes, "_sse_set_write_deadline", lambda _handler: None)

    def fake_reply(message, workspace, attachments=None, on_token=None, on_status=None):
        on_token("Persistita")
        return {"reply": "Persistita", "delegations": [], "usage": {"input_tokens": 1}}

    monkeypatch.setattr(routes, "_hermes_prime_reply", fake_reply)

    assert routes._handle_bridge_prime(handler, {"message": "salva"}) is True

    hist_handler = _Handler()
    assert routes._handle_bridge_prime_history(hist_handler) is True
    history = json.loads(hist_handler.wfile.getvalue().decode("utf-8"))
    assert history["session_id"] == "hermes-prime"
    assert history["pending_turn"] is None
    assert [m["role"] for m in history["messages"]] == ["user", "assistant"]
    assert history["messages"][1]["content"] == "Persistita"


def test_bridge_prime_history_recovers_pending_partial_on_disconnect(monkeypatch, tmp_path):
    from api import prime_session_store

    monkeypatch.setattr(
        prime_session_store,
        "_STORE",
        prime_session_store.PrimeSessionStore(tmp_path / "prime-session.json"),
    )
    handler = _Handler()
    monkeypatch.setattr(routes, "_sse_set_write_deadline", lambda _handler: None)

    def broken_reply(message, workspace, attachments=None, on_token=None, on_status=None):
        on_token("parziale recuperabile")
        raise TimeoutError("bridge cut")

    monkeypatch.setattr(routes, "_hermes_prime_reply", broken_reply)

    assert routes._handle_bridge_prime(handler, {"message": "crasha"}) is True

    hist_handler = _Handler()
    routes._handle_bridge_prime_history(hist_handler)
    history = json.loads(hist_handler.wfile.getvalue().decode("utf-8"))
    pending = history["pending_turn"]
    assert pending["stream_id"]
    assert pending["partial_output"] == "parziale recuperabile"
    assert pending["recovered"] is True


def test_bridge_prime_stream_forwards_approval_and_clarify_events(monkeypatch, tmp_path):
    from api import clarify, prime_session_store

    monkeypatch.setattr(
        prime_session_store,
        "_STORE",
        prime_session_store.PrimeSessionStore(tmp_path / "prime-session.json"),
    )
    handler = _Handler()
    monkeypatch.setattr(routes, "_sse_set_write_deadline", lambda _handler: None)

    def fake_reply(message, workspace, attachments=None, on_token=None, on_status=None):
        routes.submit_pending(
            "hermes-prime",
            {"command": "Remove-Item x", "pattern_key": "danger", "description": "Danger"},
        )
        clarify.submit_pending(
            "hermes-prime",
            {"question": "Scegli?", "choices_offered": ["A", "B"]},
        )
        time.sleep(0.25)
        on_token("ok")
        return {"reply": "ok", "delegations": [], "usage": {}}

    monkeypatch.setattr(routes, "_hermes_prime_reply", fake_reply)

    assert routes._handle_bridge_prime(handler, {"message": "serve input"}) is True
    events = _events(handler)
    assert any(event == "approval" and data["pending"]["command"] == "Remove-Item x" for event, data in events)
    assert any(event == "clarify" and data["pending"]["question"] == "Scegli?" for event, data in events)


def test_command_bridge_frontend_consumes_post_sse_without_touching_task_polling():
    source = Path("static/command_bridge.js").read_text(encoding="utf-8")

    assert "function streamPrimeResponse(response, handlers)" in source
    assert "response.body.getReader()" in source
    assert "status: function (d) { showStatus(d && d.state, d && d.tool); }" in source
    assert "token: function (d) { showToken(d && d.text); }" in source
    assert "usage: function (d) { showUsage(d && d.usage); }" in source
    assert "approval: function (d) { renderBridgeApprovalCard(d); }" in source
    assert "clarify: function (d) { renderBridgeClarifyCard(d); }" in source
    assert "done: function (d)" in source
    assert "if (d && d.usage) showUsage(d.usage);" in source
    assert "if (!settled && reply) finish('\\u2713 risposta ricevuta');" in source
    assert "if (!reply && ph && ph.parentNode)" in source
    assert "api('api/bridge/tasks')" in source
    assert "setInterval(pollTasks, 3000)" in source
    # Selettore unico modello+brain (2026-08-01): la tendina ha sostituito i tre
    # bottoni CLOUD/AUTO/CODEX, ma Codex e AUTO devono restare raggiungibili --
    # sono la via di fuga quando Claude esaurisce la finestra.
    assert 'id="cbBrainSel"' in source
    assert 'value="model:claude-opus-5"' in source
    assert 'value="lead:codex"' in source
    assert 'value="action:auto"' in source
    # Fable 5 elencato ma NON selezionabile: richiede crediti a consumo.
    assert 'value="model:claude-fable-5" disabled' in source
    assert "api/bridge/prime/lead" in source
    assert "api/bridge/prime/model" in source
    assert "action: 'auto'" in source


def test_command_bridge_frontend_loads_history_and_renders_attention_cards():
    source = Path("static/command_bridge.js").read_text(encoding="utf-8")

    assert "function loadPrimeHistory()" in source
    assert "api('/api/bridge/prime/history')" in source
    assert "function renderBridgeApprovalCard(payload)" in source
    assert "function renderBridgeClarifyCard(payload)" in source
    assert "apiPost('/api/approval/respond'" in source
    assert "apiPost('/api/clarify/respond'" in source
    assert "cb-recovered" in source


def test_command_bridge_frontend_anchors_delegation_cards_and_collapses_details():
    source = Path("static/command_bridge.js").read_text(encoding="utf-8")

    assert "data-cb-msg-index" in source
    assert "anchor_message_index" in source
    assert "function placeTaskCard(card, t)" in source
    assert "log.querySelector('[data-cb-msg-index=\"" in source
    assert "cb-deleg-done" in source
    assert "cb-deleg-error" in source
    assert "cb-deleg-summary" in source
    assert "<details class=\"cb-deleg-details\">" in source


def test_command_bridge_frontend_renders_usage_quota_and_default_view():
    bridge = Path("static/command_bridge.js").read_text(encoding="utf-8")
    panels = Path("static/panels.js").read_text(encoding="utf-8")
    index = Path("static/index.html").read_text(encoding="utf-8")

    assert "function _fmtCompactTokens(value)" in bridge
    assert "function _formatAssistantUsageBadge(usage)" in bridge
    assert "function _renderTokenQuotaPill(status)" in bridge
    assert "function pollTokenQuota()" in bridge
    assert "api('/api/usage/limits')" in bridge
    assert 'id="cbQuotaPill"' in bridge
    assert 'id="cbLiveUsage"' in bridge
    assert "cb-msg-foot" in bridge
    assert "window._showTokenUsage === true" in bridge
    assert "window._showQuotaChip !== true" in bridge
    assert "let _currentPanel = 'bridge';" in panels
    assert '<main class="main showing-bridge">' in index
    assert 'data-panel="bridge" onclick="switchPanel(\'bridge\',{fromRailClick:true})" data-tooltip="Command Bridge"' in index


def test_bridge_prime_cancel_promotes_partial_and_journals(monkeypatch, tmp_path):
    from api import prime_session_store

    store = prime_session_store.PrimeSessionStore(tmp_path / "prime-session.json")
    monkeypatch.setattr(prime_session_store, "_STORE", store)
    stream_id = store.begin_turn("ferma")
    store.append_token(stream_id, "parziale vivo")

    handler = _Handler()
    assert routes._handle_bridge_prime_cancel(handler, {"stream_id": stream_id}) is None
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    hist = store.history()

    assert payload["cancelled"] is True
    assert hist["pending_turn"] is None
    assert hist["messages"][-1]["content"] == "parziale vivo"
    assert hist["messages"][-1]["interrupted"] is True
    raw = json.loads((tmp_path / "prime-session.json").read_text(encoding="utf-8"))
    assert raw["journal"][-1]["event"] == "turn_cancelled"


def test_bridge_prime_live_exposes_pending_turn(monkeypatch, tmp_path):
    from api import prime_session_store

    store = prime_session_store.PrimeSessionStore(tmp_path / "prime-session.json")
    monkeypatch.setattr(prime_session_store, "_STORE", store)
    stream_id = store.begin_turn("continua")
    store.append_token(stream_id, "token gia arrivati")

    handler = _Handler()
    assert routes._handle_bridge_prime_live(handler) is True
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))

    assert payload["active"] is True
    assert payload["stream_id"] == stream_id
    assert payload["pending_turn"]["partial_output"] == "token gia arrivati"


def test_bridge_prime_compact_endpoint_reports_tokens(monkeypatch):
    handler = _Handler()
    seen = {}

    monkeypatch.setattr(routes, "_prime_active_snapshot", lambda: {})
    monkeypatch.setattr(routes, "_estimate_prime_history_tokens", lambda: 42)
    monkeypatch.setattr(routes, "_get_claude_registry", lambda: object())

    def fake_compact(registry, *, session_id, before_tokens=0, reason="manual", **kwargs):
        seen["registry"] = registry
        seen["session_id"] = session_id
        seen["before_tokens"] = before_tokens
        seen["reason"] = reason
        return {
            "ok": True,
            "compacted": True,
            "before_tokens": before_tokens,
            "after_tokens": 12,
            "after_tokens_unknown": False,
        }

    monkeypatch.setattr("api.prime_auto_compact.compact_prime_now", fake_compact)

    assert routes._handle_bridge_prime_compact(handler, {}) is None
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert seen == {"registry": seen["registry"], "session_id": "hermes-prime", "before_tokens": 42, "reason": "manual"}
    assert payload["before_tokens"] == 42
    assert payload["after_tokens"] == 12


def test_bridge_prime_model_and_workspace_persist(monkeypatch, tmp_path):
    from api import prime_session_store

    store = prime_session_store.PrimeSessionStore(tmp_path / "prime-session.json")
    monkeypatch.setattr(prime_session_store, "_STORE", store)
    monkeypatch.setattr(
        routes,
        "_resolve_compatible_session_model_state",
        lambda model, provider, **kwargs: (model or "claude-sonnet-4-6", provider or "anthropic", False),
    )
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda value: tmp_path / str(value or "ws"))

    class Registry:
        def close(self, session_id):
            assert session_id == "hermes-prime"

    monkeypatch.setattr(routes, "_get_claude_registry", lambda: Registry())

    model_handler = _Handler()
    routes._handle_bridge_prime_model(model_handler, {"action": "set", "model": "claude-sonnet-4-6", "profile": "default"})
    model_payload = json.loads(model_handler.wfile.getvalue().decode("utf-8"))
    assert model_payload["model"] == "claude-sonnet-4-6"
    assert model_payload["model_provider"] == "anthropic"
    assert model_payload["settings"]["profile"] == "default"

    ws_handler = _Handler()
    routes._handle_bridge_prime_workspace(ws_handler, {"action": "set", "workspace": "prime-ws"})
    ws_payload = json.loads(ws_handler.wfile.getvalue().decode("utf-8"))
    assert ws_payload["workspace"].endswith("prime-ws")
    assert store.get_settings()["workspace"].endswith("prime-ws")


def test_command_bridge_frontend_p1_controls_and_slash_commands():
    source = Path("static/command_bridge.js").read_text(encoding="utf-8")

    assert 'id="cbStop"' in source
    assert "function cancelPrimeTurn()" in source
    assert "apiPost('/api/bridge/prime/cancel'" in source
    assert "api('/api/bridge/prime/live')" in source
    assert "function handlePrimeSlashCommand(text)" in source
    assert "cmd === '/compact'" in source
    assert "apiPost('/api/bridge/prime/compact'" in source
    assert "cmd === '/model'" in source
    assert "apiPost('/api/bridge/prime/model'" in source
    assert "cmd === '/workspace'" in source
    assert "apiPost('/api/bridge/prime/workspace'" in source
