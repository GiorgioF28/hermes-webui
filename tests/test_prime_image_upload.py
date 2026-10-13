"""Composer upload recovery: no transcript messages before attachment acceptance."""
import io
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import time
import threading

import pytest

from tests.test_prime_brief_dedup import browser  # noqa: F401


ROOT = Path(__file__).resolve().parents[1]


def scene(page, source=None):
    source = source or (ROOT / 'static/command_bridge.js').read_text(encoding='utf-8')
    start = source.index('  var pendingAttachments =')
    code = source[start:source.index('  /*', source.index('function onPrimeSubmit', start))]
    styles = source[source.index('  function injectStyles()'):source.index('  /*', source.index('  function injectStyles()'))]
    page.set_content('<base href="http://upload.test/hermes/"><style>body{margin:0}main.cb-root{height:100dvh}</style><main class="cb-root"><div class="cb-hero"><section class="cb-chat"><div class="cb-chat-head">HERMES PRIME</div><div id="cbLog" class="cb-chat-log"></div><div id="cbAtt" class="cb-attbar"></div><form id="cbForm" class="cb-chat-input"><textarea id="cbInput">Guarda questa foto</textarea></form></section><div class="cb-actions"><button id="cbAttachBtn" class="cb-attach">Foto</button><button id="cbSend" class="cb-send">Invia</button></div></div></main>')
    page.evaluate("document.querySelector('#cbForm').appendChild(document.querySelector('.cb-actions'))")
    page.add_script_tag(content=r"""
    function $(id){return document.getElementById(id)}
    function el(tag,cls){var n=document.createElement(tag);n.className=cls;return n}
    function esc(s){var n=el('span','');n.textContent=String(s||'');return n.innerHTML}
    var _primeBrowserTurns=[],userEngaged=false,_cbOwnTurnCount=0,_ttsActive=false,posts=[],uploads=[],notes=[];
    window.__HERMES_CONFIG__={csrfToken:'test-token',maxUploadBytes:20971520};
    function sysNote(s){notes.push(s);var n=el('div','cb-sysnote');n.textContent=s;$('cbLog').append(n)}
    function handlePrimeSlashCommand(){return false}
    function primeSay(role,text,thumbs){var n=el('div','cb-msg cb-from-'+role);n.textContent=text;(thumbs||[]).forEach(src=>{var im=el('img','');im.src=src;n.append(im)});$('cbLog').append(n);return n}
    function pendingBubble(){return null}
    function createPrimeTurnUi(){return {updateStatus(){},close(){}}}
    function stopSpeak(){} function setOrb(){} function setPrimeStreaming(){}
    function _hasBridgeUsage(){return false} function flushSpokenSentences(){}
    function noteOwnPrimeTurnEnded(){} function repositionPrimeTaskCards(){}
    function stampPrimeNode(){} function nearBottom(){return true}
    function streamPrimeResponse(r,h){h.done({reply:'Ricevuto.'});return Promise.resolve()}
    var nativeTimer=window.setTimeout,deadline=null;
    window.setTimeout=function(fn,ms){if(ms===10000){deadline=fn;return nativeTimer(fn,ms)}return nativeTimer(fn,ms)};
    window.nativeUploadFetch=window.fetch.bind(window);
    window.fetch=function(url,opts){
      if(url.endsWith('/upload'))return new Promise((resolve,reject)=>{
        var request={url,opts,resolve,reject};uploads.push(request);
        opts.signal && opts.signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')));
      });
      posts.push(JSON.parse(opts.body));return Promise.resolve({ok:true});
    };
    function accept(index=0,status=200){uploads[index].resolve({ok:status===200,status,json:()=>Promise.resolve(status===200?{path:'/isolated/photo.png',filename:'photo.png',mime:'image/png',size:204800}:{error:'Sessione scaduta: ricarica.'})})}
    function submit(){void onPrimeSubmit()}
    function photo(){var bytes=new Uint8Array(204800);var png=atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aFZkAAAAASUVORK5CYII=');for(var i=0;i<png.length;i++)bytes[i]=png.charCodeAt(i);return new File([bytes],'foto.png',{type:'image/png'})}
    """ + styles + '\ninjectStyles();\n' + code)


