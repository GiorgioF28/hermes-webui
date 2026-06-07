from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_chat_stream_idle_watchdog_preserves_partial_before_cancel():
    src = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")

    assert "_streamIdleCancelMs" in src
    assert "window._streamIdleCancelMs" in src
    assert "function _cancelIdleStream(source)" in src

    idle_cancel = src[src.index("async function _cancelIdleStream(source)") :]
    idle_cancel = idle_cancel[: idle_cancel.index("function _markStreamActivity(source)")]

    assert "syncInflightAssistantMessage();" in idle_cancel
    assert "persistInflightState();" in idle_cancel
    assert "api/chat/cancel?stream_id=" in idle_cancel


def test_chat_stream_idle_watchdog_is_cleared_on_terminal_events():
    src = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")

    done_idx = src.index("source.addEventListener('done'")
    stream_end_idx = src.index("source.addEventListener('stream_end'")
    apperror_idx = src.index("source.addEventListener('apperror'")
    cancel_idx = src.index("source.addEventListener('cancel'")

    assert "_clearStreamIdleWatchdog();" in src[done_idx : done_idx + 200]
    assert "_clearStreamIdleWatchdog();" in src[stream_end_idx : stream_end_idx + 220]
    assert "_clearStreamIdleWatchdog();" in src[apperror_idx : apperror_idx + 220]
    assert "_clearStreamIdleWatchdog();" in src[cancel_idx : cancel_idx + 220]
