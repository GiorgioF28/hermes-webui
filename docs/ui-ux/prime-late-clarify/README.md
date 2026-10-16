# Late answer evidence

The isolated Chromium fixture exercises the actual card, streaming submitter,
SSE parser and Prime backend handler with a synthetic provider. No live state,
external delegation, application server or paid model is used.

At 1200, 650 and 360 px:

- `before-fix-*` reproduces baseline `cd1694a7`: a timed-out waiter accepts the
  choice but produces no follow-up reply (accepted legacy response simulated).
- `before-submit-*` shows the selected open question and retained draft.
- `after-submit-*` shows a resolved question, one user answer, one contextual
  provider reply, and the unchanged draft.

Automated checks also cold-render the saved resolved card and reject another
submission. The targeted runtime/history regression finished with 129 passed
and 1 optional provider smoke skipped. `node --check static/command_bridge.js`
and `git diff --check` passed. Screenshots were visually inspected at mobile
width. Physical phone and live provider behavior remain activation checks.
