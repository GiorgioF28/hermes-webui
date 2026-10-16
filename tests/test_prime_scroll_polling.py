"""Actual Bridge layout and poll renderer retain reading/copy state offline."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from tests.test_command_bridge_mobile_layout import open_bridge, browser  # noqa: F401
from tests.test_prime_delegation_timeline import renderer_source

ROOT = Path(__file__).resolve().parents[1]


def scene(page, source):
    helpers = source[source.index('  function esc('):source.index('  function _fmtCompactTokens(')]
    say = source[source.index('  function primeSay('):source.index('  var _cbHistoryLoaded')]
    page.add_script_tag(content=renderer_source(source) + helpers + say + "\nfunction speak(){} function _hasBridgeUsage(){return false} var _cbTaskWindow=null;")
    page.evaluate("""() => {
      cbLog.innerHTML=''; window._cbTasks={}; window.userEngaged=false;
      window.messages=Array.from({length:50},(_,i)=>({role:i%2?'assistant':'user',
        content:'Messaggio '+i+' — '+('ricette e progetti da leggere con calma. '.repeat(7)),
        created_at:1700000000+i,message_index:i,reply_to_index:i%2?i-1:undefined,
        stream_id:'turn-'+Math.floor(i/2)}));
      messages.forEach(m=>renderPrimeHistoryMessage(m,m.message_index));
      window.tasks=[2,8,16].map((i,n)=>({id:'test-'+n,agent:'programmatore',status:'in_corso',
        started:1700000000+i+0.5,anchor_message_index:i,anchor_reply_prefix:n?'':'Annuncio delega.',
        summary:'Verifica delega '+n}));
      tasks.forEach(t=>renderTask(t,{replay:true}));repositionPrimeTaskCards();
      cbLog.querySelector('[data-cb-msg-index="28"]').scrollIntoView({block:'center'});
      cbLog.focus({preventScroll:true});
      var text=cbLog.querySelector('[data-cb-msg-index="28"] .cb-bubble').firstChild;
      getSelection().setBaseAndExtent(text,0,text,12);
      window.initialTop=cbLog.scrollTop;
      window.initialNodes=Array.from(cbLog.children);
      window.moves=0;
      var insert=cbLog.insertBefore,replace=cbLog.replaceChild,append=cbLog.appendChild;
      cbLog.insertBefore=function(n,b){if(n.parentNode===this)moves++;return insert.call(this,n,b)};
      cbLog.replaceChild=function(n,b){moves++;return replace.call(this,n,b)};
      cbLog.appendChild=function(n){if(n.parentNode===this)moves++;return append.call(this,n)};
    }""")


def poll(page):
    return page.evaluate("""() => {
      for(var round=0;round<20;round++){
        tasks.forEach(t=>renderTask(t,{replay:true}));
        repositionPrimeTaskCards();
        messages.forEach(m=>renderPrimeHistoryMessage(m,m.message_index));
      }
      return {moves,top:cbLog.scrollTop,initialTop,selected:getSelection().toString(),
        sameNodes:initialNodes.every((n,i)=>cbLog.children[i]===n)};
    }""")


@pytest.mark.parametrize('width', [1200, 650, 360])
def test_actual_task_polls_do_not_move_messages_or_break_copy(browser, width):
    page=open_bridge(browser,width=width,height=800)
    try:
        scene(page,(ROOT/'static/command_bridge.js').read_text(encoding='utf-8'))
        result=poll(page)
        assert result['moves']==0
        assert result['sameNodes']
        assert abs(result['top']-result['initialTop'])<2
        assert result['selected']=='Messaggio 28'
        page.context.grant_permissions(['clipboard-read','clipboard-write'])
        page.keyboard.press('Control+c')
        assert page.evaluate('navigator.clipboard.readText()')=='Messaggio 28'
        # A click into the composer must not change the conversation viewport.
        before=page.evaluate('cbLog.scrollTop')
        page.locator('#cbInput').click()
        assert abs(page.evaluate('cbLog.scrollTop')-before)<2
        page.locator('[data-cb-msg-index="28"] .cb-bubble').click()
        assert abs(page.evaluate('cbLog.scrollTop')-before)<2
        page.locator('.cb-chat-name').click()
        assert abs(page.evaluate('cbLog.scrollTop')-before)<2
    finally:
        page.close()


@pytest.mark.parametrize('width', [1200, 650, 360])
def test_window_pruning_and_task_completion_keep_visible_row(browser,width):
    page=open_bridge(browser,width=width,height=800)
    try:
        source=(ROOT/'static/command_bridge.js').read_text(encoding='utf-8')
        scene(page,source)
        window_code=source[source.index('  function applyPrimeHistoryWindow('):source.index('  function renderPrimeHistoryPayload(')]
        page.add_script_tag(content=window_code)
        page.evaluate("""() => {
          window.selectedRow=cbLog.querySelector('[data-cb-msg-index="28"]');
          window.offset=selectedRow.getBoundingClientRect().top;
          renderPrimeHistoryMessage({role:'user',content:'Nuovo da altro dispositivo',created_at:1700000050},50);
          renderPrimeHistoryMessage({role:'assistant',content:'Nuova risposta',created_at:1700000051},51);
          applyPrimeHistoryWindow({history_window:{start_index:2,visible_indexes:Array.from({length:50},(_,i)=>i+2),archived_count:2}});
          renderTask({...tasks[1],status:'ok',finished:1700000080},{replay:true,briefed:true});
        }""")
        assert abs(page.evaluate('selectedRow.getBoundingClientRect().top-offset'))<2
        assert page.evaluate("getSelection().toString()==='Messaggio 28'")
        assert page.locator('[data-cb-msg-index]').count()==50
        assert 'completato' in page.locator('[data-task-id="test-1"]').inner_text()
    finally:
        page.close()


@pytest.mark.parametrize('width', [1200, 650, 360])
def test_capture_original_poll_jump_and_fixed_reading_position(browser,width):
    destination=os.getenv('HERMES_CAPTURE_PRIME_SCROLL_UI')
    if not destination:
        pytest.skip('capture baseline on demand')
    output=Path(destination);output.mkdir(parents=True,exist_ok=True)
    metrics={}
    for label in ('before','after'):
        source=(subprocess.check_output(['git','show','33df9187f:static/command_bridge.js'],cwd=ROOT).decode('utf-8')
                if label=='before' else (ROOT/'static/command_bridge.js').read_text(encoding='utf-8'))
        page=open_bridge(browser,width=width,height=800)
        try:
            scene(page,source)
            metrics[label]=poll(page)
            page.screenshot(path=str(output/f'{label}-{width}.png'))
        finally:
            page.close()
    (output/f'metrics-{width}.json').write_text(json.dumps(metrics,indent=2),encoding='utf-8')
    assert metrics['before']['moves']>0
    assert metrics['before']['selected']!='Messaggio 28' or abs(metrics['before']['top']-metrics['before']['initialTop'])>2
    assert metrics['after']['moves']==0
    assert metrics['after']['selected']=='Messaggio 28'
    assert abs(metrics['after']['top']-metrics['after']['initialTop'])<2


@pytest.mark.parametrize('width', [1200, 650, 360])
def test_submit_reveals_own_message_and_stream_respects_reading_and_selection(browser,width):
    from tests.test_prime_image_upload import scene as upload_scene

    source=(ROOT/'static/command_bridge.js').read_text(encoding='utf-8')
    page=browser.new_page(viewport={'width':width,'height':800})
    try:
        upload_scene(page,source)
        helpers=source[source.index('  function esc('):source.index('  function _fmtCompactTokens(')]
        say=source[source.index('  function primeSay('):source.index('  var _cbHistoryLoaded')]
        pending=source[source.index('  function pendingBubble('):source.index('  function streamPrimeResponse(')]
        page.add_script_tag(content=renderer_source(source)+helpers+say+pending)
        page.evaluate("""() => {
          cbLog.innerHTML='';cbLog.style.height='360px';cbLog.style.flex='0 0 360px';
          window._cbTasks={};_cbHistoryBatch=1;
          for(var i=0;i<50;i++)primeSay('prime','Storia '+i+' '+('contenuto '.repeat(30)),null,true);
          _cbHistoryBatch=0;cbLog.scrollTop=200;
          cbInput.value='Il mio nuovo messaggio deve essere visibile';
          window.handlers=null;
          streamPrimeResponse=(r,h)=>{handlers=h;h.started({stream_id:'test-stream',created_at:Date.now()/1000});return new Promise(()=>{})};
          submit();
        }""")
        page.wait_for_function('handlers!==null')
        assert page.locator('.cb-from-user .cb-bubble').last.inner_text()=='Il mio nuovo messaggio deve essere visibile'
        assert page.evaluate("cbLog.lastElementChild.previousElementSibling.getBoundingClientRect().bottom<=cbLog.getBoundingClientRect().bottom+1")
        page.evaluate("handlers.token({text:'Una risposta che continua. '});handlers.token({text:'Altra frase. '})")
        assert page.evaluate('cbLog.scrollHeight-cbLog.scrollTop-cbLog.clientHeight<3')
        # Select the actual live text, then stream again: keep its Text node.
        page.evaluate("""() => {
          var text=cbLog.lastElementChild.querySelector('.cb-bubble').firstChild;
          getSelection().setBaseAndExtent(text,0,text,12);window.liveText=text;window.readTop=cbLog.scrollTop;
          handlers.token({text:'Ancora testo in arrivo. '.repeat(12)});
        }""")
        assert page.evaluate("getSelection().toString()==='Una risposta'")
        assert page.evaluate('liveText.isConnected')
        assert abs(page.evaluate('cbLog.scrollTop-readTop'))<2
        # Scrolling into old history also suspends follow for incoming tokens.
        page.evaluate("getSelection().removeAllRanges();cbLog.scrollTop=500;window.readTop=cbLog.scrollTop;handlers.token({text:'Continua. '.repeat(30)})")
        assert abs(page.evaluate('cbLog.scrollTop-readTop'))<2
    finally:
        page.close()
