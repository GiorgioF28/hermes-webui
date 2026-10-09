"""One shared Prime turn, compact delegation cards and prose/final replay or a live model."""
import os
from pathlib import Path

import pytest

from api import prime_delegation as pd, prime_session_store as pss
from api.delegation_store import bg_task_to_canonical


def test_delegation_captures_prose_when_created_and_survives_reload(tmp_path, monkeypatch):
    store = pss.PrimeSessionStore(tmp_path / 'prime.json')
    monkeypatch.setattr(pss, 'get_prime_session_store', lambda _: store)
    monkeypatch.setattr(pd, '_DELEGATION_ANCHORS', {})
    stream = store.begin_turn('domanda')
    pd.set_delegation_anchor_context('hermes-prime', message_index=0)
    store.append_token(stream, 'Passo il lavoro al programmatore.')
    anchor = pd._delegation_anchor('hermes-prime')
    store.append_token(stream, ' Continuazione successiva.')
    assert anchor['anchor_stream_id'] == stream
    assert anchor['anchor_reply_prefix'] == 'Passo il lavoro al programmatore.'
    task = dict(id='d1', agent='programmatore', task='task', task_type='codice', status='in_corso', **anchor)
    canonical = bg_task_to_canonical(task)
    restored = pd._canonical_to_legacy(canonical)
    assert restored['anchor_reply_prefix'] == anchor['anchor_reply_prefix']
    store.upsert_delegation('d1', **anchor)
    reopened = pss.PrimeSessionStore(store.path)
    assert reopened.history()['delegations'][0]['anchor_stream_id'] == stream
    assert reopened.history()['delegations'][0]['anchor_reply_prefix'] == anchor['anchor_reply_prefix']


@pytest.fixture(scope='module')
def browser():
    pw = pytest.importorskip('playwright.sync_api')
    with pw.sync_playwright() as p:
        b = p.chromium.launch(headless=True, channel=os.getenv('HERMES_TEST_BROWSER_CHANNEL') or None)
        yield b
        b.close()


def renderer_source(source=None):
    source = source or Path('static/command_bridge.js').read_text(encoding='utf-8')
    return (source[source.index('  function taskStateClass('):source.index('  function requestBrief(t)')] +
            source[source.index('  function renderPrimeHistoryMessage('):source.index('  function renderPrimeHistoryMessages(')])


HARNESS = r'''
var _cbTasks = {}, _cbHistoryBatch = 0, userEngaged = false;
window._showTokenUsage = false;
function $(id){return document.getElementById(id)}
function el(tag, cls){var n=document.createElement(tag);n.className=cls;return n}
function esc(s){var n=document.createElement('div');n.textContent=String(s || '');return n.innerHTML}
function renderRich(node,text){node.textContent=text}
function nearBottom(){return true}
function requestBrief(){throw Error('no background generation in replay test')}
function _hasBridgeUsage(){return false}
function primeSay(role,text){var n=el('div','cb-msg cb-from-'+role);n.innerHTML='<div class="cb-who">hermes prime</div><div class="cb-bubble"></div>';n.querySelector('.cb-bubble').textContent=text;$('cbLog').appendChild(n);return n}
function makeTurn(){
 renderPrimeHistoryMessage({role:'user',content:'La mia domanda',stream_id:'s1'},0);
 var reply=primeSay('prime','Passo il lavoro.');reply.id='reply';
 reply.setAttribute('data-cb-stream-id','s1');reply.setAttribute('data-cb-role','assistant');
 updatePrimeLiveText(reply,'Passo il lavoro.');return reply;
}
function task(id,prefix,started=1){return {id,agent:'programmatore',status:'in_corso',anchor_message_index:0,anchor_stream_id:'s1',anchor_reply_prefix:prefix,summary:'Ripristinare card e timer deleghe',started}}
function rows(){return Array.from($('cbLog').children).filter(n=>!n.hidden).map(n=>n.classList.contains('cb-deleg')?n.getAttribute('data-task-id'):n.querySelector('.cb-bubble').textContent)}
'''


