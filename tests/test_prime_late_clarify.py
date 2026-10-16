"""Late choices resume their original work once, even outside model history."""
import asyncio
import json
import os
import subprocess
import threading
from pathlib import Path

import pytest

from api import ask_user_tool, clarify, prime_session_store, routes
from tests.test_prime_transport_continuity import bridge, Handler  # noqa: F401
from tests.test_prime_brief_dedup import browser  # noqa: F401
from tests.test_prime_image_upload import scene

ROOT = Path(__file__).resolve().parents[1]
QUESTION = 'Quale sourcing prepariamo per Scan 1?'
ANSWER = {'answers': {QUESTION: 'Prima Instagram diretto'}}


def request(store, detached=True):
    stream = store.begin_turn('Correggiamo il sourcing: niente filtro follower, zero DM.')
    entry = clarify.submit_pending('hermes-prime', clarify.normalize_prompt_payload(
        {'question': QUESTION, 'options': ['Prima Instagram diretto', 'Tre canali']},
        session_id='hermes-prime'))
    store.append_clarify_request(entry.clarify_id, entry.data)
    if detached:
        clarify.detach_waiter(entry)
        store.finish_turn(stream, 'La scelta resta aperta.')
    return entry


@pytest.fixture(autouse=True)
def clean_queue():
    clarify.clear_pending('hermes-prime')
    yield
    clarify.clear_pending('hermes-prime')


def test_timeout_detaches_the_real_tool_without_deciding(monkeypatch, tmp_path):
    store = prime_session_store.PrimeSessionStore(tmp_path / 'prime.json')
    monkeypatch.setattr(prime_session_store, 'get_prime_session_store', lambda _: store)
    monkeypatch.setattr(clarify, 'resolve_clarify_timeout', lambda: 0.001)
    result = asyncio.run(ask_user_tool._run_ask_user('hermes-prime', {
        'question': QUESTION, 'options': ['Instagram', 'Directory']}))
    assert 'Non decidere' in result['content'][0]['text']
    pending = clarify.get_pending('hermes-prime')
    accepted = store.accept_clarify_answer(pending['clarify_id'], ANSWER)
    assert accepted['delivery'] == 'detached'


def test_waiting_answer_wakes_original_turn_and_has_durable_receipt(bridge, monkeypatch):
    store, handler = bridge
    entry = request(store, detached=False)
    calls = []
    monkeypatch.setattr(routes, '_call_hermes_prime_reply_for_bridge', lambda *a, **kw: calls.append(a))
    body = {'clarify_id': entry.clarify_id, 'clarify_response': ANSWER}
    routes._handle_bridge_prime(handler, body)
    assert entry.event.is_set() and entry.result == ANSWER
    assert not calls
    assert b'"clarify_only": true' in handler.wfile.getvalue()
    receipt = store.browser_request('clarify-' + entry.clarify_id)
    assert receipt['state'] == 'done' and receipt['clarify_only']
    routes._handle_bridge_prime(Handler(), body)
    assert len([m for m in store.history()['messages'] if m.get('_bridge_clarify_event') == 'response']) == 1


@pytest.mark.parametrize('restart', [False, True])
def test_late_answer_restores_context_outside_window_once(bridge, monkeypatch, restart):
    store, handler = bridge
    entry = request(store)
    for i in range(55):
        store.inject_assistant_message('Unrelated new topic ' + str(i))
    assert entry.clarify_id not in json.dumps(store.history(windowed=True)['messages'])
    if restart:
        clarify.clear_pending('hermes-prime')
        store = prime_session_store.PrimeSessionStore(store.path)
        monkeypatch.setattr(prime_session_store, 'get_prime_session_store', lambda _: store)
    assert store.open_clarifications()[0]['clarify_id'] == entry.clarify_id
    calls = []
    def provider(message, *args, **kwargs):
        calls.append(message)
        return {'reply': 'Scelta applicata: Instagram diretto.'}
    monkeypatch.setattr(routes, '_call_hermes_prime_reply_for_bridge', provider)
    body = {'clarify_id': entry.clarify_id, 'clarify_response': ANSWER, 'message': 'client context must not win'}
    routes._handle_bridge_prime(handler, body)
    routes._handle_bridge_prime(Handler(), body)
    assert len(calls) == 1
    assert QUESTION in calls[0] and 'Prima Instagram diretto' in calls[0]
    assert 'niente filtro follower, zero DM' in calls[0]
    assert 'client context must not win' not in calls[0]
    rows = store.history()['messages']
    answers = [m for m in rows if m.get('_bridge_clarify_event') == 'response']
    assert len(answers) == 1
    assert rows[-1]['reply_to_index'] == rows.index(answers[0])
    assert not store.open_clarifications()
    assert not store.live()['active']


def test_simultaneous_devices_execute_one_turn(bridge, monkeypatch):
    store, _ = bridge
    entry = request(store)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def provider(*args, **kwargs):
        calls.append(args[0]); entered.set()
        assert release.wait(5)
        return {'reply': 'Unica risposta'}
    monkeypatch.setattr(routes, '_call_hermes_prime_reply_for_bridge', provider)
    body = {'clarify_id': entry.clarify_id, 'clarify_response': ANSWER}
    workers = [threading.Thread(target=routes._handle_bridge_prime, args=(Handler(), body)) for _ in range(2)]
    workers[0].start()
    assert entered.wait(3)
    workers[1].start()
    release.set()
    for worker in workers:
        worker.join(5)
        assert not worker.is_alive()
    assert len(calls) == 1


