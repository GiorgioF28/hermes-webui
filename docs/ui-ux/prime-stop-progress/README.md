# Prime Stop and observable progress

Prime now labels native Codex commands, MCP tools, web search and file changes
from actual provider events. Display metadata includes elapsed time, provider
inactivity and explicit reconnect signals. No command strings, arguments,
reasoning text or tool output enter status metadata. A transport heartbeat
keeps observation alive without resetting provider inactivity. At 60 seconds
without provider progress the UI says so; after 45 seconds without observer
signals it reports loss of connection signals. Neither warning invents a
provider failure, and Stop remains available. The existing provider watchdog
and hard cap are preserved.

Stop sends the authenticated CSRF header, targets a specific browser request
and stream, and waits up to five seconds for acknowledgement. Failure retains
the button and owner. An acknowledged Stop settles that owner's placeholder,
releases its local busy count and aborts only its fetch; late events cannot
reopen it. Queued requests are cancelled without launching a provider or
cancelling another turn. The same event survives provider startup. Stop that
beats the initial POST is retained for five minutes in a bounded map (256
entries). Prompt pipe writes run separately so a blocked stdin cannot block
cancellation polling. No delegation subprocess or unrelated task is stopped.

## Contract routing and state

Relevant contracts: docs/CONTRACTS.md, the run-state consistency and live-to-final
RFCs, pending-intent controls, docs/UIUX-GUIDE.md and DESIGN.md.
Changed layers: browser request ownership and observation, in-memory request
cancel intents, FIFO reservations, provider cancellation and progress metadata.
Durable transcript schema is unchanged; its existing cancel operation preserves
partial replies. FIFO ownership remains held until provider cleanup/settlement
returns. Stop acknowledgement records accepted cancellation, not proof of all
external effects being undone. Rollback: revert this commit; no data migration.

## Evidence

All browser scenes use actual Command Bridge functions/styles, fake providers
and synthetic messages. No live 8788 mutation or provider call is used.

| Width | Baseline placeholder | Native command activity | Acknowledged Stop |
| --- | --- | --- | --- |
| 1200 | [before](before-1200.png) | [after](after-1200.png) | [stopped](stopped-1200.png) |
| 650 | [before](before-650.png) | [after](after-650.png) | [stopped](stopped-650.png) |
| 360 | [before](before-360.png) | [after](after-360.png) | [stopped](stopped-360.png) |

Regressions cover failed/hung Stop, stale late events, queued/early Stop,
Stop beating POST, blocked prompt pipe, native activity privacy, FIFO ownership,
capacity fallback, partial/final answers, uploads and streaming continuity.
Production activation and the reported thirteen-minute freeze remain unverified.

The private journal was read only for event types, IDs and timings. The preceding
PDF-explanation turn finished in 62.7844 seconds; no cancel event is recorded for
that turn. This proves backend settlement, not that the browser saw its terminal
event or which defect caused the reported freeze. Independent read-only audit:
projects/prime-stop-progress-20261013-audit.md (outside this repository).

Validation: 121 automated tests passed (three evidence-only cases skipped in
that suite). All nine browser cases were also run with capture enabled to
produce the before/after/Stop PNGs above. node --check, Python compilation and
git diff --check passed. The repository autouse fixture starts a temporary server.py on its derived test
port with isolated home/state and tears it down. No worktree server.py process
remained after verification; production 8788 was unchanged.

Final verification after startup and recovery refinements: 61 passed, three
capture-only cases skipped, covering Stop/progress, streaming and reload sync.
These overlap the earlier suite; counts are not summed. Screenshots were
visually inspected at desktop, narrow and mobile widths.
