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
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

    def tearDown(self):
        routes.j = self._orig_j
        routes._hermes_prime_reply = self._orig_reply
        with routes._BRIEF_JOBS_LOCK:
            routes._BRIEF_JOBS.clear()

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

    def test_job_runner_records_reply(self):
        """Il worker registra l'esito nel job store (senza toccare la POST)."""
        routes._hermes_prime_reply = lambda msg, ws: {"reply": "sintesi finale"}
        routes._run_prime_brief_job("brief-async-4", "b4", "messaggio", routes.Path("."))
        rec = routes._brief_job_get("brief-async-4")
        self.assertEqual(rec.get("state"), "done")
        self.assertEqual(rec.get("reply"), "sintesi finale")


if __name__ == "__main__":
    unittest.main()
