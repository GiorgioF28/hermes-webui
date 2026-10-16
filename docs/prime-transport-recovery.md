# Prime browser transport recovery

Prime's browser is an observer of server work. A rejected fetch, EOF without
`done`, or 45 seconds without SSE heartbeat now enters recovery on the same
conversation row. It does not complete the turn or submit the prompt again.
Stop retains the original request/stream identity throughout recovery.

## State and ownership

- The durable Prime transcript stores the existing browser `request_id` on its
  user row before the first SSE acknowledgement. The field is optional, so old
  sessions and callers remain compatible. No migration is required.
- Authenticated `GET /api/bridge/prime/live?request_id=...` adds a session-scoped
  receipt. It distinguishes queued, running, done, cancelled, error, interrupted
  and unknown, and returns only the matching turn's partial/final answer.
  Another active turn cannot become the recovered reply. The lookup is read-only.
- A terminal answer replaces the local partial and adopts its original durable
  indexes, timestamps, usage and artifact cards. Restart partials keep their
  stream/owner identity and remain interrupted, never successful final answers.
- Status reads have a five-second deadline and run sequentially, with two
  seconds between attempts. A failed read keeps recovery visible. Settlement
  or acknowledged Stop cancels outstanding reads and ignores late responses.
- An unknown receipt after 30 seconds restores the text and saved attachments
  for review and asks the user to check history before resending. This is not
  proof that the original POST can never arrive. No automatic POST retry occurs.
- A new frontend against a backend without receipt support reports that recovery
  is unavailable, rather than adopting an unrelated turn. Backend and frontend
  must be activated together for full recovery.

The browser reports one bounded event to the existing authenticated
`/api/client-events/log`: source `command_bridge_prime`, reason
`fetch_or_stream_failed`, `eof_before_done`, `browser_abort` or
`heartbeat_timeout`, stream identity when known, visibility, online flag and
pathname. Prompt text, attachments, raw exception strings, query strings and
credentials are excluded. Network loss can prevent the diagnostic itself from
arriving; its absence is not proof that no disconnect occurred.

## Verification

`python -m pytest tests/test_prime_transport_recovery.py
tests/test_prime_transport_continuity.py tests/test_prime_stop_progress.py
tests/test_command_bridge_prime_streaming.py tests/test_prime_session_store_buffer.py
tests/test_prime_capacity_ui.py tests/test_prime_image_upload.py -q`

The regression uses temporary state, Chromium at 1200/650/360 pixels, simulated
fetch/EOF faults before acknowledgement and after partial output, and a bounded
HTTP server with an actual TCP shutdown. The provider is a synthetic local
function; the HTTP test proves one provider call, one durable answer and one
rendered answer. It launches no application `server.py` and closes its server.

Set `HERMES_CAPTURE_TRANSPORT_UI=docs/ui-ux/prime-transport-recovery` to capture
before/after/delivered images from baseline `05d19adb`.

The change fixes observer recovery and adds evidence; it does not establish
the root cause of a historical browser or proxy disconnect. After operator
activation, verify one ordinary turn on the actual access path and correlate
any new event's time/stream ID with sanitized server/proxy logs. Do not replay
operational tasks or intentionally disconnect a live task to test this patch.

Rollback: revert the recovery commit as a unit. The optional transcript field
can remain on disk and is ignored by older versions. Activation/restart remains
an operator decision.
