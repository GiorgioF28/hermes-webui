from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from api import identity, prime_delegation, prime_session_store, routes


def test_identity_defaults_to_giorgio_without_access_header():
    resolved = identity.resolve_email(None, tom_email="partner@example.test")
    assert resolved.user == "giorgio"
    assert resolved.prime_session_id == "hermes-prime"


def test_identity_matches_giorgio_case_insensitively():
    resolved = identity.resolve_email(
        "  GIORGIO_FALCONE@YAHOO.IT  ",
        tom_email="partner@example.test",
    )
    assert resolved.user == "giorgio"
    assert resolved.prime_session_id == "hermes-prime"


def test_identity_matches_runtime_tom_email_case_insensitively(monkeypatch):
    monkeypatch.setenv("HERMES_TOM_EMAIL", "partner@example.test")
    resolved = identity.resolve_email("PARTNER@EXAMPLE.TEST")
    assert resolved.user == "tom"
    assert resolved.prime_session_id == "hermes-prime-tom"


def test_identity_rejects_unknown_access_email():
    with pytest.raises(identity.UnknownHermesUser):
        identity.resolve_email("unknown@example.test", tom_email="partner@example.test")


def test_unknown_access_email_returns_403_before_route_dispatch(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        routes,
        "j",
        lambda handler, payload, status=200, **kwargs: captured.update(
            status=status, payload=payload
        ),
    )
    handler = SimpleNamespace(
        headers={identity.ACCESS_EMAIL_HEADER: "unknown@example.test"}
    )
    assert routes.handle_get(handler, SimpleNamespace(path="/api/bridge/prime/history")) is True
    assert captured["status"] == 403
    assert "not authorized" in captured["payload"]["error"]


def test_prime_session_stores_are_isolated_and_giorgio_path_is_unchanged(monkeypatch, tmp_path):
    giorgio_path = tmp_path / "_bridge_prime_session.json"
    giorgio_store = prime_session_store.PrimeSessionStore(giorgio_path)
    monkeypatch.setattr(prime_session_store, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(prime_session_store, "_STORE", giorgio_store)
    monkeypatch.setattr(
        prime_session_store,
        "_STORES",
        {prime_session_store.PRIME_SESSION_ID: giorgio_store},
    )

    tom_store = prime_session_store.get_prime_session_store("hermes-prime-tom")
    giorgio_store.begin_turn("giorgio only")
    tom_store.begin_turn("tom only")

    giorgio_history = giorgio_store.history()
    tom_history = tom_store.history()
    assert giorgio_store.path == giorgio_path
    assert tom_store.path == tmp_path / "_bridge_prime_session_tom.json"
    assert giorgio_history["session_id"] == "hermes-prime"
    assert tom_history["session_id"] == "hermes-prime-tom"
    assert [m["content"] for m in giorgio_history["messages"]] == ["giorgio only"]
    assert [m["content"] for m in tom_history["messages"]] == ["tom only"]


def test_tom_prompt_is_czech_visionbuilts_only_and_giorgio_default_is_stable(monkeypatch, tmp_path):
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "project-inventory.csv").write_text(
        "project_id,name,status,business_goal,next_action\n"
        "visionbuilts-console,VisionBuilts Console,active,Ship ebooks,Test PDF\n"
        "vending-machine,Vending Machine,active,Buy machines,Send RFQ\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_PRIME_CONTEXT_PROFILE", "unlocked")
    monkeypatch.setattr("api.memory_retrieval.find_prime_memory_dir", lambda: None)

    assert routes._hermes_prime_system_prompt(tmp_path) == routes._hermes_prime_system_prompt(
        tmp_path, user="giorgio"
    )
    tom_prompt = routes._hermes_prime_system_prompt(tmp_path, user="tom")
    assert "výhradně česky" in tom_prompt
    assert "VisionBuilts Console" in tom_prompt
    assert "Vending Machine" not in tom_prompt
    assert "Nikdy nemaž" in tom_prompt
    assert "Nikdy neodhaluj" in tom_prompt


def test_tom_prompt_keeps_shared_memory_in_italian_and_chat_history_isolated(tmp_path):
    prompt = routes._hermes_prime_system_prompt(tmp_path, user="tom")
    assert "výhradně v italštině" in prompt
    assert "přelož do češtiny" in prompt
    assert "historii do paměti nekopíruj" in prompt


def test_tom_delegations_impose_italian_memory_canon():
    task = prime_delegation._task_with_session_rules(
        "Proveď úkol", "hermes-prime-tom"
    )
    assert "esclusivamente in italiano" in task
    assert "history ceca di Tom" in task
    assert task.endswith("Proveď úkol")
    assert prime_delegation._task_with_session_rules(
        "Task Giorgio", "hermes-prime"
    ) == "Task Giorgio"


def test_background_delegation_cards_are_filtered_by_prime_session(monkeypatch):
    monkeypatch.setattr(
        prime_delegation,
        "_BG_TASKS",
        {
            "d-g": {
                "id": "d-g", "session_id": "hermes-prime", "status": "in_corso",
                "agent": "a", "task_type": "codice", "task": "g",
                "started": 1.0, "finished": None,
            },
            "d-t": {
                "id": "d-t", "session_id": "hermes-prime-tom", "status": "in_corso",
                "agent": "a", "task_type": "codice", "task": "t",
                "started": 2.0, "finished": None,
            },
        },
    )
    giorgio = prime_delegation.get_background_tasks(session_id="hermes-prime")
    tom = prime_delegation.get_background_tasks(session_id="hermes-prime-tom")
    assert [task["id"] for task in giorgio] == ["d-g"]
    assert [task["id"] for task in tom] == ["d-t"]


def test_giorgio_and_tom_delegations_share_one_ordered_execution_queue(monkeypatch):
    async def exercise():
        monkeypatch.setattr(prime_delegation, "_DELEGATION_EXECUTION_LOCK", asyncio.Lock())
        active = 0
        max_active = 0
        order = []

        async def fake_serial(task_id, *_args):
            nonlocal active, max_active
            order.append(task_id)
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1

        monkeypatch.setattr(prime_delegation, "_run_and_store_serial", fake_serial)
        await asyncio.gather(
            prime_delegation._run_and_store("giorgio", "codice", "a", "m", "l", "."),
            prime_delegation._run_and_store("tom", "codice", "b", "m", "l", "."),
        )
        assert order == ["giorgio", "tom"]
        assert max_active == 1

    asyncio.run(exercise())
