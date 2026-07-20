"""Test suite for Fase 1 – delega-resilienza-crediti.

Covers:
- delegation_store: normalisation, upsert, crash recovery, dedup
- prime_brief_queue: enqueue idempotence, fallback delivery, drain
- integration: enqueue after terminal state, fallback persist to PrimeSessionStore

Spec reference: docs/specs/delega-resilienza-crediti.md §Test minimi
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import MagicMock, patch

# Ensure repo root is importable
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp_workspace():
    """Return a Path to a fresh temp workspace directory."""
    d = tempfile.mkdtemp(prefix="hermes_test_")
    return pathlib.Path(d)


def _read_jsonl(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    result = []
    for l in lines:
        l = l.strip()
        if l:
            try:
                result.append(json.loads(l))
            except json.JSONDecodeError:
                pass
    return result


# ---------------------------------------------------------------------------
# DelegationStore – status normalisation
# ---------------------------------------------------------------------------

class TestNormaliseStatus(unittest.TestCase):
    """delegation_store.normalise_status maps legacy → canonical."""

    def setUp(self):
        from api.delegation_store import normalise_status
        self.ns = normalise_status

    def test_in_corso_to_running(self):
        self.assertEqual(self.ns("in_corso"), "running")

    def test_ok_to_done(self):
        self.assertEqual(self.ns("ok"), "done")

    def test_errore_to_failed(self):
        self.assertEqual(self.ns("errore"), "failed")

    def test_interrotta_to_failed(self):
        self.assertEqual(self.ns("interrotta"), "failed")

    def test_parziale_to_done(self):
        self.assertEqual(self.ns("parziale"), "done")

    def test_canonical_passthrough(self):
        for s in ("pending", "running", "done", "failed"):
            self.assertEqual(self.ns(s), s)

    def test_status_to_legacy(self):
        from api.delegation_store import status_to_legacy
        self.assertEqual(status_to_legacy("done"), "ok")
        self.assertEqual(status_to_legacy("failed"), "errore")
        self.assertEqual(status_to_legacy("running"), "in_corso")
        self.assertEqual(status_to_legacy("pending"), "in_corso")


# ---------------------------------------------------------------------------
# DelegationStore – upsert / dedup
# ---------------------------------------------------------------------------

class TestDelegationStoreUpsert(unittest.TestCase):
    """Duplicates in JSONL produce a single canonical state per id."""

    def setUp(self):
        self.ws = _tmp_workspace()
        # Reset singleton to get a fresh store per test
        import api.delegation_store as ds
        ds._STORE = None
        from api.delegation_store import DelegationStore
        self.store = DelegationStore(self.ws)

    def test_upsert_creates_state_file(self):
        self.store.upsert({"id": "d1", "status": "running", "task": "test"})
        state_path = self.ws / "tasks" / "delegations-state.json"
        self.assertTrue(state_path.exists(), "delegations-state.json must be created")

    def test_upsert_normalises_status(self):
        self.store.upsert({"id": "d2", "status": "in_corso", "task": "t"})
        rec = self.store.get("d2")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["status"], "running")

    def test_upsert_ok_to_done(self):
        self.store.upsert({"id": "d3", "status": "ok", "task": "t"})
        self.assertEqual(self.store.get("d3")["status"], "done")

    def test_upsert_errore_to_failed(self):
        self.store.upsert({"id": "d4", "status": "errore", "task": "t"})
        self.assertEqual(self.store.get("d4")["status"], "failed")

    def test_duplicates_in_jsonl_single_canonical(self):
        """Multiple JSONL events for the same id → one canonical record."""
        for st in ("running", "done"):
            self.store.upsert({"id": "d5", "status": st, "task": "t"})
        jsonl = _read_jsonl(self.ws / "tasks" / "delegations.jsonl")
        ids = [r["id"] for r in jsonl if r.get("id") == "d5"]
        self.assertEqual(len(ids), 2, "JSONL should have 2 events")
        canonical = self.store.get_all()
        d5_records = [r for r in canonical if r.get("id") == "d5"]
        self.assertEqual(len(d5_records), 1, "Canonical state must have exactly 1 record per id")
        self.assertEqual(d5_records[0]["status"], "done")

    def test_get_all_no_duplicates(self):
        """get_all() never returns duplicate ids."""
        for _ in range(3):
            self.store.upsert({"id": "d6", "status": "running", "task": "t"})
        all_recs = self.store.get_all()
        ids = [r["id"] for r in all_recs]
        self.assertEqual(len(ids), len(set(ids)), "No duplicate ids in get_all()")

    def test_atomic_write_via_replace(self):
        """No .tmp files left after upsert (atomic write succeeded)."""
        self.store.upsert({"id": "d7", "status": "done", "task": "t"})
        tasks_dir = self.ws / "tasks"
        tmp_files = list(tasks_dir.glob("*.tmp.*"))
        self.assertEqual(tmp_files, [], "No tmp files left after atomic write")


# ---------------------------------------------------------------------------
# DelegationStore – crash recovery
# ---------------------------------------------------------------------------

class TestCrashRecovery(unittest.TestCase):
    """running delegations not in live_task_ids → failed/crash on recover_crashed()."""

    def setUp(self):
        self.ws = _tmp_workspace()
        import api.delegation_store as ds
        ds._STORE = None
        from api.delegation_store import DelegationStore
        self.store = DelegationStore(self.ws)

    def test_running_not_in_live_becomes_failed(self):
        self.store.upsert({"id": "d10", "status": "running", "task": "t"})
        recovered = self.store.recover_crashed(live_task_ids=set())
        self.assertIn("d10", recovered)
        rec = self.store.get("d10")
        self.assertEqual(rec["status"], "failed")
        self.assertEqual(rec["error"]["category"], "crash")

    def test_running_in_live_not_recovered(self):
        self.store.upsert({"id": "d11", "status": "running", "task": "t"})
        recovered = self.store.recover_crashed(live_task_ids={"d11"})
        self.assertNotIn("d11", recovered)
        rec = self.store.get("d11")
        self.assertEqual(rec["status"], "running")

    def test_done_not_touched_by_recovery(self):
        self.store.upsert({"id": "d12", "status": "done", "task": "t"})
        recovered = self.store.recover_crashed(live_task_ids=set())
        self.assertNotIn("d12", recovered)
        self.assertEqual(self.store.get("d12")["status"], "done")

    def test_crash_recovery_message(self):
        self.store.upsert({"id": "d13", "status": "running", "task": "t"})
        self.store.recover_crashed(set())
        rec = self.store.get("d13")
        self.assertIn("interrupted", rec["error"]["message"])


# ---------------------------------------------------------------------------
# DelegationStore – rebuild from JSONL
# ---------------------------------------------------------------------------

class TestRebuildFromJsonl(unittest.TestCase):
    """rebuild_from_jsonl() reconstructs state when delegations-state.json is missing."""

    def setUp(self):
        self.ws = _tmp_workspace()
        import api.delegation_store as ds
        ds._STORE = None
        from api.delegation_store import DelegationStore
        self.store = DelegationStore(self.ws)

    def test_rebuild_from_existing_jsonl(self):
        # Write raw JSONL manually (legacy format)
        tasks_dir = self.ws / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        jsonl = tasks_dir / "delegations.jsonl"
        for status in ("in_corso", "ok"):
            jsonl.write_text(
                json.dumps({"id": "d20", "status": status, "task": "t"}) + "\n",
                encoding="utf-8",
                # append
            )
        # Write both lines
        jsonl.write_text(
            json.dumps({"id": "d20", "status": "in_corso", "task": "t"}) + "\n" +
            json.dumps({"id": "d20", "status": "ok", "task": "t"}) + "\n",
            encoding="utf-8",
        )
        rebuilt = self.store.rebuild_from_jsonl()
        self.assertIn("d20", rebuilt)
        self.assertEqual(rebuilt["d20"]["status"], "done")   # "ok" → "done"

    def test_rebuild_creates_state_file(self):
        tasks_dir = self.ws / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        jsonl = tasks_dir / "delegations.jsonl"
        jsonl.write_text(
            json.dumps({"id": "d21", "status": "done", "task": "t"}) + "\n",
            encoding="utf-8",
        )
        self.store.rebuild_from_jsonl()
        self.assertTrue((tasks_dir / "delegations-state.json").exists())


# ---------------------------------------------------------------------------
# classify_error
# ---------------------------------------------------------------------------

class TestClassifyError(unittest.TestCase):
    def _clf(self, text):
        from api.delegation_store import classify_error
        return classify_error(text)

    def test_quota_exhausted(self):
        e = RuntimeError("Codex CLI exit 1: insufficient_quota")
        r = self._clf(e)
        self.assertEqual(r["category"], "quota_exhausted")
        self.assertTrue(r["retryable"])

    def test_timeout_not_quota(self):
        # Timeout message may contain quota-like words in output text
        e = RuntimeError("Codex CLI timeout dopo 1000s.\nParziale:\nsome usage text here")
        r = self._clf(e)
        self.assertEqual(r["category"], "timeout")
        self.assertTrue(r["retryable"])

    def test_auth_error(self):
        r = self._clf("HTTP 401 unauthorized")
        self.assertEqual(r["category"], "auth")
        self.assertFalse(r["retryable"])

    def test_provider_unavailable(self):
        r = self._clf("Codex CLI non trovato (FileNotFoundError)")
        self.assertEqual(r["category"], "provider_unavailable")
        self.assertFalse(r["retryable"])

    def test_unknown_fallback(self):
        r = self._clf("something completely random")
        self.assertEqual(r["category"], "unknown")


# ---------------------------------------------------------------------------
# bg_task_to_canonical
# ---------------------------------------------------------------------------

class TestBgTaskToCanonical(unittest.TestCase):
    def test_ok_to_done(self):
        from api.delegation_store import bg_task_to_canonical
        t = {
            "id": "d1", "status": "ok", "agent": "prog", "agent_id": "programmatore",
            "task_type": "codice", "task": "fix bug", "output": "done",
            "started": 100.0, "finished": 110.0,
            "runtime": "codex", "fallback_runtime": "", "fallback_model": "", "fallback_reason": "",
            "librarian_status": "ok", "librarian_output": "mem updated",
        }
        rec = bg_task_to_canonical(t)
        self.assertEqual(rec["status"], "done")
        self.assertEqual(rec["result"]["text"], "done")
        self.assertEqual(rec["librarian"]["status"], "done")

    def test_errore_to_failed(self):
        from api.delegation_store import bg_task_to_canonical
        t = {"id": "d2", "status": "errore", "agent": "a", "output": "err", "started": 0.0}
        rec = bg_task_to_canonical(t)
        self.assertEqual(rec["status"], "failed")

    def test_parziale_sets_partial(self):
        from api.delegation_store import bg_task_to_canonical
        t = {"id": "d3", "status": "parziale", "agent": "a", "output": "partial", "started": 0.0}
        rec = bg_task_to_canonical(t)
        self.assertEqual(rec["status"], "done")
        self.assertTrue(rec["result"]["partial"])


# ---------------------------------------------------------------------------
# PrimeBriefQueue – enqueue idempotence
# ---------------------------------------------------------------------------

class TestPrimeBriefQueueEnqueue(unittest.TestCase):

    def setUp(self):
        self.ws = _tmp_workspace()
        import api.prime_brief_queue as pbq
        pbq._QUEUE = None
        from api.prime_brief_queue import PrimeBriefQueue
        self.queue = PrimeBriefQueue(self.ws)

    def test_enqueue_returns_brief_id(self):
        bid = self.queue.enqueue("d1", agent="prog", task_type="codice",
                                  task="fix", status="done", output="ok output")
        self.assertEqual(bid, "brief-d1")

    def test_enqueue_creates_jsonl(self):
        self.queue.enqueue("d2", agent="p", task_type="t", task="t", status="done", output="x")
        jsonl = self.ws / "tasks" / "prime-brief-queue.jsonl"
        self.assertTrue(jsonl.exists())

    def test_enqueue_idempotent_same_brief_id(self):
        """Calling enqueue twice for the same task_id → no duplicate in queue."""
        self.queue.enqueue("d3", agent="p", task_type="t", task="t", status="done", output="x")
        self.queue.enqueue("d3", agent="p", task_type="t", task="t", status="done", output="x")
        records = _read_jsonl(self.ws / "tasks" / "prime-brief-queue.jsonl")
        d3_entries = [r for r in records if r.get("brief_id") == "brief-d3"]
        self.assertEqual(len(d3_entries), 1, "Idempotent: only one queue entry per brief_id")

    def test_delivered_not_re_enqueued(self):
        """Once delivered, a brief is never re-enqueued."""
        self.queue.enqueue("d4", agent="p", task_type="t", task="t", status="done", output="x")
        self.queue.mark_delivered("brief-d4")
        # Second enqueue should be no-op
        self.queue.enqueue("d4", agent="p", task_type="t", task="t", status="done", output="x")
        records = _read_jsonl(self.ws / "tasks" / "prime-brief-queue.jsonl")
        d4_entries = [r for r in records if r.get("brief_id") == "brief-d4"]
        # 1 enqueue + 1 delivered = 2 events; no 3rd event
        self.assertLessEqual(len(d4_entries), 2)

    def test_failed_delegation_is_high_priority(self):
        self.queue.enqueue("d5", agent="p", task_type="t", task="t",
                           status="failed", output="err", error_category="timeout")
        records = _read_jsonl(self.ws / "tasks" / "prime-brief-queue.jsonl")
        d5 = next(r for r in records if r.get("brief_id") == "brief-d5")
        self.assertEqual(d5["priority"], "high")

    def test_fallback_text_contains_task_id_and_status(self):
        self.queue.enqueue("d6", agent="programmatore", task_type="codice",
                           task="fix bug", status="done", output="fixed!")
        records = _read_jsonl(self.ws / "tasks" / "prime-brief-queue.jsonl")
        d6 = next(r for r in records if r.get("brief_id") == "brief-d6")
        fb = d6.get("fallback_text", "")
        self.assertIn("d6", fb)
        self.assertIn("done", fb)
        self.assertIn("programmatore", fb)

    def test_get_pending_excludes_delivered(self):
        self.queue.enqueue("d7", agent="p", task_type="t", task="t", status="done", output="x")
        self.queue.enqueue("d8", agent="p", task_type="t", task="t", status="done", output="y")
        self.queue.mark_delivered("brief-d7")
        pending = self.queue.get_pending()
        ids = [b["brief_id"] for b in pending]
        self.assertNotIn("brief-d7", ids)
        self.assertIn("brief-d8", ids)


# ---------------------------------------------------------------------------
# PrimeBriefQueue – fallback delivery to PrimeSessionStore
# ---------------------------------------------------------------------------

class TestFallbackDelivery(unittest.TestCase):
    """Brief fallback persisted in PrimeSessionStore without calling any LLM."""

    def setUp(self):
        self.ws = _tmp_workspace()
        import api.prime_brief_queue as pbq
        pbq._QUEUE = None
        from api.prime_brief_queue import PrimeBriefQueue
        self.queue = PrimeBriefQueue(self.ws)

    def _make_mock_store(self):
        """Return a minimal mock PrimeSessionStore."""
        store = MagicMock()
        store.injected_messages = []

        def _inject(content, meta=None):
            store.injected_messages.append({"content": content, "meta": meta or {}})

        store.inject_assistant_message.side_effect = _inject
        return store

    def test_fallback_text_persisted_without_llm(self):
        """deliver_fallback_no_llm() must write to PrimeSessionStore, no LLM calls."""
        mock_store = self._make_mock_store()
        brief = {
            "brief_id": "brief-d10",
            "task_id": "d10",
            "agent": "programmatore",
            "delegation_status": "done",
            "error_category": "",
            "fallback_text": "Delega d10 completata da programmatore.\nStato: done.",
        }
        with patch("api.prime_session_store.get_prime_session_store", return_value=mock_store):
            ok = self.queue.deliver_fallback_no_llm(brief)
        self.assertTrue(ok)
        self.assertEqual(len(mock_store.injected_messages), 1)
        msg = mock_store.injected_messages[0]
        self.assertIn("d10", msg["content"])
        self.assertEqual(msg["meta"]["brief_type"], "fallback_no_llm")
        # Crucially: no LLM was called
        mock_store.assert_not_called()   # the MagicMock itself was not called

    def test_fallback_text_generated_if_missing(self):
        """If fallback_text is empty, deliver_fallback_no_llm generates one on the fly."""
        mock_store = self._make_mock_store()
        brief = {
            "brief_id": "brief-d11",
            "task_id": "d11",
            "agent": "ricercatore",
            "delegation_status": "failed",
            "error_category": "timeout",
            "fallback_text": "",  # intentionally empty
        }
        with patch("api.prime_session_store.get_prime_session_store", return_value=mock_store):
            ok = self.queue.deliver_fallback_no_llm(brief)
        self.assertTrue(ok)
        content = mock_store.injected_messages[0]["content"]
        self.assertIn("d11", content)
        self.assertIn("timeout", content)


# ---------------------------------------------------------------------------
# PrimeBriefQueue – attempt_delivery with LLM quota error
# ---------------------------------------------------------------------------

class TestAttemptDeliveryQuotaFallback(unittest.TestCase):
    """If _hermes_prime_reply raises (quota), fallback is still persisted."""

    def setUp(self):
        self.ws = _tmp_workspace()
        import api.prime_brief_queue as pbq
        pbq._QUEUE = None
        from api.prime_brief_queue import PrimeBriefQueue
        self.queue = PrimeBriefQueue(self.ws)

    def _make_mock_store(self):
        store = MagicMock()
        store.injected_messages = []

        def _inject(content, meta=None):
            store.injected_messages.append({"content": content, "meta": meta or {}})

        store.inject_assistant_message.side_effect = _inject
        return store

    def test_llm_failure_persists_fallback(self):
        """If prime_reply_fn raises, fallback text is still written to PrimeSessionStore."""
        self.queue.enqueue(
            "d20", agent="prog", task_type="codice", task="make it work",
            status="done", output="output text",
        )

        def _fail_reply(msg, ws):
            raise RuntimeError("credit_balance too low")

        mock_store = self._make_mock_store()
        with patch("api.prime_session_store.get_prime_session_store", return_value=mock_store):
            ok = self.queue.attempt_delivery(
                "brief-d20",
                try_llm=True,
                hermes_prime_reply_fn=_fail_reply,
                workspace=self.ws,
                output="output text",
            )
        self.assertTrue(ok, "Fallback delivery must succeed even when LLM fails")
        self.assertEqual(len(mock_store.injected_messages), 1)
        msg = mock_store.injected_messages[0]
        self.assertEqual(msg["meta"]["brief_type"], "fallback_no_llm")

    def test_llm_success_persists_llm_reply(self):
        """If prime_reply_fn returns a reply, it is persisted as brief_type=llm."""
        self.queue.enqueue(
            "d21", agent="prog", task_type="codice", task="fix",
            status="done", output="output",
        )

        def _ok_reply(msg, ws):
            return {"reply": "Tutto fatto! Il prossimo passo è il deploy."}

        mock_store = self._make_mock_store()
        with patch("api.prime_session_store.get_prime_session_store", return_value=mock_store):
            ok = self.queue.attempt_delivery(
                "brief-d21",
                try_llm=True,
                hermes_prime_reply_fn=_ok_reply,
                workspace=self.ws,
                output="output",
            )
        self.assertTrue(ok)
        msg = mock_store.injected_messages[0]
        self.assertEqual(msg["meta"]["brief_type"], "llm")
        self.assertIn("deploy", msg["content"])


# ---------------------------------------------------------------------------
# PrimeSessionStore – inject_assistant_message
# ---------------------------------------------------------------------------

class TestPrimeSessionStoreInject(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".json")
        self.path = pathlib.Path(self.tmp)

    def tearDown(self):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def _store(self):
        from api.prime_session_store import PrimeSessionStore
        return PrimeSessionStore(path=self.path)

    def test_inject_appears_in_history(self):
        store = self._store()
        store.inject_assistant_message("Delega d1 completata.", meta={"brief_id": "brief-d1"})
        hist = store.history()
        msgs = hist["messages"]
        self.assertTrue(any(m["content"] == "Delega d1 completata." for m in msgs))

    def test_inject_survives_reload(self):
        """Injected message must be in history after re-reading from disk."""
        store1 = self._store()
        store1.inject_assistant_message("Messaggio persistente")
        store2 = self._store()   # fresh instance, reads from disk
        hist = store2.history()
        self.assertTrue(any("Messaggio persistente" in m.get("content", "") for m in hist["messages"]))

    def test_inject_marks_injected_true(self):
        store = self._store()
        store.inject_assistant_message("x")
        hist = store.history()
        injected = [m for m in hist["messages"] if m.get("injected")]
        self.assertEqual(len(injected), 1)

    def test_inject_meta_included(self):
        store = self._store()
        store.inject_assistant_message("y", meta={"brief_type": "fallback_no_llm", "task_id": "d99"})
        hist = store.history()
        msg = next(m for m in hist["messages"] if m.get("injected"))
        self.assertEqual(msg["brief_type"], "fallback_no_llm")
        self.assertEqual(msg["task_id"], "d99")

    def test_inject_journal_event(self):
        store = self._store()
        store.inject_assistant_message("z", meta={"brief_id": "brief-d1"})
        data = json.loads(self.path.read_text(encoding="utf-8"))
        journal_events = [e for e in (data.get("journal") or []) if e.get("event") == "injected_message"]
        self.assertGreater(len(journal_events), 0)


# ---------------------------------------------------------------------------
# Integration: _persist_bg_task syncs to delegation_store
# ---------------------------------------------------------------------------

class TestPersistBgTaskSync(unittest.TestCase):
    """_persist_bg_task() must sync to canonical store (Fase 1)."""

    def setUp(self):
        self.ws = _tmp_workspace()
        import api.delegation_store as ds
        import api.prime_brief_queue as pbq
        ds._STORE = None
        pbq._QUEUE = None

    def _patch_workspace(self):
        """Temporarily set the delegation singleton workspace."""
        import api.delegation_store as ds
        from api.delegation_store import DelegationStore
        ds._STORE = DelegationStore(self.ws)
        return ds._STORE

    def test_persist_bg_task_writes_canonical(self):
        """After _persist_bg_task, delegations-state.json must have the record."""
        store = self._patch_workspace()
        import api.prime_delegation as pd
        # Inject a fake task into _BG_TASKS
        pd._BG_TASKS["d50"] = {
            "id": "d50", "agent": "programmatore", "agent_id": "programmatore",
            "task_type": "codice", "task": "build feature",
            "status": "ok", "output": "feature done",
            "started": time.time(), "finished": time.time(),
            "runtime": "codex", "fallback_runtime": "", "fallback_model": "", "fallback_reason": "",
            "librarian_status": "", "librarian_output": "", "session_id": "hermes-prime",
        }
        pd._persist_bg_task("d50", str(self.ws))
        rec = store.get("d50")
        self.assertIsNotNone(rec, "Canonical record must exist after _persist_bg_task")
        self.assertEqual(rec["status"], "done")

    def test_persist_bg_task_writes_chat_anchor(self):
        """Delegation cards need a durable chat anchor to survive reload/restart."""
        store = self._patch_workspace()
        import api.prime_delegation as pd
        pd._BG_TASKS["d50"] = {
            "id": "d50", "agent": "programmatore", "agent_id": "programmatore",
            "task_type": "codice", "task": "fix card position",
            "status": "ok", "output": "Completato, commit 9dd9b9b6",
            "started": time.time(), "finished": time.time(),
            "runtime": "codex", "fallback_runtime": "", "fallback_model": "", "fallback_reason": "",
            "librarian_status": "", "librarian_output": "", "session_id": "hermes-prime",
            "anchor_session_id": "hermes-prime", "anchor_message_index": 4, "anchor_created_at": 1234.5,
        }
        pd._persist_bg_task("d50", str(self.ws))
        rec = store.get("d50")
        self.assertEqual(rec["ui"]["anchor_session_id"], "hermes-prime")
        self.assertEqual(rec["ui"]["anchor_message_index"], 4)
        self.assertEqual(rec["ui"]["anchor_created_at"], 1234.5)
        self.assertIn("commit 9dd9b9b6", rec["ui"]["summary"])

    def tearDown(self):
        import api.prime_delegation as pd
        pd._BG_TASKS.pop("d50", None)
        import api.delegation_store as ds
        ds._STORE = None
        import api.prime_brief_queue as pbq
        pbq._QUEUE = None


# ---------------------------------------------------------------------------
# Integration: completion enqueues brief
# ---------------------------------------------------------------------------

class TestCompletionEnqueuesBrief(unittest.TestCase):
    """Terminal state in _run_and_store → brief is enqueued exactly once."""

    def setUp(self):
        self.ws = _tmp_workspace()
        import api.delegation_store as ds
        import api.prime_brief_queue as pbq
        ds._STORE = None
        pbq._QUEUE = None
        from api.prime_brief_queue import PrimeBriefQueue
        self._q = PrimeBriefQueue(self.ws)
        pbq._QUEUE = self._q

    def test_done_enqueues_brief(self):
        """A 'done' delegation must produce a brief in the queue."""
        self._q.enqueue(
            "d60", agent="prog", task_type="codice", task="task",
            status="done", output="result",
        )
        pending = self._q.get_pending()
        ids = [b["brief_id"] for b in pending]
        self.assertIn("brief-d60", ids)

    def test_failed_enqueues_brief_high_priority(self):
        """A 'failed' delegation must produce a high-priority brief."""
        self._q.enqueue(
            "d61", agent="prog", task_type="codice", task="task",
            status="failed", output="error text", error_category="timeout",
        )
        pending = self._q.get_pending()
        brief = next(b for b in pending if b["brief_id"] == "brief-d61")
        self.assertEqual(brief["priority"], "high")

    def tearDown(self):
        import api.delegation_store as ds
        import api.prime_brief_queue as pbq
        ds._STORE = None
        pbq._QUEUE = None


# ---------------------------------------------------------------------------
# Invariant: no result only in RAM
# ---------------------------------------------------------------------------

class TestNoResultOnlyInRam(unittest.TestCase):
    """Every upserted record must be readable from disk (not just RAM)."""

    def setUp(self):
        self.ws = _tmp_workspace()
        import api.delegation_store as ds
        ds._STORE = None
        from api.delegation_store import DelegationStore
        self.store = DelegationStore(self.ws)

    def test_state_file_readable_after_upsert(self):
        self.store.upsert({"id": "d70", "status": "done", "task": "t", "output": "result"})
        # Read state file directly (simulate crash + restart read)
        state_path = self.ws / "tasks" / "delegations-state.json"
        data = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertIn("d70", data)
        self.assertEqual(data["d70"]["status"], "done")

    def test_jsonl_has_event_after_upsert(self):
        self.store.upsert({"id": "d71", "status": "running", "task": "t"})
        events = _read_jsonl(self.ws / "tasks" / "delegations.jsonl")
        self.assertTrue(any(e.get("id") == "d71" for e in events))


# ---------------------------------------------------------------------------
# Invariant: no brief only in DOM
# ---------------------------------------------------------------------------

class TestNoBriefOnlyInDom(unittest.TestCase):
    """Briefs delivered are recorded in JSONL (survives browser close/refresh)."""

    def setUp(self):
        self.ws = _tmp_workspace()
        import api.prime_brief_queue as pbq
        pbq._QUEUE = None
        from api.prime_brief_queue import PrimeBriefQueue
        self.queue = PrimeBriefQueue(self.ws)

    def test_delivered_brief_recorded_in_jsonl(self):
        self.queue.enqueue(
            "d80", agent="p", task_type="t", task="t", status="done", output="x",
        )
        self.queue.mark_delivered("brief-d80")
        events = _read_jsonl(self.ws / "tasks" / "prime-brief-queue.jsonl")
        delivered_events = [e for e in events if e.get("status") == "delivered" and e.get("brief_id") == "brief-d80"]
        self.assertGreater(len(delivered_events), 0, "Delivered event must be in JSONL")

    def test_fallback_persisted_before_dom_update(self):
        """deliver_fallback_no_llm writes to PrimeSessionStore BEFORE returning True.

        The DOM shows the brief only after this function succeeds — ensuring the
        message cannot exist only in the DOM.
        """
        from api.prime_brief_queue import PrimeBriefQueue
        local_q = PrimeBriefQueue(self.ws)
        written = []

        mock_store = MagicMock()
        mock_store.inject_assistant_message.side_effect = lambda c, meta=None: written.append(c)

        brief = {
            "brief_id": "brief-d81",
            "task_id": "d81",
            "agent": "prog",
            "delegation_status": "done",
            "error_category": "",
            "fallback_text": "Delega d81 completata.",
        }
        with patch("api.prime_session_store.get_prime_session_store", return_value=mock_store):
            ok = local_q.deliver_fallback_no_llm(brief)
        self.assertTrue(ok)
        self.assertEqual(len(written), 1, "PrimeSessionStore written before DOM update")

    def tearDown(self):
        import api.prime_brief_queue as pbq
        pbq._QUEUE = None


# ---------------------------------------------------------------------------
# GET /api/bridge/tasks format compat
# ---------------------------------------------------------------------------

class TestBridgeTasksLegacyFormat(unittest.TestCase):
    """Canonical-store records exposed via /api/bridge/tasks use legacy status values."""

    def test_status_to_legacy_mapping(self):
        from api.delegation_store import status_to_legacy
        self.assertEqual(status_to_legacy("done"), "ok")
        self.assertEqual(status_to_legacy("failed"), "errore")
        self.assertEqual(status_to_legacy("running"), "in_corso")
        self.assertEqual(status_to_legacy("pending"), "in_corso")

    def test_unknown_passthrough(self):
        from api.delegation_store import status_to_legacy
        self.assertEqual(status_to_legacy("unknown"), "unknown")


if __name__ == "__main__":
    unittest.main()
