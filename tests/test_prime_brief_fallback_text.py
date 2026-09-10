"""Tests for _build_fallback_text (spec: docs/specs/brief-fallback-leggibile.md).

The no-LLM fallback shown in the Command Bridge chat must be short, Italian and
free of raw agent output. The raw output stays durable in the queue and in
tasks/delegations.jsonl; it is just never echoed in the chat text.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.prime_brief_queue import _build_fallback_text  # noqa: E402


# A realistic raw agent output: English log noise, file names, spec sections.
RAW_OUTPUT = (
    "I'll start by inspecting api/prime_brief_queue.py and docs/specs/"
    "brief-fallback-leggibile.md ... You've hit your session limit."
)


class TestFallbackTextShape(unittest.TestCase):
    def test_first_line_has_id_agent_and_task(self):
        text = _build_fallback_text(
            "d1", "programmatore", "done", "", task="sistemare il brief di fallback"
        )
        first = text.split("\n")[0]
        self.assertEqual(
            first, "Delega d1 (programmatore): sistemare il brief di fallback"
        )

    def test_empty_task_falls_back_to_short_header(self):
        text = _build_fallback_text("d2", "ricercatore", "done", "")
        self.assertEqual(text.split("\n")[0], "Delega d2 (ricercatore).")

    def test_blank_agent_becomes_generic_agente(self):
        text = _build_fallback_text("d3", "", "done", "")
        self.assertEqual(text.split("\n")[0], "Delega d3 (agente).")

    def test_never_more_than_three_lines(self):
        text = _build_fallback_text(
            "d4",
            "programmatore",
            "failed",
            RAW_OUTPUT,
            error_category="timeout",
            task="x" * 400,
        )
        self.assertEqual(len(text.split("\n")), 3)


class TestTaskTruncation(unittest.TestCase):
    def test_task_line_truncated_at_90_chars_with_ellipsis(self):
        long_task = "a" * 200
        text = _build_fallback_text("d5", "programmatore", "done", "", task=long_task)
        summary = text.split("\n")[0].split(": ", 1)[1]
        self.assertEqual(len(summary), 90)
        self.assertTrue(summary.endswith("..."))

    def test_task_of_exactly_90_chars_is_not_truncated(self):
        task = "b" * 90
        text = _build_fallback_text("d6", "programmatore", "done", "", task=task)
        summary = text.split("\n")[0].split(": ", 1)[1]
        self.assertEqual(summary, task)
        self.assertNotIn("...", summary)

    def test_multiline_task_collapses_to_one_line(self):
        text = _build_fallback_text(
            "d7", "programmatore", "done", "", task="prima riga\n\n  seconda riga  "
        )
        self.assertEqual(len(text.split("\n")), 2)
        self.assertIn("prima riga seconda riga", text)


class TestOutcomeLine(unittest.TestCase):
    def _outcome_line(self, status, category="", output=""):
        return _build_fallback_text(
            "dX", "programmatore", status, output, error_category=category, task="t"
        ).split("\n")[1]

    def test_completed(self):
        self.assertEqual(
            self._outcome_line("completed"),
            "Completata. Verifico gli artefatti prima di dartela per buona.",
        )

    def test_ok(self):
        self.assertEqual(
            self._outcome_line("ok"),
            "Completata. Verifico gli artefatti prima di dartela per buona.",
        )

    def test_done_is_the_status_used_by_prime_delegation(self):
        # api/prime_delegation.py enqueues successful delegations with status="done".
        self.assertEqual(
            self._outcome_line("done"),
            "Completata. Verifico gli artefatti prima di dartela per buona.",
        )

    def test_quota_exhausted(self):
        self.assertEqual(
            self._outcome_line("failed", "quota_exhausted"),
            "Fermata: crediti esauriti dell'agente. Riparte quando la quota si "
            "resetta; nel frattempo non ho un esito affidabile.",
        )

    def test_timeout(self):
        self.assertEqual(
            self._outcome_line("failed", "timeout"),
            "Interrotta per tempo scaduto: il lavoro puo' essere parziale, va "
            "ricontrollato sul disco.",
        )

    def test_crash(self):
        self.assertEqual(
            self._outcome_line("failed", "crash"),
            "Fallita per un errore tecnico dell'agente. Da rilanciare.",
        )

    def test_runtime_error(self):
        self.assertEqual(
            self._outcome_line("failed", "runtime_error"),
            "Fallita per un errore tecnico dell'agente. Da rilanciare.",
        )

    def test_transport(self):
        self.assertEqual(
            self._outcome_line("failed", "transport"),
            "Fallita: agente non raggiungibile. Da rilanciare.",
        )

    def test_provider_unavailable(self):
        self.assertEqual(
            self._outcome_line("failed", "provider_unavailable"),
            "Fallita: agente non raggiungibile. Da rilanciare.",
        )

    def test_unknown_category_failed(self):
        self.assertEqual(self._outcome_line("failed", "unknown"), "Fallita. Da rilanciare.")

    def test_empty_category_failed(self):
        self.assertEqual(self._outcome_line("failed", ""), "Fallita. Da rilanciare.")

    def test_unmapped_category_failed(self):
        self.assertEqual(
            self._outcome_line("failed", "process_exit"), "Fallita. Da rilanciare."
        )


class TestWorkLogHint(unittest.TestCase):
    def test_third_line_present_only_with_raw_output(self):
        with_output = _build_fallback_text(
            "d8", "programmatore", "failed", RAW_OUTPUT, "timeout", "t"
        )
        self.assertEqual(
            with_output.split("\n")[2], "Dettaglio tecnico disponibile nel Work Log."
        )

    def test_no_third_line_without_raw_output(self):
        text = _build_fallback_text("d9", "programmatore", "failed", "   ", "timeout", "t")
        self.assertEqual(len(text.split("\n")), 2)
        self.assertNotIn("Work Log", text)


class TestNoRawOutputLeak(unittest.TestCase):
    def test_english_agent_log_never_appears_in_text(self):
        text = _build_fallback_text(
            "d10",
            "programmatore",
            "failed",
            RAW_OUTPUT,
            error_category="quota_exhausted",
            task="implementare la spec del brief",
        )
        for leak in (
            "You've hit your session limit",
            "I'll start by inspecting",
            "api/prime_brief_queue.py",
            "brief-fallback-leggibile.md",
        ):
            self.assertNotIn(leak, text)

    def test_no_fragment_of_raw_output_leaks(self):
        # Not even a truncated excerpt: no 20-char window of the raw output survives.
        text = _build_fallback_text("d11", "p", "failed", RAW_OUTPUT, "crash", "t")
        windows = [RAW_OUTPUT[i:i + 20] for i in range(0, len(RAW_OUTPUT) - 20)]
        leaked = [w for w in windows if w in text]
        self.assertEqual(leaked, [])

    def test_old_english_and_noise_lines_are_gone(self):
        text = _build_fallback_text("d12", "p", "failed", RAW_OUTPUT, "timeout", "t")
        self.assertNotIn("Esito grezzo", text)
        self.assertNotIn("Stato:", text)
        self.assertNotIn("Categoria errore", text)
        self.assertNotIn("brief intelligente rimandato", text)


class TestBackwardCompatibility(unittest.TestCase):
    def test_task_kwarg_is_optional_positionally_and_by_name(self):
        # Legacy call shape (5 positional args) must still work.
        legacy = _build_fallback_text("d13", "p", "failed", "out", "timeout")
        self.assertTrue(legacy.startswith("Delega d13 (p)."))
        self.assertEqual(
            legacy,
            _build_fallback_text("d13", "p", "failed", "out", "timeout", task=""),
        )


if __name__ == "__main__":
    unittest.main()
