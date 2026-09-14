"""Provider selection stays independent of the execution workspace."""
import pytest
from api import routes, lead_brain, prime_delegation as pd, agent_models


def test_prime_uses_selector_pin_from_control_workspace(monkeypatch, tmp_path):
    control = tmp_path / "control"
    execution = tmp_path / "project"
    execution.mkdir()
    lead_brain.set_lead(control, "codex", manual=True)
    lead_brain.set_lead(execution, "claude", manual=True)
    monkeypatch.setattr(routes, "DEFAULT_WORKSPACE", control)
    calls = []
    def codex(message, workspace, *args, **kwargs):
        calls.append((message, workspace))
        return {"reply": "ok"}
    def claude(*args, **kwargs):
        pytest.fail("Claude must not run when the selector pins Codex")
    monkeypatch.setattr(routes, "_hermes_prime_reply_codex", codex)
    monkeypatch.setattr(routes, "_hermes_prime_reply_claude", claude)
    assert routes._hermes_prime_reply("ciao", execution)["reply"] == "ok"
    assert calls == [("ciao", execution)]
    assert lead_brain.get_lead(execution) == "claude"


def test_brain_command_updates_same_state_as_selector(monkeypatch, tmp_path):
    control = tmp_path / "control"
    execution = tmp_path / "project"
    monkeypatch.setattr(routes, "DEFAULT_WORKSPACE", control)
    monkeypatch.setattr(routes, "_prime_background_tasks", lambda *a: [])
    routes._hermes_prime_reply("/brain codex", execution)
    assert lead_brain.get_lead_state(control)["manual"] is True
    assert lead_brain.get_lead(control) == "codex"
    assert not (execution / "tasks" / "lead-brain.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("cooldown", [False, True])
@pytest.mark.parametrize("error", ["Codex CLI not found", "Codex CLI exit 1: usage limit reached"])
async def test_pinned_agent_never_calls_claude(monkeypatch, tmp_path, cooldown, error):
    monkeypatch.setattr(agent_models, "get_overrides", lambda: {"memory-librarian": "codex"})
    monkeypatch.setattr(pd, "_codex_fallback_status", lambda: {"active": cooldown})
    calls = []
    async def codex(*a, **kw):
        calls.append("codex")
        raise RuntimeError(error)
    async def claude(*a, **kw):
        pytest.fail("Manual Codex pin must prohibit Claude fallback")
    monkeypatch.setattr(pd, "_run_codex_worker", codex)
    monkeypatch.setattr(pd, "_run_worker", claude)
    with pytest.raises(RuntimeError, match=error):
        await pd._run_codex_worker_with_fallback("task", str(tmp_path), agent_id="librarian")
    assert calls == ["codex"]
