"""Rolling Prime transcript window, lossless retrieval and compact delegation UI."""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from api import codex_prime, prime_session_store as pss, routes
from api.prime_session_store import PrimeSessionStore


def populated(tmp_path, count):
    store = PrimeSessionStore(tmp_path / 'prime.json')
    for i in range(count):
        store.inject_assistant_message(f'message {i}', {'brief_id': f'brief-{i}'})
    return store


@pytest.mark.parametrize('count', [0, 49, 50, 51, 52, 151])
def test_window_boundary_retrieval_and_read_only_projection(tmp_path, count):
    store = populated(tmp_path, count)
    before = store.path.read_bytes() if store.path.exists() else None
    hist = store.history(windowed=True)
    floor = max(0, count - 50)
    assert len(hist['messages']) == min(50, count)
    assert [m['message_index'] for m in hist['messages']] == list(range(floor, count))
    assert hist['message_count'] == hist['total'] == count
    assert hist['history_window']['start_index'] == floor
    assert hist['history_window']['archived_count'] == floor
    if count:
        assert store.retrieve_history(0, 1)['messages'][0]['content'] == 'message 0'
        assert store.inject_assistant_message('retry', {'brief_id': 'brief-0'}) == 0
        assert store.path.read_bytes() == before
        assert len(PrimeSessionStore(store.path).history(windowed=True)['messages']) == min(50, count)
    else:
        assert not store.path.exists()


def test_delta_cursor_survives_window_movement_and_pending_turn(tmp_path):
    store = populated(tmp_path, 75)
    stream = store.begin_turn('current question')
    history = store.history(windowed=True)
    assert history['messages'][-1]['content'] == 'current question'
    assert history['pending_turn']['stream_id'] == stream
    assert store.finish_turn(stream, 'current answer') == 76
    delta = store.history(75, windowed=True)
    assert delta['since_index'] == 75
    assert [m['message_index'] for m in delta['messages']] == [75, 76]
    assert delta['messages'][-1]['reply_to_index'] == 75
    assert store.history(77, windowed=True)['messages'] == []
    # A device returning after a large gap receives a bounded tail and advances
    # its cursor over every original slot, not just the rendered rows.
    gap = store.history(2, windowed=True)
    assert gap['since_index'] == 2 and gap['message_count'] == 77
    assert len(gap['messages']) == 50
    assert gap['messages'][0]['message_index'] == 27


def test_compact_history_excludes_worker_logs_but_keeps_final_brief(tmp_path):
    store = populated(tmp_path, 60)
    store.upsert_delegation('d1', task_excerpt='PRIVATE LONG TASK', status='ok', brief_status='delivered', anchor_message_index=60)
    stream = store.begin_turn('question')
    store.append_tool_event(stream, 'mcp__hermes_prime__delega', 'FULL WORKER LOG')
    store.finish_turn(stream, 'delegated')
    store.inject_assistant_message('Verified summary only.', {'task_id': 'd1', 'brief_id': 'brief-d1'})
    before = store.path.read_bytes()
    payload = store.history_with_tool_events(compact=True)
    assert len(payload['delegations']) == 1
    assert payload['tool_events'] == []
    assert 'task_excerpt' not in payload['delegations'][0]
    assert payload['delegations'][0]['summary'] == 'PRIVATE LONG TASK'
    assert 'FULL WORKER LOG' not in json.dumps(payload)
    assert payload['messages'][-1]['content'] == 'Verified summary only.'
    assert store.get_tool_events()[0]['summary'] == 'FULL WORKER LOG'
    assert store.get_delegations()[0]['task_excerpt'] == 'PRIVATE LONG TASK'
    assert store.path.read_bytes() == before


def test_codex_context_excludes_rolling_archive(tmp_path, monkeypatch):
    from api import memory_retrieval
    store = populated(tmp_path, 90)
    monkeypatch.setattr(pss, 'get_prime_session_store', lambda _: store)
    monkeypatch.setattr(routes, '_prime_system_prompt_for_user', lambda *args: 'Instructions')
    monkeypatch.setattr(memory_retrieval, 'build_prime_unlocked_memory_detail', lambda *args, **kw: '')
    monkeypatch.setattr(memory_retrieval, 'build_prime_memory_context', lambda *args, **kw: '')
    prompt = codex_prime.build_prompt('current', tmp_path, session_id='hermes-prime', user='giorgio')
    assert '"content": "message 0"' not in prompt
    assert 'message 89' in prompt and 'prime_history' in prompt
    assert 'history_window' in prompt and '"total_messages": 90' in prompt
    assert store.retrieve_history(0, 1)['messages'][0]['content'] == 'message 0'


