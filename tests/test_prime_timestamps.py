"""Original timestamps define the dated scene across late polling and reload."""
import os
import subprocess
from pathlib import Path

import pytest

from api.prime_session_store import PrimeSessionStore
from tests.test_prime_delegation_timeline import HARNESS, browser, renderer_source  # noqa: F401


def test_delivery_timestamp_is_original_and_reads_do_not_rewrite(tmp_path):
    store = PrimeSessionStore(tmp_path / 'prime.json')
    stream = store.begin_turn('Question')
    user_at = store.message_created_at(0)
    index = store.finish_turn(stream, 'Answer')
    answer_at = store.message_created_at(index)
    brief = store.inject_assistant_message('Recap', {'brief_id': 'brief-d1'})
    original = store.brief_delivery('brief-d1')
    before = store.path.read_bytes()
    assert store.message_created_at(-1) is None
    assert store.message_created_at(None) is None
    assert store.message_created_at(999) is None
    assert user_at <= answer_at <= original['created_at']
    assert store.brief_delivery('brief-d1') == original
    assert store.path.read_bytes() == before
    assert store.inject_assistant_message('Retry', {'brief_id': 'brief-d1'}) == brief
    reopened = PrimeSessionStore(store.path)
    assert reopened.brief_delivery('brief-d1') == original
    assert reopened.history()['messages'][0]['created_at'] == user_at


def setup(page, source):
    page.set_content('<style>body{margin:0;background:#111;color:#eee}#cbLog{display:flex;flex-direction:column;gap:12px;padding:18px;--cb-accent:#ff6a00;--cb-accent-dim:#874021;--cb-accent-2:#ffb47e;--cb-text:#eee;--cb-muted:#aaa;--cb-faint:#aaa;--cb-sans:Arial;--cb-mono:monospace}</style><div id="cbLog"></div>')
    styles = source[source.index('  function injectStyles()'):source.index('  /*', source.index('  function injectStyles()'))]
    say = source[source.index('  function primeSay('):source.index('  var _cbHistoryLoaded')]
    window = source[source.index('  function applyPrimeHistoryWindow('):source.index('  function renderPrimeHistoryPayload(')]
    page.add_script_tag(content=HARNESS + renderer_source(source) + say + window + styles + '\ninjectStyles();')
    page.evaluate("""var base=1791615600;
      renderPrimeHistoryMessage({role:'user',content:'Voglio ordine e orari visibili',created_at:base},10);
      renderPrimeHistoryMessage({role:'assistant',content:'Verifico la cronologia.',created_at:base+60},11);
      renderPrimeHistoryMessage({role:'user',content:'Questa è la mia richiesta più recente',created_at:base+180},12);
      renderPrimeHistoryMessage({role:'assistant',content:'La risposta resta dopo la tua richiesta.',created_at:base+240},13);
      renderTask({id:'d1',agent:'programmatore',status:'in_corso',started:base+90,summary:'Correggere ordine e timestamp',anchor_message_index:10},{replay:true});
      repositionPrimeTaskCards();""")


