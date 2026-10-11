"""Saved recap notices settle before a concurrent local turn's rendering."""
import os
from pathlib import Path
import subprocess

import pytest
from tests.test_prime_brief_notices import setup, renderer
from tests.test_prime_brief_dedup import browser  # noqa: F401


@pytest.mark.parametrize('width', [1200, 650, 360])
def test_saved_recap_clears_running_notice_during_own_turn(browser, width):  # noqa: F811
    page = browser.new_page(viewport={'width': width, 'height': 600})
    try:
        evidence = os.getenv('HERMES_CAPTURE_RECAP_FIFO_UI')
        if evidence:
            baseline = subprocess.run(['git', 'show', 'd8f1eaad:static/command_bridge.js'],
                                      capture_output=True, encoding='utf-8', check=True).stdout
            setup(page, baseline)
            page.evaluate("_cbOwnTurnCount=1;nextPoll();finishStatus({state:'done',reply:message.content,message_index:12});")
            page.wait_for_function("typeof nextPoll === 'function'")
            assert page.locator('[data-cb-brief-notice]').count() == 1
            Path(evidence).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(evidence) / f'before-{width}.png'))
        setup(page)
        page.evaluate("_cbOwnTurnCount=1;nextPoll();finishStatus({state:'done',reply:message.content,message_index:12});")
        page.wait_for_function("document.querySelector('[data-cb-brief-notice]')===null")
        assert page.locator('[data-cb-brief-id]').count() == 0
        assert page.evaluate('_cbOwnTurnCount') == 1
        assert page.evaluate("!!_cbBriefRequests['brief-d528']")
        if evidence:
            page.screenshot(path=str(Path(evidence) / f'after-{width}.png'))
        page.evaluate("var posts=0;window.fetch=()=>{posts++;throw Error('duplicate')};requestBrief({id:'d528'});_cbOwnTurnCount=0;nextPoll();")
        assert page.evaluate('posts') == 0
        assert page.locator('[data-cb-brief-notice]').count() == 0
        assert page.locator('[data-cb-brief-id]').count() == 1
        assert not page.evaluate("!!_cbBriefRequests['brief-d528']")
        page.evaluate('renderPrimeHistoryMessage(message,12);requestBrief({id:"d528"});')
        assert page.locator('[data-cb-brief-id]').count() == 1
        assert page.evaluate('posts') == 0
        if evidence:
            page.screenshot(path=str(Path(evidence) / f'delivered-{width}.png'))
    finally:
        page.close()


@pytest.mark.parametrize('width', [1200, 650, 360])
def test_two_results_during_local_turn_settle_once_in_durable_order(browser, width):  # noqa: F811
    page = browser.new_page(viewport={'width': width, 'height': 600})
    try:
        page.set_content('<base href="http://brief.test/"><div id="cbLog"></div>')
        page.add_script_tag(content=renderer())
        page.evaluate("""var timers=[],statusReplies={},posts=0;
            window.setTimeout=cb=>{timers.push(cb)};
            window.fetch=()=>{posts++;return Promise.resolve({json:()=>Promise.resolve({pending:true,state:'queued'})})};
            api=url=>new Promise(resolve=>{statusReplies[url.split('task_id=')[1]]=resolve});
            _cbOwnTurnCount=1;requestBrief({id:'d663'});requestBrief({id:'d664'});""")
        page.wait_for_function('timers.length===2')
        page.evaluate("""timers.shift()();timers.shift()();
            statusReplies.d664({state:'done',reply:'Second recap',message_index:14,created_at:20});
            statusReplies.d663({state:'done',reply:'First recap',message_index:13,created_at:10});""")
        page.wait_for_function('timers.length===2')
        assert page.locator('[data-cb-brief-notice]').count() == 0
        assert page.locator('[data-cb-brief-id]').count() == 0
        assert page.evaluate('_cbOwnTurnCount') == 1
        page.evaluate("requestBrief({id:'d663'});requestBrief({id:'d664'});_cbOwnTurnCount=0;timers.shift()();timers.shift()();")
        assert page.locator('[data-cb-brief-id]').count() == 2
        assert page.evaluate('posts') == 2
        assert page.locator('[data-cb-brief-id] .cb-bubble').all_text_contents() == ['First recap', 'Second recap']
        page.evaluate("renderPrimeHistoryMessage({role:'assistant',content:'First recap',brief_id:'brief-d663',created_at:10},13);renderPrimeHistoryMessage({role:'assistant',content:'Second recap',brief_id:'brief-d664',created_at:20},14);")
        assert page.locator('[data-cb-brief-id]').count() == 2
        assert page.evaluate('_cbOwnTurnCount') == 0
    finally:
        page.close()
