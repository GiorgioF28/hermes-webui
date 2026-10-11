# Prime recap FIFO and settled notices

The old renderer left a recap's last “in elaborazione” notice visible after
the server reported durable delivery whenever a local user turn was still
running. `finish()` deferred both rendering and lifecycle cleanup. The fixed
renderer settles the request's notice immediately and delays only the saved
row's rendering, retaining retry deduplication until adoption.

The shared Python mutex prevented simultaneous provider execution but did not
promise FIFO ordering. A per-session condition queue now reserves arrival
order before recap worker dispatch and before a user turn begins. Ownership
covers provider work and durable persistence. Startup failure cancels the
reservation, worker exceptions release ownership and mark retryable failure,
and tracked dead workers cancel waiting reservations and report recovery.
The user-turn offline fallback drain skips recaps with a reserved live worker;
it cannot replace their pending model result or bypass their FIFO slot.
No timeout revokes ownership from a still-running provider.

## Contract routing

Runtime/recovery and live-to-final rendering: `docs/CONTRACTS.md`,
`docs/rfcs/webui-run-state-consistency-contract.md`,
`docs/rfcs/live-to-final-assistant-replies.md`, `docs/UIUX-GUIDE.md`, `DESIGN.md`.
State layers changed: in-memory turn ownership, in-memory brief job health,
and browser notice lifecycle. Transcript schema, durable brief identity,
timestamps, retained history and delegation execution are unchanged.

The invariant is one provider/transcript settlement owner per session, in
reservation order; a delivered recap cannot retain a running notice.
Roll back the change as one commit if needed; no data migration is required.

## Evidence

Tests use temporary state and an isolated Chromium renderer with actual
Command Bridge functions and styles, synthetic delegation content and provider
substitutes. Pytest's shared fixture starts and stops an isolated test server;
no production process is restarted or provider contacted. Baseline: `d8f1eaad`.

| Width | Before: saved result with lingering notice | After: notice settled during user turn | Result adopted once |
| --- | --- | --- | --- |
| 1200 | [before](before-1200.png) | [after](after-1200.png) | [delivered](delivered-1200.png) |
| 650 | [before](before-650.png) | [after](after-650.png) | [delivered](delivered-650.png) |
| 360 | [before](before-360.png) | [after](after-360.png) | [delivered](delivered-360.png) |

The browser regression holds a local user turn open, accepts a delivered
recap, asserts that its running notice is gone, then settles the user turn.
It verifies one recap row and no duplicate POST through status/history replay.
Backend regressions dispatch three reserved workers in reverse order, mix a
user reservation between recaps, block the first save, inject worker/startup
errors and reconcile a dead worker against durable delivery.

Validation: 104 tests and 4 subtests passed across the recap, recovery,
transport, async, deduplication, re-entry, timestamp and reload suites; the
final six-case UI run also passed (three cases overlap that suite, for 107
distinct tests). JavaScript syntax, diff whitespace and ruff checks on the
new queue and regression modules passed. Test-server teardown left no process
from this worktree; the production process start time was unchanged.

Operator-controlled activation and a real multi-result live check remain
pending; these fixtures do not claim to prove live runtime behavior.
