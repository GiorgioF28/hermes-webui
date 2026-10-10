# Hermes Daily Brief v2

## Email storage and delivery

Email intake, pending analysis, and replay deduplication use the Python
standard-library SQLite database at `STATE_DIR/daily-brief/email-queue.sqlite3`
(`STATE_DIR` is `HERMES_WEBUI_STATE_DIR`, or Hermes WebUI's normal private state
directory). SQLite uses WAL, `synchronous=FULL`, a 15-second busy timeout, and
unique `(account, message key)` constraints. The key prefers the provider's
stable message ID and otherwise hashes account, sender, subject, and receive
time. Accumulation returns HTTP 200 only after its transaction commits.

The first use backs up the old `email-archive.json` and
`email-inbox-accumulator.json` into the private state's
`daily-brief/migration-backups/`, then imports pending message bodies and
processed message receipts once. Source JSON remains untouched for rollback,
but once the migration marker commits, SQLite is authoritative and the legacy
JSON files are not read or parsed again. Stale pending rows cannot restore
processed bodies after restart. A replay matching a receipt is skipped before
an INSERT, including after outbox recovery and cleanup.

The recap uses the latest completed civil window `[07:00 previous day, 07:00
current day)` in `Europe/Rome` and records its UTC start/end. DST days naturally
span 23 or 25 hours. The start is inclusive and the end exclusive, so a message
at exactly 07:00 belongs to the next window. The queue snapshot selects only
that interval in one SQLite transaction; older backlog and late arrivals stay
pending for an explicit `windowStart`/`windowEnd` recovery request. A completed
window returns the same persisted digest on retry rather than publishing a
second recap. An explicit repeat of a completed window checks for eligible late
arrivals; it analyzes only those new rows and reconciles their summaries into
the same window ledger, without replaying already receipted messages. Arrivals
during AI analysis remain pending. Every non-noise
snapshot row needs one valid
Prime result. Provider errors and partial replies return retryable HTTP 503 and
retain the queue. After successful analysis, SQLite commits a prepared recap to
an outbox, Hermes atomically publishes the same versioned digest read by the
existing Daily Brief card/API, then SQLite records minimal receipts and deletes
only that snapshot's bodies. A retry after a crash republishes the same
`digestId` and finishes cleanup without appending a second recap. The SQLite
outbox and JSON digest are separate stores; this ordered recovery protocol is
the consistency boundary, not a cross-store transaction.

An empty recap does not prove zero mail. Each source needs a successful
same-day acquisition record; failed or unverified accounts carry an explicit
error. The IMAP trigger branches remain independent and do not mark mail read.
Instagram remains a separate source.

## CRM email outbox

The same SQLite transaction that accepts an email also records a minimized CRM
event (normalized sender email, account, provider message ID, timestamp, short
subject/excerpt summary, and next action). The original body stays only in the
email queue. Recap cleanup can therefore proceed independently of Notion
availability without losing a CRM event. A separate authenticated
`POST /api/cron/daily-brief/crm-consume` drains this outbox. It queries every
exact Email match in People with pagination, updates only one existing page,
and requires PATCH plus GET readback before marking the event acknowledged.
Zero or multiple matches move the event to durable review; transient failures
leave it retryable. The consumer never creates a person or sends a message.

The n8n workflow must call the CRM consumer on a bounded recurring trigger and
retain its existing Header Auth credential reference. This code change does not
alter the live workflow or establish that the consumer is scheduled.

The default runtime database path is
`<HERMES_WEBUI_STATE_DIR>/daily-brief/email-queue.sqlite3`; when the variable
is unset it follows Hermes' configured private state directory. The CRM
consumer and reply ledger resolve beside this database. Do not derive either
path from the repository `data/` directory.

The VisionBuilts Console's signed Meta webhook now also writes minimized CRM
relay receipts to KV. Authenticated GET polling and POST ack use the existing
`CRM_SYNC_TOKEN`. Relay events preserve the IGSID separately from
`verified_handle`; Hermes only matches the verified handle against Notion's
exact `Handle IG` property. An event without a verified handle stays in durable
review and must not be matched by treating an IGSID as a username.

