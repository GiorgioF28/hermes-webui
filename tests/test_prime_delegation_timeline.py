"""One shared Prime turn, ordered prose/card/final replay without a live server."""
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
    return (source[source.index('  function placeTaskCard('):source.index('  function requestBrief(t)')] +
            source[source.index('  function renderPrimeHistoryMessage('):source.index('  function renderPrimeHistoryMessages(')])


HARNESS = r'''
var _cbTasks = {}, userEngaged = false;
window._showTokenUsage = false;
function $(id){return document.getElementById(id)}
function el(tag, cls){var n=document.createElement(tag);n.className=cls;return n}
function esc(s){var n=document.createElement('div');n.textContent=String(s || '');return n.innerHTML}
function renderRich(node,text){node.textContent=text}
function taskSummary(t){return t.id}
function taskIsRunning(s){return s==='in_corso'}
function taskStateClass(){return 'running'}
function taskTimerHtml(){return ''}
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
function task(id,prefix,started=1){return {id,agent:'programmatore',status:'in_corso',anchor_message_index:0,anchor_stream_id:'s1',anchor_reply_prefix:prefix,started}}
function rows(){return Array.from($('cbLog').children).filter(n=>!n.hidden).map(n=>n.classList.contains('cb-deleg')?n.getAttribute('data-task-id'):n.querySelector('.cb-bubble').textContent)}
'''


@pytest.mark.parametrize('width', [1200, 650, 360])
def test_announcement_card_continuation_and_final_replay(browser, width):
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


def test_card_waits_for_owner_and_legacy_card_is_after_reply(browser):
    page = browser.new_page()
    try:
        page.set_content('<div id="cbLog"></div>')
        page.add_script_tag(content=HARNESS + renderer_source())
        page.evaluate("renderPrimeHistoryMessage({role:'user',content:'domanda',stream_id:'s1'},0);renderTask(task('d1','')); ")
        assert page.evaluate('rows()') == ['domanda']
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
        assert page.locator('.cb-deleg-progress').count() == 3
        assert page.evaluate('rows()') == ['La mia domanda', 'Passo il lavoro.', 'd1', ' Poi delego ancora.', 'd2', 'd3', ' Attendo.']
    finally:
        page.close()
