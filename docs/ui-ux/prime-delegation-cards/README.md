# Compact Command Bridge delegation cards

Removing the full prompt disclosure had also disabled `renderTask`, stripped
card metadata from compact task polling and removed cards during history sync.
This repair restores one visible card per delegation with ID, agent, state,
timer and a bounded objective. The disclosure arrow, full prompt, worker output
and diagnostic log are absent. Running cards remain visible before the owner's
first reply token and across history-window changes. Finished cards use a fixed
duration and are retained while their owner or final brief remains in the window.

## Contract routing and change

The changed layers are the browser scene/cache and read-only browser payload
projection (`docs/prime-history-archive-contract.md`). Previously compact chat
omitted all delegation cards. It now projects compact cards independently of
the final brief. Durable transcript slots, journal, worker state, model context,
brief generation/acknowledgement, planet activity and voice keep their existing
ownership. A revision without new messages reconciles cards; cold replay never
requests a brief, and repeated polls reuse the task ID.

## Verification and evidence

Browser tests execute the real rendering functions and injected Command Bridge
CSS with synthetic tasks in headless Chromium. Evidence shows the same running
task before and after the fix. Tests cover orange running, green completion,
red failure, ticking/frozen duration, no prompt disclosure or worker logs in DOM,
ordering of announcements and replies, multiple delegations, duplicate polls,
cold hydration, and pruning settled cards while preserving active work.
Late running snapshots cannot downgrade an observed terminal outcome.

| Viewport | Before | After |
| --- | --- | --- |
| Desktop 1200 | [Before](before-1200.png) | [After](after-1200.png) |
| Narrow 650 | [Before](before-650.png) | [After](after-650.png) |
| Mobile 360 | [Before](before-360.png) | [After](after-360.png) |

Endpoint/store tests use isolated state and verify bounded card metadata, retained
window filtering, lossless original retrieval and unchanged worker records. This
is deterministic regression evidence; a full live operator smoke remains separate.
Activation of the paired backend/frontend remains operator-controlled.

Rollback: revert the focused repair commit; no data migration is needed.