def test_unknown_cross_session_and_conflicting_answers_are_rejected(tmp_path):
    store = prime_session_store.PrimeSessionStore(tmp_path / 'prime.json')
    entry = request(store)
    other = prime_session_store.PrimeSessionStore(tmp_path / 'tom.json', session_id='hermes-prime-tom')
    with pytest.raises(ValueError):
        other.accept_clarify_answer(entry.clarify_id, ANSWER)
    store.accept_clarify_answer(entry.clarify_id, ANSWER)
    with pytest.raises(ValueError):
        store.accept_clarify_answer(entry.clarify_id, 'Different')


def test_a_repeated_question_reattaches_its_waiter(tmp_path):
    store = prime_session_store.PrimeSessionStore(tmp_path / 'prime.json')
    entry = request(store)
    same = clarify.submit_pending('hermes-prime', entry.data)
    assert same is entry
    assert store.accept_clarify_answer(entry.clarify_id, ANSWER)['delivery'] == 'waiting'


@pytest.mark.parametrize('width', [1200, 650, 360])
def test_browser_choice_preserves_draft_and_resumes_backend(browser, bridge, monkeypatch, width):
    store, _ = bridge
    entry = request(store)
    calls = []
    monkeypatch.setattr(routes, '_call_hermes_prime_reply_for_bridge',
                        lambda message, *a, **kw: calls.append(message) or {'reply': 'Instagram diretto confermato.'})
    page = browser.new_page(viewport={'width': width, 'height': 820})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    source = (ROOT / 'static/command_bridge.js').read_text(encoding='utf-8')
    try:
        page.route('http://upload.test/hermes/', lambda route: route.fulfill(body='<html></html>', content_type='text/html'))
        page.goto('http://upload.test/hermes/')
        scene(page, source)
        cards = source[source.index('  function normalizeAttentionPending('):source.index('  // La GET dello storico')]
        stream = source[source.index('  function streamPrimeResponse('):source.index('  function _ensureCtx(')]
        page.add_script_tag(content=cards + stream)
        page.add_script_tag(content="window.fetch=window.nativeUploadFetch;function renderRich(n,t){n.textContent=t};function renderTodos(){};function renderToolCard(){};function speak(){};function applyPrimeTimestamp(){}")
        page.add_script_tag(content="function pendingBubble(){var n=el('div','cb-msg cb-from-prime');n.innerHTML='<div class=cb-bubble>sto ragionando</div><div class=cb-msg-foot></div>';$('cbLog').append(n);return n}")
        def serve(route):
            handler = Handler()
            routes._handle_bridge_prime(handler, route.request.post_data_json)
            route.fulfill(status=200, content_type='text/event-stream', body=handler.wfile.getvalue())
        page.route('**/api/bridge/prime', serve)
        page.evaluate('p => renderBridgeClarifyCard({pending:p})', entry.data)
        page.locator('#cbInput').fill('Bozza da conservare')
        page.locator('[data-option-label="Prima Instagram diretto"]').click()
        evidence = os.getenv('HERMES_CAPTURE_LATE_CLARIFY_UI')
        if evidence:
            target = Path(evidence); target.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(target / f'before-submit-{width}.png'))
        page.locator('.cb-clarify-send').click()
        try:
            page.wait_for_function('document.querySelector(".cb-clarify-card").classList.contains("cb-resolved")', timeout=4000)
        except Exception:
            pytest.fail(f'browser errors={errors}; calls={len(calls)}; log={page.locator("#cbLog").inner_text()}')
        page.wait_for_function('document.querySelector("#cbLog").textContent.includes("Instagram diretto confermato.")', timeout=4000)
        assert page.locator('#cbInput').input_value() == 'Bozza da conservare'
        assert len(calls) == 1
        assert not errors
        if evidence:
            page.screenshot(path=str(target / f'after-submit-{width}.png'))
        # A cold history card stays resolved and cannot submit again.
        renderer = source[source.index('  function renderPrimeHistoryMessage('):source.index('  function renderPrimeHistoryMessages(')]
        page.add_script_tag(content=renderer + '\nfunction orderPrimeTimeline(){}')
        saved_question = next(m for m in store.history()['messages'] if m.get('_bridge_clarify_event') == 'request')
        page.evaluate('m => { $("cbLog").innerHTML="";renderPrimeHistoryMessage(m,1,""); }', saved_question)
        assert page.locator('.cb-clarify-card.cb-resolved').count() == 1
        assert page.locator('.cb-clarify-send').count() == 0
        assert not errors
        if evidence:
            baseline = subprocess.run(['git', 'show', 'cd1694a7:static/command_bridge.js'], cwd=ROOT,
                                      capture_output=True, check=True).stdout.decode('utf-8')
            scene(page, baseline)
            old_cards = baseline[baseline.index('  function normalizeAttentionPending('):baseline.index('  // La GET dello storico')]
            page.add_script_tag(content=old_cards + '\nfunction apiPost(){return Promise.resolve({ok:true})}')
            page.evaluate('p => renderBridgeClarifyCard({pending:p})', entry.data)
            page.locator('[data-option-label="Prima Instagram diretto"]').click()
            page.locator('.cb-clarify-send').click()
            page.wait_for_function('document.querySelector(".cb-clarify-card").classList.contains("cb-resolved")')
            page.screenshot(path=str(target / f'before-fix-{width}.png'))
    finally:
        page.close()