The workflow patch candidate adds a one-minute CRM consumer schedule,
independent of the 07:00 recap. Publishing still requires a configured,
authenticated Console relay poll/ingest/ack chain and a current n8n runtime
compatibility check. Never substitute the Hermes cron credential for the
separate Console relay credential.

The authenticated `POST /api/cron/daily-brief/status` reports storage protocol
and pending count without exposing message content. A workflow candidate must
not be imported or activated until it reports `storage=sqlite`,
`schemaVersion=2`, and `recapProtocol=outbox-v1`. CRM consumer readiness is a
separate authenticated call; it does not change the recap storage gate.

## Rollback

Before rolling back to the JSON implementation, run
`python scripts/daily_brief_sqlite_export_legacy.py --data-dir <legacy-data-dir>`
while the SQLite implementation is available. The exporter backs up existing
legacy files, writes SQLite pending rows to the accumulator, and merges pending
rows and minimal receipts into the archive. Review the exports; keep SQLite
unchanged until the old process is verified. The digest JSON already contains
the last published recap. Never restore a migration backup over current queue
state without merging post-migration pending rows.
Receipts retain only the message ID or fallback identity (sender, subject,
receive time), never message bodies. Export marks an existing pending archive
row processed when its receipt matches, preventing the legacy reader from
reanalyzing it after rollback, including fallback-hash rows.

## n8n workflow

`HermesDailyBriefV2` uses the existing Header Auth credential
`Hermes Cron Token` (`X-Hermes-Cron-Token`) for all Hermes endpoints. Do not
copy its value into workflow code, docs, backups, or logs.

Read-only inspection on 2026-10-09 found the workflow active. The latest
execution, `269382`, was a manual Gmail-only run and did not reach an HTTP node.
The latest observed `POST email accumulate` failure was execution `269368` on
2026-10-07. `IMAP Gmail secondario` reached the POST node, where
`emails[0].receivedAt` was rejected as invalid (HTTP 422 under the current route
contract). The current Gmail normalizer also references an undefined
`bodyText`. Execution payloads and email contents must not be copied to logs or
reports.

`scripts/prepare_daily_brief_workflow_patch.py` transforms an exported workflow
and writes a candidate JSON file; it never writes to the n8n API. It validates
workflow identity and active state, fixes Gmail date/body normalization,
records source status through the accumulation endpoint, separates Gmail
acquisition from recap generation, adds bounded retries, and leaves IMAP
triggers independent. It copies the existing Header Auth reference unchanged.
Prepare/import only after the status endpoint reports SQLite `outbox-v1`. Until
then, leave the live workflow and credentials unchanged because the 8788
process may still have the old backend loaded.

The intended graph is:

```text
Schedule 07:00 Europe/Rome ──┬── Gmail personale → normalize → email-accumulate → daily recap
                             └── check DM
IMAP Gmail secondario ─────────── email-accumulate
IMAP Yahoo ───────────────────── email-accumulate
```

Gmail errors continue through normalization as an explicit source error, so
the recap still runs from already accumulated mail. Invalid dates are reported
as source errors and never replaced with the current time. Retries are bounded
to three attempts; IMAP post-process remains `nothing`.

## Verification after activation

1. Ask Giorgio to restart 8788 after reviewing the pushed commit; this runbook
   does not restart it.
2. Call the authenticated status endpoint and confirm SQLite, schema 2,
   `outbox-v1`, and the pending count.
3. Import/activate the candidate workflow and verify one synthetic or
   deduplicated intake returns HTTP 200 only after the pending count changes.
4. Verify the recap API/card exposes summaries and source errors, and that no
   digest field contains `bodyText` or `bodyExcerpt`.
5. Confirm only the delivered snapshot is removed from SQLite and its minimal
   receipt prevents an IMAP replay from re-adding it.

The n8n workflow remains unchanged until those conditions are met.
