"""Real browser renderer with simulated time; no live server/provider/state."""
import os
from pathlib import Path
import subprocess

import pytest
from tests.test_prime_brief_dedup import HARNESS, browser  # noqa: F401


SOURCE = Path('static/command_bridge.js').read_text(encoding='utf-8')


def renderer(source=SOURCE):
    # Use the actual accessible retry DOM, plus the existing isolated harness.
    harness = HARNESS.replace("function sysNoteRetry(text, retry) { retryNotices.push({text:text, retry:retry}); }", '')
    harness += "\nvar _cbTasks={}, _cbHistoryBatch=0; function el(t,c){var n=document.createElement(t);n.className=c;return n} function esc(s){var n=el('div','');n.textContent=String(s||'');return n.innerHTML}"
    retry = source[source.index('  function sysNoteRetry('):source.index('  // ── Todos panel')]
    cards = source[source.index('  function taskStateClass('):source.index('  function requestBrief(t)')]
    request = source[source.index('  function requestBrief(t)'):source.index('  var _cbPollTimer')]
    history = source[(source.index('  function primeTimestamp(') if '  function primeTimestamp(' in source else source.index('  function renderPrimeHistoryMessage(')):source.index('  function renderPrimeHistoryMessages(')]
    styles = source[source.index('  function injectStyles()'):source.index('  /*', source.index('  function injectStyles()'))]
    return harness + retry + cards + request + history + styles + '\ninjectStyles();'


def setup(page, source=SOURCE):
    page.set_content('<base href="http://brief.test/"><style>body{margin:0;background:#111;color:#eee}#cbLog{display:flex;flex-direction:column;gap:12px;padding:18px;--cb-accent:#ff6a00;--cb-accent-dim:#874021;--cb-accent-2:#ffb47e;--cb-text:#eee;--cb-muted:#aaa;--cb-faint:#aaa;--cb-sans:Arial;--cb-mono:monospace}</style><div id="cbLog"></div>')
    page.add_script_tag(content=renderer(source))
    page.evaluate("renderTask({id:'d528',agent:'programmatore',status:'ok',summary:'Correggere gli avvisi dei recap',started:1,finished:121},{replay:true,briefed:true}); requestBrief({id:'d528'});")
    page.wait_for_function("typeof nextPoll === 'function'")


@pytest.mark.parametrize('width', [1200, 650, 360])
def test_queue_over_15_minutes_then_late_delivery_and_evidence(browser, width):
    page = browser.new_page(viewport={'width': width, 'height': 600})
    try:
        evidence = os.getenv('HERMES_CAPTURE_BRIEF_NOTICE_UI')
        if evidence:
            baseline = subprocess.run(['git', 'show', '9fd6b8b7:static/command_bridge.js'], capture_output=True, encoding='utf-8', check=True).stdout
            setup(page, baseline)
            page.evaluate('var clock=Date.now()+20*60*1000; Date.now=()=>clock; nextPoll();')
            assert page.locator('.cb-sysnote-retry').count() == 1
            Path(evidence).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(evidence) / f'before-{width}.png'))
        setup(page)
        page.evaluate('var clock=Date.now()+20*60*1000; Date.now=()=>clock; nextPoll(); finishStatus({state:"queued",pending:true});')
        page.wait_for_function("document.querySelector('[data-cb-brief-notice]').textContent.includes('in coda')")
        assert page.locator('.cb-sysnote-retry').count() == 0
        assert page.locator('.cb-deleg-done').count() == 1
        if evidence:
            page.screenshot(path=str(Path(evidence) / f'after-{width}.png'))
        page.evaluate('nextPoll(); finishStatus({state:"running",pending:true});')
        page.wait_for_function("document.querySelector('[data-cb-brief-notice]').textContent.includes('in elaborazione')")
        page.evaluate('nextPoll(); finishStatus({state:"done",reply:message.content,usage:message.usage});')
        page.wait_for_function("document.querySelector('[data-cb-brief-id]') !== null")
        page.evaluate('renderPrimeHistoryMessage(message,12); renderPrimeHistoryMessage(message,12);')
        assert page.locator('[data-cb-brief-notice]').count() == 0
        assert page.locator('[data-cb-brief-id]').count() == 1
        assert page.locator('.cb-deleg-done').count() == 1
        assert page.locator('.cb-deleg-timer').get_attribute('data-running') == '0'
        if evidence:
            page.screenshot(path=str(Path(evidence) / f'delivered-{width}.png'))
    finally:
        page.close()


