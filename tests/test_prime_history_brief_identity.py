"""Offline tests for Prime history/live reconciliation by durable brief identity."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_brief_live_and_history_reconcile_by_id_without_text_dedup():
    if not shutil.which("node"):
        pytest.skip("node is required for the dependency-free JS harness")
    source = Path("static/command_bridge.js").read_text(encoding="utf-8")
    start = source.index("  function renderPrimeHistoryMessage(")
    end = source.index("  function renderPrimeHistoryMessages(", start)
    renderer = source[start:end]
    # This dependency-free identity stub omits browser timestamp/layout APIs;
    # actual metadata and chronology are exercised by the Chromium suite.
    renderer = 'function stampPrimeNode(){} function orderPrimeTimeline(){}\n' + renderer
    harness = r"""
const attrs = n => n.attrs;
const nodes = [];
const log = {
  querySelector(selector) {
    const m = selector.match(/data-cb-msg-index=\"(\d+)\"/);
    return m ? nodes.find(n => attrs(n)['data-cb-msg-index'] === m[1]) || null : null;
  },
  querySelectorAll(selector) {
    const key = selector.includes('data-cb-brief-id') ? 'data-cb-brief-id' : 'data-cb-stream-id';
    return nodes.filter(n => attrs(n)[key] !== undefined);
  },
  appendChild(n) { nodes.push(n); n.parentNode = log; }
};
function $(id) { return id === 'cbLog' ? log : null; }
function primeSay(role, content) {
  const node = {attrs:{}, parentNode:null,
    setAttribute(k,v){this.attrs[k]=String(v)},
    getAttribute(k){return this.attrs[k] ?? null},
    hasAttribute(k){return this.attrs[k] !== undefined},
    querySelector(sel){return sel === '.cb-bubble' ? this.bubble : (sel === '.cb-who' ? this.who : (sel === '.cb-msg-foot' ? this.foot : null));}};
  node.bubble={textContent:content,removeAttribute(){}}; node.who={textContent:''}; node.foot={textContent:'',hidden:true};
  log.appendChild(node); return node;
}
function renderRich(node, text) { node.textContent = text; }
function renderBridgeClarifyCard(){ throw Error('unexpected clarify branch'); }
function _hasBridgeUsage(){ return false; }
function _formatAssistantUsageBadge(){ return ''; }
function _bridgeUsageTitle(){ return ''; }
const window = {_showTokenUsage:false};
RENDERER
const msg = {role:'assistant',content:'brief identico',brief_id:'brief-a',task_id:'a'};
function run(historyFirst){
  nodes.length=0;
  if(historyFirst){renderPrimeHistoryMessage(msg,4);renderPrimeHistoryMessage(msg,null)}
  else {renderPrimeHistoryMessage(msg,null);renderPrimeHistoryMessage(msg,4)}
  const sameId=nodes.length;
  renderPrimeHistoryMessage({role:'assistant',content:'brief identico',brief_id:'brief-b',task_id:'b'},5);
  return {sameId,distinctIds:nodes.length};
}
console.log(JSON.stringify({liveFirst:run(false),historyFirst:run(true)}));
""".replace("RENDERER", renderer)
    result = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["liveFirst"]["sameId"] == 1
    assert payload["historyFirst"]["sameId"] == 1
    assert payload["liveFirst"]["distinctIds"] == 2
    assert payload["historyFirst"]["distinctIds"] == 2


def test_late_brief_waits_for_the_active_local_prime_turn():
    if not shutil.which("node"):
        pytest.skip("node is required for the dependency-free JS harness")
    source = Path("static/command_bridge.js").read_text(encoding="utf-8")
    start = source.index("  function requestBrief(t)")
    end = source.index("  var _cbPollTimer", start)
    request = source[start:end]
    harness = r"""
const window = {__HERMES_CONFIG__:{}, _showTokenUsage:false};
const document = {baseURI:'http://prime.test/',querySelectorAll:()=>[]};
let _cbRenderedCount=0;
const location = {href:'http://prime.test/'};
let _cbOwnTurnCount=1, userEngaged=false, timers=[], orbWrites=[], deliveredAt=[];
function setOrb(...args){orbWrites.push(args)}
function createPrimeTurnUi(){let closed=false;return {isClosed:()=>closed,close:()=>{closed=true}}}
function pendingBubble(){return null}
function renderPrimeHistoryMessage(m){deliveredAt.push(_cbOwnTurnCount)}
function _hasBridgeUsage(){return false}
function fetch(){return Promise.resolve({json:()=>Promise.resolve({reply:'brief'})})}
function setTimeout(cb){timers.push(cb)}
REQUEST
async function main(){
  requestBrief({id:'task-1'});
  await new Promise(setImmediate);
  const early=deliveredAt.slice(); const orbDuring=orbWrites.slice();
  _cbOwnTurnCount=0; if(timers.length) timers.shift()();
  await Promise.resolve();
  console.log(JSON.stringify({early,orbDuring,deliveredAt}));
}
main().catch(e=>{console.error(e);process.exitCode=1});
""".replace("REQUEST", request)
    result = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["early"] == []
    assert payload["orbDuring"] == []
    assert payload["deliveredAt"] == [0]
