# Hermes Daily Brief v2 — runbook

## Runtime files

Hermes stores only compact local state in `data/`: `daily-email-digest.json`,
`email-noise-list.json`, `daily-brief-run.json`, and optional `email-vip.json`.
All are git-ignored. Writes use a sibling `.tmp` followed by `os.replace`.

## Required configuration

1. Set `HERMES_CRON_TOKEN` in the local WebUI `.env`; never commit its value.
2. In n8n create one Header Auth credential whose header name is
   `X-Hermes-Cron-Token`, then select it on both HTTP Request nodes.
3. Create one Gmail OAuth2 credential for each Gmail mailbox and select it on
   the corresponding executable Gmail action node. Gmail Workspace policy no
   longer permits App Passwords, so Gmail must use OAuth2.

## Current n8n workflow

Workflow `HermesDailyBriefV2` remains inactive until Giorgio completes the
credential setup and performs a reviewed manual run.

- `Schedule 07:00 Europe Rome` starts two executable Gmail action nodes:
  `Gmail personale` and `Gmail secondario`.
- Both use `Message: Get Many`, limit 50, simplified output disabled, and Gmail
  query `newer_than:1d`.
- Each Gmail node feeds its account label, then `Merge accounts`,
  `Normalize headers only`, and `POST email digest`.
- Yahoo has no branch in the workflow. Yahoo mail is auto-forwarded to Gmail
  and is collected there.
- `POST check DM` remains a direct branch from the 07:00 schedule.

The n8n API reports Gmail credentials with the node credential type
`gmailOAuth2`. `Gmail personale` currently has `Gmail account` selected.
`Gmail secondario` intentionally has no credential selected and shows the n8n
credential-missing warning until Giorgio completes the second OAuth consent.

## Create a Gmail OAuth2 credential

Repeat these steps for each mailbox that does not yet have its own credential:

1. In Google Cloud Console, select or create the project used for n8n.
2. Enable the Gmail API for that project.
3. Configure the OAuth consent screen if Google requires it.
4. Create an OAuth client ID with application type **Web application**.
5. In n8n, open the Gmail OAuth2 credential dialog and copy the redirect URI
   shown by n8n.
6. Add that exact URI to the Google OAuth client's authorized redirect URIs.
7. Copy the Client ID and Client Secret into the n8n credential dialog. Never
   place either value in this repository or in a run report.
8. Click **Sign in with Google**, choose the intended mailbox, grant consent,
   and save the credential.
9. Open the matching Gmail node and select the credential from its dropdown.

Keep the workflow inactive until both Gmail nodes have their intended
credentials and the manual verification below succeeds.

## Verification before activation

- Run manually once and confirm HTTP 200 on both Hermes endpoints.
- Confirm each mailbox remains unread exactly as before the run.
- Confirm the card shows a fresh local time, email counts, and DM counts.
- Confirm no email body appears in the execution data sent to Hermes.
- Activate the workflow only after all checks pass.
