# Prime capacity recovery

Codex can reject a short request with `Selected model is at capacity` before
producing any answer. Previously this became a generic `codex_exit` warning.
The classifier now exposes `codex_capacity`. Prime retries once with its worker
Codex profile when the chief attempt produced neither assistant text nor tool
activity. The normal chief profile remains unchanged. The composer displays
the fallback status; usage identifies the actual model and requested model.

## State and boundaries

The mutated layer is the provider attempt inside an already owned Prime turn,
plus the live status renderer. Transcript and FIFO ownership remain authoritative
and unchanged. The second attempt keeps the same prompt and session. Each MCP
registration is revoked before retry. Any observed tool/command/file activity,
MCP callback, assistant item or cancellation prevents replay. Quota, auth,
watchdog and generic errors do not trigger a capacity fallback. A second
capacity rejection reports its cause; there is no retry loop.

## Offline verification

`python -m pytest tests/test_prime_codex_capacity.py
tests/test_prime_codex_item_phases.py tests/test_prime_codex_watchdog.py
tests/test_bridge_errors.py tests/test_codex_prime_usage.py
tests/test_prime_recap_fifo.py tests/test_prime_brief_recovery.py
tests/test_prime_transport_continuity.py tests/test_prime_capacity_ui.py -q`

The provider tests inject actual JSON event streams into the production runner.
They verify two launches at most, identical prompts, actual usage, cleanup,
cancellation and no repeated observed work. Browser tests use the real composer
and status functions with mocked transport, at 1200/650/360 pixels. No server,
live provider, real session or delegation is started by these tests.

Set `HERMES_CAPTURE_CAPACITY_UI=docs/ui-ux/prime-capacity-recovery` to capture
before/after/delivered PNGs. Before shows the baseline generic exit warning;
after shows recovery in the existing response bubble. Delivered shows one
settled reply and no stale status. All content is synthetic.

Verification: 77 tests passed; `node --check static/command_bridge.js` and
`git diff --check` passed. The existing `audioop` deprecation warning remains.
The usage test's stale mock now accepts the existing windowed-history argument.

| View | Before | Recovery | Delivered |
| --- | --- | --- | --- |
| Desktop | [1200](before-1200.png) | [1200](after-1200.png) | [1200](delivered-1200.png) |
| Narrow | [650](before-650.png) | [650](after-650.png) | [650](delivered-650.png) |
| Mobile | [360](before-360.png) | [360](after-360.png) | [360](delivered-360.png) |

## Runtime limitation

The capacity rejection was confirmed in the private Prime turn journal.
Offline tests prove recovery mechanics, not current provider availability.
Real recovery depends on the fallback model being available; two rejected
models still produce a clear terminal error with the request preserved.
Activation and real-browser verification remain operator controlled.