@pytest.mark.parametrize('width', [1200, 650, 360])
def test_timestamp_alignment_late_events_polling_and_cold_replay(browser, width):
    source = Path('static/command_bridge.js').read_text(encoding='utf-8')
    page = browser.new_page(viewport={'width': width, 'height': 700}, timezone_id='Europe/Rome')
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        evidence = os.getenv('HERMES_CAPTURE_TIMESTAMP_UI')
        if evidence:
            baseline = subprocess.check_output(['git', 'show', '08048728:static/command_bridge.js']).decode('utf-8')
            setup(page, baseline)
            Path(evidence).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(evidence) / f'before-{width}.png'))
        setup(page, source)
        assert page.evaluate('rows()') == ['Voglio ordine e orari visibili', 'Verifico la cronologia.', 'd1', 'Questa è la mia richiesta più recente', 'La risposta resta dopo la tua richiesta.']
        assert page.locator('.cb-from-user .cb-msg-time').first.evaluate('(n)=>getComputedStyle(n).textAlign') == 'left'
        assert page.locator('.cb-from-prime .cb-msg-time').first.evaluate('(n)=>getComputedStyle(n).textAlign') == 'right'
        assert page.locator('.cb-deleg .cb-msg-time').evaluate('(n)=>getComputedStyle(n).textAlign') == 'right'
        original = page.locator('.cb-deleg time').get_attribute('datetime')
        # Fifty older tasks with no surviving owner must not rebuild the tail.
        page.evaluate("applyPrimeHistoryWindow({history_window:{start_index:10,visible_indexes:[10,11,12,13]}});for(var poll=0;poll<20;poll++){for(var i=0;i<50;i++)renderTask({id:'old'+i,status:'ok',started:base-3600-i,brief_status:'delivered'});renderTask({id:'d1',status:'ok',started:base+300,finished:base+300},{replay:true,briefed:true});}")
        assert page.locator('.cb-deleg').count() == 1
        assert page.locator('.cb-deleg time').get_attribute('datetime') == original
        assert page.evaluate('rows()')[-2:] == ['Questa è la mia richiesta più recente', 'La risposta resta dopo la tua richiesta.']
        # A late recap uses its saved timestamp even with a later transcript slot.
        page.evaluate("renderPrimeHistoryMessage({role:'assistant',content:'Recap salvato prima della richiesta',brief_id:'brief-d1',created_at:base+120},14);repositionPrimeTaskCards();")
        expected = page.evaluate('rows()')
        assert expected[3] == 'Recap salvato prima della richiesta'
        assert expected[-1] == 'La risposta resta dopo la tua richiesta.'
        # Cold hydrate in deliberately reversed transport order, then poll again.
        page.evaluate("""var snapshot=Array.from($('cbLog').querySelectorAll('[data-cb-msg-index]')).map(n=>({role:n.getAttribute('data-cb-role'),content:n.querySelector('.cb-bubble').textContent,created_at:Number(n.getAttribute('data-cb-created-at')),message_index:Number(n.getAttribute('data-cb-msg-index')),brief_id:n.getAttribute('data-cb-brief-id')}));var saved=_cbTasks.d1.task;$('cbLog').innerHTML='';_cbTasks={};snapshot.reverse().forEach(m=>renderPrimeHistoryMessage(m,m.message_index));renderTask(saved,{replay:true,briefed:true});repositionPrimeTaskCards();""")
        assert page.evaluate('rows()') == expected
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        assert page.locator('time[datetime]').count() == 6
        if evidence:
            page.screenshot(path=str(Path(evidence) / f'after-{width}.png'))
        assert not errors
    finally:
        page.close()


def test_equal_times_missing_times_and_live_adoption(browser):
    page = browser.new_page()
    try:
        setup(page, Path('static/command_bridge.js').read_text(encoding='utf-8'))
        page.evaluate("$('cbLog').innerHTML='';_cbTasks={};renderPrimeHistoryMessage({role:'assistant',content:'Second',created_at:base},2);renderPrimeHistoryMessage({role:'user',content:'First',created_at:base},1);orderPrimeTimeline();")
        assert page.evaluate('rows()') == ['First', 'Second']
        page.evaluate("var live=primeSay('prime','Live',null,true);live.setAttribute('data-cb-stream-id','s2');renderPrimeHistoryMessage({role:'assistant',content:'Final',stream_id:'s2',created_at:base+20},3);renderPrimeHistoryMessage({role:'assistant',content:'Final',stream_id:'s2',created_at:base+20},3);")
        assert page.locator('[data-cb-msg-index="3"]').count() == 1
        assert page.locator('[data-cb-msg-index="3"]').get_attribute('data-cb-created-at') == str(1791615620)
        page.evaluate("renderPrimeHistoryMessage({role:'assistant',content:'Legacy'},4)")
        assert page.locator('[data-cb-msg-index="4"] time').inner_text() == 'Ora non disponibile'
        assert page.locator('[data-cb-msg-index="4"]').get_attribute('data-cb-created-at') is None
        assert page.evaluate("primeTimestamp('invalid')") is None
        assert page.evaluate('primeTimestamp(base*1000)') == 1791615600
    finally:
        page.close()


def test_recovered_live_row_uses_original_start_and_stops_mutating_final(browser):
    source = Path('static/command_bridge.js').read_text(encoding='utf-8')
    polling = source[source.index('  function startPrimeLivePolling('):source.index('  function formatCompactTokens(')]
    page = browser.new_page()
    try:
        setup(page, source)
        page.add_script_tag(content="var _primeLiveTimer=null,_primeLiveGeneration=0;function setPrimeStreaming(){};var livePayload;function api(){return Promise.resolve(livePayload)};" + polling)
        page.evaluate("var live=primeSay('prime','Recovering',null,true);live.id='recovering';livePayload={active:true,pending_turn:{stream_id:'s3',started_at:base+200,partial_output:'Working'}};startPrimeLivePolling(live.querySelector('.cb-bubble'));")
        page.wait_for_function("$('recovering').getAttribute('data-cb-created-at')===String(base+200)")
        page.evaluate("renderPrimeHistoryMessage({role:'assistant',stream_id:'s3',content:'Final',created_at:base+300},15);livePayload.pending_turn.started_at=base+100;startPrimeLivePolling(live.querySelector('.cb-bubble'));")
        page.wait_for_timeout(30)
        assert page.locator('#recovering').get_attribute('data-cb-created-at') == str(1791615900)
        assert page.locator('#recovering .cb-bubble').inner_text() == 'Final'
    finally:
        page.close()