def test_browser_history_retains_recent_and_running_cards_without_model_cards(tmp_path):
    store = populated(tmp_path, 100)
    store.upsert_delegation('old', anchor_message_index=0, status='ok')
    store.upsert_delegation('recent', anchor_message_index=75, status='ok',
                            task_excerpt='Correggere il timer. ' + 'LONG PROMPT ' * 100)
    store.upsert_delegation('brief', anchor_message_index=1, brief_message_index=90, status='errore')
    store.upsert_delegation('running', anchor_message_index=2, status='in_corso')
    before = store.path.read_bytes()
    cards = store.history_with_tool_events(compact=True)['delegations']
    assert [c['id'] for c in cards] == ['recent', 'brief', 'running']
    assert len(cards[0]['summary']) == 160
    assert cards[0]['summary'].startswith('Correggere il timer.')
    assert all('task_excerpt' not in c and 'output' not in c for c in cards)
    assert store.history(windowed=True)['delegations'] == []
    assert store.path.read_bytes() == before


def test_tasks_compact_wire_drops_large_fields_without_mutating_workers(tmp_path, monkeypatch):
    from api import delegation_store, prime_delegation
    task = {'id': 'd1', 'agent': 'programmatore', 'status': 'ok', 'task': 'T' * 100000,
            'output': 'R' * 100000, 'diagnostic_log': 'L' * 100000, 'librarian_status': 'in_corso'}
    monkeypatch.setattr(prime_delegation, 'get_background_tasks', lambda: [task])
    monkeypatch.setattr(delegation_store, 'get_delegation_store', lambda _: type('Empty', (), {'get_all': lambda _: []})())
    monkeypatch.setattr(routes, '_request_prime_session_id', lambda _: 'hermes-prime')
    monkeypatch.setattr(routes, '_sync_prime_delegation_records', lambda *args: None)
    monkeypatch.setattr(pss, 'get_prime_session_store', lambda _: PrimeSessionStore(tmp_path / 'p.json'))
    captured = []
    monkeypatch.setattr(routes, 'j', lambda _, payload, **kw: captured.append(payload) or True)
    routes._handle_bridge_tasks(object(), urlsplit('/api/bridge/tasks?compact=1'))
    assert captured[-1]['tasks'][0]['summary'] == 'T' * 159 + '…'
    assert not {'task', 'output', 'diagnostic_log'} & captured[-1]['tasks'][0].keys()
    assert len(json.dumps(captured[-1])) < 1000
    assert len(task['output']) == 100000


@pytest.fixture(scope='module')
def browser():
    pw = pytest.importorskip('playwright.sync_api')
    with pw.sync_playwright() as p:
        b = p.chromium.launch(headless=True, channel=os.getenv('HERMES_TEST_BROWSER_CHANNEL') or None)
        yield b
        b.close()


def ui_source():
    source = Path('static/command_bridge.js').read_text(encoding='utf-8')
    return (source[source.index('  function taskStateClass('):source.index('  // opts.replay')] +
            source[source.index('  function renderTask('):source.index('  function requestBrief(t)')] +
            source[(source.index('  function primeTimestamp(') if '  function primeTimestamp(' in source else source.index('  function renderPrimeHistoryMessage(')):source.index('  // Record durevole')] +
            source[source.index('  function applyPrimeHistoryWindow('):source.index('  function renderPrimeHistoryPayload(')] +
            source[source.index('  function syncPrimeTranscriptFromServer('):source.index('  /*', source.index('  function syncPrimeTranscriptFromServer('))])


