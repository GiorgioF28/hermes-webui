"""Brief automatico asincrono — regressione spam "Request timed out".

Il turno LLM del brief dura anche 4 minuti. Se gira dentro la POST
/api/bridge/prime/brief, la connessione resta occupata e — insieme alle SSE —
satura il pool di connessioni HTTP del browser (~6 per origine): tutte le fetch
di polling si accodano, superano il timeout di 30s di workspace.js e sparano il
toast a raffica.

Questi test bloccano la regressione: la POST deve tornare SUBITO con
pending=True e il turno LLM deve girare in un thread separato, con l'esito
leggibile da GET /api/bridge/prime/brief/status.
"""

import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.prime_brief_queue as pbq  # noqa: E402
import api.routes as routes  # noqa: E402


class BriefAsyncTests(unittest.TestCase):
    def setUp(self):
        self.captured = []
        self._orig_j = routes.j
        self._orig_reply = routes._hermes_prime_reply
        self._orig_get_task = None
        routes.j = lambda handler, payload, **kw: self.captured.append(payload) or True
        with routes._BRIEF_JOBS_LOCK:
            routes._BRIEF_JOBS.clear()
        # Coda brief isolata su tempdir: i test NON devono scrivere nel file
        # di produzione tasks/prime-brief-queue.jsonl del workspace reale.
        self._tmp = tempfile.TemporaryDirectory()
        self.queue = pbq.PrimeBriefQueue(self._tmp.name)
        self._orig_gbq = pbq.get_brief_queue
        pbq.get_brief_queue = lambda ws: self.queue

    def tearDown(self):
        routes.j = self._orig_j
        routes._hermes_prime_reply = self._orig_reply
        with routes._BRIEF_JOBS_LOCK:
            routes._BRIEF_JOBS.clear()
        pbq.get_brief_queue = self._orig_gbq
        self._tmp.cleanup()

    def _install_task(self, task_id, status="ok"):
        import api.prime_delegation as pd
        pd._BG_TASKS[task_id] = {
            "id": task_id,
            "status": status,
            "agent": "programmatore",
            "task_type": "codice",
            "task": "task di prova",
            "output": "fatto",
        }
        self.addCleanup(lambda: pd._BG_TASKS.pop(task_id, None))

    def test_post_returns_immediately_while_llm_is_slow(self):
        """La POST non deve aspettare l'LLM: risposta < 1s anche con LLM lento."""
        self._install_task("brief-async-1")
        released = threading.Event()

        def slow_reply(msg, workspace):
            released.wait(5)
            return {"reply": "brief lento"}

        routes._hermes_prime_reply = slow_reply
        t0 = time.time()
        routes._handle_bridge_prime_brief(object(), {"task_id": "brief-async-1"})
        elapsed = time.time() - t0
        released.set()

        self.assertLess(elapsed, 1.0, "la POST ha aspettato il turno LLM")
        self.assertTrue(self.captured, "nessuna risposta emessa")
        payload = self.captured[-1]
        self.assertTrue(payload.get("pending"), "la risposta deve essere pending")
        self.assertEqual(payload.get("reply"), "")

    def test_status_endpoint_reports_running_then_done(self):
        """Lo status passa da running a done e restituisce la reply."""
        self._install_task("brief-async-2")
        routes._brief_job_set("brief-async-2", state="running", reply="", brief_id="b2")

        class _Parsed:
            query = "task_id=brief-async-2"

        routes._handle_bridge_prime_brief_status(object(), _Parsed())
        self.assertTrue(self.captured[-1].get("pending"))
        self.assertEqual(self.captured[-1].get("state"), "running")

        routes._brief_job_set(
            "brief-async-2", state="done", reply="ecco il brief", finished_at=time.time()
        )
        routes._handle_bridge_prime_brief_status(object(), _Parsed())
        self.assertFalse(self.captured[-1].get("pending"))
        self.assertEqual(self.captured[-1].get("reply"), "ecco il brief")

    def test_status_unknown_task_is_not_pending(self):
        """Un task_id sconosciuto non deve lasciare il frontend in polling eterno."""
        class _Parsed:
            query = "task_id=mai-visto"

        routes._handle_bridge_prime_brief_status(object(), _Parsed())
        self.assertFalse(self.captured[-1].get("pending"))
        self.assertEqual(self.captured[-1].get("state"), "unknown")

    def test_second_post_does_not_start_a_second_llm_turn(self):
        """Idempotenza: se il job e' running, la seconda POST non ri-lancia l'LLM."""
        self._install_task("brief-async-3")
        calls = []
        released = threading.Event()

        def counting_reply(msg, workspace):
            calls.append(1)
            released.wait(5)
            return {"reply": "ok"}

        routes._hermes_prime_reply = counting_reply
        routes._handle_bridge_prime_brief(object(), {"task_id": "brief-async-3"})
        routes._handle_bridge_prime_brief(object(), {"task_id": "brief-async-3"})
        time.sleep(0.3)
        released.set()
        self.assertEqual(len(calls), 1, "il turno LLM e' partito due volte")

    def test_replayed_post_after_restart_does_not_relaunch_llm(self):
        """Fix tempesta replay 2026-08-31: brief gia' delivered -> nessun turno LLM.

        Dopo un riavvio il registro job in-memory e' vuoto e il frontend
        ri-POSTa le card storiche: la POST deve rispondere already_delivered
        senza lanciare il worker, anche se _BRIEF_JOBS e' vuoto.
        """
        self._install_task("replay-1")
        self.queue.enqueue(
            "replay-1", agent="programmatore", task_type="codice",
            task="task storico", status="done", output="fatto",
        )
        self.queue.mark_delivered("brief-replay-1")
        calls = []
        routes._hermes_prime_reply = lambda msg, ws: calls.append(1) or {"reply": "x"}

        routes._handle_bridge_prime_brief(object(), {"task_id": "replay-1"})
        time.sleep(0.3)

        payload = self.captured[-1]
        self.assertTrue(payload.get("already_delivered"), "manca already_delivered")
        self.assertFalse(payload.get("pending"), "non deve essere pending")
        self.assertEqual(calls, [], "il turno LLM e' stato rigiocato")
        self.assertIsNone(routes._brief_job_get("replay-1"), "worker lanciato inutilmente")

    def test_fresh_brief_still_starts_llm_turn(self):
        """La guardia non deve bloccare i brief nuovi (mai consegnati)."""
        self._install_task("fresh-1")
        routes._hermes_prime_reply = lambda msg, ws: {"reply": "brief nuovo"}
        store = MagicMock()
        delegation_store = MagicMock()
        with (
            patch("api.prime_session_store.get_prime_session_store", return_value=store),
            patch("api.delegation_store.get_delegation_store", return_value=delegation_store),
        ):
            routes._handle_bridge_prime_brief(object(), {"task_id": "fresh-1"})
            self.assertTrue(self.captured[-1].get("pending"), "brief nuovo deve essere pending")
            deadline = time.time() + 3
            while time.time() < deadline:
                rec = routes._brief_job_get("fresh-1")
                if rec and rec.get("state") == "done":
                    break
                time.sleep(0.05)
        rec = routes._brief_job_get("fresh-1")
        self.assertEqual((rec or {}).get("state"), "done", "worker non completato")
        self.assertEqual(rec.get("reply"), "brief nuovo")
        self.assertTrue(self.queue.is_delivered("brief-fresh-1"))

    def test_job_runner_records_reply(self):
        """Il worker registra l'esito nel job store (senza toccare la POST)."""
        routes._hermes_prime_reply = lambda msg, ws: {"reply": "sintesi finale"}
        routes._run_prime_brief_job("brief-async-4", "b4", "messaggio", routes.Path("."))
        rec = routes._brief_job_get("brief-async-4")
        self.assertEqual(rec.get("state"), "done")
        self.assertEqual(rec.get("reply"), "sintesi finale")

    def test_job_runner_persists_and_exposes_llm_usage(self):
        """Il vero worker asincrono conserva usage sia in history sia nello status."""
        expected_usage = {
            "input_tokens": 1200,
            "output_tokens": 80,
            "estimated_cost_usd": 0.0042,
        }
        routes._hermes_prime_reply = lambda msg, ws: {
            "reply": "sintesi con token",
            "usage": expected_usage,
        }
        queue = MagicMock()
        store = MagicMock()
        delegation_store = MagicMock()

        with (
            patch("api.prime_brief_queue.get_brief_queue", return_value=queue),
            patch("api.prime_session_store.get_prime_session_store", return_value=store),
            patch("api.delegation_store.get_delegation_store", return_value=delegation_store),
        ):
            routes._run_prime_brief_job(
                "brief-async-usage", "b-usage", "messaggio", routes.Path(".")
            )

        injected_meta = store.inject_assistant_message.call_args.kwargs["meta"]
        self.assertEqual(injected_meta["usage"], expected_usage)
        rec = routes._brief_job_get("brief-async-usage")
        self.assertEqual(rec["usage"], expected_usage)

        class _Parsed:
            query = "task_id=brief-async-usage"

        routes._handle_bridge_prime_brief_status(object(), _Parsed())
        self.assertEqual(self.captured[-1]["usage"], expected_usage)


