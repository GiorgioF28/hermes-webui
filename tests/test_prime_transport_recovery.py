"""A lost browser observer reconciles only its original durable submission."""
# ruff: noqa: F811 -- imported pytest fixtures are injected by name
import os
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
from urllib.parse import urlsplit

import pytest

from api import prime_session_store, routes
from tests.test_prime_transport_continuity import bridge  # noqa: F401
from tests.test_prime_brief_dedup import browser  # noqa: F401
from tests.test_prime_image_upload import scene

ROOT = Path(__file__).resolve().parents[1]


def test_request_receipt_survives_completion_reload_and_other_turn(tmp_path):
    store = prime_session_store.PrimeSessionStore(tmp_path / 'prime.json')
    assert store.browser_request('r1') == {'state': 'unknown'}
    stream = store.begin_turn('question', request_id='r1')
    store.append_token(stream, 'partial')
    assert store.browser_request('r1')['partial_output'] == 'partial'
    store.finish_turn(stream, 'final', usage={'output_tokens': 4})
    store = prime_session_store.PrimeSessionStore(store.path)
    other = store.begin_turn('other', request_id='r2')
    before = store.path.read_bytes()
    receipt = store.browser_request('r1')
    assert receipt['state'] == 'done' and receipt['reply'] == 'final'
    assert receipt['stream_id'] == stream != other
    assert receipt['assistant_message_index'] == 1
    assert receipt['usage'] == {'output_tokens': 4}
    assert store.path.read_bytes() == before


@pytest.mark.parametrize('terminal', ['error', 'cancelled', 'restart'])
def test_partial_and_empty_terminal_are_never_completed(tmp_path, terminal):
    store = prime_session_store.PrimeSessionStore(tmp_path / 'prime.json')
    stream = store.begin_turn('question', request_id='receipt')
    store.append_token(stream, 'partial')
    if terminal == 'error':
        store.mark_error(stream, 'provider failed')
        assert not store.live()['active']
    elif terminal == 'cancelled':
        store.cancel_turn(stream)
    else:
        store.recover_stale_pending_turn()
    assert store.browser_request('receipt')['state'] == ('interrupted' if terminal == 'restart' else terminal)
    stream = store.begin_turn('empty', request_id='empty')
    store.cancel_turn(stream)
    assert store.browser_request('empty')['state'] == 'cancelled'


def test_live_receipt_is_session_scoped_and_queued(bridge, monkeypatch):
    store, handler = bridge
    monkeypatch.setattr(routes, '_request_prime_session_id', lambda _: 'one')
    monkeypatch.setattr(routes, 'j', lambda _, data, **kw: data)
    routes._PRIME_BROWSER_REQUESTS[('one', 'queued')] = {'stream_id': None}
    try:
        parsed = urlsplit('/api/bridge/prime/live?request_id=queued')
        assert routes._handle_bridge_prime_live(handler, parsed)['request']['state'] == 'queued'
        monkeypatch.setattr(routes, '_request_prime_session_id', lambda _: 'two')
        assert routes._handle_bridge_prime_live(handler, parsed)['request']['state'] == 'unknown'
    finally:
        routes._PRIME_BROWSER_REQUESTS.pop(('one', 'queued'), None)


def test_post_persists_request_before_first_sse_frame(bridge, monkeypatch):
    store, handler = bridge
    def sse(_, event, payload):
        if event == 'started':
            assert store.browser_request('r1')['stream_id'] == payload['stream_id']
            raise BrokenPipeError('lost acknowledgement')
    monkeypatch.setattr('api.streaming._sse', sse)
    monkeypatch.setattr(routes, '_call_hermes_prime_reply_for_bridge', lambda *a, **kw: {'reply': 'final'})
    routes._handle_bridge_prime(handler, {'message': 'question', 'request_id': 'r1'})
    assert store.browser_request('r1')['reply'] == 'final'


