# Verified inbound replies to VisionBuilts CRM

The adapter updates one existing People/CRM page after a source identity is matched exactly and checked against the page. It never sends messages, creates contacts, or infers commercial interest. A first inbound from an existing People email address is sufficient; thread/outbound correspondence is not required.

Email intake writes a minimized record to the `crm_outbox` table in the same SQLite transaction as the email queue insert. `api.email_crm_consumer` queries all exact `Email` matches in People, marks zero/multiple matches for review, and acknowledges one matching page only after PATCH + GET readback. Failed Notion requests remain retryable. The outbox is independent of digest cleanup. The authenticated endpoint is `POST /api/cron/daily-brief/crm-consume`; a workflow or operator must invoke it. These code and fixture changes do not imply live scheduling or a live Notion write.

## Executable consumer

`python -m scripts.sync_verified_replies --events <authorized-events.jsonl> --mapping <reviewed-map.json> --ledger <private-path>/reply-receipts.sqlite`

Set `NOTION_API_TOKEN` and `NOTION_CRM_DATABASE_ID` in the process environment. The command emits aggregate outcome counts only. Do not place tokens or the reviewed mapping in the repository.

Each JSONL event contains `channel`, `account`, immutable `message_id`, timezone-aware `occurred_at`, short `summary`, `next_action`, and source `identity_key`, plus explicit `inbound: true`, `echo: false`, and `autoresponder: false`. Missing or contradictory direction/classification flags are excluded. Instagram additionally requires `recipient_account == account` and `sender_igsid == identity_key`. The legacy export consumer still uses a reviewed mapping. The Daily Brief queue consumer instead resolves an email against every exact, paginated People email match and does not require outbound-thread evidence. Identity mismatch or missing/ambiguous identity is review-only.

The event export itself must come from an authorized source reader. The consumer is not an inbox reader or an authentication bypass. No Meta access, Gmail auth, OAuth repair, secret retrieval, or message send is implemented here. The current authorized Meta export path was unavailable in D637; Gmail primary remains blocked by the deferred `invalid_grant`.

## Email queue boundary

The Daily Brief SQLite queue owns email bodies and its digest receipt gate. CRM events contain only a short summary and are retained independently, so recap cleanup does not delete an unacknowledged CRM event. The consumer never creates new contacts and keeps unmatched or ambiguous events in durable review.

## Update and receipt behavior

- Dedupe key: SHA-256 of receiving account + channel + immutable message ID.
- Reject outbound echoes and autoresponders; reject non-inbound events.
- Preserve `In trattativa`, `Chiuso`, `Perso`, source, owner, DM history, and all prior notes. Advance an exact known contact to `Risposto` on its first inbound.
- Append a message marker and short summary without truncating long Notion rich text. Set response date and optional channel/action only when those properties exist.
- Never replace a later response. Distinct events with equal timestamps both retain their marker.
- A receipt is written only after PATCH and GET confirm status, semantically normalized date, optional properties, and marker. A repeated event after a successful PATCH is idempotent and remains recoverable until readback succeeds.

## Run checks

`pytest -q tests/test_verified_reply_sync.py tests/test_sync_verified_replies.py`

Fixture tests cover mapping spoof rejection, review-only unmatched events, preserved long notes, realistic Notion timestamp normalization, protected statuses, identity conflicts, equal-time distinct messages, out-of-order events, retry/readback failure, and SQLite reopen/cleanup. They do not prove a live source event or Notion update.
