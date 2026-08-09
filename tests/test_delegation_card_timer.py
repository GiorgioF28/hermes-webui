"""Le card delega del Command Bridge mostrano un timer (in corso da / durata).

Il timer e' calcolato lato UI da ``started`` (+ ``finished`` quando la delega
e' conclusa). Prima di questo fix lo snapshot JSON esposto alla UI conteneva
solo ``finished``: senza ``started`` la card non poteva sapere da quanto gira.

Questi test bloccano la regressione: ``started`` DEVE arrivare alla UI sia per
i record legacy (``started``) sia per quelli in schema canonico
(``started_at`` / ``created_at``).
"""
from __future__ import annotations

import pathlib
import re
import sys
import time
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _base_task(**over):
    t = {
        "id": "d900", "session_id": "hermes-prime", "agent": "programmatore",
        "agent_id": "programmatore", "task_type": "codice", "task": "task di prova",
        "status": "in_corso", "output": "", "started": time.time(), "finished": None,
        "runtime": "codex",
    }
    t.update(over)
    return t


class TestStartedExposedToUi(unittest.TestCase):
    def setUp(self):
        import api.prime_delegation as pd
        pd._BG_TASKS.clear()
        self.pd = pd

    def tearDown(self):
        self.pd._BG_TASKS.clear()

    def test_running_task_exposes_started(self):
        started = time.time() - 42
        self.pd._BG_TASKS["d900"] = _base_task(started=started)
        [task] = self.pd.get_background_tasks()
        self.assertEqual(task["started"], started)
        self.assertIsNone(task["finished"])

    def test_finished_task_exposes_both_timestamps(self):
        started = time.time() - 120
        finished = time.time() - 5
        self.pd._BG_TASKS["d901"] = _base_task(
            id="d901", status="ok", output="fatto", started=started, finished=finished
        )
        [task] = self.pd.get_background_tasks()
        self.assertEqual(task["started"], started)
        self.assertEqual(task["finished"], finished)

    def test_canonical_record_falls_back_to_started_at(self):
        """Record in schema canonico: started_at deve valere come started."""
        started_at = time.time() - 30
        rec = _base_task(id="d902")
        rec.pop("started")
        rec["started_at"] = started_at
        self.pd._BG_TASKS["d902"] = rec
        [task] = self.pd.get_background_tasks()
        self.assertEqual(task["started"], started_at)


class TestCommandBridgeTimerWiring(unittest.TestCase):
    """La UI deve avere il codice che disegna e aggiorna il timer."""

    def setUp(self):
        self.js = (REPO_ROOT / "static" / "command_bridge.js").read_text(encoding="utf-8")

    def test_timer_helpers_present(self):
        for needle in ("function fmtDuration(", "function taskTimerHtml(", "function tickTaskTimers("):
            self.assertIn(needle, self.js, needle)

    def test_timer_is_rendered_in_delegation_card(self):
        self.assertIsNotNone(
            re.search(r"sl \+ '</span>' \+ taskTimerHtml\(t\)", self.js),
            "taskTimerHtml() non e' agganciato all'header della card delega",
        )

    def test_timer_ticks_every_second(self):
        self.assertIn("setInterval(tickTaskTimers, 1000)", self.js)

    def test_only_running_timers_are_ticked(self):
        self.assertIn(".cb-deleg-timer[data-running=\"1\"]", self.js)


if __name__ == "__main__":
    unittest.main()
