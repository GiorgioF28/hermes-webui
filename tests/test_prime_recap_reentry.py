"""Re-entry observes old completions without launching a backlog of Prime turns."""
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from api import prime_brief_queue as pbq, prime_session_store as pss, routes
from tests.test_prime_brief_notices import renderer
from tests.test_prime_brief_dedup import browser  # noqa: F401


def scene(page, source):
    page.set_content('<base href="http://brief.test/"><style>body{margin:0;background:#111;color:#eee}#cbLog{display:flex;flex-direction:column;gap:12px;padding:18px;--cb-accent:#ff6a00;--cb-accent-dim:#874021;--cb-accent-2:#ffb47e;--cb-text:#eee;--cb-muted:#aaa;--cb-faint:#aaa;--cb-sans:Arial;--cb-mono:monospace}</style><div id="cbLog"></div>')
    page.add_script_tag(content=renderer(source) + """
    var posts=[];
    window.fetch=(_,opts)=>{posts.push(JSON.parse(opts.body).task_id);return Promise.resolve({json:()=>Promise.resolve({pending:true,state:'queued'})})};
    var base=1791710000;
    renderPrimeHistoryMessage({role:'user',content:'La mia ultima richiesta',created_at:base+200},100);
    renderPrimeHistoryMessage({role:'assistant',content:'Risposta alla richiesta recente',created_at:base+210},101);
    var oldTasks=Array.from({length:50},(_,i)=>({id:'old'+i,agent:'librarian',status:'ok',started:base+i,finished:base+60+i,anchor_message_index:100,brief_status:'pending',summary:'Esito storico ancora disponibile'}));
    oldTasks.forEach(t=>renderTask(t));
    """)


@pytest.mark.parametrize('width', [1200, 650, 360])
def test_reentry_never_dispatches_old_terminal_snapshots(browser, width):
    page = browser.new_page(viewport={'width': width, 'height': 800})
    source = Path('static/command_bridge.js').read_text(encoding='utf-8')
    evidence = os.getenv('HERMES_CAPTURE_REENTRY_UI')
    try:
        if evidence:
            baseline = subprocess.run(['git', 'show', '76dda5c4:static/command_bridge.js'],
                                      capture_output=True, encoding='utf-8', check=True).stdout
            scene(page, baseline)
            page.wait_for_function('posts.length === 50 && document.querySelectorAll("[data-cb-brief-notice]").length === 50', timeout=5000)
            Path(evidence).mkdir(parents=True, exist_ok=True)
            page.evaluate("$('cbLog').lastElementChild.scrollIntoView(false)")
            page.screenshot(path=str(Path(evidence) / f'before-{width}.png'))
        # Initial entry, repeated poll, closing/reopening, second device.
        for _ in range(2):
            scene(page, source)
            page.evaluate('for(var p=0;p<20;p++)oldTasks.forEach(t=>renderTask(t));')
            assert page.evaluate('posts') == []
            assert page.locator('[data-cb-brief-notice]').count() == 0
            assert page.locator('.cb-deleg').count() == 50
            assert page.locator('[data-cb-msg-index]').count() == 2
            assert page.locator('#cbLog > :last-child .cb-bubble').inner_text() == 'Risposta alla richiesta recente'
        if evidence:
            page.evaluate("$('cbLog').lastElementChild.scrollIntoView(false)")
            page.screenshot(path=str(Path(evidence) / f'after-{width}.png'))
    finally:
        page.close()


def test_running_hydration_is_not_live_but_real_completion_still_dispatches(browser):
    page = browser.new_page()
    try:
        scene(page, Path('static/command_bridge.js').read_text(encoding='utf-8'))
        page.evaluate("""var t={...oldTasks[0],id:'offline',status:'in_corso'};
            renderTask(t,{replay:true});renderTask({...t,status:'ok'});""")
        assert page.evaluate('posts') == []
        page.evaluate("""var live={...oldTasks[1],id:'live',status:'in_corso'};
            renderTask(live,{replay:true});renderTask(live);
            renderTask({...live,status:'parziale'});
            for(var p=0;p<20;p++)renderTask({...live,status:'parziale'});""")
        assert page.evaluate('posts') == ['live']
        # Even stale task flags cannot dispatch a recap already in history.
        page.evaluate("renderPrimeHistoryMessage({role:'assistant',content:'Recap originale',brief_id:'brief-saved',created_at:base+205},102);requestBrief({id:'saved'});")
        assert page.evaluate('posts') == ['live']
    finally:
        page.close()


@pytest.mark.parametrize('archived', [False, True])
def test_stale_queue_ack_recovers_without_model_or_transcript_rewrite(tmp_path, monkeypatch, archived):
    store = pss.PrimeSessionStore(tmp_path / 'prime.json')
    queue = pbq.PrimeBriefQueue(tmp_path)
    bid = queue.enqueue('old', 'librarian', 'semplice', 'old task', 'done', 'report')
    store.inject_assistant_message('Original recap', {'brief_id': bid})
    if archived:
        with store._lock:
            data = store._read_locked()
            data['messages'] = [{'_prime_archive': {'index': 0}}]
            data['_prime_archive'] = {'brief_indexes': {bid: 0}}
            store._write_locked(data)
    before = store.path.read_bytes()
    generate = MagicMock(return_value={'reply': 'Wrong replay'})
    monkeypatch.setattr(pss, 'get_prime_session_store', lambda _: store)
    assert queue.attempt_delivery(bid, hermes_prime_reply_fn=generate)
    assert pbq.PrimeBriefQueue(tmp_path).is_delivered(bid)
    assert store.path.read_bytes() == before
    generate.assert_not_called()
    # Genuine offline results are retained and delivered once by normal drain.
    pending = queue.enqueue('pending', 'programmatore', 'codice', 'fix', 'done', 'report')
    assert queue.drain_pending(try_llm=False, session_id='hermes-prime') == 1
    assert store.brief_delivery(pending) is not None
    assert queue.drain_pending(try_llm=False) == 0


def test_post_delivery_precedes_stale_queued_worker(tmp_path, monkeypatch):
    store = pss.PrimeSessionStore(tmp_path / 'prime.json')
    store.inject_assistant_message('Original recap', {'brief_id': 'brief-old'})
    monkeypatch.setattr(pss, 'get_prime_session_store', lambda _: store)
    monkeypatch.setattr('api.prime_delegation.get_background_task', lambda _: {'id': 'old', 'status': 'ok'})
    monkeypatch.setattr(routes, '_BRIEF_JOBS', {'old': {'state': 'queued', 'brief_id': 'brief-old'}})
    payloads = []
    monkeypatch.setattr(routes, 'j', lambda _, p, **kw: payloads.append(p) or True)
    worker = MagicMock()
    monkeypatch.setattr(routes, 'threading', SimpleNamespace(Thread=worker))
    routes._handle_bridge_prime_brief(object(), {'task_id': 'old'})
    assert payloads[-1]['already_delivered']
    assert payloads[-1]['message_index'] == 0
    worker.assert_not_called()
