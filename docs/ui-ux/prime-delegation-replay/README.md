# Delegation polling chronology repair

The previous renderer pruned settled cards when history advanced, then recreated
them on the next three-second task poll. A missing owner caused those cards to
append after the latest reply. Task delivery flags could still say pending even
when the durable transcript already contained the matching brief.

The repair changes the read-only compact polling projection and browser scene.
The transcript and archive delivery index are authoritative for delivered brief
identity. Operational task records, queue entries, user messages, stream owners
and archive originals are preserved. Active cards retain their colors and timers;
obsolete settled cards stay outside the window. Late status replies use their
absolute transcript slot rather than appending an unindexed historical recap.

## Evidence

Synthetic fixtures execute the actual JavaScript functions and CSS in Chromium;
no application server, provider, real credentials or operator state is used.
Before images show four obsolete completed cards appended after the latest
question/answer. After images show that exchange remaining at the bottom.

| Width | Before | After |
| --- | --- | --- |
| 1200 | [Before](before-1200.png) | [After](after-1200.png) |
| 650 | [Before](before-650.png) | [After](after-650.png) |
| 360 | [Before](before-360.png) | [After](after-360.png) |

Regressions cover repeated polls, out-of-window completion, old active owners,
archived delivery metadata, unchanged queue/transcript records, late status
ordering and preservation of the current question/answer. Existing suites cover
new cards, timers, status colors, cold hydration, brief idempotency, notices,
local-turn races and multi-device synchronization.

Run `pytest tests/test_prime_delegation_replay.py tests/test_prime_brief_dedup.py`
with isolated `HERMES_HOME` and `HERMES_WEBUI_STATE_DIR`. To regenerate images,
set `HERMES_CAPTURE_REPLAY_UI=docs/ui-ux/prime-delegation-replay`.

Full live browser verification remains separate and operator-controlled.
Rollback: revert this focused commit; no state migration is required.
