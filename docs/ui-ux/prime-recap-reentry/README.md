# Command Bridge recap re-entry

Opening the bridge is an observation path. Seeing a finished task, including a
genuinely unbriefed one inside the current 50-message window, cannot dispatch
another Prime turn. An actual running observation followed by completion still
starts its automatic recap once. A running card restored from history alone is
not a live observation. Manual retries remain available for confirmed failures.

Previously every terminal task without a delivered flag could start generation
on its first polling snapshot. This was distinct from duplicate transcript
injection and from the older out-of-window card replay: a backlog of genuinely
pending results was started simply by returning to the page.

Layers changed: browser recap dispatch, POST delivery projection, durable queue
acknowledgement recovery. Transcript contents, original timestamps and indexes,
operational task execution, archive originals, voice and planetary rendering
are preserved. Delivery truth is the durable transcript/archive identity;
the queue is acknowledged without calling a model when that identity exists.
An already-delivered recap also wins over a stale queued/running worker record.

Offline pending results are not deleted, marked delivered or hidden by this fix.
Their cards still display their terminal state. The existing bounded no-model
queue drain on a subsequent user turn and explicit retry remain recovery paths;
re-entry itself neither processes old tasks nor launches a burst of recap turns.

## Evidence

Synthetic Chromium runs the real renderer, request code and CSS without a
provider. Fifty terminal tasks with pending recaps and retained owners expose
the prior first-poll burst; the corrected renderer generates zero POSTs over
20 polls and repeated cold entries. A real observed completion still dispatches
once. Backend fixtures cover lost queue acks, archive identity, true pending
results, and stale worker reservations.

| Width | Before | After |
| --- | --- | --- |
| 1200 | [Before](before-1200.png) | [After](after-1200.png) |
| 650 | [Before](before-650.png) | [After](after-650.png) |
| 360 | [Before](before-360.png) | [After](after-360.png) |

Baseline: `76dda5c4`. Commands and operator-controlled live checks are in
[TESTING.md](../../../TESTING.md). These are isolated regression proofs;
physical-device and updated live-process verification remain separate.
Rollback: revert this focused commit; no state migration is required.
