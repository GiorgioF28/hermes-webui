# Hermes Daily Brief v2 — runbook

## Runtime files

Hermes stores only compact local state in `data/`: `daily-email-digest.json`,
`email-noise-list.json`, `daily-brief-run.json`, and optional `email-vip.json`.
All are git-ignored. Writes use a sibling `.tmp` followed by `os.replace`.

## Required configuration

1. Set `HERMES_CRON_TOKEN` in the local WebUI `.env`; never commit its value.
2. In n8n create one Header Auth credential whose header name is
   `X-Hermes-Cron-Token`, then select it on both HTTP Request nodes.
3. Create the two Gmail and one Yahoo IMAP credentials manually with app
   passwords, port 993 and SSL. Keep `postProcessAction: nothing` so messages are
   never marked read.

## n8n 2.16.2 blocker

The installed built-in node `Email Trigger (IMAP)` has `inputs: []`, belongs to
the `trigger` group, and listens continuously. It cannot be invoked by the
07:00 Schedule Trigger. The imported workflow therefore remains inactive and
its three IMAP branches remain disabled. Do not activate it as-is: replace
those branches with a reviewed executable IMAP action/community node capable
of an on-demand `SINCE` query, then connect Schedule → three branches → Merge →
Normalize → POST email digest → POST check DM.

The Schedule → POST check DM branch is structurally runnable after the Header
Auth credential is selected, but the full workflow must stay inactive until
the IMAP blocker is resolved.

## Verification after Giorgio activates a corrected workflow

- Run manually once and confirm HTTP 200 on both Hermes endpoints.
- Confirm each mailbox remains unread exactly as before the run.
- Confirm the card shows a fresh local time, email counts, and DM counts.
- Confirm no email body appears in the execution data sent to Hermes.
