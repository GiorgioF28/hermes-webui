# Hermes Daily Brief v2 — runbook

## Runtime files

Hermes stores local state in `data/`: `daily-email-digest.json`,
`email-inbox-accumulator.json` (pending analysis), `email-archive.json`
(persistent unique messages), `email-noise-list.json`, `daily-brief-run.json`,
and optional `email-vip.json`. All are git-ignored. Writes use a sibling `.tmp`
followed by `os.replace`.

The pending queue has no age or item-count pruning. The archive is independent
of the queue and is never flushed when a digest succeeds. Both deduplicate by
account and stable message ID (fallback: account, sender, subject and receive
time). A model error or partial/invalid response writes only a fallback digest
and leaves pending rows queued; successful model classification runs in batches
of 80 and clears only the cutoff rows after the digest write succeeds. Verify
the local deployment's request and storage limits before relying on large
message bodies; the cron JSON request limit is 1 MiB.

## Required configuration

1. Set `HERMES_CRON_TOKEN` in the local WebUI `.env`; never commit its value.
2. In n8n use the existing Header Auth credential `Hermes Cron Token`, whose
   header name is `X-Hermes-Cron-Token`, on all three HTTP Request nodes. Never
   copy its value into code, docs, backups, or logs.
3. The two IMAP credentials are `IMAP account` (id `i3B9BJIveTSJgECN`) and
   `IMAP account 2` (id `VjYxl7eLUtwmtpSM`).
4. The Gmail OAuth2 credential is `Gmail account` (id `EYHotM8cXYMUtKFz`),
   credential type `gmailOAuth2` in the public API.

## Current n8n workflow (2026-09-04)

Workflow `HermesDailyBriefV2` is `active=true` and was updated at
`2026-09-04T14:03Z`.

- `Schedule 07:00 Europe Rome` → `POST check DM` and → `Gmail personale`.
- `Gmail personale` (`Message: Get Many`, limit 50, simple output off, query
  `newer_than:1d`) → `Label Gmail personale` → `Normalize email rows` → `POST
  email digest`.
- `IMAP Gmail secondario` (`IMAP account 2`) → `Label Gmail secondario` → `POST
  email accumulate`.
- `IMAP Yahoo` (`IMAP account`) → `Label Yahoo` → `POST email accumulate`.

Both IMAP nodes keep `postProcessAction: nothing` (mail is never marked as
read), `trackLastMessageId: true`, `forceReconnect: 60`, and the current filter
`UNSEEN SINCE Sept 4`. Their label nodes emit `messageId` and a 2000-character
`bodyExcerpt`.

## Accumulation and 07:00 analysis

`n8n-nodes-base.emailReadImap` is a trigger with no inputs. The two IMAP
branches therefore run independently and call `POST
/api/cron/daily-brief/email-accumulate` as messages arrive. They must not be
connected to the Schedule node. Only the Gmail branch is driven by the 07:00
schedule.

At 07:00, `POST /api/cron/daily-brief/email` captures a cutoff, merges Gmail
rows with accumulated rows at or before that cutoff, deduplicates, applies the
existing noise/VIP rules, and analyses up to 80 messages in one Prime/Anthropic
batch. A successful digest is version 2 and adds `summary`, `why`, and
`importance` without removing v1 fields. It then flushes consumed accumulator
rows while preserving rows newer than the cutoff.

Instagram replies in the Daily Brief are read from the original reply
`timestamp` (not the scanner's `detectedAt`) and limited to the preceding 24
hours. Invalid and future timestamps are excluded before the latest reply per
handle is selected. Empty subjects are retained as empty strings; missing
subject fields, senders, or invalid/missing receive dates remain source data
errors and must not be replaced with fabricated values.

The brief API adds `email.sourceStatus` (`current`, `stale`, or `error`),
`email.generatedAt`, and separate `email.accumulatorStatus` metadata. The
`daily-brief-run.json` source records for digest, IMAP accumulation, and DM
checks are independent; success in one source does not clear another source's
error. A missing or prior-day digest is reported as stale even when a newer DM
check succeeds.

If the Anthropic credential is missing, the request times out, the call raises,
or strict JSON parsing fails, Hermes writes the digest anyway with deterministic
`classify_importance` results, empty `summary`/`why`, `analysisEngine: rules`,
and a short non-secret `analysisError`. Model failure alone never turns the
email endpoint into HTTP 500.

## Recovery and verification after auth-gate fix

Commit `8d111b27` lets `/api/cron/daily-brief/*` pass the cookie gate so the
dedicated cron-token validation can run. It is committed and tested (19 tests)
but is not live until Giorgio chooses to restart the 8788 process.

- After restart, toggle the workflow `active` off/on via API to revive the IMAP
  triggers, which stopped producing executions after the OOM crashes.
- Confirm one n8n `POST email accumulate` execution returns HTTP 200 and the
  accumulator receives an item.
- The next scheduled digest is at 07:00 Europe/Rome. To obtain a brief on the
  same day, use `Execute workflow` in the n8n UI; the Public API cannot start a
  manual workflow execution.
- Keep in mind that `postProcessAction=nothing` leaves mail UNSEEN: reconnects
  can replay it. Hermes deduplicates content, but future work should reduce the
  n8n load and OOM risk.

## Verification checklist

- Confirm in the n8n UI that `IMAP account` is the Yahoo mailbox and `IMAP
  account 2` is the secondary Gmail mailbox.
- Put the real local cron value into `Hermes Cron Token` without recording it,
  then run manually and confirm HTTP 200 on the accumulator, email digest, and
  check-DM endpoints.
- Confirm each IMAP message remains unread.
- Confirm the card shows mailbox badges, importance ordering, sender, subject,
  and summary/why when present; a v1 digest must still render.
- Confirm `daily-email-digest.json` contains no `bodyExcerpt` and the
  accumulator is empty after flush except for rows newer than the cutoff.
- Activate the workflow only after all checks pass.

## Backups

`docs/backups/daily-brief-v2/` holds pre/post snapshots of every API PUT. The
accumulator rewiring snapshots are:

- `20260901T160208Z-pre-accumulator.json`
- `20260901T160208Z-post-accumulator.json`
