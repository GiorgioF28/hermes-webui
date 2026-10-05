# Prime history archive module contract

**Status: prepared and tested; not integrated or active.** The module in
`api/prime_history_archive.py` is a pure planning/retrieval layer plus an
atomic guarded apply helper. No existing route, store, prompt, or browser code
calls it.

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

## Exact Prime integration points

These are the current `feat/prime-context-unlocked` source locations, not
changes made by this package:

1. **Apply and active-turn gate:** `api/routes.py::_prime_turn_lock` (around
   line 11548), `_prime_active_snapshot` (around 11616), and the turn entry
   paths around 11798/11992. The archive operation must acquire the existing
   per-session turn lock and `PrimeSessionStore._lock`, check both in-memory
   activity and persisted `pending_turn`, then persist bundle before replacing
   the store. Never run it during a Prime turn.
2. **Durable slots/cursors:** `api/prime_session_store.py::history` (line 523)
   and `history_with_tool_events` (line 798). Keep the original slot list and
   `total`/`message_count` semantics. Do not compact the list or shift offsets.
   Retain journal and tool events unchanged.
3. **Visible transcript:** `api/routes.py::_handle_bridge_prime_history` (line
   12147) and `static/command_bridge.js::syncPrimeTranscriptFromServer` (line
   2624), plus the history renderer it calls. Send/consume the same slot count;
   skip rendering tombstone rows while advancing the cursor for every slot.
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