if __name__ == "__main__":
    unittest.main()


class BriefOutputCapTest(unittest.TestCase):
    """Regressione 2026-08-25: "Prompt is too long" al ritorno di una delega.

    Il brief automatico incorporava l'output del sotto-agente PER INTERO nel
    prompt di Prime. Una delega lunga (d383: 16m50s di lavoro del programmatore)
    produce un report enorme che, iniettato tutto, sfonda la finestra di
    contesto: Prime muore proprio nel momento in cui deve riferire l'esito.
    """

    def test_short_output_is_left_untouched(self):
        from api import routes

        text = "tutto ok, commit abc1234"
        self.assertEqual(routes._brief_output_excerpt(text), text)

    def test_huge_output_is_capped(self):
        from api import routes

        huge = "x" * 500_000
        got = routes._brief_output_excerpt(huge)
        self.assertLess(len(got), 10_000)
        self.assertIn("troncato", got)

    def test_cap_keeps_head_and_tail(self):
        from api import routes

        # La conclusione di un report sta spesso in fondo (esito, errore finale):
        # troncare solo la coda perderebbe proprio la parte che serve al brief.
        huge = "INIZIO-REPORT" + ("m" * 400_000) + "ESITO-FINALE"
        got = routes._brief_output_excerpt(huge)
        self.assertIn("INIZIO-REPORT", got)
        self.assertIn("ESITO-FINALE", got)
