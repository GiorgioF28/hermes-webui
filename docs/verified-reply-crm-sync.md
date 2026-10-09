# Verified inbound replies to VisionBuilts CRM

`api/verified_reply_sync.py` is an isolated adapter for applying a verified
inbound reply to an existing People/CRM page. It does not send messages, create
contacts, infer identity, or classify sales interest.

## Source event contract

The source adapter passes account, channel, immutable message ID, source time,
an identity key, an existing CRM page ID, and an explicit verified identity
result. Instagram identity must be scoped to the receiving business account and
IGSID; email identity must be supported by normalized sender plus matching
outreach thread/message headers. Handle similarity and generated summaries are
not identity evidence. A conflicting or missing mapping remains in review.

`identity_check(event, page)` is the boundary where the source adapter must
verify that exact mapping against the existing CRM page. It must fail closed.
The callback and event must be built from authoritative source records, not
user supplied free text.

## Update behavior

- Dedupe key: SHA-256 of receiving account + channel + immutable message ID.
- Ignore outbound echoes and autoresponders; reject non-inbound events.
- Only update the supplied existing page after identity verification.
- Set `Stato=Risposto` from `Contattato`/`Risposto`; preserve `In trattativa`,
  `Chiuso`, and `Perso`. For inconsistent statuses, require both `DM inviato`
  and `Data contatto` evidence before advancing.
- Never overwrite a newer `Data risposta`.
- Append a message-ID marker and short summary to `Note`; set `Data risposta`.
  `Canale risposta` and `Prossima azione` are written only if the current page
  exposes those properties. Existing owner, source, DM, and other fields are
  left untouched.
- A receipt is inserted only after PATCH and GET confirm the values. Failed
  writes/readback remain replayable. Audit data stores hashes/IDs, not bodies.

## Current integration boundary

The adapter is not yet wired to the live sources. Meta inbox access was blocked
in D637 (no authenticated, authorized read path); Gmail primary is blocked by
the deferred OAuth `invalid_grant`. D636 owns the active email/n8n/SQLite files,
so this patch does not edit them. Console source was inspected read-only. No
historical reply was applied to Notion and no DM/email was sent. Integration
requires coordinated source adapters and a confirmed live CRM schema before
activation.

## Verification

Run `pytest -q tests/test_verified_reply_sync.py` for adapter contract tests.