@pytest.mark.parametrize('width', [1200, 650, 360])
def test_announcement_compact_card_continuation_and_final(browser, width):
    page = browser.new_page(viewport={'width': width, 'height': 900})
    try:
        page.set_content('<div id="cbLog"></div>')
        page.add_script_tag(content=HARNESS + renderer_source())
        page.evaluate("makeTurn(); renderTask(task('d1','Passo il lavoro.')); updatePrimeLiveText(reply,'Passo il lavoro. Continuo qui.');")
        assert page.evaluate('rows()') == ['La mia domanda', 'Passo il lavoro.', 'd1', ' Continuo qui.']
        page.evaluate("renderPrimeHistoryMessage({role:'assistant',content:'Risposta finale.',stream_id:'s1',reply_to_index:0},1); repositionPrimeTaskCards();")
        assert page.evaluate('rows()') == ['La mia domanda', 'Passo il lavoro.', 'd1', 'Risposta finale.']
        # Cold mobile/desktop replay produces exactly the same order.
        page.evaluate("$('cbLog').innerHTML='';_cbTasks={};renderPrimeHistoryMessage({role:'user',content:'La mia domanda',stream_id:'s1'},0);renderPrimeHistoryMessage({role:'assistant',content:'Risposta finale.',stream_id:'s1',reply_to_index:0},1);renderTask(task('d1','Passo il lavoro.'));repositionPrimeTaskCards();")
        assert page.evaluate('rows()') == ['La mia domanda', 'Passo il lavoro.', 'd1', 'Risposta finale.']
    finally:
        page.close()


def test_card_visible_before_first_token_and_without_owner(browser):
    page = browser.new_page()
    try:
        page.set_content('<div id="cbLog"></div>')
        page.add_script_tag(content=HARNESS + renderer_source())
        page.evaluate("renderPrimeHistoryMessage({role:'user',content:'domanda',stream_id:'s1'},0);renderTask(task('d1','')); ")
        assert page.evaluate('rows()') == ['domanda', 'd1']
        page.evaluate("renderPrimeHistoryMessage({role:'assistant',content:'risposta',stream_id:'s1',reply_to_index:0},1);repositionPrimeTaskCards();")
        assert page.evaluate('rows()') == ['domanda', 'risposta', 'd1']
        page.evaluate("renderTask({...task('unowned',''),anchor_message_index:null,anchor_stream_id:null});")
        assert page.evaluate('rows()') == ['domanda', 'risposta', 'd1', 'unowned']
    finally:
        page.close()


def test_multiple_delegations_do_not_repeat_cumulative_prose(browser):
    page = browser.new_page()
    try:
        page.set_content('<div id="cbLog"></div>')
        page.add_script_tag(content=HARNESS + renderer_source())
        page.evaluate("makeTurn();renderTask(task('d2','Passo il lavoro. Poi delego ancora.',2));renderTask(task('d1','Passo il lavoro.',1));renderTask(task('d3','Passo il lavoro. Poi delego ancora.',3));updatePrimeLiveText(reply,'Passo il lavoro. Poi delego ancora. Attendo.');")
        assert page.evaluate('rows()') == ['La mia domanda', 'Passo il lavoro.', 'd1', ' Poi delego ancora.', 'd2', 'd3', ' Attendo.']
        page.evaluate("renderTask(task('d1','Passo il lavoro.',1));repositionPrimeTaskCards();")
        assert page.locator('.cb-deleg').count() == 3
        assert page.evaluate('rows()') == ['La mia domanda', 'Passo il lavoro.', 'd1', ' Poi delego ancora.', 'd2', 'd3', ' Attendo.']
    finally:
        page.close()