HARNESS = r'''
var _cbTasks={}, _cbHistoryBatch=0, _cbRenderedCount=0, _cbDelegationsRev=0;
function esc(s){var n=document.createElement('div');n.textContent=String(s||'');return n.innerHTML}
var _cbHistoryLoaded=true, _cbSyncBusy=false, _cbRemoteTurnNode=null;
var requested=[], payload=null, _sb=true, userEngaged=false;
window._showTokenUsage=false;
function $(id){return document.getElementById(id)}
function el(tag,cls){var n=document.createElement(tag);n.className=cls;return n}
function nearBottom(){return _sb}
function renderRich(n,text){n.textContent=text}
function _hasBridgeUsage(){return false}
function ownPrimeTurnInFlight(){return false}
function requestBrief(t){requested.push(t.id)}
function hydrateDelegationCards(){throw Error('unexpected card hydration')}
function fetchPrimeHistory(){return Promise.resolve(payload)}
function primeSay(role,text){var n=el('div','cb-msg cb-from-'+role);n.innerHTML='<div class="cb-who">'+role+'</div><div class="cb-bubble"></div>';n.querySelector('.cb-bubble').textContent=text;$('cbLog').appendChild(n);return n}
function seed(count){for(var i=0;i<count;i++)renderPrimeHistoryMessage({role:'assistant',content:'Messaggio '+i},i)}
'''


@pytest.mark.parametrize('width', [1200, 650, 360])
def test_browser_cold_replay_delta_large_gap_and_summary_only(browser, width, tmp_path):
    page = browser.new_page(viewport={'width': width, 'height': 900})
    try:
        page.set_content('<div id="cbLog" style="height:800px;overflow:auto"></div>')
        page.add_script_tag(content=HARNESS + ui_source())
        page.evaluate('seed(50); _cbRenderedCount=50;')
        # Running tasks and repeated polls never attach full worker text to DOM.
        page.evaluate("renderTask({id:'d1',status:'in_corso',output:'FULL WORKER OUTPUT',task:'FULL TASK'});renderTask({id:'d1',status:'ok'});renderTask({id:'d1',status:'ok'});")
        assert page.evaluate('requested') == ['d1']
        assert page.locator('.cb-deleg').count() == 1
        assert 'FULL TASK' not in page.locator('#cbLog').inner_text()
        assert 'FULL WORKER' not in page.locator('#cbLog').inner_text()
        page.evaluate("payload={since_index:50,message_count:51,delegations_rev:1,history_window:{start_index:1,archived_count:1},messages:[{role:'assistant',content:'Riassunto verificato.',message_index:50,brief_id:'brief-d1',task_id:'d1'}]}; hydrateDelegationCards=function(){};syncPrimeTranscriptFromServer(51,1);")
        page.wait_for_function('!_cbSyncBusy && _cbRenderedCount===51')
        assert page.locator('[data-cb-msg-index]').count() == 50
        assert page.locator('[data-cb-msg-index="0"]').count() == 0
        assert page.locator('[data-cb-brief-id="brief-d1"]').count() == 1
        # An unindexed live reply must survive a window advance.
        page.evaluate("var live=primeSay('prime','Turno attivo');live.id='active';payload={since_index:51,message_count:200,delegations_rev:2,history_window:{start_index:150,archived_count:150},messages:Array.from({length:50},(_,i)=>({role:'assistant',content:'Recente '+(150+i),message_index:150+i}))};syncPrimeTranscriptFromServer(200,2);")
        page.wait_for_function('!_cbSyncBusy && _cbRenderedCount===200')
        assert page.locator('[data-cb-msg-index]').count() == 50
        assert page.locator('#active').count() == 1
        assert page.locator('[data-cb-window-note]').inner_text().startswith('Ultimi 50 messaggi')
        # A revision reconciles cards even when message_count has not changed.
        page.evaluate("syncPrimeTranscriptFromServer(200,99);")
        page.wait_for_function('!_cbSyncBusy')
        assert page.locator('[data-cb-msg-index]').count() == 50
    finally:
        page.close()

