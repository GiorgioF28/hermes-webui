# Hermes Daily Brief v2 — runbook

## Runtime files

Hermes stores local state in `data/`: `daily-email-digest.json`,
`email-inbox-accumulator.json`, `email-noise-list.json`, `daily-brief-run.json`,
and optional `email-vip.json`. All are git-ignored. Writes use a sibling `.tmp`
followed by `os.replace`.

The accumulator keeps at most 400 messages and prunes rows older than 48 hours.
It stores only the first 2000 normalized characters of the body. The body is
used for the 07:00 analysis and is deleted when that message is flushed. It is
never written to `daily-email-digest.json`.

## Required configuration

1. Set `HERMES_CRON_TOKEN` in the local WebUI `.env`; never commit its value.
2. In n8n use the existing Header Auth credential `Hermes Cron Token`, whose
   header name is `X-Hermes-Cron-Token`, on all three HTTP Request nodes. Never
   copy its value into code, docs, backups, or logs.
3. The two IMAP credentials are `IMAP account` (id `i3B9BJIveTSJgECN`) and
   `IMAP account 2` (id `VjYxl7eLUtwmtpSM`).
4. The Gmail OAuth2 credential is `Gmail account` (id `EYHotM8cXYMUtKFz`),
   credential type `gmailOAuth2` in the public API.

## Current n8n workflow (2026-09-01)

Workflow `HermesDailyBriefV2` stays `active=false` until Giorgio verifies the
credential-to-mailbox mapping and performs a reviewed manual run.

- `Schedule 07:00 Europe Rome` → `POST check DM` and → `Gmail personale`.
- `Gmail personale` (`Message: Get Many`, limit 50, simple output off, query
  `newer_than:1d`) → `Label Gmail personale` → `Normalize email rows` → `POST
  email digest`.
- `IMAP Gmail secondario` (`IMAP account 2`) → `Label Gmail secondario` → `POST
  email accumulate`.
- `IMAP Yahoo` (`IMAP account`) → `Label Yahoo` → `POST email accumulate`.

Both IMAP nodes use `format: resolved`, keep `postProcessAction: nothing` (mail
is never marked as read), and no longer set `SINCE today`. Their label nodes
emit `messageId` and a 2000-character `bodyExcerpt`.

## Accumulation and 07:00 analysis

`n8n-nodes-base.emailReadImap` is a trigger with no inputs. The two IMAP
branches therefore run independently and call `POST
/api/cron/daily-brief/email-accumulate` as messages arrive, only while the
workflow is active. With `active=false` they do not accumulate anything. Only
the Gmail branch is driven by the 07:00 schedule.

At 07:00, `POST /api/cron/daily-brief/email` captures a cutoff, merges Gmail
rows with accumulated rows at or before that cutoff, deduplicates, applies the
existing noise/VIP rules, and analyses up to 80 messages in one Prime/Anthropic
batch. A successful digest is version 2 and adds `summary`, `why`, and
`importance` without removing v1 fields. It then flushes consumed accumulator
rows while preserving rows newer than the cutoff.

If the Anthropic credential is missing, the request times out, the call raises,
or strict JSON parsing fails, Hermes writes the digest anyway with deterministic
`classify_importance` results, empty `summary`/`why`, `analysisEngine: rules`,
and a short non-secret `analysisError`. Model failure alone never turns the
email endpoint into HTTP 500.

## Verification before activation

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
