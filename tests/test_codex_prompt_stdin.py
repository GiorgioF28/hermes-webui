"""Il prompt delle deleghe Codex deve viaggiare su STDIN, mai come argomento.

Su Windows `codex.cmd` e' un batch file: CreateProcess lo lancia via
`cmd.exe /c`, che ri-parsa la riga di comando. Un task che contiene `&`, `|`,
`(`, `)`, `^`, `<`, `>` o newline viene troncato — e pezzi di testo possono
essere eseguiti come comandi. Sintomo osservato 2026-08-03: il sotto-agente
riceveva solo l'inizio del task e rispondeva "Ricevuto, dimmi cosa verificare".

Fix: `codex exec -` con il prompt su stdin.
"""

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.prime_delegation as pd  # noqa: E402


TASK_WITH_METACHARS = (
    "TEST (sola lettura, non modificare). Riporta (1) il numero di righe & "
    "(2) le prime 10 righe | niente altro > fine"
)


class CodexPromptStdinTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._orig_resolve = pd._resolve_codex_executable
        self._orig_run = subprocess.run
        pd._resolve_codex_executable = lambda: "codex.cmd"

        class _Proc:
            returncode = 0
            stdout = "fatto"
            stderr = ""

        def fake_run(cmd, **kw):
            self.calls.append((list(cmd), kw))
            return _Proc()

        subprocess.run = fake_run

    def tearDown(self):
        pd._resolve_codex_executable = self._orig_resolve
        subprocess.run = self._orig_run

    def test_prompt_is_not_passed_as_argv(self):
        pd._codex_exec_blocking(TASK_WITH_METACHARS, ".")
        cmd, kw = self.calls[-1]
        for arg in cmd:
            self.assertNotIn(
                "sola lettura",
                arg,
                "il task e' finito in argv: cmd.exe lo troncherebbe sui metacaratteri",
            )
        self.assertEqual(cmd[-1], "-", "il prompt deve essere letto da stdin")
        self.assertEqual(kw.get("input"), TASK_WITH_METACHARS)
        self.assertFalse(kw.get("shell"), "shell=True riaprirebbe lo stesso buco")

    def test_command_shape_unchanged(self):
        pd._codex_exec_blocking("ciao", "C:\\ws")
        cmd, kw = self.calls[-1]
        self.assertEqual(cmd[0], "codex.cmd")
        self.assertEqual(cmd[1], "exec")
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", cmd)
        self.assertEqual(cmd[cmd.index("-C") + 1], "C:\\ws")

    def test_cmd_wrapper_really_mangles_argv(self):
        """Prova concreta del bug: un batch wrapper tronca l'argomento."""
        if os.name != "nt":
            self.skipTest("comportamento specifico di cmd.exe")
        subprocess.run = self._orig_run  # qui serve il vero subprocess
        import tempfile
        from pathlib import Path

        bat = Path(tempfile.gettempdir()) / "hermes_echo_args_test.cmd"
        bat.write_text("@echo off\r\necho A2=[%~2]\r\n", encoding="ascii")
        proc = subprocess.run(
            [str(bat), "exec", TASK_WITH_METACHARS],
            capture_output=True,
            text=True,
            shell=False,
        )
        combined = (proc.stdout or "") + (proc.stderr or "")
        self.assertFalse(
            proc.returncode == 0 and "fine" in combined,
            "cmd.exe ha preservato il task: la premessa del fix andrebbe rivista",
        )
        try:
            bat.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    unittest.main()