@pytest.mark.parametrize('count, rotations', [(50, 0), (51, 1)])
def test_claude_does_not_resume_archived_context(tmp_path, monkeypatch, count, rotations):
    import asyncio
    from concurrent.futures import Future, ThreadPoolExecutor
    from api import bridge_attachments, memory_retrieval, prime_auto_compact
    store = populated(tmp_path, count)
    monkeypatch.setattr(pss, 'get_prime_session_store', lambda _: store)
    monkeypatch.setattr(routes, '_prime_system_prompt_for_user', lambda *args: 'Instructions')
    monkeypatch.setattr(routes, '_in_progress_projects_brief', lambda *args, **kw: '')
    monkeypatch.setattr(memory_retrieval, 'build_prime_unlocked_memory_detail', lambda *args, **kw: '')
    monkeypatch.setattr(memory_retrieval, 'build_prime_memory_context', lambda *args, **kw: '')
    monkeypatch.setattr(bridge_attachments, 'prime_turn_started', lambda **kw: 1)
    monkeypatch.setattr(bridge_attachments, 'record_prime_images', lambda *args, **kw: None)
    monkeypatch.setattr(routes, '_claude_attachment_note', lambda *args, **kw: '')
    monkeypatch.setattr(prime_auto_compact, 'maybe_auto_compact_prime', lambda *args, **kw: None)
    monkeypatch.setattr(prime_auto_compact, 'pop_compact_after_task', lambda: False)
    monkeypatch.setattr(routes, '_prime_background_tasks', lambda *args: [])
    seen = []
    class Client:
        async def query(self, message, **kwargs):
            seen.append(message)
        async def receive_response(self):
            event = {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'answer'}}
            yield type('StreamMessage', (), {'event': event})()
    class Registry:
        client = Client()
        closed = 0
        def get(self, session_id):
            return self.client
        def close(self, session_id):
            self.closed += 1
            self.client = None
        def get_or_create(self, session_id, **kw):
            self.client = self.client or Client()
        def submit_turn(self, session_id, drive):
            future = Future()
            with ThreadPoolExecutor(max_workers=1) as executor:
                future.set_result(executor.submit(lambda: asyncio.run(drive(self.client))).result())
            return future
    registry = Registry()
    monkeypatch.setattr(routes, '_get_claude_registry', lambda: registry)
    result = routes._hermes_prime_reply_claude('current question', tmp_path,
                                              model_state={'model': 'claude-sonnet-4-6'})
    assert result['reply'] == 'answer'
    assert registry.closed == rotations
    assert seen[0].startswith('current question')
    if rotations:
        assert 'history_window' in seen[0] and f'message {count-1}' in seen[0]
        assert '"content": "message 0"' not in seen[0]


def test_pending_owner_survives_even_many_background_appends(tmp_path):
    store = populated(tmp_path, 60)
    stream = store.begin_turn('OWNING QUESTION')
    for i in range(60):
        store.inject_assistant_message(f'background {i}')
    hist = store.history(windowed=True)
    assert len(hist['messages']) == 50
    assert hist['messages'][0]['message_index'] == 60
    assert hist['messages'][0]['content'] == 'OWNING QUESTION'
    assert hist['pending_turn']['stream_id'] == stream
    assert len(hist['history_window']['visible_indexes']) == 50
    store.finish_turn(stream, 'answer')
    assert len(store.history(windowed=True)['messages']) == 50


def test_rolling_window_and_cold_archive_share_lossless_retrieval(tmp_path):
    path = tmp_path / 'prime.json'
    state = {'session_id': 'hermes-prime', 'messages': [
        {'role': 'assistant', 'content': f'original {i}',
         'created_at': '2026-10-03T12:00:00Z' if i < 60 else '2026-10-05T12:00:00Z'}
        for i in range(120)]}
    path.write_text(json.dumps(state), encoding='utf-8')
    path.with_suffix('.archive-request.json').write_text(json.dumps({
        'schema': 1, 'session_id': 'hermes-prime', 'cutoff': '2026-10-04T00:00:00+02:00',
        'summary': 'Prior decisions in memory.'}), encoding='utf-8')
    store = PrimeSessionStore(path)
    hist = store.history_with_tool_events(compact=True)
    assert hist['archive']['archived_count'] == 60
    assert hist['history_window']['archived_count'] == 70
    assert [m['message_index'] for m in hist['messages']] == list(range(70, 120))
    assert store.retrieve_history(0, 1)['messages'][0]['content'] == 'original 0'
    assert store.retrieve_history(65, 1)['messages'][0]['content'] == 'original 65'
