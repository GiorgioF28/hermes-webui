# Prime provider selection

Prime provider selection lives in `DEFAULT_WORKSPACE/tasks/lead-brain.json`.
The selector, `/brain` commands, turn dispatch and quota timer use that same
control workspace. The execution workspace may change independently; changing
folders must not reset a manual provider selection.

An agent explicitly pinned to Codex runs Codex even if an automatic quota
cooldown exists. Startup, authentication or quota failures surface as Codex
errors instead of silently invoking Claude. Agents in Auto retain fallback.

Validation: `python -m pytest tests/test_prime_provider_pin.py tests/test_lead_brain.py tests/test_lead_brain_auto_revert.py tests/test_subagent_codex_fallback.py tests/test_prime_claude_model_guard.py -q`.
