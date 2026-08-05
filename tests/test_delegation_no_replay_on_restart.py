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


def test_canonical_row_in_jsonl_normalised_on_load(monkeypatch, tmp_path):
    """Fix d153 – Punto 1: riga canonica (appesa da DelegationStore.upsert) viene
    normalizzata a schema legacy quando _load_bg_tasks legge delegations.jsonl.

    Scenario: dopo il riavvio l'ultima riga per 'd150' è in schema canonico
    (status=done, result.text=..., finished_at=..., NO finished/output).
    Prima del fix: _BG_TASKS["d150"] aveva finished=None → get_background_tasks()
    non la filtrava per età → card riappariva come fantasma.
    Dopo il fix: il record viene normalizzato → status='ok', output=...,
    finished è valorizzato → il filtro max_age funziona.
    """
    from api import prime_delegation as delegation

    monkeypatch.setattr(delegation, "_BG_TASKS", {})
    monkeypatch.setattr(delegation, "_DELEGATIONS_LOADED", False)
    monkeypatch.setattr(delegation, "_TASK_SEQ", itertools.count(1))

    # Riga "canonica" come la scrive DelegationStore.upsert:
    # status=done, result={text:...}, started_at/finished_at, NO output/started/finished.
    canonical_row = {
        "id": "d150",
        "agent": "programmatore",
        "agent_id": "programmatore",
        "task_type": "codice",
        "task": "restyle solar",
        "status": "done",          # canonical: done
        "result": {
            "text": "Solar restyle completato.",
            "raw_excerpt": "Solar restyle completato.",
            "artifact_paths": [],
            "stdout_tail": "",
            "stderr_tail": "",
            "partial": False,
        },
        "started_at": 1000.0,
        "finished_at": 1100.0,     # canonical: finished_at, NO 'finished'
        "created_at": 1000.0,
        # 'output', 'started', 'finished' NON presenti
    }

    log = tmp_path / "tasks" / "delegations.jsonl"
    _write_jsonl(log, [canonical_row])

    delegation._load_bg_tasks(str(tmp_path))

    task = delegation._BG_TASKS.get("d150")
    assert task is not None, "d150 deve essere caricato in _BG_TASKS"

    # Punto 1: status deve essere normalizzato a legacy
    assert task["status"] == "ok", f"status atteso 'ok', ottenuto '{task['status']}'"

    # Punto 1: output deve essere estratto da result.text
    assert task["output"] == "Solar restyle completato.", \
        f"output atteso 'Solar restyle completato.', ottenuto '{task['output']}'"

    # Punto 1: finished deve essere valorizzato (da finished_at)
    assert task["finished"] == 1100.0, \
        f"finished atteso 1100.0, ottenuto {task['finished']!r}"

    # Punto 1: started deve essere valorizzato (da started_at)
    assert task["started"] == 1000.0, \
        f"started atteso 1000.0, ottenuto {task['started']!r}"


def test_canonical_row_filtered_by_max_age(monkeypatch, tmp_path):
    """Fix d153 – Punto 1+2: record canonico caricato al boot viene filtrato
    correttamente da get_background_tasks() una volta scaduto max_age.
    """
    from api import prime_delegation as delegation

    monkeypatch.setattr(delegation, "_BG_TASKS", {})
    monkeypatch.setattr(delegation, "_DELEGATIONS_LOADED", False)
    monkeypatch.setattr(delegation, "_TASK_SEQ", itertools.count(1))

    old_finished = time.time() - 700  # più vecchio di 600s (max_age default)
    canonical_row = {
        "id": "d151",
        "agent": "programmatore",
        "task_type": "codice",
        "task": "fix old",
        "status": "done",
        "result": {"text": "done", "raw_excerpt": "", "artifact_paths": [], "stdout_tail": "", "stderr_tail": "", "partial": False},
        "started_at": old_finished - 60,
        "finished_at": old_finished,
    }

    log = tmp_path / "tasks" / "delegations.jsonl"
    _write_jsonl(log, [canonical_row])

    delegation._load_bg_tasks(str(tmp_path))

    # get_background_tasks con max_age=600 deve escludere d151 (finita 700s fa)
    tasks = delegation.get_background_tasks(max_age=600.0)
    ids = [t["id"] for t in tasks]
    assert "d151" not in ids, \
        f"d151 (finita {700:.0f}s fa) non deve apparire nella lista (bug card fantasma)"

    # Con max_age molto alto deve includerla (verifica che il record sia caricato)
    tasks_all = delegation.get_background_tasks(max_age=99999.0)
    ids_all = [t["id"] for t in tasks_all]
    assert "d151" in ids_all, "d151 deve apparire con max_age alto"


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

    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    d128_rows = [r for r in rows if r.get("id") == "d128"]
    assert len(d128_rows) >= 1, "Deve esserci almeno una riga per d128 nel JSONL"
    # Dopo c106de14 _persist_bg_task scrive DUE righe: una legacy (status="interrotta")
    # e una canonica (status="failed" appesa da DelegationStore.upsert).
    # Verifichiamo che la riga legacy sia presente nel JSONL.
    legacy_rows = [r for r in d128_rows if r.get("status") == "interrotta"]
    assert legacy_rows, (
        f"Deve esserci almeno una riga legacy con status='interrotta'; "
        f"statuses trovati: {[r.get('status') for r in d128_rows]}"
    )
    assert legacy_rows[0].get("librarian_status") == "interrotta"


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

        def get(self, session_id):
            return None  # simula assenza sessione → system prompt calcolato

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
    # routes._PRIME_SDK_SESSION_ID non esiste più come costante pubblica;
    # la session_id usata da _drive() è client._hermes_sdk_session_id (se presente)
    # oppure "default". Qui verifichiamo solo che la query sia stata emessa.
    assert len(client.queries) >= 1, "Deve esserci almeno un turno query al client"
