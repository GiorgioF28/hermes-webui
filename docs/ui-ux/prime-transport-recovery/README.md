# Prime transport recovery verification

Baseline: `05d19adb`. The same Chromium scene and real composer/status helpers
are used for baseline and changed captures at 1200, 650 and 360 pixels.

- `before-*.png`: partial response followed by the old transport error row.
- `after-*.png`: updated partial in the original reply, quiet recovery status
  and usable Stop while the server works.
- `delivered-*.png`: one final answer in that reply, with recovery settled.

Verification on 2026-10-16:

- Combined regression: 161 passed, 1 skipped (optional real Codex/provider smoke).
- Final transport regression, including the additional missing-heartbeat test:
  22 passed. This overlaps 21 cases in the combined run: 162 distinct passed
  cases overall, with one optional provider smoke skipped.
- Includes actual TCP shutdown through a temporary HTTP server, browser
  fetch/SSE reader, real receipt GET route and isolated durable store. Exactly
  one synthetic provider invocation and one persisted/rendered answer.
- `node --check static/command_bridge.js` and `git diff --check` pass.
- Ruff passes on the changed store and new test module. Routes retains its
  24 baseline diagnostics; comparison by rule/message/source line confirms
  zero new diagnostics. Unrelated lint cleanup is outside this fix.
- No horizontal overflow or browser script errors in responsive recovery
  scenarios. Late status after Stop cannot replace the settled partial.

No application server was started, and no live provider, live session or
production listener was modified by these tests. Physical phone, real tunnel
and live browser verification remain operator activation checks. The patch
fixes recovery; these captures do not establish a historical network cause.
