"""I sotto-agenti ricevono le regole del workspace (AGENTS.md) nel prompt.

Come in hermes-agent upstream ("every subagent's prompt embeds the workspace's
project context files"): i worker ricevevano solo la nota persona, mentre le
regole di lavoro (base = branch checkout-ato, niente origin/master, niente
segreti nel repo, idee in 02-Ideas) stanno in AGENTS.md del workspace.
"""

from __future__ import annotations

import pytest

from api import prime_delegation as pd

AGENTS_MD = "# Hermes Agent Guide\n\n## Regole di lavoro\n\n- MAI fast-forwardare su origin/master.\n- Non salvare token nel repo.\n"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("HERMES_WORKER_CONTEXT_FILES", raising=False)


def _workspace(tmp_path, agents_md=AGENTS_MD):
    if agents_md is not None:
        (tmp_path / "AGENTS.md").write_text(agents_md, encoding="utf-8")
    return tmp_path


def test_agents_md_is_embedded_in_worker_prompt(tmp_path):
    ws = _workspace(tmp_path)
    prompt = pd._worker_system_prompt("ricercatore", str(ws))
    assert "MAI fast-forwardare su origin/master" in prompt
    assert "Regole del workspace" in prompt


def test_agents_md_comes_before_safety_rules_and_after_persona(tmp_path):
    ws = _workspace(tmp_path)
    (ws / "obsidian-vault" / "06-Agents").mkdir(parents=True)
    (ws / "obsidian-vault" / "06-Agents" / "Research Analyst.md").write_text("# Agent: Research Analyst\n\nPERSONA-MARKER\n", encoding="utf-8")
    prompt = pd._worker_system_prompt("ricercatore", str(ws))
    assert prompt.index("PERSONA-MARKER") < prompt.index("MAI fast-forwardare") < prompt.index("REGOLE DI SICUREZZA")


def test_without_agents_md_prompt_is_unchanged(tmp_path):
    ws = _workspace(tmp_path, agents_md=None)
    assert "Regole del workspace" not in pd._worker_system_prompt("ricercatore", str(ws))


def test_agents_md_is_capped(tmp_path):
    ws = _workspace(tmp_path, agents_md="x" * 50_000)
    prompt = pd._worker_system_prompt("ricercatore", str(ws))
    assert len(prompt) < 12_000
    assert "tronc" in prompt.lower()


def test_can_be_disabled(tmp_path, monkeypatch):
    ws = _workspace(tmp_path)
    monkeypatch.setenv("HERMES_WORKER_CONTEXT_FILES", "0")
    assert "MAI fast-forwardare" not in pd._worker_system_prompt("ricercatore", str(ws))


def test_codex_prompt_carries_it_too(tmp_path):
    ws = _workspace(tmp_path)
    prompt = pd._codex_worker_prompt("fai una cosa", "programmatore", str(ws))
    assert "MAI fast-forwardare" in prompt
    assert prompt.index("MAI fast-forwardare") < prompt.index("# TASK DA ESEGUIRE ORA")
