from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BRIDGE = (ROOT / "static" / "command_bridge.js").read_text(encoding="utf-8")
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


def test_daily_brief_has_only_the_right_panel_card_entrypoint():
    assert "openCommandBridgeDailyBrief" not in INDEX
    assert "openCommandBridgeDailyBrief" not in BRIDGE
    assert "cbDailyOpen" not in BRIDGE
    assert 'id="cbDaily"' not in BRIDGE
    assert 'id="cbBrief"' in BRIDGE
    assert BRIDGE.index('id="cbBrief"') < BRIDGE.index("'</div>' +\n        '</section>' +\n      '</div>' +", BRIDGE.index('id="cbBrief"'))


def test_daily_brief_card_uses_new_endpoint_and_never_renders_delegations():
    assert "api/bridge/daily-brief" in BRIDGE
    assert "api/bridge/prime/daily-brief" not in BRIDGE
    assert "data.briefs" not in BRIDGE
    assert "item.outcome" not in BRIDGE
    assert "deleghe completate" not in BRIDGE


def test_daily_brief_enforces_visible_row_and_text_limits():
    assert ".slice(0, 15)" in BRIDGE
    assert ".slice(0, 10)" in BRIDGE
    assert "briefTrim(item.subject, 90)" in BRIDGE
    assert "briefTrim(reply.text, 120)" in BRIDGE


def test_daily_brief_reuses_worklog_polling_timer_at_ten_minutes():
    assert "_cbBriefPollTicks += 1" in BRIDGE
    assert "_cbBriefPollTicks >= 10" in BRIDGE
    assert "setInterval(refreshDailyBrief" not in BRIDGE
    assert "setInterval(pollWorklog, 60000)" in BRIDGE


def test_prime_header_no_longer_contains_daily_brief_button():
    start = BRIDGE.index('<div class="cb-chat-head">')
    end = BRIDGE.index('<div class="cb-chat-log"', start)
    header = BRIDGE[start:end]
    assert "Hermes Prime" in header
    assert "Opus 5" in header
    assert "Daily Brief" not in header
