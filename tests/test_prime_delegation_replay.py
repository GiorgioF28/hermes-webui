"""Polling old delegations cannot rebuild an obsolete tail or replay a delivered recap."""
import copy
import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from api import delegation_store, prime_delegation, prime_session_store as pss, routes
from tests.test_prime_delegation_timeline import HARNESS, browser, renderer_source  # noqa: F401


def test_poll_delivery_projection_uses_transcript_and_preserves_real_queue(tmp_path, monkeypatch):
    store = pss.PrimeSessionStore(tmp_path / 'prime.json')
    store.inject_assistant_message('Already verified', {'brief_id': 'brief-old'})
    for i in range(60):
        store.inject_assistant_message(f'Current {i}')
    tasks = [
        {'id': 'old', 'status': 'ok', 'anchor_message_index': 0, 'brief_status': 'pending'},
        {'id': 'pending', 'status': 'ok', 'anchor_message_index': 0, 'brief_status': 'pending'},
        {'id': 'active', 'status': 'in_corso', 'anchor_message_index': 0},
        {'id': 'recent', 'status': 'ok', 'anchor_message_index': 60, 'brief_status': 'pending'},
    ]
    original = copy.deepcopy(tasks)
    before = store.path.read_bytes()
    projected = {c['id']: c for c in store.project_task_cards(tasks)}
    assert projected['old']['brief_status'] == 'delivered'
    assert projected['old']['brief_message_index'] == 0
    assert not projected['old']['card_visible']
    assert not projected['pending']['card_visible']
    assert projected['pending']['brief_status'] == 'pending'
    assert projected['active']['card_visible'] and projected['recent']['card_visible']
    assert tasks == original and store.path.read_bytes() == before
    monkeypatch.setattr(pss, 'get_prime_session_store', lambda _: store)
    monkeypatch.setattr(prime_delegation, 'get_background_tasks', lambda: tasks)
    monkeypatch.setattr(delegation_store, 'get_delegation_store', lambda _: type('Empty', (), {'get_all': lambda _: []})())
    monkeypatch.setattr(routes, '_sync_prime_delegation_records', lambda *args: None)
    monkeypatch.setattr(routes, '_request_prime_session_id', lambda _: 'hermes-prime')
    captured = []
    monkeypatch.setattr(routes, 'j', lambda _, payload, **kw: captured.append(payload) or True)
    routes._handle_bridge_tasks(object(), urlsplit('/api/bridge/tasks?compact=1'))
    assert {c['id']: c for c in captured[-1]['tasks']} == projected


def test_archive_delivery_index_is_used_without_loading_originals(tmp_path):
    import json
    path = tmp_path / 'prime.json'
    path.write_text(json.dumps({'messages': [{'role': 'assistant', 'content': 'Current'}],
        '_prime_archive': {'archive_id': 'a', 'cutoff': '2026-10-04', 'archived_indexes': [],
                           'brief_indexes': {'brief-old': 0}}}), encoding='utf-8')
    store = pss.PrimeSessionStore(path)
    assert store.project_task_cards([{'id': 'old', 'status': 'ok'}])[0]['brief_status'] == 'delivered'


def setup(page, source):
    page.set_content('<style>body{margin:0;background:#111;color:#eee}#cbLog{display:flex;flex-direction:column;gap:12px;padding:18px;--cb-accent:#ff6a00;--cb-accent-dim:#874021;--cb-accent-2:#ffb47e;--cb-text:#eee;--cb-faint:#aaa;--cb-sans:Arial;--cb-mono:monospace}</style><div id="cbLog"></div>')
    window = source[source.index('  function applyPrimeHistoryWindow('):source.index('  function renderPrimeHistoryPayload(')]
    styles = source[source.index('  function injectStyles()'):source.index('  /*', source.index('  function injectStyles()'))]
    page.add_script_tag(content=HARNESS + renderer_source(source) + window + styles +
        "\nvar requested=[];function requestBrief(t){requested.push(t.id)};injectStyles();")
    page.evaluate("""for(var i=50;i<100;i++)renderPrimeHistoryMessage({role:i===98?'user':'assistant',content:i===98?'La mia ultima richiesta':i===99?'La risposta alla richiesta recente':'Messaggio '+i},i);
        applyPrimeHistoryWindow({history_window:{start_index:50,visible_indexes:Array.from({length:50},(_,i)=>i+50),archived_count:50}});
        var oldTasks=Array.from({length:4},(_,i)=>({id:'d'+(620+i),agent:'programmatore',status:'ok',started:60,finished:90,anchor_message_index:i+1,brief_status:'delivered',summary:'Una delega storica già conclusa'}));
        oldTasks.forEach(t=>renderTask(t));""")


