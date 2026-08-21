import json
from pathlib import Path
import subprocess


UI_JS = Path("static/ui.js").read_text(encoding="utf-8")
SESSIONS_JS = Path("static/sessions.js").read_text(encoding="utf-8")
BOOT_JS = Path("static/boot.js").read_text(encoding="utf-8")
COMMAND_BRIDGE_JS = Path("static/command_bridge.js").read_text(encoding="utf-8")


def _function_body(source: str, name: str, next_marker: str) -> str:
    start = source.index(f"function {name}")
    end = source.index(next_marker, start)
    return source[start:end]


def _function_source(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    brace = source.index("{", start)
    depth = 0
    for pos in range(brace, len(source)):
        if source[pos] == "{":
            depth += 1
        elif source[pos] == "}":
            depth -= 1
            if depth == 0:
                return source[start : pos + 1]
    raise AssertionError(f"Unclosed function: {name}")


def test_session_entry_scroll_waits_for_async_layout_without_smooth_animation():
    helper = _function_body(
        UI_JS,
        "settleSessionEntryScrollToBottom",
        "function _fmtOllamaLabel",
    )
    assert "requestAnimationFrame(()=>requestAnimationFrame(snap))" in helper
    assert "new ResizeObserver(snap)" in helper
    assert "new MutationObserver" in helper
    assert "addEventListener('load',snap,{once:true})" in helper
    assert "[80,180,400,800,1400]" in helper
    assert "behavior:'smooth'" not in helper


def test_completed_session_load_arms_entry_scroll_but_force_refresh_preserves_position():
    load_session = _function_body(SESSIONS_JS, "loadSession", "// ── Handoff hint logic")
    assert "if(!sameSessionForceReload&&typeof settleSessionEntryScrollToBottom==='function')" in load_session
    assert "settleSessionEntryScrollToBottom(sid);" in load_session


def test_tab_return_and_bfcache_restore_rearm_bottom_settle():
    assert "await refreshActiveSessionIfExternallyUpdated('visible');" in SESSIONS_JS
    assert "settleSessionEntryScrollToBottom(sid);" in SESSIONS_JS
    pageshow = BOOT_JS[BOOT_JS.index("window.addEventListener('pageshow'") :]
    assert "settleSessionEntryScrollToBottom(S.session.session_id);" in pageshow


def test_streaming_follow_helpers_are_unchanged_by_session_entry_hook():
    stream_follow = _function_body(UI_JS, "scrollIfPinned", "function scrollToBottom")
    assert "settleSessionEntryScrollToBottom" not in stream_follow
    messages_js = Path("static/messages.js").read_text(encoding="utf-8")
    assert "settleSessionEntryScrollToBottom" not in messages_js


def test_command_bridge_history_arms_settle_on_its_real_scroller():
    """d355 only covered #messages; Prime history lives in the separate #cbLog."""
    load_history = _function_body(
        COMMAND_BRIDGE_JS,
        "loadPrimeHistory",
        "/* ── Allegati foto per Hermes Prime",
    )
    tool_events = load_history.index("(data.tool_events || []).forEach")
    settle = load_history.index("settlePrimeHistoryScrollToBottom()")
    assert settle > tool_events


def test_command_bridge_settle_tracks_late_layout_and_stops_on_user_intent():
    """Execute the real helper against the #cbLog geometry missed by d355."""
    helper = _function_source(COMMAND_BRIDGE_JS, "settlePrimeHistoryScrollToBottom")
    script = f"""
const listeners = {{}};
const child = {{ nodeType: 1, tagName: 'DIV', querySelectorAll: () => [] }};
const log = {{
  scrollHeight: 1000,
  scrollTop: 0,
  children: [child],
  querySelectorAll: () => [],
  addEventListener: (name, fn) => {{ listeners[name] = fn; }},
  removeEventListener: (name, fn) => {{ if (listeners[name] === fn) delete listeners[name]; }}
}};
function $(id) {{ return id === 'cbLog' ? log : null; }}
let resizeCallback = null;
class ResizeObserver {{ constructor(cb) {{ resizeCallback = cb; }} observe() {{}} disconnect() {{}} }}
let mutationCallback = null;
class MutationObserver {{ constructor(cb) {{ mutationCallback = cb; }} observe() {{}} disconnect() {{}} }}
function requestAnimationFrame(cb) {{ cb(); }}
function setTimeout() {{ return 1; }}
function clearTimeout() {{}}
var _cancelPrimeHistoryBottomSettle = null;
{helper}
settlePrimeHistoryScrollToBottom();
if (log.scrollTop !== 1000) throw new Error('initial Prime history was not pinned');
log.scrollHeight = 1400;
resizeCallback();
if (log.scrollTop !== 1400) throw new Error('late Prime layout growth was not followed');
log.scrollHeight = 1600;
mutationCallback([{{ addedNodes: [child] }}]);
if (log.scrollTop !== 1600) throw new Error('late Prime card insertion was not followed');
listeners.wheel();
log.scrollHeight = 1800;
resizeCallback();
if (log.scrollTop !== 1600) throw new Error('user scroll intent did not cancel settling');
console.log(JSON.stringify({{ok: true, scrollTop: log.scrollTop}}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"ok": True, "scrollTop": 1600}