def setup(page, source=None):
    source = source or (ROOT / 'static/command_bridge.js').read_text(encoding='utf-8')
    page.goto('about:blank')
    scene(page, source)
    page.evaluate("var stopButton=document.createElement('button');stopButton.id='cbStop';stopButton.className='cb-stop';stopButton.type='button';stopButton.hidden=true;stopButton.textContent='Stop';$('cbSend').before(stopButton)")
    controls = source[source.index('  function createPrimeTurnUi('):source.index('  function streamPrimeResponse(')]
    cancel = source[source.index('  function setPrimeStreaming('):source.index('  function startPrimeLivePolling(')]
    page.add_script_tag(content=controls + cancel + r"""
    var _primeStreaming=false,_primeStreamId=null,diagnostics=[],reads=[],nextRecovery=null,statusDeadline=null;
    function renderRich(node,text){node.textContent=text}
    function updatePrimeLiveText(root,text){root.querySelector('.cb-bubble').textContent=text}
    function applyPrimeTimestamp(){}
    function startPrimeLivePolling(){window.legacyRecovery=true}
    function streamPrimeResponse(response,handlers){window.turnHandlers=handlers;return new Promise((resolve,reject)=>{window.cut=reject;window.eof=resolve})}
    var normalTimeout=window.setTimeout;
    var normalInterval=window.setInterval;
    window.setInterval=function(fn,ms){if(ms===1000)window.progressTick=fn;return normalInterval(fn,ms)};
    window.setTimeout=function(fn,ms){if(ms===2000){nextRecovery=fn;return 12345}if(ms===5000)statusDeadline=fn;return normalTimeout(fn,ms)};
    window.fetch=function(url,opts){
      if(url.includes('/prime/live?'))return new Promise((resolve,reject)=>{
        reads.push({url,opts,resolve,reject});
        opts.signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')));
      });
      if(url.endsWith('/client-events/log')){diagnostics.push(JSON.parse(opts.body));return Promise.resolve({ok:true})}
      if(url.endsWith('/prime/cancel')){window.cancelBody=JSON.parse(opts.body);return Promise.resolve({ok:true,json:()=>Promise.resolve({ok:true,cancelled:true})})}
      posts.push(JSON.parse(opts.body));return Promise.resolve({ok:true});
    };
    function status(request,index=reads.length-1){reads[index].resolve({ok:true,json:()=>Promise.resolve({active:true,pending_turn:{stream_id:'unrelated'},request})})}
    function advance(){var fn=nextRecovery;nextRecovery=null;fn()}
    function receipt(state,extra){return Object.assign({state,stream_id:'owned',user_message_index:10,created_at:1792135000},extra||{})}
    function begin(){ $('cbInput').value='Controlla il progetto';submit() }
    $('cbStop').onclick=cancelPrimeTurn;
    """)
    page.evaluate('delete window.turnHandlers;begin()')
    page.wait_for_function('window.turnHandlers !== undefined')


@pytest.mark.parametrize('width', [1200, 650, 360])
@pytest.mark.parametrize('failure', ['before_started', 'partial', 'eof'])
def test_disconnect_recovers_one_owned_final_without_resubmitting(browser, width, failure):
    page = browser.new_page(viewport={'width': width, 'height': 700})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    evidence = os.getenv('HERMES_CAPTURE_TRANSPORT_UI')
    try:
        if evidence and failure == 'partial':
            baseline = subprocess.check_output(['git', 'show', '05d19adb:static/command_bridge.js'], cwd=ROOT).decode('utf-8')
            setup(page, baseline)
            page.evaluate("turnHandlers.started({stream_id:'owned'});turnHandlers.token({text:'Verifico il progetto.'});cut(new TypeError('Failed to fetch'))")
            page.wait_for_function('window.legacyRecovery===true')
            Path(evidence).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(evidence) / f'before-{width}.png'))
        setup(page)
        if failure != 'before_started':
            page.evaluate("turnHandlers.started({stream_id:'owned',user_message_index:10});turnHandlers.token({text:'Verifico il progetto.'})")
        page.evaluate("eof()" if failure == 'eof' else "cut(new TypeError('Failed to fetch'))")
        page.wait_for_function('reads.length===1')
        assert page.evaluate('_cbOwnTurnCount') == 1
        assert page.locator('.cb-sysnote').count() == 0
        assert page.evaluate('reads[0].url.includes(posts[0].request_id)')
        page.evaluate("status(receipt('running',{partial_output:'Verifico il progetto. Ho trovato il problema.'}))")
        page.wait_for_function('nextRecovery !== null')
        assert 'Ho trovato il problema.' in page.locator('.cb-from-prime .cb-bubble').inner_text()
        assert page.evaluate('_primeStreamId') == 'owned'
        assert page.locator('#cbStop').is_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if evidence and failure == 'partial': page.screenshot(path=str(Path(evidence) / f'after-{width}.png'))
        page.evaluate("advance();status(receipt('done',{reply:'Correzione completata.',assistant_message_index:11}))")
        page.wait_for_function('_cbOwnTurnCount===0')
        assert page.locator('.cb-from-prime').count() == 1
        assert page.locator('.cb-from-prime .cb-bubble').inner_text() == 'Correzione completata.'
        assert page.evaluate('posts.length') == 1
        assert page.evaluate('diagnostics.length') == 1
        assert page.evaluate("diagnostics[0].source") == 'command_bridge_prime'
        assert 'Controlla' not in page.evaluate('JSON.stringify(diagnostics)')
        assert page.evaluate('_primeBrowserTurns.length') == 0
        if evidence and failure == 'partial': page.screenshot(path=str(Path(evidence) / f'delivered-{width}.png'))
        assert errors == []
    finally:
        page.close()


def test_recovery_timeout_retries_and_stop_ignores_late_status(browser):
    page = browser.new_page()
    try:
        setup(page)
        page.evaluate("cut(new TypeError('offline'))")
        page.wait_for_function('reads.length===1')
        page.evaluate('statusDeadline()')
        page.wait_for_function('nextRecovery !== null')
        page.evaluate("advance();status(receipt('running',{partial_output:'Parziale'}))")
        page.wait_for_function("_primeStreamId==='owned'")
        page.evaluate('advance()')
        page.locator('#cbStop').click()
        page.wait_for_function('_cbOwnTurnCount===0')
        assert page.evaluate('cancelBody.stream_id') == 'owned'
        assert page.evaluate('cancelBody.request_id===posts[0].request_id')
        page.evaluate("status(receipt('done',{reply:'stale'}))")
        assert 'stale' not in page.locator('#cbLog').inner_text()
        assert page.evaluate('posts.length') == 1
    finally:
        page.close()


