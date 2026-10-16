"""Actual Command Bridge submit/Stop code in an isolated browser."""
import os
from pathlib import Path
import subprocess

import pytest
from tests.test_prime_brief_dedup import browser  # noqa: F401
from tests.test_prime_image_upload import scene, ROOT


def setup(page, source=None):
    source = source or (ROOT / "static/command_bridge.js").read_text(encoding="utf-8")
    scene(page, source)
    lifecycle = source[source.index("  function createPrimeTurnUi("):source.index("  function _ensureCtx(")]
    controls = source[source.index("  function setPrimeStreaming("):source.index("  function startPrimeLivePolling(")]
    page.add_script_tag(content=lifecycle + controls)
    page.evaluate("""var _primeStreaming=false,_primeStreamId=null,_primeLiveTimer=null,_primeLiveGeneration=0;
        var mainRequest=null,cancelRequest=null,handlers=null,cancelMode='ok',ticks=[];
        var stop=document.createElement('button');stop.id='cbStop';stop.className='cb-stop';stop.textContent='Stop';document.querySelector('.cb-actions').appendChild(stop);
        window.setInterval=(fn,ms)=>{ticks.push(fn);return ticks.length};window.clearInterval=()=>{};
        function apiPost(url,body){return fetch(url,{method:'POST',body:JSON.stringify(body)}).then(r=>r.json())}
        window.fetch=(url,opts)=>{
            if(url.endsWith('/cancel')){cancelRequest=opts;
                if(cancelMode==='http')return Promise.resolve({ok:false,status:403});
                if(cancelMode==='hang')return new Promise((resolve,reject)=>{opts.signal.addEventListener('abort',()=>reject(Error('timeout')))});
                return Promise.resolve({ok:true,json:()=>Promise.resolve({ok:true,cancelled:true})});}
            mainRequest=opts;return Promise.resolve({ok:true});};
        streamPrimeResponse=(r,h)=>{handlers=h;h.started({stream_id:'owned',created_at:1});return new Promise(()=>{})};
        window.__HERMES_CONFIG__.csrfToken='test-token';
        submit();""")
    page.wait_for_function("handlers!==null")


@pytest.mark.parametrize("width", [1200, 650, 360])
def test_stop_closes_own_stream_and_ignores_late_result(browser, width):
    page = browser.new_page(viewport={"width": width, "height": 600})
    try:
        setup(page)
        page.evaluate("handlers.status({state:'tool',tool:'comando locale'})")
        assert "comando locale" in page.locator('#cbLog').inner_text()
        evidence = os.getenv('HERMES_CAPTURE_STOP_UI')
        if evidence:
            Path(evidence).mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(Path(evidence)/f'after-{width}.png'))
        page.evaluate("cancelPrimeTurn()")
        page.wait_for_function("_cbOwnTurnCount===0")
        assert page.evaluate("mainRequest.signal.aborted")
        assert page.evaluate("cancelRequest.headers['X-CSRF-Token']") == 'test-token'
        assert page.evaluate("JSON.parse(cancelRequest.body).stream_id") == 'owned'
        assert page.evaluate("JSON.parse(cancelRequest.body).request_id===JSON.parse(mainRequest.body).request_id")
        assert page.locator('#cbStop').is_hidden()
        assert 'interrotto' in page.locator('#cbLog').inner_text()
        page.evaluate("handlers.done({reply:'late result'});handlers.token({text:'late token'});handlers.status({state:'reasoning'})")
        assert 'late' not in page.locator('#cbLog').inner_text()
        assert page.locator('.cb-prime-status').count()==0
        assert page.evaluate('_primeBrowserTurns.length')==0
        if evidence: page.screenshot(path=str(Path(evidence)/f'stopped-{width}.png'))
    finally:
        page.close()


@pytest.mark.parametrize('mode', ['http','hang'])
def test_failed_stop_keeps_control_available(browser, mode):
    page=browser.new_page()
    try:
        setup(page)
        page.evaluate("mode=>{cancelMode=mode;cancelPrimeTurn()}",mode)
        if mode=='hang':
            page.evaluate("cancelRequest.signal.dispatchEvent(new Event('abort'))")
        page.wait_for_function("notes.length===1")
        assert page.evaluate('_cbOwnTurnCount')==1
        assert page.locator('#cbStop').is_visible()
        assert page.locator('#cbStop').is_enabled()
        assert not page.evaluate('mainRequest.signal.aborted')
    finally: page.close()


def test_silence_and_connection_loss_are_visible_without_fabricated_failure(browser):
    page=browser.new_page()
    try:
        setup(page)
        page.evaluate("handlers.status({state:'reasoning',idle_seconds:75});ticks[0]()")
        assert 'Nessun progresso' in page.locator('#cbLog').inner_text()
        page.evaluate("var actualNow=Date.now;Date.now=()=>actualNow()+50000;ticks[0]()")
        # The transport watchdog now hands silence to observer recovery.
        assert 'Connessione non disponibile: continuo a recuperare il turno' in page.locator('#cbLog').inner_text()
        assert page.locator('#cbStop').is_visible()
        assert page.evaluate('_cbOwnTurnCount')==1
        page.evaluate("Date.now=actualNow;handlers.heartbeat({progress:{state:'tool',tool:'team_status',idle_seconds:0}})")
        assert 'team_status' in page.locator('#cbLog').inner_text()
    finally: page.close()


@pytest.mark.parametrize('width',[1200,650,360])
def test_capture_baseline_reasoning(browser,width):
    evidence=os.getenv('HERMES_CAPTURE_STOP_UI')
    if not evidence: pytest.skip('evidence only')
    source=subprocess.run(['git','show','64bbc20b:static/command_bridge.js'],capture_output=True,encoding='utf-8',check=True).stdout
    page=browser.new_page(viewport={'width':width,'height':600})
    try:
        setup(page,source)
        page.evaluate("handlers.status({state:'reasoning'})")
        page.screenshot(path=str(Path(evidence)/f'before-{width}.png'))
    finally:page.close()


def test_recovered_turn_shows_tool_and_failed_connection(browser):
    page=browser.new_page()
    try:
        setup(page)
        source=(ROOT/'static/command_bridge.js').read_text(encoding='utf-8')
        code=source[source.index('  function startPrimeLivePolling('):source.index('  function formatCompactTokens(')]
        page.add_script_tag(content=code)
        page.evaluate("""api=()=>Promise.resolve({active:true,pending_turn:{stream_id:'owned',partial_output:''},progress:{state:'tool',tool:'team_status',elapsed_seconds:12}});
            var recovered=document.querySelector('.cb-from-prime .cb-bubble');startPrimeLivePolling(recovered);""")
        page.wait_for_function("document.querySelector('#cbLog').textContent.includes('team_status')")
        page.evaluate("api=()=>Promise.reject(Error('offline'));ticks[ticks.length-1]()")
        page.wait_for_function("document.querySelector('#cbLog').textContent.includes('Connessione di stato non disponibile')")
        assert page.locator('#cbStop').is_visible()
    finally:page.close()
