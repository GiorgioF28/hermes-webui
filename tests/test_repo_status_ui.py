"""Execute the real sidebar rendering/refresh code with an isolated UI harness."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_repo_badges_refresh_and_out_of_order_responses():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable")
    source = (Path(__file__).resolve().parents[1] / "static/command_bridge.js").read_text(encoding="utf-8")
    code = source[source.index("  function renderRepoStatus(data)"):source.index("  // Phase 4")]
    harness = r"""
const assert = require('node:assert/strict');
const list = {innerHTML: ''};
const label = {textContent: 'refresh'};
const button = {textContent: 'Stato repo / Live', querySelector: () => label};
const $ = id => id === 'cbRepoList' ? list : button;
const esc = value => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
let _repoStatusCache = {};
const pending = [];
const api = url => new Promise((resolve, reject) => pending.push({url, resolve, reject}));
const listeners = {};
const window = {addEventListener: (name, cb) => listeners[name] = cb};
const document = {hidden: false, addEventListener: (name, cb) => listeners[name] = cb};
let poll, delay;
const setInterval = (fn, ms) => {poll = fn; delay = ms; return 1;};
const clearInterval = () => {};
const base = {name:'Repo',status:'ok',branch:'main',head:{hash:'abc'},dirty:0,ahead:0,behind:0};
const payload = repo => ({repos:[{...base,...repo}],checked_at:1000});
"""
    checks = r"""
(async () => {
  for (const [repo, text] of [
    [{}, 'in pari'],
    [{dirty:1,ahead:2}, 'da committare'],
    [{ahead:2}, 'da pushare'],
    [{behind:1}, 'da aggiornare'],
    [{ahead:1,behind:1}, 'da sincronizzare'],
    [{stranded:[{branch:'old-idea'}]}, 'in pari'],
  ]) {
    renderRepoStatus(payload(repo));
    assert(list.innerHTML.includes('>' + text + '</span>'), text);
  }
  renderRepoStatus(payload({dirty:1,untracked:1,dirty_files:[{status:'??',path:'<new>.md'}]}));
  assert(list.innerHTML.includes('&lt;new>.md'));
  assert(list.innerHTML.includes('1 nuovi'));
  const old = refreshRepoStatus();
  const fresh = refreshRepoStatus(true);
  assert.equal(pending[1].url, '/api/repo-status?refresh=1');
  pending[1].resolve(payload({})); await fresh;
  pending[0].resolve(payload({dirty:1})); await old;
  assert(list.innerHTML.includes('>in pari</span>'));
  assert.equal(button.textContent, 'Stato repo / Live');
  assert.equal(label.textContent, 'refresh');
  assert(button.title.includes('Verificato'));
  const failed = refreshRepoStatus(true);
  pending[2].reject(new Error('offline')); await failed;
  assert(list.innerHTML.includes('n/d'));
  startRepoStatusRefresh();
  assert.equal(delay, 15000);
  assert.equal(pending.at(-1).url, '/api/repo-status?refresh=1');
  const count = pending.length;
  document.hidden = true; poll(); assert.equal(pending.length,count);
  document.hidden = false; listeners.visibilitychange();
  assert.equal(pending.at(-1).url, '/api/repo-status?refresh=1');
  listeners.focus();
  assert.equal(pending.at(-1).url, '/api/repo-status?refresh=1');
  console.log('UI lifecycle verified');
})().catch(error => {console.error(error);process.exit(1);});
"""
    result = subprocess.run([node, "-"], input=harness + code + checks, text=True, encoding="utf-8", capture_output=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
