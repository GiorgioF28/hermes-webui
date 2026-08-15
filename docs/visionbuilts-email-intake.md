# VisionBuilts email intake runbook

The endpoint `POST /api/intake/email` accepts the strict event-v1 contract from the
separate n8n workflow `VisionBuilts — Email Intake → Hermes`.

## Runtime configuration

Configure these values only in the runtime/credential stores; never commit them:

- Hermes: `HERMES_INTAKE_TOKEN`, `NOTION_API_TOKEN`, `NOTION_CRM_DATABASE_ID`.
- n8n: Gmail OAuth2 for `admin@visionbuilts.net` and an HTTP Header Auth credential
  with header `Authorization` and value `Bearer <same HERMES_INTAKE_TOKEN>`.

Before enabling the workflow, add and verify these CRM properties: `External ID`
(rich text), `Canale` (select), `Email` (email), `Nome` (rich text), `Ultimo intake`
(date), and `Ultimo oggetto` (rich text). The adapter fails closed without them.

## Controlled rollout

1. Restart Hermes WebUI in an approved window so the Python endpoint is loaded.
2. Connect both n8n credentials while the workflow remains inactive.
3. Temporarily narrow the Gmail query to an explicitly approved TEST message.
4. Activate, send one test, and verify `202 queued`; replay the same Gmail message
   and verify `200 duplicate` with no second Notion page.
5. Restore `to:admin@visionbuilts.net`, then monitor failures for 48 hours.

The endpoint is local-only by design and must not be exposed through a public tunnel.
The workflow does not download attachments or mark mail as read.

## Rollback

Deactivate only the email-intake workflow, rotate/remove `HERMES_INTAKE_TOKEN`, and
revert the intake commit before an operator-approved Hermes restart. Do not touch the
ebook workflow `vt67KEU7MY0vd9vJ`.
