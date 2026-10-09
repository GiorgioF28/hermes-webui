# Verified inbound replies to VisionBuilts CRM

The adapter updates one existing People/CRM page after a source identity is mapped and checked against the page. It never sends messages, creates contacts, or infers commercial interest.

## Executable consumer

`python -m scripts.sync_verified_replies --events <authorized-events.jsonl> --mapping <reviewed-map.json> --ledger <private-path>/reply-receipts.sqlite`

Set `NOTION_API_TOKEN` and `NOTION_CRM_DATABASE_ID` in the process environment. The command emits aggregate outcome counts only. Do not place tokens or the reviewed mapping in the repository.

Each JSONL event contains `channel`, `account`, immutable `message_id`, timezone-aware `occurred_at`, short `summary`, `next_action`, and source `identity_key`, plus inbound/echo/autoresponder flags. Instagram additionally requires `recipient_account == account` and `sender_igsid == identity_key`. Email additionally requires normalized `sender_email`, the mapped thread ID, and `in_reply_to` or a `references` entry matching a mapped outbound message ID. The input cannot nominate a Notion page or assert verified identity: the consumer discards those fields and resolves the page through the separate reviewed mapping. Mapping keys are `account|channel|identity_key`; values contain `page_id`, `notion_property` (`Handle IG` or `Email`), and the exact `identity_value` expected on that page. Email mappings also include `thread_id` and `outbound_message_ids`. Identity mismatch or missing evidence is review-only.

The event export itself must come from an authorized source reader. The consumer is not an inbox reader or an authentication bypass. No Meta access, Gmail auth, OAuth repair, secret retrieval, or message send is implemented here. The current authorized Meta export path was unavailable in D637; Gmail primary remains blocked by the deferred `invalid_grant`.

## D641 email queue boundary

D641 owns the SQLite email queue and its delivery/receipt gate. This consumer does not open, mutate, acknowledge, or delete D641 queue rows and does not process newsletter/non-CRM bodies. Before connecting the queue, D641 must provide a handoff contract that supplies source identity plus thread/outbound evidence, retains the body until CRM acknowledgement where applicable, and distinguishes excluded, review, retryable error, and synced outcomes. Until then, use a separately authorized event export. This command does not claim automatic scheduling or delivery-recap integration.

## Update and receipt behavior

- Dedupe key: SHA-256 of receiving account + channel + immutable message ID.
- Reject outbound echoes and autoresponders; reject non-inbound events.
- Preserve `In trattativa`, `Chiuso`, `Perso`, source, owner, DM history, and all prior notes. Advance only `Contattato`/`Risposto`, or a stale status with documented outbound evidence.
- Append a message marker and short summary without truncating long Notion rich text. Set response date and optional channel/action only when those properties exist.
- Never replace a later response. Distinct events with equal timestamps both retain their marker.
- A receipt is written only after PATCH and GET confirm status, semantically normalized date, optional properties, and marker. A repeated event after a successful PATCH is idempotent and remains recoverable until readback succeeds.

## Run checks

`pytest -q tests/test_verified_reply_sync.py tests/test_sync_verified_replies.py`

Fixture tests cover mapping spoof rejection, review-only unmatched events, preserved long notes, realistic Notion timestamp normalization, protected statuses, identity conflicts, equal-time distinct messages, out-of-order events, retry/readback failure, and SQLite reopen/cleanup. They do not prove a live source event or Notion update.
