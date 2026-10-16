# Prime replies to open questions

Prime questions remain answerable after their tool wait ends. A timeout no
longer instructs Prime to choose for the user. Answering an open card uses the
authenticated Prime streaming endpoint and its existing FIFO/Stop/recovery path.

If the original tool still owns a waiter, the answer wakes it and no second
provider turn starts. Timeout detach and response delivery share the clarify
lock, including the exact deadline race. After timeout or process restart, the
answer starts one continuation carrying the original question, the user's
answer and a bounded snapshot of the six preceding conversational messages.
Snapshot rows retain original indexes for full `prime_history` retrieval.
The continuation instructs Prime to verify current work and avoid duplicate
delegations. Provider inference and external actions still require their usual
operational verification; this transport fix cannot guarantee model decisions.

## State and invariants

- The session's `clarify_requests` registry owns question payloads, context,
  accepted answers and delivery identity independently of the 50-message view.
  It is additive private session state, with lazy hydration of older retained
  question rows. Already archived pre-feature questions cannot be reconstructed
  automatically from an ID absent from retained state.
- The visible transcript retains one question and one answer per ID. A late
  continuation reuses the answer row as stream owner, without another user row.
  Recovery context is sent to the provider separately from visible answer text.
- `clarify-<id>` is the durable request receipt. The FIFO owner checks it before
  starting provider work; simultaneous devices and identical retries observe
  the first result. Conflicting answers and unknown/cross-session IDs fail.
  Interrupted or cancelled execution is not automatically repeated.
- History exposes unresolved cards independently of the rolling window, so a
  reload can restore them after timeout/restart. Only resolved cards are read
  only. Answer submission preserves the composer's draft and attachments.
- In-memory clarify queues track waiter ownership; session JSON owns durable
  answers; the existing pending turn, journal and stream own continuation work.
  No polling or history read starts provider work.

## Verification and activation

Run `python -m pytest tests/test_prime_late_clarify.py
tests/test_ask_user_command_bridge.py tests/test_clarify_timeout_unlimited.py
tests/test_prime_transport_recovery.py tests/test_prime_transport_continuity.py -q`.
The browser tests drive the real card, submitter, SSE parser and backend handler
with isolated state and a synthetic provider at 1200/650/360 px. They do not
launch `server.py` or contact the live provider. Set
`HERMES_CAPTURE_LATE_CLARIFY_UI` to capture before/after submission evidence.

After operator-controlled activation, answer a timed-out open question and
confirm one contextual response appears below the answer; reload and verify the
card is resolved. A still-waiting question must continue its original reply.
Rollback is the code revert with operator-controlled activation; the additive
registry can stay in private session state.
