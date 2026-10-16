"""Prime history polling must preserve transcript identity, order and reading state."""
import os
from pathlib import Path

import pytest
from tests.test_prime_brief_dedup import browser  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def _renderer():
    source = (ROOT / "static/command_bridge.js").read_text(encoding="utf-8")
    start = source.index("  function primeTimestamp(")
    end = source.index("  // Record durevole della delega", start)
    return source[start:end]


HARNESS = r"""
function $(id){return document.getElementById(id)}
function el(tag, cls){var n=document.createElement(tag);n.className=cls||'';return n}
var _cbHistoryBatch=0,_cbRenderedCount=0,_cbTaskWindow=null,_cbTasks={};
function renderRich(node,text){node.textContent=text}
function primeSay(role,text){var n=el('article','cb-msg cb-from-'+role);var b=el('div','cb-bubble');b.textContent=text;n.appendChild(b);$('cbLog').appendChild(n);return n}
function _hasBridgeUsage(){return false} function _formatAssistantUsageBadge(){return ''} function _bridgeUsageTitle(){return ''}
function clearBriefNotice(){} function taskCardInWindow(){return true}
function nearBottom(log){return log.scrollHeight-log.scrollTop-log.clientHeight<64}
"""


@pytest.mark.parametrize("width", [1200, 650, 360])
def test_unchanged_history_polls_keep_nodes_scroll_selection_and_copy(browser, width, tmp_path):
    page = browser.new_page(viewport={"width": width, "height": 620})
    try:
        page.set_content('<style>body{margin:0}#cbLog{height:500px;overflow:auto;width:100%;font:16px/1.5 sans-serif}.cb-msg{min-height:90px;padding:8px}.cb-bubble{user-select:text}</style><div id="cbLog"></div><textarea id="composer"></textarea>')
        page.add_script_tag(content=HARNESS + _renderer())
        page.evaluate("""()=>{
          var rows=[]; for(var i=0;i<50;i++) rows.push({role:i%2?'assistant':'user',content:'Messaggio lungo '+i+'\\n'+('testo transcript '.repeat(12)),created_at:1700000000+i,message_index:i});
          renderPrimeHistoryMessages(rows,0);
          var log=$('cbLog'), card=document.createElement('article');card.className='cb-deleg';card.textContent='Delegation card';card.setAttribute('data-task-id','d-1');log.appendChild(card);
          window.rows=rows; window.card=card; window.original=Array.from(log.children);
          // Count actual transcript child moves after initial render.
          window.moves=0; ['appendChild','insertBefore','replaceChild'].forEach(function(k){var native=log[k];log[k]=function(){if(arguments[0]&&arguments[0].nodeType===1&&arguments[0].classList.contains('cb-msg'))moves++;return native.apply(this,arguments)}});
          log.scrollTop=420;
          var text=log.querySelector('[data-cb-msg-index="20"] .cb-bubble').firstChild;
          var range=document.createRange();range.setStart(text,0);range.setEnd(text,14);getSelection().removeAllRanges();getSelection().addRange(range);
          window.readTop=log.scrollTop;
        }""")
        # Same durable 3-second history payload, delivered in repeated polls.
        page.evaluate("""()=>{for(var n=0;n<5;n++)renderPrimeHistoryMessages(rows,0)}""")
        assert page.evaluate("moves") == 0
        assert page.evaluate("Array.from(cbLog.children).length") == 51
        assert page.evaluate("original.every((n,i)=>cbLog.children[i]===n)")
        assert page.evaluate("Math.abs(cbLog.scrollTop-readTop)<2")
        assert page.evaluate("getSelection().toString().length") == 14
        page.evaluate("navigator.clipboard && navigator.clipboard.writeText(getSelection().toString()).catch(()=>{})")
        if os.getenv("HERMES_CAPTURE_PRIME_SCROLL_UI"):
            out = Path(os.environ["HERMES_CAPTURE_PRIME_SCROLL_UI"]); out.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out / f"poll-{width}.png"))
    finally:
        page.close()


def test_changed_and_out_of_order_rows_keep_chronology_and_selection(browser):
    page=browser.new_page(viewport={"width":900,"height":650})
    try:
        page.set_content('<style>#cbLog{height:400px;overflow:auto}.cb-msg{height:100px}</style><div id="cbLog"></div>')
        page.add_script_tag(content=HARNESS+_renderer())
        page.evaluate("""()=>{renderPrimeHistoryMessages([
          {role:'user',content:'uno',created_at:30,message_index:0},
          {role:'assistant',content:'due',created_at:40,message_index:1},
          {role:'user',content:'tre',created_at:50,message_index:2}],0);
          cbLog.scrollTop=80; var t=cbLog.querySelector('[data-cb-msg-index="1"] .cb-bubble').firstChild;
          var r=document.createRange();r.selectNodeContents(t);getSelection().removeAllRanges();getSelection().addRange(r);window.keep=cbLog.scrollTop;
          renderPrimeHistoryMessage({role:'assistant',content:'due',created_at:45,message_index:1},1);
          renderPrimeHistoryMessage({role:'assistant',content:'inserito',created_at:35,message_index:8},8);
          renderPrimeHistoryMessages([{role:'user',content:'tre',created_at:50,message_index:2}],0);
        }""")
        assert page.locator("[data-cb-msg-index='1'] .cb-bubble").inner_text() == "due"
        assert page.evaluate("Array.from(cbLog.querySelectorAll('[data-cb-created-at]')).map(n=>+n.dataset.cbCreatedAt).join(',')") == "30,35,45,50"
        assert page.evaluate("Math.abs(cbLog.scrollTop-keep)<3")
        assert page.evaluate("getSelection().toString()==='due'")
    finally: page.close()
