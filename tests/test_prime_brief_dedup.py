"""Brief identity across status polling, history replay and retries."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path

import pytest
from api.prime_session_store import PrimeSessionStore


def test_brief_injection_is_atomic_and_survives_reload(tmp_path):
    store = PrimeSessionStore(tmp_path / "prime.json")
    with ThreadPoolExecutor(max_workers=4) as pool:
        indices = list(pool.map(lambda _: store.inject_assistant_message(
            "brief", {"brief_id": "brief-d528"}), range(8)))
    assert indices == [0] * 8
    reloaded = PrimeSessionStore(store.path)
    assert reloaded.inject_assistant_message("retry", {"brief_id": "brief-d528"}) == 0
    assert [m["content"] for m in reloaded.history()["messages"]] == ["brief"]
    assert reloaded.inject_assistant_message("brief", {"brief_id": "brief-d529"}) == 1
    assert reloaded.inject_assistant_message("brief") == 2
    assert reloaded.inject_assistant_message("brief") == 3


@pytest.fixture(scope="module")
def browser():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel=os.getenv("HERMES_TEST_BROWSER_CHANNEL") or None)
        yield browser
        browser.close()


def _renderer_source():
    source = Path("static/command_bridge.js").read_text(encoding="utf-8")
    request = source[source.index("  function requestBrief(t)"):source.index("  var _cbPollTimer")]
    history = source[source.index("  function renderPrimeHistoryMessage("):source.index("  function renderPrimeHistoryMessages(")]
    return request + history


HARNESS = """
var _cbRenderedCount = 12, _cbOwnTurnCount = 0, userEngaged = true, spoken = [];
var retryNotices = [];
function sysNoteRetry(text, retry) { retryNotices.push({text:text, retry:retry}); }
window._showTokenUsage = true;
function $(id) { return document.getElementById(id); }
function setOrb() {}
function renderRich(node, text) { node.textContent = text; }
function speak(text) { spoken.push(text); }
function _hasBridgeUsage(usage) { return !!(usage && usage.input_tokens); }
function _formatAssistantUsageBadge(usage) { return 'in ' + usage.input_tokens; }
function _bridgeUsageTitle() { return 'usage'; }
function alignRenderedCountAfterOwnTurn() { throw Error('brief skipped unseen history'); }
function primeSay(role, text) {
  var node = document.createElement('div'); node.className = 'cb-msg';
  node.innerHTML = '<div class="cb-who">HERMES PRIME</div><div class="cb-bubble"></div><div class="cb-msg-foot" hidden></div>';
  node.querySelector('.cb-bubble').textContent = text; $('cbLog').appendChild(node); return node;
}
function pendingBubble() { return primeSay('prime', 'Sto ragionando…'); }
function createPrimeTurnUi() {
  var closed = false; return {isClosed: () => closed, close: () => {closed = true;}};
}
window.setTimeout = cb => { window.nextPoll = cb; };
window.fetch = () => Promise.resolve({json: () => Promise.resolve({pending: true})});
function api() { return new Promise(resolve => { window.finishStatus = resolve; }); }
var message = {role:'assistant', content:'Dossier verificato. Futureino resta un candidato: documentazione incompleta.',
  brief_id:'brief-d528', usage:{input_tokens:79000}};
