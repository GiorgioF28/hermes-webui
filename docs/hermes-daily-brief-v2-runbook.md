# Hermes Daily Brief v2 — runbook

## Runtime files

Hermes stores only compact local state in `data/`: `daily-email-digest.json`,
`email-noise-list.json`, `daily-brief-run.json`, and optional `email-vip.json`.
All are git-ignored. Writes use a sibling `.tmp` followed by `os.replace`.

## Required configuration

1. Set `HERMES_CRON_TOKEN` in the local WebUI `.env`; never commit its value.
2. In n8n create one Header Auth credential whose header name is
   `X-Hermes-Cron-Token`, then select it on both HTTP Request nodes.
3. Create one `gmailOAuth2Api` credential for each Gmail mailbox and use it on
   an executable Gmail action node (`Message: Get Many`) with query
   `newer_than:1d`. Gmail Workspace policy no longer permits App Passwords, so
   Gmail must use OAuth2. Yahoo still requires a separately reviewed executable
   mail action; do not enable its IMAP trigger branch.

## n8n 2.16.2 blocker

The installed built-in node `Email Trigger (IMAP)` has `inputs: []`, belongs to
the `trigger` group, and listens continuously. It cannot be invoked by the
07:00 Schedule Trigger. Gmail has a supported executable path: replace each
Gmail IMAP trigger with a Gmail OAuth2 action node (`Message: Get Many`) and
connect Schedule → Gmail action → the existing account label → Merge.

The 2026-09-01 API inventory found no `gmailOAuth2Api` credentials, so no Gmail
node could be replaced safely: both Gmail IMAP triggers remain disabled. After
their OAuth2 credentials are created, those two branches can be migrated to
executable action nodes. Yahoo remains the open structural blocker because its
branch is still an IMAP trigger and must stay disabled until a reviewed
on-demand action is available.

The Schedule → POST check DM branch is structurally runnable after the Header
Auth credential is selected, but the full workflow must stay inactive until
the Gmail OAuth2 actions are installed and the Yahoo branch is resolved or
explicitly excluded from the reviewed design.

## Verification after Giorgio activates a corrected workflow

- Run manually once and confirm HTTP 200 on both Hermes endpoints.
- Confirm each mailbox remains unread exactly as before the run.
- Confirm the card shows a fresh local time, email counts, and DM counts.
- Confirm no email body appears in the execution data sent to Hermes.
