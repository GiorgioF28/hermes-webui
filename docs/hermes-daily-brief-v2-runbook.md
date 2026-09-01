# Hermes Daily Brief v2 — runbook

## Runtime files

Hermes stores only compact local state in `data/`: `daily-email-digest.json`,
`email-noise-list.json`, `daily-brief-run.json`, and optional `email-vip.json`.
All are git-ignored. Writes use a sibling `.tmp` followed by `os.replace`.

## Required configuration

1. Set `HERMES_CRON_TOKEN` in the local WebUI `.env`; never commit its value.
2. In n8n create one Header Auth credential whose header name is
   `X-Hermes-Cron-Token`, then select it on both HTTP Request nodes.
3. Two IMAP credentials already exist and are used: `IMAP account`
   (id `i3B9BJIveTSJgECN`) and `IMAP account 2` (id `VjYxl7eLUtwmtpSM`).
4. One Gmail OAuth2 credential already exists: `Gmail account`
   (id `EYHotM8cXYMUtKFz`), credential type `gmailOAuth2` in the public API.

## Current n8n workflow (2026-09-01)

Workflow `HermesDailyBriefV2` stays inactive until Giorgio verifies the
credential-to-mailbox mapping and performs a reviewed manual run.

Three mail sources, one Gmail and two IMAP:

- `Schedule 07:00 Europe Rome` → `POST check DM` and → `Gmail personale`.
- `Gmail personale` is the OAuth2 action node (`Message: Get Many`, limit 50,
  simple output off, query `newer_than:1d`) → `Label Gmail personale` →
  `Merge accounts` input 0.
- `IMAP Gmail secondario` (credential `IMAP account 2`) → `Label Gmail
  secondario` → `Merge accounts` input 1.
- `IMAP Yahoo` (credential `IMAP account`) → `Label Yahoo` → `Merge accounts`
  input 2.
- `Merge accounts` (3 inputs) → `Normalize headers only` → `POST email digest`.

Both IMAP nodes are enabled, `postProcessAction: nothing` (mail is never marked
as read), `customEmailConfig` restricted to `SINCE` today, `forceReconnect` 60.

## Structural constraint of the IMAP nodes

`n8n-nodes-base.emailReadImap` is a **trigger** node: it declares no inputs, so
it cannot be placed downstream of `Schedule 07:00 Europe Rome`. n8n core has no
IMAP *action* node, so there is no mode switch that turns it into one. The two
IMAP branches therefore run as independent trigger entry points: they fire when
new mail arrives, and only while the workflow is active. Only the Gmail branch
is driven by the 07:00 schedule.

Consequence for `Merge accounts`: an execution started by one IMAP trigger
carries only that account's rows. `Normalize headers only` labels rows by
account (`gmail-personale`, `gmail-secondario`, `yahoo-personale`) and reports a
zero count for the accounts absent from that execution.

## Verification before activation

- Confirm in the n8n UI that `IMAP account` is really the Yahoo mailbox and
  `IMAP account 2` is really the secondary Gmail mailbox; the two credentials
  were previously assigned to all three nodes indiscriminately, so the mapping
  written here is an assignment to be checked, not an observed fact.
- Paste the real `HERMES_CRON_TOKEN` value into the `Hermes Cron Token`
  credential, then run manually once and confirm HTTP 200 on both endpoints.
- Confirm each mailbox remains unread exactly as before the run.
- Confirm the card shows a fresh local time, email counts, and DM counts.
- Confirm no email body appears in the execution data sent to Hermes.
- Activate the workflow only after all checks pass.

## Backups

`docs/backups/daily-brief-v2/` holds the pre/post snapshots of every API PUT,
including `20260901T153038Z-pre-imap-restore.json` and
`20260901T153047Z-post-imap-restore.json` for this change.
