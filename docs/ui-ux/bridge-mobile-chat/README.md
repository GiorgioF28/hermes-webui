# Command Bridge mobile conversation layout

This branch preserves the existing mobile layout patch. Below 680 pixels the
conversation fills the available width, the planet scene becomes a noninteractive
background without backdrop blur, and touch controls sit below the draft.
The fullscreen button provides native fullscreen where available and a CSS
fallback. Long drafts can collapse when the conversation is tapped and reopen
without changing their value or selection.

Only browser presentation state changes. The transcript, provider requests,
voice handlers, delegation lifecycle and scene renderer are unchanged.

## Verification

`tests/test_command_bridge_mobile_layout.py` exercises the real scaffold, styles
and scene renderer with isolated browser fixtures and stubbed submit/API/voice
hooks. Ten browser tests passed in installed Chrome at widths 1200, 650, 390 and
360, including long-draft round trips, keyboard-sized viewports and native and
fallback fullscreen. JavaScript syntax validation also passed. No application
server or real user state was opened.

The fallback fixture assigns its rejecting stub inside a block so Playwright
does not await the intentionally rejected function itself.

| State | Before | After |
| --- | --- | --- |
| Desktop | [1200](before-1200.png) | [1200](after-1200.png) |
| Narrow | [650](before-650.png) | [650](after-650.png) |
| Mobile | [390](before-390.png) | [390](after-390.png), [360](after-360.png) |
| Draft | | [Expanded](draft-expanded.png), [collapsed](draft-collapsed.png) |
| Fullscreen | | [Native](fullscreen-native.png), [fallback](fullscreen-fallback.png) |

This is an isolated branch commit, not a live deployment or integration into the
operational checkout. A physical smartphone check with the real keyboard and
navigation remains necessary before claiming live validation.
