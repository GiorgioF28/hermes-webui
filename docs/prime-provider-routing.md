# Prime provider selection

Prime provider selection lives in `DEFAULT_WORKSPACE/tasks/lead-brain.json`.
The selector, `/brain` commands, turn dispatch and quota timer use that same
control workspace. The execution workspace may change independently; changing
folders must not reset a manual provider selection.

An agent explicitly pinned to Codex runs Codex even if an automatic quota
cooldown exists. Startup, authentication or quota failures surface as Codex
errors instead of silently invoking Claude. Agents in Auto retain fallback.

Validation: `python -m pytest tests/test_prime_provider_pin.py tests/test_lead_brain.py tests/test_lead_brain_auto_revert.py tests/test_subagent_codex_fallback.py tests/test_prime_claude_model_guard.py -q`.

## Codex tools and context

Codex Prime connects through a loopback-only, per-turn authenticated MCP
facade to the same `delega`, `task_done` and `ask_user` handlers as Claude.
The facade shares Hermes' persistent event loop: background delegations and
Librarian passes outlive the CLI turn and keep canonical task IDs and status.
`team_status` and paginated `prime_history` read the current session only.
Capabilities are held in memory and revoked when the turn finishes or cancels.
No global Codex configuration or account credentials are changed.

Prime uses its full shared persona, relevant memory and the latest 40 canonical
messages (up to 120k characters); older messages remain accessible via
`prime_history`. Each CLI turn reconstructs context from this store rather
than resuming an unrelated personal Codex thread. The former 2–4 sentence
restriction is removed. CLI JSON events drive response/status/usage; Stop
terminates the owned process tree. Codex uses `--approve-for-me`, which includes
workspace-write sandboxing. Automatic Librarian passes honor its explicit
model selection, including Codex.

Tests: `python -m pytest tests/test_codex_prime_tools.py`.
Optional real read-only MCP smoke: set `HERMES_CODEX_TOOL_SMOKE=1` before that
suite. This calls only an isolated diagnostic tool; it performs no real work.