def test_missing_heartbeat_detaches_observer_and_recovers(browser):
    page = browser.new_page()
    try:
        setup(page)
        page.evaluate("var realNow=Date.now;Date.now=()=>realNow()+46000;progressTick()")
        page.wait_for_function('reads.length===1')
        assert page.evaluate('diagnostics[0].reason') == 'heartbeat_timeout'
        assert page.evaluate('_primeBrowserTurns[0].controller.signal.aborted')
        assert page.locator('#cbStop').is_visible()
        assert page.evaluate('posts.length') == 1
    finally:
        page.close()


@pytest.mark.parametrize('state', ['error', 'interrupted', 'unknown', 'queued'])
def test_nonfinal_receipts_do_not_complete_or_adopt_other_turn(browser, state):
    page = browser.new_page()
    try:
        setup(page)
        page.evaluate("cut(new TypeError('offline'))")
        page.wait_for_function('reads.length===1')
        if state == 'unknown': page.evaluate('var actualNow=Date.now;Date.now=()=>actualNow()+31000')
        page.evaluate("s=>status(receipt(s,{stream_id:s==='queued'||s==='unknown'?null:'owned',partial_output:'Parziale'}))", state)
        if state == 'queued':
            page.wait_for_function('nextRecovery !== null')
            assert page.evaluate('_cbOwnTurnCount') == 1
            assert page.evaluate('_primeStreamId') is None
        else:
            page.wait_for_function('_cbOwnTurnCount===0')
            assert page.locator('.cb-sysnote').count() == 1
        if state == 'unknown': assert page.locator('#cbInput').input_value() == 'Controlla il progetto'
        assert page.evaluate('posts.length') == 1
        assert page.evaluate('_primeStreamId') != 'unrelated'
    finally:
        page.close()


def test_real_http_eof_recovers_durable_reply_without_second_provider(browser, monkeypatch, tmp_path):
    """Actual TCP close, browser fetch/reader, GET route and temporary store."""
    store = prime_session_store.PrimeSessionStore(tmp_path / 'prime.json')
    monkeypatch.setattr(prime_session_store, 'get_prime_session_store', lambda _: store)
    monkeypatch.setattr(routes, '_sse_set_write_deadline', lambda _: None)
    monkeypatch.setattr(routes, '_bridge_prime_start_attention_relays', lambda *a, **kw: lambda: None)
    release = threading.Event()
    calls = []
    original_sse = __import__('api.streaming', fromlist=['_sse'])._sse
    def cut_socket(handler, event, payload):
        original_sse(handler, event, payload)
        if event == 'token':
            handler.connection.shutdown(socket.SHUT_RDWR)
    monkeypatch.setattr('api.streaming._sse', cut_socket)
    def reply(*a, on_token, **kw):
        calls.append('provider')
        on_token('Sto verificando.')
        assert release.wait(5)
        return {'reply': 'Risposta recuperata via HTTP.'}
    monkeypatch.setattr(routes, '_call_hermes_prime_reply_for_bridge', reply)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path.endswith('/live'):
                routes._handle_bridge_prime_live(self, parsed)
                release.set()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'<html><body></body></html>')
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)))
            if self.path == '/api/bridge/prime':
                routes._handle_bridge_prime(self, body)
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{}')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    page = browser.new_page()
    try:
        origin = f'http://127.0.0.1:{server.server_port}/'
        page.goto(origin)
        source = (ROOT / 'static/command_bridge.js').read_text(encoding='utf-8')
        scene(page, source)
        controls = source[source.index('  function createPrimeTurnUi('):source.index('  function _ensureCtx(')]
        page.add_script_tag(content=controls + r"""
        function renderRich(node,text){node.textContent=text}
        function updatePrimeLiveText(root,text){root.querySelector('.cb-bubble').textContent=text}
        function applyPrimeTimestamp(){}
        window.fetch=window.nativeUploadFetch;
        var actualTimeout=window.setTimeout;
        window.setTimeout=(fn,ms)=>actualTimeout(fn,ms===2000?50:ms);
        """)
        page.evaluate("origin=>{document.querySelector('base').href=origin;$('cbInput').value='Test isolato';submit()}", origin)
        page.wait_for_function("document.querySelector('.cb-from-prime .cb-bubble')?.textContent==='Risposta recuperata via HTTP.'")
        assert calls == ['provider']
        assert page.locator('.cb-from-prime').count() == 1
        assert page.locator('.cb-sysnote').count() == 0
        assert [m['content'] for m in store.history()['messages']] == ['Test isolato', 'Risposta recuperata via HTTP.']
    finally:
        release.set()
        page.close()
        server.shutdown()
        server.server_close()
        worker.join(2)
    assert not worker.is_alive()
