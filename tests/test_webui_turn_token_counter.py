from pathlib import Path

from api.usage import normalize_stream_usage


ROOT = Path(__file__).resolve().parents[1]
STREAMING_PY = (ROOT / "api" / "streaming.py").read_text(encoding="utf-8")
GATEWAY_CHAT_PY = (ROOT / "api" / "gateway_chat.py").read_text(encoding="utf-8")
MESSAGES_JS = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")
UI_JS = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")


def test_normalize_stream_usage_preserves_anthropic_cache_fields():
    usage = normalize_stream_usage(
        {
            "input_tokens": 12345,
            "output_tokens": 450,
            "cache_read_input_tokens": 11900,
            "cache_creation_input_tokens": 25,
        }
    )

    assert usage["input_tokens"] == 12345
    assert usage["output_tokens"] == 450
    assert usage["cache_read_input_tokens"] == 11900
    assert usage["cache_creation_input_tokens"] == 25
    assert usage["cache_read_tokens"] == 11900
    assert usage["cache_write_tokens"] == 25


def test_streaming_forwards_dedicated_usage_sse_event():
    assert "normalize_stream_usage" in STREAMING_PY
    assert "put('usage', usage_payload)" in STREAMING_PY
    assert "event != 'usage'" in STREAMING_PY


def test_gateway_forwards_live_usage_events_from_stream_chunks():
    assert 'put_gateway_event("usage", {"session_id": session_id, **usage})' in GATEWAY_CHAT_PY
    assert "normalize_stream_usage" in GATEWAY_CHAT_PY


def test_frontend_consumes_usage_event_and_normalizes_anthropic_fields():
    assert "source.addEventListener('usage'" in MESSAGES_JS
    assert "_normalizeUsagePayload" in MESSAGES_JS
    assert "cache_read_input_tokens:cacheRead" in MESSAGES_JS
    assert "cache_creation_input_tokens:cacheWrite" in MESSAGES_JS
    assert "'metering','usage','apperror'" in MESSAGES_JS


def test_assistant_turn_usage_badge_shows_input_output_cache_with_tooltip():
    assert "function _formatAssistantUsageBadge" in UI_JS
    assert "\\u2191 ${_fmtCompactTokens(inTok)} in" in UI_JS
    assert "\\u2193 ${_fmtCompactTokens(outTok)} out" in UI_JS
    assert "Cache read input tokens:" in UI_JS
    assert "Cache creation input tokens:" in UI_JS
    assert "usage.title=formattedUsage.title" in UI_JS
    assert "usage.setAttribute('aria-label',formattedUsage.title)" in UI_JS
