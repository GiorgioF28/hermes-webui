# Delegation recap delivery notices

The browser previously classified every recap that took more than 15 minutes
as undelivered, including time waiting for Prime's current turn. It stopped
observing the job and retained the retry notice even after history delivered
the recap. Waiting is now neutral status: `in coda: attende Prime` before the
shared turn lock is acquired, and `in elaborazione` afterwards. There is no
browser failure deadline. Connection observation and worker recovery have
distinct wording; a confirmed persistence failure still offers keyboard and
pointer retry.

## Contract routing

Relevant contracts: `docs/CONTRACTS.md`, `docs/UIUX-GUIDE.md`, `DESIGN.md`,
`docs/rfcs/webui-run-state-consistency-contract.md` and
`docs/rfcs/live-to-final-assistant-replies.md`.

Changed layers: brief-job observation/reservation, read-only durable delivery
projection and browser scene notices. The existing transcript is delivery truth:
its brief identity takes precedence over a stale failure or missing RAM job.
Archive brief indexes also prove historical delivery without resurrecting
archived messages. Queue event patches retain their original session metadata;
status reads do not mutate transcript, history cursors or worker results.
A durable pending queue entry without a RAM worker reports `pending_recovery`,
and a repeated POST can recover it. Concurrent retries reserve one worker under
the existing job lock; the shared Prime turn lock remains intact. A worker that
finds a recap already persisted while it waited skips provider generation.

The browser attaches notices to `brief_id`, removes only matching notices on
delivery/history/card reconciliation and deduplicates pending local requests.
An in-flight failure response cannot recreate an error after history delivery.
Compact delegation cards, timers, voice and planet ownership retain their
existing behavior. Background recaps still wait for local turns before rendering
new reply text. No data migration or new dependency is needed.

## Verification

Tests run against isolated stores and real Chromium renderer functions with
synthetic tasks and controlled timers. Evidence uses the same completed worker
and a simulated 20-minute wait before delivery; it is not a live runtime capture.

| Viewport | Before: false failure | After: waiting | Late delivery |
| --- | --- | --- | --- |
| Desktop 1200 | [Before](before-1200.png) | [After](after-1200.png) | [Delivered](delivered-1200.png) |
| Narrow 650 | [Before](before-650.png) | [After](after-650.png) | [Delivered](delivered-650.png) |
| Mobile 360 | [Before](before-360.png) | [After](after-360.png) | [Delivered](delivered-360.png) |

Backend tests cover queue metadata after terminal patches, session isolation,
restart recovery, transcript precedence despite missing queue acknowledgement,
lock waiting and simultaneous retry reservation. Browser tests cover queued to
running to done, delayed failure after history, selective removal (preserving
another recap and unrelated history errors), duplicate history and accessible
retry. Existing delivery tests verify failed writes remain retryable and
fallback acknowledgements occur only after persistence.

Activation of the paired backend/frontend remains operator-controlled; a live
smoke check after activation is still required. Rollback: revert this focused
repair commit; persisted transcript and queue formats remain compatible.
