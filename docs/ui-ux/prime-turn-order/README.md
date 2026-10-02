# Command Bridge reply ownership and brief reconciliation

The repair affects the Prime durable transcript and browser timeline. Each user
turn records its stream identity; its final reply retains that identity and the
user's transcript index. The request lock protects the whole begin/finish boundary,
including background brief generation and persistence. A provider lock alone
cannot protect persistence after the provider returns.

Brief delivery uses one renderer for status polling and history. A durable
`brief_id` yields one transcript entry and one visible row. Distinct briefs with
equal text remain distinct. Background completion updates the delegation card
without introducing another reasoning placeholder into an active user reply.
Codex's canonical final item replaces intermediate prose in the settled reply.

SSE connections send heartbeat comments. A disconnected observer no longer
aborts the provider; the completed reply persists for recovery from history.
Provider errors remain errors, and EOF without a terminal event is not success.
No existing transcript is rewritten or purged by this repair.

## Verification

114 tests passed and one environment-dependent test skipped across the Prime
streaming, transport, brief identity, asynchronous delivery, session buffering,
reload sync and Codex compatibility suites. The browser cases execute the actual
renderer and status-polling functions in Chrome with synthetic responses, at
1200, 650 and 360 pixels. They include both race orderings, cold replay, empty
status fallback, usage display and unrelated messages. No application server or
model was launched for these checks.

The same deterministic history-first race produces two copies before the repair
and one afterward. These screenshots use the Command Bridge's actual CSS and
renderer with synthetic fixture text; they are not a full live-application smoke
test.

| Width | Before | After |
| --- | --- | --- |
| Desktop, 1200 | [Before](before-1200.png) | [After](after-1200.png) |
| Narrow, 650 | [Before](before-650.png) | [After](after-650.png) |
| Mobile, 360 | [Before](before-360.png) | [After](after-360.png) |

## Activation and remaining verification

The backend and frontend must be activated together by an operator-controlled
server restart and browser reload. Full live verification should exercise two
submissions, a delegation completing during a reply, and refresh during a quiet
turn. The original historical `transport_cut` incident lacked a corresponding
stack trace; these tests prove the repaired disconnect behavior, not the cause
of that historical incident.
