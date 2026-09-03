"""Il sotto-agente Codex deve ricevere la sua persona, non il task nudo.

Due buchi trovati il 2026-08-03:

1) `_run_codex_worker` mandava a `codex exec` SOLO il testo del task. Il path
   Claude passa `system_prompt=_worker_system_prompt(...)` via
   ClaudeAgentOptions; il path Codex non passava niente — niente persona,
   niente regole di sicurezza, niente nota agente del Vault. Da quando i
   sotto-agenti girano su Codex per default, di fatto lavoravano senza
   contratto operativo.

2) `_agent_note_path` matchava solo il nome file/titolo ESATTO, quindi
   `agent="programmatore"` non trovava "Programmatore Project Engineer.md"
   (verificato: programmatore/ricercatore/social -> None). Anche il path
   Claude cadeva quindi sulla persona generica.

Questi test bloccano entrambe le regressioni.
"""

import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.prime_delegation as pd  # noqa: E402


def _make_vault(root: Path) -> str:
    agents = root / "obsidian-vault" / "06-Agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "Programmatore Project Engineer.md").write_text(
        "# Agent: Programmatore Project Engineer\n\nCONTRATTO-PROGRAMMATORE-TEST\n",
        encoding="utf-8",
    )
    (agents / "Research Analyst.md").write_text(
        "# Agent: Research Analyst\n\nCONTRATTO-RICERCATORE-TEST\n", encoding="utf-8"
    )
    (agents / "Social Client Contact.md").write_text(
        "# Agent: Social Client Contact\n\nCONTRATTO-SOCIAL-TEST\n", encoding="utf-8"
    )
    (agents / "Social Outreach Playbook VisionBuilts.md").write_text(
        "# Social Outreach Playbook VisionBuilts\n\nNON-E-UNA-PERSONA\n", encoding="utf-8"
    )
    (agents / "Memory Librarian.md").write_text(
        "# Agent: Memory Librarian\n\nCONTRATTO-LIBRARIAN-TEST\n", encoding="utf-8"
    )
    return str(root)


class AgentNoteResolutionTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = _make_vault(Path(self._tmp.name))
        self.addCleanup(self._tmp.cleanup)

    def test_common_agent_ids_resolve_their_note(self):
        cases = {
            "programmatore": "Programmatore Project Engineer.md",
            "ricercatore": "Research Analyst.md",
            "social": "Social Client Contact.md",
            "librarian": "Memory Librarian.md",
            "memory-librarian": "Memory Librarian.md",
        }
        for agent_id, expected in cases.items():
            with self.subTest(agent=agent_id):
                path = pd._agent_note_path(agent_id, self.ws)
                self.assertIsNotNone(path, f"nota agente non risolta per {agent_id!r}")
                self.assertEqual(path.name, expected)

    def test_unknown_agent_still_returns_none(self):
        self.assertIsNone(pd._agent_note_path("agente-inesistente", self.ws))

    def test_system_prompt_contains_note_and_safety_rules(self):
        prompt = pd._worker_system_prompt("programmatore", self.ws)
        self.assertIn("CONTRATTO-PROGRAMMATORE-TEST", prompt)
        self.assertIn("REGOLE DI SICUREZZA", prompt)


class CodexSystemPromptTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = _make_vault(Path(self._tmp.name))
        self.addCleanup(self._tmp.cleanup)
        self.sent = []
        self._orig_exec = pd._codex_exec_blocking

        def fake_exec(prompt, workspace, timeout=None):  # timeout: fase del run budget
            self.sent.append(prompt)
            return "fatto"

        pd._codex_exec_blocking = fake_exec
        self.addCleanup(lambda: setattr(pd, "_codex_exec_blocking", self._orig_exec))

    def test_codex_prompt_carries_persona_and_task(self):
        out = asyncio.run(
            pd._run_codex_worker("Aggiorna il file X", self.ws, agent_id="programmatore")
        )
        self.assertEqual(out, "fatto")
        prompt = self.sent[-1]
        self.assertIn("CONTRATTO-PROGRAMMATORE-TEST", prompt, "nota agente non passata a Codex")
        self.assertIn("REGOLE DI SICUREZZA", prompt, "regole di sicurezza non passate a Codex")
        self.assertIn("Aggiorna il file X", prompt, "task perso")
        self.assertLess(
            prompt.index("CONTRATTO-PROGRAMMATORE-TEST"),
            prompt.index("Aggiorna il file X"),
            "il system prompt deve precedere il task",
        )

    def test_codex_prompt_without_agent_still_has_base_persona(self):
        asyncio.run(pd._run_codex_worker("task nudo", self.ws))
        prompt = self.sent[-1]
        self.assertIn("sotto-agente operativo di Hermes", prompt)
        self.assertIn("task nudo", prompt)

    def test_task_and_system_are_delimited(self):
        prompt = pd._codex_worker_prompt("fai la cosa", "ricercatore", self.ws)
        self.assertIn("# ISTRUZIONI DI SISTEMA", prompt)
        self.assertIn("# TASK DA ESEGUIRE ORA", prompt)
        self.assertIn("CONTRATTO-RICERCATORE-TEST", prompt)

    def test_agent_id_reaches_codex_from_run_and_store(self):
        """La delega deve propagare agent_id fino al prompt Codex."""
        seen = {}

        async def fake_codex(task, workspace, *, agent_id=None):
            seen["agent_id"] = agent_id
            return "ok"

        orig = pd._run_codex_worker
        pd._run_codex_worker = fake_codex
        self.addCleanup(lambda: setattr(pd, "_run_codex_worker", orig))
        pd._BG_TASKS["d-persona"] = {
            "id": "d-persona", "agent": "programmatore", "agent_id": "programmatore",
            "task_type": "codice", "task": "t", "status": "in_corso", "output": "",
        }
        self.addCleanup(lambda: pd._BG_TASKS.pop("d-persona", None))
        asyncio.run(
            pd._run_and_store("d-persona", "codice", "t", pd._CODEX_MODEL, "Codex", self.ws)
        )
        self.assertEqual(seen.get("agent_id"), "programmatore")


if __name__ == "__main__":
    unittest.main()
