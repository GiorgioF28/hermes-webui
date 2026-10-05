# Prime history archive module contract

**Status: integrated; activated only by an operator-staged cold-start request.**
`api/prime_history_archive.py` supplies planning/retrieval and guarded apply;
`api/prime_archive_activation.py` persists the checked archive and original
backup before replacing the active state. The running process is never changed
by staging a request.

## State model

- Each archived entry records its original absolute `messages[]` index and the
  complete original message. Applying a plan replaces that slot with a small
  `_prime_archive` tombstone. The array length never changes, so browser cursors,
  `prime_history` offsets, and delegation/brief message anchors stay absolute.
- A tombstone is not active history or model context. It is not fetched from the
  archive during polling. Old content is available only through an explicit
  archive lookup by original index.
- Entries before the timezone-aware cutoff are selected by message timestamp.
  Missing or unparseable timestamps are retained. The cutoff is exclusive.
- The archive bundle must be atomically persisted and verified before the live
  state file is replaced. `apply_archive_atomically` checks the original file
  hash and rejects active turns. Its caller must hold the store lock while
  reading the active-turn state, persisting the archive, and applying the plan.
- Restore verifies archive identity and tombstone identity, then puts each
  original message back into its exact slot. The external archive file remains
  the recovery source until a separately reviewed retention decision.
- Preserve `pending_turn`, `journal`/tool events, `settings` (including pending
  todo snapshot), delegation records/revisions, and all non-message state as-is.

## Cold-start integration

`PrimeSessionStore` checks for `<session-stem>.archive-request.json` after
recovering a stale pending turn, before accepting any new turns. The request is
private state beside the session file, never tracked in Git. Its fields are
`schema: 1`, the matching `session_id`, a timezone-aware `cutoff`, and a short
non-secret `summary` (at most 6,000 characters). No request means no archive.

The operation runs under the store lock during cold construction. It builds
from the current disk state, so messages written after a dry run are preserved;
the hash guard rejects a source changed during application. It writes a full
byte-exact original backup, archive bundle and checksum manifest in
`sessions/_prime_archives/<source-sha256>/`, then commits the candidate state.
`updated_at` is preserved: maintenance is not conversation activity. On failure
the request remains available for retry; after success it becomes
`<session-stem>.archive-request.applied.json`. Repeated identical requests check
the existing bundle and never archive the session twice.

The active slots contain small tombstones. `history()` filters those slots and
includes each visible message's absolute `message_index`; `message_count` and
`since_index` still count all slots. The browser consumes the absolute index and
shows a collapsed archive summary. Polling never retrieves archived contents;
settled delegation cards anchored entirely to the archive are hidden, while
active/pending records and their durable anchors remain intact.

Codex receives the retained messages and the summary. A fresh Claude client
receives the same bounded recovery context; its new SDK session does not resume
the archived conversation. `prime_history` uses `retrieve_history()` to recover
full originals only when explicitly requested. Retrieval checks the bundle hash
and identity. An archived brief retry reuses its original message index.

## Integration surfaces

1. **Apply and active-turn gate:** `PrimeSessionStore._activate_archive_request`
   runs at cold construction, before route/provider workers exist, under
   `PrimeSessionStore._lock`; active/pending turns and buffered tokens are rejected
   by `activate_request`. There is no live archive mutation endpoint.
2. **Durable slots/cursors:** `api/prime_session_store.py::history` (line 523)
   and `history_with_tool_events` (line 798). Keep the original slot list and
   `total`/`message_count` semantics. Do not compact the list or shift offsets.
   Retain journal and tool events unchanged.
3. **Visible transcript:** `api/routes.py::_handle_bridge_prime_history` (line
   12147) and `static/command_bridge.js::syncPrimeTranscriptFromServer` (line
   2624), plus the history renderer it calls. Send/consume the same slot count;
   return only visible rows with absolute indexes while advancing the cursor
   for every slot.
   Do not fetch archive contents in initial load, delta polls, or task polls.
4. **AI context:** `api/codex_prime.py::prompt_history` (line 305) and
   `build_prompt` (line 340). Filter tombstones from model context while keeping
   each retained row's original slot index and total slot count. This is what
   prevents the next Codex turn from rehydrating old content.
5. **Explicit retrieval:** `api/codex_prime.py::build_tools` / `prime_history`
   (lines 30–44). Resolve an archived index from the private bundle only when
   Prime requests that original offset; report archived total/offsets without
   placing archive contents into routine context.
6. **Anchors and pending state:** the delegation fields
   `anchor_message_index`/`brief_message_index`, pending clarify/brief markers,
   `pending_turn`, settings, journal, and tool events remain byte/logically
   unchanged by planning. Add integration tests for UI cursor, context filter,
   explicit retrieval, concurrent append/hash mismatch, and active-turn reject
   before exposing the archive action.

## Standalone API

- `build_archive_bundle(source_bytes, cutoff, summary=None)` returns a manifest
  bundle plus candidate state without writing either.
- `select_archive_entries(messages, cutoff)` selects only reliably dated rows.
- `retrieve_archived_message(bundle, original_index)` is explicit retrieval.
- `restore_archived_messages(state, bundle)` returns a validated restored copy.
- `write_json_atomic(path, payload)` atomically replaces one JSON file.
- `apply_archive_atomically(path, expected_source_sha256, candidate_state,
  active_turn=...)` rejects active turns and changed source. The caller must
  hold the store lock and atomically persist the archive bundle first.

`build_archive_bundle` can plan from an active snapshot, but the apply helper
cannot apply it while a turn is active. Planning is not evidence that the
archive is active.

## Rollback

With the server stopped, verify the bundle checksum recorded in session
`_prime_archive`, call `restore_archived_messages(current_state, bundle)`, and
persist the returned state atomically. This restores original slots while
preserving all newer messages, journals and delegation records. Keep the
original backup and bundle; remove any unapplied request before starting again.
Copying `original.json` directly would discard newer turns and is only appropriate
when no activity has occurred since archiving.
