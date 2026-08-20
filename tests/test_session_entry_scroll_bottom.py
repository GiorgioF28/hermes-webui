from pathlib import Path


UI_JS = Path("static/ui.js").read_text(encoding="utf-8")
SESSIONS_JS = Path("static/sessions.js").read_text(encoding="utf-8")
BOOT_JS = Path("static/boot.js").read_text(encoding="utf-8")


def _function_body(source: str, name: str, next_marker: str) -> str:
    start = source.index(f"function {name}")
    end = source.index(next_marker, start)
    return source[start:end]


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
