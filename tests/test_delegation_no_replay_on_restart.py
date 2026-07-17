import asyncio
import itertools
import json
import time
from concurrent.futures import Future


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_restart_load_marks_delegation_and_librarian_interrupted(monkeypatch, tmp_path):
    from api import prime_delegation as delegation

    monkeypatch.setattr(delegation, "_BG_TASKS", {})
    monkeypatch.setattr(delegation, "_DELEGATIONS_LOADED", False)
    monkeypatch.setattr(delegation, "_TASK_SEQ", itertools.count(1))

    log = tmp_path / "tasks" / "delegations.jsonl"
    _write_jsonl(
        log,
        [
            {
                "id": "d128",
                "agent": "programmatore",
                "agent_id": "programmatore",
                "task_type": "codice",
                "task": "implementa spec",
                "status": "in_corso",
                "output": "parziale",
                "started": 123.0,
                "finished": None,
                "librarian_status": "in_corso",
                "librarian_output": "",
                "runtime": "codex",
            }
        ],
    )

    delegation._load_bg_tasks(str(tmp_path))

    task = delegation._BG_TASKS["d128"]
    assert task["status"] == "interrotta"
    assert task["finished"] == 123.0
    assert task["librarian_status"] == "interrotta"
    assert "non ri-eseguire" in task["librarian_output"]
    assert next(delegation._TASK_SEQ) == 129

    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["id"] == "d128"
    assert rows[-1]["status"] == "interrotta"
    assert rows[-1]["librarian_status"] == "interrotta"


def test_prime_session_store_closes_stale_pending_turn_on_load(tmp_path):
    from api.prime_session_store import PrimeSessionStore

    path = tmp_path / "prime-session.json"
    path.write_text(
        json.dumps(
            {
                "session_id": "hermes-prime",
                "created_at": time.time(),
                "updated_at": time.time(),
                "messages": [{"role": "user", "content": "delega questo"}],
                "pending_turn": {
                    "stream_id": "stale-stream",
                    "message": "delega questo",
                    "started_at": time.time() - 100,
                    "partial_output": "Ho avviato la delega.",
                    "recovered": False,
                },
                "journal": [
                    {
                        "event": "tool_call",
                        "stream_id": "stale-stream",
                        "tool": "delega",
                        "summary": "d128 implementa spec",
                    }
                ],
                "settings": {},
            }
        ),
        encoding="utf-8",
    )

    store = PrimeSessionStore(path)
    history = store.history()

    assert history["pending_turn"] is None
    assert history["messages"][-1]["role"] == "assistant"
    assert history["messages"][-1]["interrupted"] is True
    assert history["messages"][-1]["content"] == "Ho avviato la delega."
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["journal"][-1]["event"] == "turn_interrupted_on_restart"


def test_first_prime_turn_after_restart_does_not_create_new_delegation(monkeypatch, tmp_path):
    from api import prime_delegation as delegation
    from api import routes
    from api import prime_auto_compact

    monkeypatch.setattr(delegation, "_BG_TASKS", {
        "d128": {
            "id": "d128",
            "session_id": "hermes-prime",
            "agent": "programmatore",
            "agent_id": "programmatore",
            "task_type": "codice",
            "task": "implementa spec",
            "status": "interrotta",
            "output": "parziale",
            "started": 123.0,
            "finished": 123.0,
        }
    })
    monkeypatch.setattr(prime_auto_compact, "maybe_auto_compact_prime", lambda *a, **k: None)
    monkeypatch.setattr(prime_auto_compact, "pop_compact_after_task", lambda: False)
    monkeypatch.setattr("api.memory_retrieval.build_prime_memory_context", lambda *a, **k: "")

    class ResultMessage:
        result = "Turno pulito."

    class FakeClient:
        def __init__(self):
            self.queries = []

        async def query(self, prompt, session_id="default"):
            self.queries.append((prompt, session_id))

        async def receive_response(self):
            yield ResultMessage()

    client = FakeClient()

    class FakeRegistry:
        def __init__(self):
            self.closed = []

        def get_or_create(self, session_id, **kwargs):
            assert session_id == "hermes-prime"
            return client

        def submit_turn(self, session_id, coro_factory):
            fut = Future()
            try:
                asyncio.run(coro_factory(client))
            except Exception as exc:  # pragma: no cover - preserves Future semantics
                fut.set_exception(exc)
            else:
                fut.set_result(None)
            return fut

        def close(self, session_id):
            self.closed.append(session_id)

    monkeypatch.setattr(routes, "_get_claude_registry", lambda: FakeRegistry())

    result = routes._hermes_prime_reply_claude(
        "prossimo turno dopo riavvio",
        tmp_path,
        on_token=lambda _text: None,
        on_status=lambda _status: None,
        model_state={"model": "claude-fable-5"},
        stream_id="post-restart",
    )

    assert result["reply"] == "Turno pulito."
    assert list(delegation._BG_TASKS) == ["d128"]
    assert delegation._BG_TASKS["d128"]["status"] == "interrotta"
    assert client.queries[0][1] == routes._PRIME_SDK_SESSION_ID
    assert client.queries[0][1] != "default"