def test_fast_upload_and_duplicate_submit_send_once(browser):
    page = browser.new_page()
    try:
        scene(page)
        page.evaluate('uploadAttachment(photo());submit();submit();')
        assert page.evaluate('posts.length') == 0
        assert page.locator('#cbLog').inner_text() == ''
        assert page.locator('#cbInput').input_value() == 'Guarda questa foto'
        assert page.locator('#cbSend').is_disabled()
        assert page.evaluate("uploads[0].url") == 'http://upload.test/hermes/api/bridge/prime/upload'
        assert page.evaluate("uploads[0].opts.headers['X-CSRF-Token']") == 'test-token'
        page.evaluate('accept()')
        page.wait_for_function('posts.length===1')
        assert page.evaluate('posts[0].attachments') == [{'name': 'photo.png', 'path': '/isolated/photo.png'}]
        assert page.evaluate('posts[0].message') == 'Guarda questa foto'
        assert page.evaluate('notes') == []
        assert page.locator('#cbInput').input_value() == ''
        assert page.locator('#cbSend').is_enabled()
    finally:
        page.close()


@pytest.mark.parametrize('failure', ['timeout', 'http', 'network', 'json', 'json_stall'])
def test_failure_preserves_photo_and_draft_retry_never_autosends(browser, failure):
    page = browser.new_page()
    try:
        scene(page)
        page.evaluate('uploadAttachment(photo());submit()')
        if failure == 'timeout':
            page.evaluate('deadline()')
            assert page.evaluate('uploads[0].opts.signal.aborted')
        elif failure == 'http':
            page.evaluate('accept(0,403)')
        elif failure == 'json':
            page.evaluate("uploads[0].resolve({ok:true,status:200,json:()=>Promise.reject(new Error('invalid JSON'))})")
        elif failure == 'json_stall':
            page.evaluate("uploads[0].resolve({ok:true,status:200,json:()=>new Promise(()=>{})})")
            page.evaluate('deadline()')
        else:
            page.evaluate("uploads[0].reject(new TypeError('offline'))")
        page.wait_for_function("pendingAttachments[0].state==='error' && !_cbAttachmentSubmitWaiting")
        assert page.locator('#cbInput').input_value() == 'Guarda questa foto'
        assert page.locator('#cbLog').inner_text() == ''
        assert page.evaluate('posts.length') == 0
        assert page.evaluate('pendingAttachments[0].file.size') == 204800
        page.locator('[data-upload-retry]').click()
        page.evaluate('accept(1)')
        page.wait_for_function("pendingAttachments[0].state==='ready'")
        assert page.evaluate('posts.length') == 0
        page.evaluate('submit()')
        page.wait_for_function('posts.length===1')
        assert page.evaluate('notes') == []
    finally:
        page.close()


def test_removing_inflight_photo_aborts_and_cancels_send_intent(browser):
    page = browser.new_page()
    try:
        scene(page)
        page.evaluate('uploadAttachment(photo());submit()')
        page.locator('[data-upload-remove]').click()
        page.wait_for_function('!_cbAttachmentSubmitWaiting')
        page.evaluate('accept()')  # A stale successful response cannot resurrect it.
        assert page.evaluate('uploads[0].opts.signal.aborted')
        assert page.evaluate('pendingAttachments.length') == 0
        assert page.evaluate('posts.length') == 0
        assert page.locator('#cbInput').input_value() == 'Guarda questa foto'
        assert page.locator('#cbLog').inner_text() == ''
    finally:
        page.close()


def test_multiple_uploads_all_must_be_accepted_and_oversize_is_local(browser):
    page = browser.new_page()
    try:
        scene(page)
        page.evaluate('uploadAttachment(photo());uploadAttachment(photo());submit();accept(1)')
        assert page.evaluate('posts.length') == 0
        page.evaluate('accept(0)')
        page.wait_for_function('posts.length===1')
        assert page.evaluate('posts[0].attachments.length') == 2
        page.evaluate("window.__HERMES_CONFIG__.maxUploadBytes=100;uploadAttachment(photo())")
        assert page.evaluate('uploads.length') == 2
        assert page.evaluate('pendingAttachments.length') == 0
        assert page.evaluate('notes') == []
    finally:
        page.close()