def test_history_removes_only_matching_retry_even_on_duplicate_index(browser):
    page = browser.new_page()
    try:
        setup(page)
        page.evaluate("nextPoll(); finishStatus({state:'failed_retryable'});")
        page.wait_for_function("document.querySelector('.cb-sysnote-retry') !== null")
        page.evaluate("showBriefNotice('brief-d529','Recap d529: salvataggio non riuscito',()=>{});sysNoteRetry('Storico non caricato',()=>{});renderPrimeHistoryMessage(message,12);")
        assert page.locator('[data-cb-brief-notice="brief-d528"]').count() == 0
        assert page.locator('[data-cb-brief-notice="brief-d529"]').count() == 1
        assert page.locator('.cb-sysnote-retry').count() == 2
        # Even a stale duplicate-index history event reconciles notices.
        page.evaluate("var stale=sysNoteRetry('stale',()=>{});stale.setAttribute('data-cb-brief-notice','brief-d528');renderPrimeHistoryMessage(message,12);")
        assert page.locator('[data-cb-brief-notice="brief-d528"]').count() == 0
    finally:
        page.close()


def test_inflight_failure_after_history_cannot_recreate_warning(browser):
    page = browser.new_page()
    try:
        setup(page)
        page.evaluate("nextPoll();renderPrimeHistoryMessage(message,12);finishStatus({state:'failed_retryable'});")
        page.evaluate('() => Promise.resolve()')
        assert page.locator('[data-cb-brief-notice]').count() == 0
        assert page.locator('.cb-sysnote-retry').count() == 0
        assert page.locator('[data-cb-brief-id]').count() == 1
    finally:
        page.close()


def test_recovery_is_distinct_and_repeated_requests_do_not_post_twice(browser):
    page = browser.new_page()
    try:
        setup(page)
        page.evaluate("var posts=0;window.fetch=()=>{posts++;return Promise.resolve({json:()=>Promise.resolve({pending:true,state:'queued'})})};requestBrief({id:'d528'});nextPoll();finishStatus({state:'pending_recovery'});")
        page.wait_for_function("document.querySelector('.cb-sysnote-retry') !== null")
        assert page.evaluate('posts') == 0
        assert 'stato da recuperare' in page.locator('.cb-sysnote-retry').inner_text()
        page.locator('.cb-sysnote-retry .cb-bubble').press('Enter')
        page.wait_for_function('posts===1')
        page.evaluate("requestBrief({id:'d528'});")
        assert page.evaluate('posts') == 1
    finally:
        page.close()


def test_lost_post_response_checks_delivery_without_posting_again(browser):
    page = browser.new_page()
    try:
        page.set_content('<base href="http://brief.test/"><div id="cbLog"></div>')
        page.add_script_tag(content=renderer())
        page.evaluate("var posts=0;window.fetch=()=>{posts++;return Promise.reject(Error('connection lost'))};requestBrief({id:'d528'});")
        page.wait_for_function("typeof nextPoll==='function'")
        assert page.locator('.cb-sysnote-retry').count() == 0
        assert 'verifica connessione' in page.locator('[data-cb-brief-notice]').inner_text()
        page.evaluate('nextPoll();finishStatus({state:"done",reply:message.content});')
        page.wait_for_function("document.querySelector('[data-cb-brief-id]') !== null")
        assert page.evaluate('posts') == 1
        assert page.locator('[data-cb-brief-notice]').count() == 0
    finally:
        page.close()