@pytest.mark.parametrize('width', [1200, 650, 360])
def test_card_state_timer_no_prompt_and_responsive_evidence(browser, width):
    import subprocess
    source = Path('static/command_bridge.js').read_text(encoding='utf-8')
    baseline = subprocess.run(['git', 'show', 'b32a9cec:static/command_bridge.js'],
                              capture_output=True, encoding='utf-8')
    before = baseline.stdout if baseline.returncode == 0 else ''
    evidence = Path('docs/ui-ux/prime-delegation-cards')
    evidence.mkdir(parents=True, exist_ok=True)
    page = browser.new_page(viewport={'width': width, 'height': 600})
    try:
        for label, code in [('before', before), ('after', source)]:
            if not code:  # Shallow CI checkouts can lack the historical baseline.
                continue
            page.set_content('<style>body{margin:0;background:#111;color:#eee}#cbLog{display:flex;flex-direction:column;gap:12px;padding:18px;--cb-accent:#ff6a00;--cb-accent-dim:#874021;--cb-accent-2:#ffb47e;--cb-text:#eee;--cb-faint:#aaa;--cb-sans:Arial;--cb-mono:monospace}</style><div id="cbLog"></div>')
            styles = code[code.index('  function injectStyles()'):code.index('  /*', code.index('  function injectStyles()'))]
            page.add_script_tag(content=HARNESS + renderer_source(code) + styles)
            page.evaluate("injectStyles();Date.now=()=>120000;makeTurn();renderTask({...task('d1','Passo il lavoro.',60),summary:'Ripristinare le card deleghe con stato e timer.',task:'RAW PROMPT SECRET',output:'RAW RESULT',diagnostic_log:'RAW LOG'});")
            page.screenshot(path=str(evidence / f'{label}-{width}.png'))
            if label == 'before':
                assert page.locator('.cb-deleg').count() == 0
                continue
            card = page.locator('.cb-deleg')
            assert card.count() == 1 and card.is_visible()
            assert 'd1 · in corso' in card.inner_text()
            assert card.locator('.cb-deleg-timer').inner_text() == '⏱ 1m 00s'
            assert card.evaluate('(n)=>getComputedStyle(n).borderLeftColor') == 'rgb(255, 106, 0)'
            page.evaluate('Date.now=()=>125000;tickTaskTimers()')
            assert card.locator('.cb-deleg-timer').inner_text() == '⏱ 1m 05s'
            page.evaluate("renderTask({...task('d1','Passo il lavoro.',60),status:'ok',finished:122,summary:'Ripristinare le card deleghe con stato e timer.'},{replay:true})")
            assert card.evaluate('(n)=>getComputedStyle(n).borderLeftColor') == 'rgb(72, 199, 116)'
            assert card.locator('.cb-deleg-timer').inner_text() == '⏱ 1m 02s'
            page.evaluate('Date.now=()=>200000;tickTaskTimers()')
            assert card.locator('.cb-deleg-timer').inner_text() == '⏱ 1m 02s'
            page.evaluate("renderTask({...task('d1','Passo il lavoro.',60),status:'errore',finished:122},{replay:true})")
            assert card.evaluate('(n)=>getComputedStyle(n).borderLeftColor') == 'rgb(255, 107, 92)'
            assert card.locator('button,details,.cb-deleg-taskfull').count() == 0
            card.click()
            assert 'RAW' not in page.locator('#cbLog').inner_text()
            assert page.evaluate("!('task' in _cbTasks.d1.task) && !('output' in _cbTasks.d1.task)")
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    finally:
        page.close()


def test_cold_hydration_updates_status_and_window_keeps_running_cards(browser):
    source = Path('static/command_bridge.js').read_text(encoding='utf-8')
    hydrate = source[source.index('  function delegationRecordToTask('):source.index('  function renderPrimeHistoryPayload(')]
    page = browser.new_page()
    try:
        page.set_content('<div id="cbLog"></div>')
        page.add_script_tag(content=HARNESS + renderer_source() + hydrate)
        page.evaluate("makeTurn();var d={id:'d1',agent:'programmatore',summary:'Ripristinare card deleghe',status:'in_corso',started_at:60,anchor_message_index:0,anchor_stream_id:'s1',anchor_reply_prefix:'Passo il lavoro.'};hydrateDelegationCards([d]);hydrateDelegationCards([d]);")
        assert page.locator('.cb-deleg').count() == 1
        page.evaluate("hydrateDelegationCards([{...d,status:'ok',finished_at:90,brief_status:'delivered'}]);")
        assert page.locator('.cb-deleg-done').count() == 1
        assert page.evaluate('_cbTasks.d1.briefed')
        page.evaluate('hydrateDelegationCards([d]);')
        assert page.locator('.cb-deleg-done').count() == 1
        page.evaluate("hydrateDelegationCards([{...d,id:'running',anchor_message_index:1,status:'in_corso'}]);applyPrimeHistoryWindow({history_window:{start_index:10,visible_indexes:[10],archived_count:10}});")
        assert page.locator('.cb-deleg').count() == 1
        assert page.locator('.cb-deleg').get_attribute('data-task-id') == 'running'
        assert page.locator('.cb-deleg-progress[data-progress-task-id="d1"]').count() == 0
    finally:
        page.close()