@pytest.mark.parametrize('width', [1200, 650, 360])
def test_upload_notice_before_after_responsive_evidence(browser, width):
    page = browser.new_page(viewport={'width': width, 'height': 800})
    evidence = os.getenv('HERMES_CAPTURE_UPLOAD_UI')
    try:
        if evidence:
            baseline = subprocess.run(['git', 'show', 'e9d29e76:static/command_bridge.js'], capture_output=True, encoding='utf-8', check=True).stdout
            scene(page, baseline)
            page.evaluate('uploadAttachment(photo());submit()')
            assert page.locator('.cb-sysnote').count() == 1
            Path(evidence).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(evidence) / f'before-{width}.png'))
        scene(page)
        page.evaluate('uploadAttachment(photo());submit();deadline()')
        page.wait_for_function("pendingAttachments[0].state==='error' && !_cbAttachmentSubmitWaiting")
        assert page.locator('.cb-sysnote').count() == 0
        assert page.locator('[data-upload-retry]').is_visible()
        assert page.locator('#cbInput').input_value() == 'Guarda questa foto'
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        if evidence:
            page.screenshot(path=str(Path(evidence) / f'after-{width}.png'))
    finally:
        page.close()


def test_real_multipart_200kb_backend_writes_without_model(tmp_path, monkeypatch):
    from api import upload
    monkeypatch.setenv('HERMES_WEBUI_ATTACHMENT_DIR', str(tmp_path))
    responses = []
    monkeypatch.setattr(upload, 'j', lambda handler, data, status=200: responses.append((status, data)))
    body = b'\x89PNG\r\n' + b'x' * (204800 - 6)
    raw = (b'--unit\r\nContent-Disposition: form-data; name="file"; filename="photo.png"\r\n'
           b'Content-Type: image/png\r\n\r\n' + body + b'\r\n--unit--\r\n')
    class Handler:
        headers = {'Content-Type': 'multipart/form-data; boundary=unit', 'Content-Length': str(len(raw))}
        rfile = io.BytesIO(raw)
    start = time.monotonic()
    upload.handle_prime_upload(Handler())
    elapsed = time.monotonic() - start
    status, data = responses[0]
    assert status == 200
    assert Path(data['path']).read_bytes() == body
    assert Path(data['path']).is_relative_to(tmp_path)
    print(f'200 KB multipart parse+write: {elapsed*1000:.3f} ms')


def test_browser_http_200kb_upload_then_send_with_real_handler(browser, tmp_path, monkeypatch):
    """Real HTTP/multipart/disk path, with the provider replaced by a terminal SSE."""
    from api import upload
    monkeypatch.setenv('HERMES_WEBUI_ATTACHMENT_DIR', str(tmp_path))
    sent = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'<html></html>')

        def do_POST(self):
            if self.path.endswith('/upload'):
                upload.handle_prime_upload(self)
            else:
                sent.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                self.wfile.write(b'event: done\ndata: {"reply":"Accepted"}\n\n')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    page = browser.new_page()
    try:
        origin = f'http://127.0.0.1:{server.server_port}'
        page.goto(origin)
        scene(page)
        page.evaluate("origin=>{document.querySelector('base').href=origin+'/';window.fetch=window.nativeUploadFetch}", origin)
        source = (ROOT / 'static/command_bridge.js').read_text(encoding='utf-8')
        page.add_script_tag(content=source[source.index('  function streamPrimeResponse('):source.index('  function _ensureCtx(')])
        page.evaluate('window.uploadStarted=performance.now();uploadAttachment(photo());submit()')
        page.wait_for_function("_cbOwnTurnCount===0 && pendingAttachments.length===0 && $('cbInput').value===''")
        elapsed = page.evaluate('performance.now()-uploadStarted')
        assert len(sent) == 1
        attachment = Path(sent[0]['attachments'][0]['path'])
        assert attachment.is_relative_to(tmp_path)
        assert attachment.stat().st_size == 204800
        assert page.locator('#cbLog').inner_text() == 'Guarda questa foto'
        assert page.evaluate('notes') == []
        print(f'Browser 200 KB HTTP upload+send+SSE: {elapsed:.1f} ms')
    finally:
        page.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