"""


@pytest.mark.parametrize("width", [1200, 650, 360])
@pytest.mark.parametrize("history_first", [True, False])
def test_status_and_history_share_one_node(browser, width, history_first):
    page = browser.new_page(viewport={"width": width, "height": 900})
    try:
        page.set_content('<base href="http://brief.test/"><div id="cbLog"></div>')
        page.add_script_tag(content=HARNESS + _renderer_source())
        page.evaluate("requestBrief({id:'d528'})")
        page.wait_for_function("typeof nextPoll === 'function'")
        page.evaluate("nextPoll()")
        if history_first:
            page.evaluate("renderPrimeHistoryMessage(message, 12)")
        page.evaluate("finishStatus({reply:message.content, usage:message.usage})")
        page.wait_for_function("spoken.length === 1")
        if not history_first:
            page.evaluate("renderPrimeHistoryMessage(message, 12)")
        page.evaluate("renderPrimeHistoryMessage(message, 12); renderPrimeHistoryMessage(message, 13)")
        assert page.locator(".cb-msg").count() == 1
        assert page.locator(".cb-msg").get_attribute("data-cb-msg-index") == "12"
        assert page.locator(".cb-bubble").inner_text() == page.evaluate("message.content")
        assert page.locator(".cb-msg-foot").inner_text() == "in 79000"
        assert page.evaluate("_cbRenderedCount") == 12
        page.evaluate("renderPrimeHistoryMessage({role:'user',content:'Altro dispositivo'}, 14); "
                      "renderPrimeHistoryMessage({role:'assistant',content:message.content}, 15)")
        assert page.locator(".cb-msg").count() == 3
    finally:
        page.close()


def test_fallback_history_survives_empty_status_and_cold_replay(browser):
    page = browser.new_page()
    try:
        page.set_content('<base href="http://brief.test/"><div id="cbLog"></div>')
        page.add_script_tag(content=HARNESS + _renderer_source())
        page.evaluate("requestBrief({id:'d528'})")
        page.wait_for_function("typeof nextPoll === 'function'")
        page.evaluate("nextPoll(); renderPrimeHistoryMessage(message, 12); finishStatus({reply:''})")
        page.evaluate("() => Promise.resolve()")
        assert page.locator(".cb-msg").count() == 1
        page.evaluate("$('cbLog').innerHTML=''; renderPrimeHistoryMessage(message, 12); renderPrimeHistoryMessage(message, 13)")
        assert page.locator(".cb-msg").count() == 1
        assert page.evaluate("spoken.length") == 0
    finally:
        page.close()


def test_failed_delivery_is_visible_and_can_be_retried(browser):
    page = browser.new_page()
    try:
        page.set_content('<base href="http://brief.test/"><div id="cbLog"></div>')
        page.add_script_tag(content=HARNESS + _renderer_source())
        page.evaluate("requestBrief({id:'d528'})")
        page.wait_for_function("typeof nextPoll === 'function'")
        page.evaluate("nextPoll(); finishStatus({state:'failed_retryable',reply:''})")
        page.wait_for_function("retryNotices.length === 1")
        assert 'd528' in page.evaluate("retryNotices[0].text")
        assert page.locator('.cb-msg').count() == 0
        page.evaluate("async () => { retryNotices[0].retry(); "
                      "for(var i=0;i<5;i++) await Promise.resolve(); "
                      "nextPoll(); finishStatus({state:'done',reply:message.content}); }")
        page.wait_for_function("spoken.length === 1")
        assert page.locator('.cb-msg').count() == 1
    finally:
        page.close()


@pytest.mark.parametrize('width', [1200, 650, 360])
def test_background_brief_and_idle_placeholder_do_not_hide_recaps(browser, width):
    source = Path('static/command_bridge.js').read_text(encoding='utf-8')
    sync = source[source.index('  function ownPrimeTurnInFlight()'):source.index('  function syncPrimeTranscriptFromServer(')]
    harness = """
var _primeStreaming=false, _cbRemoteTurnNode=null, _cbOwnTurnEndedAt=0;
var _primeLiveGeneration=0, _primeLiveTimer=null, syncs=0;
function setPrimeStreaming(active) {_primeStreaming=active;}
function startPrimeLivePolling() {_primeStreaming=true;}
function syncPrimeTranscriptFromServer() {syncs++;}
"""
    page = browser.new_page(viewport={'width': width, 'height': 900})
    try:
        page.set_content('<base href="http://brief.test/"><div id="cbLog"></div>')
        page.add_script_tag(content=HARNESS + _renderer_source() + harness + sync)
        page.evaluate('applyPrimeLiveSnapshot({streaming:false,background_activity:true,message_count:12})')
        assert page.locator('.cb-remote-turn').count() == 0
        page.evaluate('applyPrimeLiveSnapshot({streaming:true,message_count:12})')
        assert page.locator('.cb-remote-turn').count() == 1
        assert 'altro dispositivo' not in page.locator('.cb-remote-turn').inner_text()
        if os.getenv('HERMES_CAPTURE_RECAP_UI'):
            output = Path(os.environ['HERMES_CAPTURE_RECAP_UI'])
            output.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(output / f'running-{width}.png'))
        page.evaluate("renderPrimeHistoryMessage(message, 12); "
                      "renderPrimeHistoryMessage({...message,brief_id:'brief-d529',task_id:'d529',content:'Secondo esito'}, 13); "
                      "_cbOwnTurnCount=1; _primeStreaming=true; "
                      "applyPrimeLiveSnapshot({streaming:false,message_count:14})")
        assert page.locator('.cb-remote-turn').count() == 0
        assert page.evaluate('_primeStreaming') is True
        assert page.locator('[data-cb-brief-id]').count() == 2
        page.evaluate("_cbOwnTurnCount=0; applyPrimeLiveSnapshot({streaming:false,message_count:14})")
        assert page.locator('[data-cb-brief-id]').count() == 2
        if os.getenv('HERMES_CAPTURE_RECAP_UI'):
            page.screenshot(path=str(output / f'final-{width}.png'))
    finally:
        page.close()