@pytest.mark.parametrize('width', [1200, 650, 360])
def test_old_polling_cannot_append_after_latest_reply_or_retrigger_briefs(browser, width):
    source = Path('static/command_bridge.js').read_text(encoding='utf-8')
    page = browser.new_page(viewport={'width': width, 'height': 700})
    evidence = os.getenv('HERMES_CAPTURE_REPLAY_UI')
    try:
        if evidence:
            baseline = subprocess.run(['git', 'show', 'd41d1491:static/command_bridge.js'], capture_output=True, encoding='utf-8', check=True).stdout
            setup(page, baseline)
            assert page.locator('.cb-deleg').count() == 4
            Path(evidence).mkdir(parents=True, exist_ok=True)
            page.evaluate("$('cbLog').scrollIntoView(false)")
            page.screenshot(path=str(Path(evidence) / f'before-{width}.png'))
        setup(page, source)
        page.evaluate('for(var p=0;p<20;p++)oldTasks.forEach(t=>renderTask(t));')
        assert page.locator('.cb-deleg').count() == 0
        assert page.evaluate('requested') == []
        assert page.locator('#cbLog > :last-child .cb-bubble').inner_text() == 'La risposta alla richiesta recente'
        # Old running work stays visible in chronological order. Completion
        # outside the window removes only that card; it never restarts a recap.
        page.evaluate("renderTask({...oldTasks[0],status:'in_corso',brief_status:''});")
        assert page.locator('.cb-deleg').count() == 1
        assert page.locator('.cb-deleg').evaluate('(n)=>Number(n.nextElementSibling.getAttribute("data-cb-msg-index"))') == 50
        page.evaluate("renderTask({...oldTasks[0],status:'ok',brief_status:'delivered'});")
        assert page.locator('.cb-deleg').count() == 0
        # A status response with an old message index stays outside the window.
        page.evaluate("renderPrimeHistoryMessage({role:'assistant',content:'Old recap',brief_id:'brief-d620'},1)")
        assert page.locator('[data-cb-brief-id="brief-d620"]').count() == 0
        # A late response inside the retained window uses its absolute slot,
        # without replacing or following the latest user/reply pair.
        page.evaluate("$('cbLog').querySelector('[data-cb-msg-index=\"60\"]').remove();renderPrimeHistoryMessage({role:'assistant',content:'Recovered recap',brief_id:'brief-recovered'},60)")
        assert page.locator('[data-cb-brief-id="brief-recovered"]').evaluate('(n)=>n.nextElementSibling.getAttribute("data-cb-msg-index")') == '61'
        assert page.locator('[data-cb-msg-index="99"] .cb-bubble').inner_text() == 'La risposta alla richiesta recente'
        # A completion we actually observed still delivers its new recap,
        # even when the long-running task's owner has left the window.
        page.evaluate("renderTask({...oldTasks[1],status:'in_corso',brief_status:'',card_visible:true});renderTask({...oldTasks[1],status:'ok',brief_status:'pending',card_visible:false});")
        assert page.evaluate('requested') == ['d621']
        page.evaluate('oldTasks.forEach(t=>renderTask(t))')
        assert page.locator('.cb-deleg').count() == 0
        if evidence:
            page.evaluate("$('cbLog').scrollIntoView(false)")
            page.screenshot(path=str(Path(evidence) / f'after-{width}.png'))
    finally:
        page.close()
