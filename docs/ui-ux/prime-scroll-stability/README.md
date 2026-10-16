# Prime conversation scroll stability

The browser scene is the only mutated state layer. Transcript persistence,
message indexes, timestamps, task execution, asynchronous recap generation,
voice and the planet renderer keep their existing behavior. The relevant
contract is the live UI scene/cache invariant in
[run-state consistency](../../rfcs/webui-run-state-consistency-contract.md).

Previously task placement moved a dated card back beside its owner on every
poll, while chronological sorting moved it back by timestamp. Sorting detached
every dated row through placeholder comments; each card also rebuilt its HTML.
This invalidated native text selections and generated redundant layout work.
The new placement leaves existing dated rows to the chronological sorter;
unchanged cards and timestamps do not rewrite their contents. A real reorder
keeps the longest already ordered subsequence connected, moves only displaced
rows, and restores the visible row offset and surviving text selection.

Window pruning and history synchronization retain the visible row, including
when browser scroll anchoring has already compensated for a deletion. Composer
resizing restores the prior scroll offset after measuring its temporary height.
Explicit Send reveals the new turn. Stream follow pauses while selecting text
or reading away from the bottom; cumulative tokens append to the existing Text
node when possible, preserving a selection in the live reply.

## Evidence

`tests/test_prime_scroll_polling.py` loads the real Bridge scaffold, styles and
renderers in an offline Chromium page. API/provider events and messages are
synthetic; no application server or live state is used. Fifty long messages,
three delegations with real owner/reply relationships, and twenty repeated poll
cycles produce the following comparison against `33df9187f`:

| Width | Prior move/replace operations | Fixed operations | Selection after polls |
| --- | ---: | ---: | --- |
| 1200 px | 8,960 | 0 | Retained; real clipboard copy/readback passed |
| 650 px | 8,960 | 0 | Retained; real clipboard copy/readback passed |
| 360 px | 8,960 | 0 | Retained; real clipboard copy/readback passed |

The old version lost its selection at every width. Final scroll offsets in this
stable Chromium fixture happened to remain unchanged in both versions: this is
evidence for unnecessary DOM churn and selection loss, not a measurement of the
reported production jump to the top or a frame-rate benchmark. The fixed tests
also verify viewport stability across composer clicks, actual window pruning,
task completion, explicit submit and synthetic streaming, including selecting
the live reply. `tests/test_prime_scroll_stability.py` separately exercises
history replay and out-of-order insertion. No live provider or HTTP polling
interval is exercised by these browser fixtures.

| Viewport | Before | After | Operation counts |
| --- | --- | --- | --- |
| Desktop 1200 x 800 | [Before](before-1200.png) | [After](after-1200.png) | [JSON](metrics-1200.json) |
| Narrow 650 x 800 | [Before](before-650.png) | [After](after-650.png) | [JSON](metrics-650.json) |
| Mobile 360 x 800 | [Before](before-360.png) | [After](after-360.png) | [JSON](metrics-360.json) |

Capture with `HERMES_CAPTURE_PRIME_SCROLL_UI=docs/ui-ux/prime-scroll-stability`
and `python -m pytest --noconftest tests/test_prime_scroll_polling.py -q`.
Baseline capture requires the historical commit to exist locally. After
operator-controlled activation, verify ordinary clicks, selection/copy, reading
older rows during delegation updates, new sends and long streaming replies in
the live Command Bridge. Live activation and the historical top-jump symptom
remain unverified until that operator check; no restart is part of this change.

The focused and adjacent regression run passed 135 tests, with nine optional
image-capture cases skipped. Baseline/after capture separately passed all six
poll/copy and evidence cases. JavaScript syntax and whitespace checks passed.
The adjacent Stop UI test now expects the observer-recovery status introduced
by the earlier transport fix; its prior assertion failed identically against
the pre-scroll-fix source. No Stop or recovery behavior was changed here.
