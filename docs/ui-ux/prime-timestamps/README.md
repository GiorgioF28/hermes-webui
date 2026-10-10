# Command Bridge timestamp chronology

Every message and delegation displays a date and time including seconds.
User timestamps align left within their row; Prime and delegation timestamps
align right. Dates use the browser's local timezone; the semantic `time` element
and its tooltip retain UTC. Bubble placement and compact card status/timer stay
unchanged.

The source of truth is the stored message `created_at` and delegation `started` /
`started_at`, not the time a browser polls or reloads. SSE start/done events and
recap delivery responses expose the same saved timestamps as history. Live rows
adopt their server timestamp without duplicating the row. A delegation's status
and duration change in place; its original start time remains fixed.

The mutated layers are the read-only API metadata projection and browser scene.
Transcript content/indexes, provider context, operational task records, archive
originals and brief queues are preserved. Dated scene rows sort by original time,
with stable message-index/task-identity ties. Missing legacy times are explicitly
unavailable; those rows retain durable index/owner placement. Completed unowned
tasks older than the dated active window cannot recreate an obsolete tail.

## Evidence

Synthetic Chromium fixtures execute actual renderer functions and CSS without an
application server, model calls, credentials or live state. Tests include 20 polls
of 50 obsolete unowned tasks, late recap delivery, equal timestamps, stream adoption
and cold hydration with reversed input order. Backend tests compare SSE/status
metadata with durable timestamps and verify reads do not rewrite state.

| Width | Before | After |
| --- | --- | --- |
| 1200 | [Before](before-1200.png) | [After](after-1200.png) |
| 650 | [Before](before-650.png) | [After](after-650.png) |
| 360 | [Before](before-360.png) | [After](after-360.png) |

Run commands and operator-controlled live checks are in [TESTING.md](../../../TESTING.md).
The fixed historical baseline is `08048728`. Live activation/physical-device QA
remains separate; no live restart or state migration is part of this verification.
Rollback: revert this focused commit; the API timestamp fields are additive.
