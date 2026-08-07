/* ──────────────────────────────────────────────────────────────────────────
 * Hermes Command Bridge — opening/home view.
 * Phase 3: 3-zone shell (Hermes Prime chat · center stage · projects strip).
 * The center stage is the future Vault Planet + Voice Orb (Phases 4-5); for now
 * it shows the real vault at a glance. All data is real (/api/vault/graph,
 * /api/projects/overview). Pure vanilla JS, integrated as the `bridge` panel.
 *
 * Signature palette: near-black space, an incandescent WHITE core (Hermes) that
 * bleeds outward into FLUO ORANGE at the leaves.
 * ────────────────────────────────────────────────────────────────────────── */
(function () {
  'use strict';

  var BUILT = false;
  var reduceMotion = false;
  try { reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) {}

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }
  function api(path) {
    return fetch(new URL(path, document.baseURI || location.href).href, { credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error(path + ' -> ' + r.status); return r.json(); });
  }
  function apiPost(path, payload) {
    return fetch(new URL(path, document.baseURI || location.href).href, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {})
    }).then(function (r) { if (!r.ok) throw new Error(path + ' -> ' + r.status); return r.json(); });
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[c];
    });
  }
  // Scroll to bottom only if the user is already near it, so reading older
  // messages isn't interrupted while Prime keeps writing.
  function nearBottom(log) { return !log || (log.scrollHeight - log.scrollTop - log.clientHeight) < 64; }
  // Render Prime text as markdown (reuses the main chat renderer); plain fallback.
  function renderRich(node, text) {
    if (!node) return;
    var raw = String(text || '');
    if (typeof window.renderMd === 'function') {
      try {
        node.innerHTML = window.renderMd(raw);
        return;
      } catch (e) {
        console.error('[Hermes Prime] Rendering markdown finale fallito; mostro il testo grezzo.', e);
      }
    }
    node.textContent = raw;
  }
  function _fmtCompactTokens(value) {
    var n = Number(value) || 0;
    if (n >= 1000000) return (n / 1000000).toFixed(n >= 10000000 ? 0 : 1).replace(/\.0$/, '') + 'M';
    if (n >= 1000) return (n / 1000).toFixed(n >= 10000 ? 0 : 1).replace(/\.0$/, '') + 'k';
    return String(Math.max(0, Math.round(n)));
  }
  function _usageInputTokens(usage) { usage = usage || {}; return Number(usage.input_tokens || 0); }
  function _usageOutputTokens(usage) { usage = usage || {}; return Number(usage.output_tokens || 0); }
  function _usageCacheReadTokens(usage) { usage = usage || {}; return Number(usage.cache_read_tokens || usage.cache_read_input_tokens || 0); }
  function _usageCacheWriteTokens(usage) { usage = usage || {}; return Number(usage.cache_write_tokens || usage.cache_creation_input_tokens || 0); }
  function _usageCost(usage) {
    usage = usage || {};
    var n = Number(usage.estimated_cost_usd != null ? usage.estimated_cost_usd : usage.estimated_cost);
    return Number.isFinite(n) && n > 0 ? n : 0;
  }
  function _hasBridgeUsage(usage) {
    return !!(usage && (_usageInputTokens(usage) || _usageOutputTokens(usage) || _usageCacheReadTokens(usage) || _usageCacheWriteTokens(usage) || _usageCost(usage)));
  }
  function _formatAssistantUsageBadge(usage) {
    if (!_hasBridgeUsage(usage)) return '';
    var parts = ['in ' + _fmtCompactTokens(_usageInputTokens(usage)), 'out ' + _fmtCompactTokens(_usageOutputTokens(usage))];
    var cacheRead = _usageCacheReadTokens(usage), cacheWrite = _usageCacheWriteTokens(usage);
    if (cacheRead || cacheWrite) parts.push('cache ' + _fmtCompactTokens(cacheRead) + '/' + _fmtCompactTokens(cacheWrite));
    var cost = _usageCost(usage);
    if (cost) parts.push('~$' + (cost < 0.01 ? cost.toFixed(4) : cost.toFixed(2)));
    return parts.join(' · ');
  }
  function _formatLiveUsage(usage) {
    if (!_hasBridgeUsage(usage)) return '';
    return 'in ' + _fmtCompactTokens(_usageInputTokens(usage)) + ' · out ' + _fmtCompactTokens(_usageOutputTokens(usage));
  }
  function _formatQuotaMoneyShort(value) {
    var n = Number(value);
    if (!Number.isFinite(n)) return '';
    if (Math.abs(n) >= 100) return '$' + n.toFixed(0);
    if (Math.abs(n) >= 10) return '$' + n.toFixed(1);
    return '$' + n.toFixed(2);
  }
  function _formatQuotaPercentShort(value) {
    var n = Number(value);
    if (!Number.isFinite(n)) return '';
    return Math.max(0, Math.min(100, n)).toFixed(0) + '%';
  }
  function _renderTokenQuotaPill(status) {
    var pill = $('cbQuotaPill');
    if (!pill) return;
    if (window._showQuotaChip !== true || !status) {
      pill.hidden = true; pill.textContent = ''; pill.removeAttribute('title'); return;
    }
    var label = '';
    var title = status.message || 'Provider usage loaded';
    if (Number.isFinite(Number(status.primary_used_percent))) {
      var usedPct = Math.max(0, Math.min(100, Number(status.primary_used_percent)));
      label = 'Codex ' + Math.max(0, 100 - usedPct).toFixed(0) + '%';
      title = 'Codex quota';
      if (status.plan) title += ' - ' + status.plan;
      if (status.primary_resets_at) title += ' - resets ' + status.primary_resets_at;
    }
    var accountLimits = status.account_limits || null;
    if (!label && accountLimits && Array.isArray(accountLimits.windows) && accountLimits.windows.length) {
      var w = accountLimits.windows.find(function (x) { return x && Number.isFinite(Number(x.remaining_percent)); }) || accountLimits.windows[0];
      var remainingPct = _formatQuotaPercentShort(w && w.remaining_percent);
      if (remainingPct) label = (status.display_name || status.provider || 'Codex') + ' ' + remainingPct;
    }
    if (!label && status.quota) {
      var remainingMoney = _formatQuotaMoneyShort(status.quota.limit_remaining);
      if (remainingMoney) {
        label = (status.display_name || status.provider || 'Codex') + ' ' + remainingMoney;
        var used = _formatQuotaMoneyShort(status.quota.usage), limit = _formatQuotaMoneyShort(status.quota.limit);
        var bits = [];
        if (used) bits.push('used ' + used);
        if (limit) bits.push('limit ' + limit);
        if (bits.length) title += ' - ' + bits.join(' · ');
      }
    }
    if (!label) { pill.hidden = true; pill.textContent = ''; pill.removeAttribute('title'); return; }
    pill.textContent = label; pill.title = title; pill.hidden = false;
  }
  var _cbQuotaRefreshInFlight = false;
  function pollTokenQuota() {
    if (window._showQuotaChip !== true) { _renderTokenQuotaPill(null); return Promise.resolve(); }
    if (_cbQuotaRefreshInFlight) return Promise.resolve();
    _cbQuotaRefreshInFlight = true;
    return api('/api/usage/limits')
      .then(function (d) { _renderTokenQuotaPill(d && (d.codex || d.provider || d)); })
      .catch(function () { _renderTokenQuotaPill(null); })
      .then(function () { _cbQuotaRefreshInFlight = false; });
  }

  /* ── fonts + scoped styles ─────────────────────────────────────────────── */
  function injectFonts() {
    if ($('cb-fonts')) return;
    var l = document.createElement('link');
    l.id = 'cb-fonts';
    l.rel = 'stylesheet';
    l.href = 'https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500&display=swap';
    document.head.appendChild(l);
  }

  function injectStyles() {
    if ($('cb-styles')) return;
    var css = [
'#mainBridge{padding:0!important;min-width:0;max-width:100%;overflow-x:hidden;}',
/* the bridge is a full-bleed stage: hide the (empty) 300px sidebar panel */
'.layout:has(main.main.showing-bridge) .sidebar{width:0!important;min-width:0!important;opacity:0!important;pointer-events:none;border:none!important;overflow:hidden;}',
'.cb-root{',
'  --cb-bg:#06070b; --cb-bg2:#0a0c12; --cb-core:#ffffff;',
'  --cb-accent:#ff6a00; --cb-accent-2:#ff9248; --cb-accent-dim:rgba(255,106,0,.22);',
'  --cb-text:#e9e9ee; --cb-muted:#7d808c; --cb-faint:#494c58;',
'  --cb-success:#86c08b; --cb-warning:#e0b15d; --cb-error:#f87171;',
'  --cb-line:rgba(255,255,255,.07); --cb-line2:rgba(255,255,255,.04);',
'  --cb-disp:"Chakra Petch",system-ui,sans-serif;',
'  --cb-mono:"IBM Plex Mono",ui-monospace,monospace;',
'  --cb-sans:"IBM Plex Sans",system-ui,sans-serif;',
'  position:relative;height:100%;width:100%;min-width:0;max-width:100%;overflow-y:auto;overflow-x:hidden;',
'  background:radial-gradient(120% 80% at 70% -10%,#0e1019 0%,var(--cb-bg) 55%,#040509 100%);',
'  color:var(--cb-text);font-family:var(--cb-sans);scrollbar-width:thin;',
'  scrollbar-color:rgba(255,255,255,.12) transparent;}',
'.cb-root::-webkit-scrollbar{width:9px;}',
'.cb-root::-webkit-scrollbar-thumb{background:rgba(255,255,255,.10);border-radius:6px;}',
/* starfield */
'.cb-stars{position:absolute;inset:0;pointer-events:none;opacity:.6;',
'  background-image:radial-gradient(1px 1px at 20% 30%,rgba(255,255,255,.7),transparent),',
'  radial-gradient(1px 1px at 75% 18%,rgba(255,255,255,.5),transparent),',
'  radial-gradient(1px 1px at 48% 62%,rgba(255,255,255,.45),transparent),',
'  radial-gradient(1px 1px at 88% 70%,rgba(255,255,255,.55),transparent),',
'  radial-gradient(1px 1px at 12% 80%,rgba(255,255,255,.4),transparent),',
'  radial-gradient(1px 1px at 62% 88%,rgba(255,255,255,.35),transparent),',
'  radial-gradient(1.5px 1.5px at 33% 12%,rgba(255,170,110,.5),transparent);',
'  background-repeat:no-repeat;}',
/* hero */
'.cb-hero{position:relative;min-height:100%;min-width:0;max-width:100%;overflow-x:hidden;}',
/* Hermes Prime = glass console on the LEFT; the planet stays centered behind/right of it */
'.cb-chat{position:absolute;left:22px;top:22px;bottom:22px;z-index:3;display:flex;flex-direction:column;width:min(26.875rem,40vw);max-width:100%;',
'  min-width:0;border:1px solid var(--cb-line);border-radius:18px;overflow:hidden;',
'  background:linear-gradient(180deg,rgba(8,9,14,.66),rgba(6,7,11,.86));backdrop-filter:blur(11px);',
'  box-shadow:0 30px 90px -30px rgba(0,0,0,.9),inset 0 0 0 1px rgba(255,255,255,.02);}',
'.cb-hero.cb-collapsed .cb-chat{opacity:0;pointer-events:none;transform:translateX(-10px);}',
'.cb-chat-head{display:flex;align-items:center;gap:10px;min-width:0;max-width:100%;padding:18px 20px 14px;border-bottom:1px solid var(--cb-line2);}',
'.cb-dot{width:9px;height:9px;border-radius:50%;background:var(--cb-accent);box-shadow:0 0 10px var(--cb-accent);flex:0 0 auto;}',
'.cb-chat-name{font-family:var(--cb-disp);font-weight:700;letter-spacing:.14em;font-size:13px;text-transform:uppercase;}',
'.cb-chat-role{font-family:var(--cb-mono);font-size:10px;color:var(--cb-muted);letter-spacing:.08em;margin-top:1px;}',
'.cb-quota{font-family:var(--cb-mono);font-size:9px;color:var(--cb-accent-2);border:1px solid var(--cb-accent-dim);border-radius:999px;padding:4px 7px;background:rgba(255,106,0,.07);white-space:nowrap;}',
'.cb-quota[hidden]{display:none;}',
'.cb-voicetoggle{background:transparent;border:1px solid var(--cb-line);color:var(--cb-faint);border-radius:8px;width:32px;height:32px;cursor:pointer;display:flex;align-items:center;justify-content:center;flex:0 0 auto;transition:.15s;}',
'.cb-voicetoggle:hover{color:var(--cb-text);}',
'.cb-voicetoggle.cb-on{color:var(--cb-accent);border-color:var(--cb-accent-dim);}',
'.cb-brainctl{display:flex;align-items:center;min-width:0;max-width:100%;padding:2px;border:1px solid var(--cb-line);border-radius:9px;background:rgba(255,255,255,.025);}',
'.cb-brainsel{appearance:none;-webkit-appearance:none;min-width:0;border:0;border-radius:7px;background:transparent;color:var(--cb-accent);font:600 9px var(--cb-mono);letter-spacing:.08em;padding:5px 20px 5px 8px;cursor:pointer;transition:.15s;max-width:150px;'
  + 'background-image:url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 12 12\'%3E%3Cpath d=\'M2.5 4.5L6 8l3.5-3.5\' fill=\'none\' stroke=\'%23888\' stroke-width=\'1.4\' stroke-linecap=\'round\'/%3E%3C/svg%3E");'
  + 'background-repeat:no-repeat;background-position:right 5px center;background-size:10px;}',
'.cb-brainsel:hover{background-color:rgba(255,255,255,.05);color:var(--cb-text);}',
'.cb-brainsel:focus-visible{outline:2px solid var(--cb-accent-dim);outline-offset:1px;}',
// La tendina e\' scura come il resto: senza questo il menu nativo su Windows
// esce bianco e le voci disabilitate diventano illeggibili.
'.cb-brainsel option{background:#12151c;color:var(--cb-text);font:600 11px var(--cb-mono);}',
'.cb-brainsel option:disabled{color:#5a6070;font-style:italic;}',
'.cb-brainsel.cb-codex{color:var(--cb-accent-2);}',
'.cb-brainctl.cb-busy{opacity:.55;pointer-events:none;}',
'.cb-chat-log{flex:1;min-width:0;min-height:0;max-width:100%;overflow-x:hidden;overflow-y:auto;padding:18px 20px;display:flex;flex-direction:column;gap:14px;}',
'.cb-chat-log>*,.cb-msg,.cb-bubble{min-width:0;max-width:100%;box-sizing:border-box;}',
'.cb-msg{max-width:92%;font-size:13.5px;line-height:1.5;}',
'.cb-bubble,.cb-bubble *,.cb-attention-card,.cb-deleg,.cb-tool-card{overflow-wrap:anywhere;word-break:break-word;}',
'.cb-bubble pre{max-width:100%;box-sizing:border-box;white-space:pre-wrap;overflow-x:auto;overflow-wrap:anywhere;word-break:break-word;}',
'.cb-bubble pre code{white-space:inherit;overflow-wrap:inherit;word-break:inherit;}',
'.cb-bubble img,.cb-bubble table,.cb-bubble iframe,.cb-bubble svg,.cb-bubble video{max-width:100%;}',
'.cb-bubble img,.cb-bubble svg,.cb-bubble video{height:auto;}',
'.cb-bubble table{width:100%;table-layout:fixed;}',
'.cb-bubble th,.cb-bubble td{min-width:0;overflow-wrap:anywhere;word-break:break-word;}',
'.cb-bubble .katex-display,.cb-bubble .katex-block,.cb-bubble .mermaid-block{max-width:100%;overflow-x:auto;}',
'.cb-msg .cb-who{font-family:var(--cb-mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--cb-faint);margin-bottom:4px;}',
'.cb-msg.cb-from-prime{align-self:flex-start;}',
'.cb-msg.cb-from-user{align-self:flex-end;text-align:right;color:#fff;}',
'.cb-msg.cb-from-prime .cb-bubble{color:var(--cb-text);}',
'.cb-prime-status{margin-top:5px;font-family:var(--cb-mono);font-size:9.5px;letter-spacing:.06em;color:var(--cb-faint);}',
'.cb-msg-foot{margin-top:7px;font-family:var(--cb-mono);font-size:9.5px;letter-spacing:.04em;color:var(--cb-faint);}',
'.cb-msg-foot[hidden]{display:none;}',
'.cb-attention-card{align-self:stretch;max-width:100%;border:1px solid rgba(255,160,92,.34);background:rgba(36,24,14,.86);border-radius:8px;padding:12px;color:var(--cb-text);box-shadow:0 16px 34px rgba(0,0,0,.26);}',
'.cb-attention-k{font-family:var(--cb-mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--cb-accent);margin-bottom:6px;}',
'.cb-attention-title{font-size:13px;font-weight:700;color:#fff;margin-bottom:7px;}',
'.cb-attention-body{font-size:12px;line-height:1.45;color:var(--cb-muted);white-space:pre-wrap;word-break:break-word;}',
'.cb-attention-actions{display:flex;flex-wrap:wrap;gap:7px;margin-top:10px;}',
'.cb-attention-actions button{border:1px solid var(--cb-line);background:rgba(255,255,255,.06);color:#fff;border-radius:7px;padding:7px 9px;font-family:var(--cb-mono);font-size:10px;letter-spacing:.06em;cursor:pointer;}',
'.cb-attention-actions button:hover{border-color:rgba(255,255,255,.28);background:rgba(255,255,255,.1);}',
'.cb-attention-actions button.deny{color:#ffb4a8;border-color:rgba(255,107,92,.34);}',
'.cb-attention-input{width:100%;box-sizing:border-box;margin-top:9px;border:1px solid var(--cb-line);border-radius:8px;background:rgba(0,0,0,.2);color:#fff;padding:9px;font-size:12px;}',
'.cb-question-block{display:flex;flex-direction:column;gap:8px;margin-top:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,.07);}',
'.cb-question-block:first-of-type{border-top:0;padding-top:0;}',
'.cb-question-title{display:flex;align-items:center;gap:7px;font-size:12px;font-weight:700;color:#fff;line-height:1.4;}',
'.cb-question-chip{max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;border:1px solid var(--cb-accent-dim);border-radius:999px;padding:2px 6px;font-family:var(--cb-mono);font-size:9px;letter-spacing:.08em;text-transform:uppercase;color:var(--cb-accent-2);background:rgba(255,106,0,.08);}',
'.cb-option-label{display:block;}',
'.cb-option-description{display:block;margin-top:3px;color:var(--cb-muted);font-family:var(--cb-sans);font-size:11px;font-weight:400;letter-spacing:0;line-height:1.35;}',
'.cb-question-hint{font-size:10px;color:var(--cb-muted);font-family:var(--cb-mono);}',
'.cb-other-row{display:flex;align-items:center;gap:7px;width:100%;font-family:var(--cb-mono);font-size:10px;color:var(--cb-muted);}',
'.cb-other-row .cb-attention-input{margin-top:0;min-width:0;}',
'.cb-attention-card.cb-readonly .cb-attention-actions button,.cb-attention-card.cb-readonly input{pointer-events:none;opacity:.68;}',
'.cb-choice.selected{border-color:var(--cb-accent);box-shadow:0 0 0 2px rgba(255,106,0,.16) inset;}',
'.cb-recovered .cb-who:after{content:" · recuperato";color:var(--cb-accent);}',
'.cb-deleg{align-self:flex-start;max-width:96%;border:1px solid var(--cb-accent-dim);border-left:3px solid var(--cb-accent);',
'  border-radius:8px;padding:9px 12px;background:rgba(255,106,0,.07);font-family:var(--cb-mono);font-size:11px;box-shadow:0 12px 30px -24px rgba(255,106,0,.75);}',
'.cb-deleg b{color:var(--cb-accent-2);font-weight:600;}',
'.cb-deleg.cb-deleg-done{border-color:rgba(72,199,116,.38);border-left-color:#48c774;background:rgba(72,199,116,.08);box-shadow:0 12px 30px -24px rgba(72,199,116,.75);}',
'.cb-deleg.cb-deleg-error{border-color:rgba(255,92,92,.42);border-left-color:#ff6b5c;background:rgba(255,92,92,.08);box-shadow:0 12px 30px -24px rgba(255,92,92,.75);}',
'.cb-deleg-summary{margin-top:6px;color:var(--cb-text);font-family:var(--cb-sans);font-size:12.5px;line-height:1.35;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}',
'.cb-deleg-details{margin-top:6px;color:var(--cb-muted);font-family:var(--cb-sans);font-size:11.5px;}',
'.cb-deleg-details summary{cursor:pointer;color:var(--cb-faint);font-family:var(--cb-mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;}',
'.cb-deleg-details[open] .cb-deleg-full{margin-top:6px;color:var(--cb-text);font-size:12px;line-height:1.42;white-space:pre-wrap;word-break:break-word;}',
/* input: a single compact row, fixed at the bottom of the chat console */
'.cb-chat-input{display:flex;padding:12px 14px 14px;border-top:1px solid var(--cb-line2);}',
'.cb-chat-input textarea{flex:1;min-width:0;box-sizing:border-box;background:rgba(255,255,255,.04);border:1px solid var(--cb-line);border-radius:12px;',
'  color:var(--cb-text);font-family:var(--cb-sans);font-size:13.5px;line-height:1.45;padding:11px 14px;outline:none;',
'  resize:none;display:block;min-height:44px;max-height:46vh;overflow-y:auto;}',
'.cb-chat-input textarea:focus{border-color:var(--cb-accent);box-shadow:0 0 0 3px rgba(255,106,0,.12);}',
'.cb-live-usage{align-self:center;margin-left:10px;font-family:var(--cb-mono);font-size:9.5px;color:var(--cb-faint);white-space:nowrap;}',
'.cb-live-usage[hidden]{display:none;}',
/* action buttons: floating just OUTSIDE the chat card, to its right */
'.cb-actions{position:absolute;z-index:4;left:calc(22px + min(26.875rem,40vw) + 14px);bottom:30px;display:flex;flex-direction:column;gap:9px;}',
'.cb-hero.cb-collapsed .cb-actions{opacity:0;pointer-events:none;}',
'.cb-mic,.cb-attach,.cb-send,.cb-stop{width:40px;height:40px;border-radius:11px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:.15s;flex:0 0 auto;border:1px solid var(--cb-line);',
'  background:linear-gradient(180deg,rgba(12,14,20,.7),rgba(8,9,14,.82));backdrop-filter:blur(8px);box-shadow:0 10px 30px -16px rgba(0,0,0,.9);}',
'.cb-mic,.cb-attach,.cb-stop{color:var(--cb-muted);}',
'.cb-mic:hover,.cb-attach:hover,.cb-stop:hover{color:var(--cb-text);border-color:var(--cb-accent);}',
'.cb-mic.cb-on{color:#1a0c00;background:var(--cb-accent);border-color:var(--cb-accent);animation:cb-micpulse 1.1s ease-in-out infinite;}',
'@keyframes cb-micpulse{0%,100%{box-shadow:0 0 0 0 rgba(255,106,0,.5);}50%{box-shadow:0 0 0 6px rgba(255,106,0,0);}}',
'.cb-send{background:var(--cb-accent);border-color:var(--cb-accent);color:#1a0c00;font-weight:700;}',
'.cb-send:hover{filter:brightness(1.12);}',
'.cb-stop{color:#ffccaa;border-color:rgba(255,106,0,.35);}',
'.cb-stop[hidden]{display:none;}',
'@media(max-width:680px){.cb-actions{left:auto;right:12px;bottom:auto;top:74px;}}',
'.cb-attbar{display:flex;flex-wrap:wrap;gap:8px;padding:0 16px;}',
'.cb-attbar:not(:empty){padding-top:12px;}',
'.cb-chip{position:relative;display:flex;align-items:center;gap:7px;background:rgba(255,255,255,.05);border:1px solid var(--cb-line);border-radius:8px;padding:4px 7px 4px 5px;max-width:190px;}',
'.cb-chip img{width:32px;height:32px;object-fit:cover;border-radius:5px;display:block;flex:0 0 auto;}',
'.cb-chip-info{display:flex;flex-direction:column;min-width:0;line-height:1.25;}',
'.cb-chip-name{font-family:var(--cb-mono);font-size:10px;color:var(--cb-text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}',
'.cb-chip-type{font-family:var(--cb-mono);font-size:9px;color:var(--cb-faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}',
'.cb-chip.cb-chip-up .cb-chip-type{font-style:italic;}',
'.cb-chip button{background:none;border:none;color:var(--cb-faint);cursor:pointer;font-size:13px;line-height:1;padding:0 2px;flex:0 0 auto;}',
'.cb-chip button:hover{color:var(--cb-accent);}',
'.cb-msg.cb-from-user .cb-bubble img{width:min(10rem,100%);max-width:100%;border-radius:8px;margin-top:6px;display:inline-block;}',
/* center stage: petal core (center) · memory planet (right) */
'.cb-stage{position:absolute;inset:0;z-index:1;overflow:hidden;}',
'.cb-stage-toggle{position:absolute;top:30px;z-index:5;border:1px solid var(--cb-line);color:var(--cb-muted);width:34px;height:34px;',
'  border-radius:9px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:.15s;',
'  background:linear-gradient(180deg,rgba(12,14,20,.72),rgba(8,9,14,.84));backdrop-filter:blur(8px);box-shadow:0 10px 30px -16px rgba(0,0,0,.9);}',
'.cb-stage-toggle:hover{color:var(--cb-text);border-color:var(--cb-accent);}',
'.cb-stage-toggle svg{transition:transform .25s;}',
/* chat toggle sits just right of the chat card; memory toggle just left of the memory card */
'#cbToggle{left:calc(22px + min(26.875rem,40vw) + 14px);}',
'#cbMemToggle{right:calc(22px + min(34%,470px) + 14px);}',
'.cb-hero.cb-collapsed #cbToggle svg{transform:rotate(180deg);}',
'.cb-hero.cb-mem-collapsed #cbMemToggle svg{transform:rotate(180deg);}',
'.cb-hero.cb-mem-collapsed .cb-right{opacity:0;pointer-events:none;transform:translateX(14px);}',
'.cb-hero.cb-mem-collapsed .cb-petals{right:30px;}',
'@media(max-width:980px){#cbMemToggle{display:none;}}',
/* Hermes Prime petal-core: the luminous center; sub-agents radiate outward */
'.cb-petals{position:absolute;top:0;bottom:0;left:min(430px,40vw);right:calc(min(34%,470px) + 30px);z-index:1;pointer-events:none;transition:left .25s,right .25s;}',
'.cb-petals canvas{display:block;width:100%;height:100%;}',
'.cb-hero.cb-collapsed .cb-petals{left:0;}',
'.cb-petal-label{position:absolute;left:50%;bottom:7%;transform:translateX(-50%);text-align:center;z-index:2;pointer-events:none;}',
'.cb-petal-label .cb-h{font-family:var(--cb-disp);font-weight:700;font-size:13px;letter-spacing:.34em;text-transform:uppercase;',
'  background:linear-gradient(90deg,#fff 0%,#fff 30%,#ffb673 70%,var(--cb-accent) 100%);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;color:transparent;}',
'.cb-petal-label .cb-s{font-family:var(--cb-mono);font-size:9.5px;letter-spacing:.16em;color:var(--cb-muted);margin-top:4px;}',
/* right rail: compact live/repo status above the existing memory planet */
'.cb-right{position:absolute;right:22px;top:22px;bottom:22px;width:min(34%,470px);z-index:2;display:flex;flex-direction:column;gap:10px;transition:opacity .25s,transform .25s;}',
'.cb-repo{flex:0 0 auto;border:1px solid var(--cb-line);border-radius:14px;overflow:hidden;background:linear-gradient(180deg,rgba(8,9,14,.5),rgba(6,7,11,.66));backdrop-filter:blur(6px);}',
'.cb-repo-head{width:100%;display:flex;align-items:center;gap:9px;padding:11px 14px;border:0;border-bottom:1px solid var(--cb-line2);background:transparent;color:var(--cb-muted);cursor:pointer;text-align:left;font-family:var(--cb-mono);font-size:10px;letter-spacing:.16em;text-transform:uppercase;}',
'.cb-repo-head:hover,.cb-repo-head:focus-visible{color:var(--cb-text);}',
'.cb-repo-refresh{margin-left:auto;color:var(--cb-faint);font-size:9px;letter-spacing:.08em;text-transform:none;}',
'.cb-repo-list{padding:5px 10px 7px;}',
'.cb-repo-row{display:grid;grid-template-columns:minmax(0,1fr) auto auto auto;gap:7px;align-items:center;min-height:25px;border-bottom:1px solid var(--cb-line2);font-family:var(--cb-mono);font-size:9px;color:var(--cb-muted);}',
'.cb-repo-row:last-child{border-bottom:0;}',
'.cb-repo-main{min-width:0;display:flex;gap:5px;align-items:baseline;overflow:hidden;}',
'.cb-repo-name{color:var(--cb-text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}',
'.cb-repo-branch{color:var(--cb-faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}',
'.cb-repo-badge{border:1px solid var(--cb-line);border-radius:999px;padding:2px 5px;white-space:nowrap;}',
'.cb-repo-badge.ok{color:var(--cb-success);border-color:color-mix(in srgb,var(--cb-success) 34%,transparent);}',
'.cb-repo-badge.warn{color:var(--cb-warning);border-color:color-mix(in srgb,var(--cb-warning) 36%,transparent);}',
'.cb-repo-badge.error{color:var(--cb-error);border-color:color-mix(in srgb,var(--cb-error) 38%,transparent);}',
'.cb-repo-dirty,.cb-repo-headhash{white-space:nowrap;color:var(--cb-faint);}',
'.cb-repo-workflow{grid-template-columns:minmax(0,1fr) auto;color:var(--cb-faint);}',
'.cb-repo-workflow .cb-repo-name{color:var(--cb-muted);}',
'/* memory planet panel (right): the vault graph, kept as the live memory view */',
'.cb-mem{min-height:0;flex:1;display:flex;flex-direction:column;',
'  border:1px solid var(--cb-line);border-radius:16px;overflow:hidden;',
'  background:linear-gradient(180deg,rgba(8,9,14,.34),rgba(6,7,11,.52));backdrop-filter:blur(6px);',
'  box-shadow:0 30px 90px -40px rgba(0,0,0,.9);}',
'.cb-mem-head{display:flex;align-items:center;gap:9px;padding:13px 16px;border-bottom:1px solid var(--cb-line2);',
'  font-family:var(--cb-mono);font-size:10px;letter-spacing:.18em;text-transform:uppercase;color:var(--cb-muted);}',
'.cb-mem-dot{width:7px;height:7px;border-radius:50%;background:var(--cb-accent);box-shadow:0 0 8px var(--cb-accent);flex:0 0 auto;}',
'.cb-agents{border-bottom:1px solid var(--cb-line2);background:rgba(255,255,255,.018);}',
'.cb-agents summary{list-style:none;display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 14px;cursor:pointer;',
'  font-family:var(--cb-mono);font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--cb-muted);}',
'.cb-agents summary::-webkit-details-marker{display:none;}',
'.cb-agents summary:hover{color:var(--cb-text);}',
'.cb-agents-count{color:var(--cb-accent-2);font-weight:600;letter-spacing:.08em;}',
'.cb-agent-list{max-height:240px;overflow:auto;padding:0 12px 12px;display:flex;flex-direction:column;gap:8px;}',
'.cb-agent-row{display:grid;grid-template-columns:10px minmax(0,1fr) auto;gap:9px;align-items:start;border:1px solid var(--cb-line2);',
'  border-radius:8px;padding:8px;background:rgba(0,0,0,.18);}',
'.cb-agent-state{width:8px;height:8px;border-radius:50%;margin-top:4px;background:#6b7280;box-shadow:0 0 8px rgba(107,114,128,.35);}',
'.cb-agent-state.cb-live{background:#48c774;box-shadow:0 0 10px rgba(72,199,116,.65);}',
'.cb-agent-state.cb-waiting{background:#f5c518;box-shadow:0 0 10px rgba(245,197,24,.6);}',
'.cb-agent-name{font-family:var(--cb-disp);font-size:12px;font-weight:600;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}',
'.cb-agent-role{font-size:11px;line-height:1.35;color:var(--cb-muted);margin-top:2px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}',
'.cb-agent-meta{font-family:var(--cb-mono);font-size:9.5px;color:var(--cb-faint);text-align:right;white-space:nowrap;}',
'.cb-flex{border-bottom:1px solid var(--cb-line2);padding:12px 14px;background:rgba(255,255,255,.014);}',
'.cb-flex-top{display:grid;grid-template-columns:minmax(82px,.9fr) minmax(0,1.4fr);gap:12px;align-items:center;}',
'.cb-flex-num{font-family:var(--cb-disp);font-size:42px;font-weight:750;line-height:.9;color:#fff;}',
'.cb-flex-label{font-family:var(--cb-mono);font-size:9px;letter-spacing:.16em;text-transform:uppercase;color:var(--cb-muted);margin-top:6px;}',
'.cb-flex-delta{font-family:var(--cb-mono);font-size:10px;color:var(--cb-faint);margin-top:4px;}',
'.cb-flex-badges{display:flex;flex-wrap:wrap;gap:6px;justify-content:flex-end;}',
'.cb-flex-badge{border:1px solid var(--cb-line2);border-radius:999px;padding:4px 7px;font-family:var(--cb-mono);font-size:9.5px;color:var(--cb-muted);background:rgba(0,0,0,.16);white-space:nowrap;}',
'.cb-flex-badge.hot{color:#fff;border-color:rgba(255,138,61,.45);box-shadow:0 0 18px -12px var(--cb-accent);}',
'.cb-flex-projects{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:10px;}',
'.cb-flex-proj{border:1px solid var(--cb-line2);border-radius:8px;padding:7px;background:rgba(0,0,0,.16);min-width:0;}',
'.cb-flex-proj .n{font-family:var(--cb-disp);font-size:15px;color:#fff;line-height:1;}',
'.cb-flex-proj .l{font-family:var(--cb-mono);font-size:8.5px;text-transform:uppercase;color:var(--cb-faint);margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}',
'.cb-flex-recent{margin-top:10px;display:flex;flex-direction:column;gap:6px;max-height:118px;overflow:auto;}',
'.cb-flex-day{font-family:var(--cb-mono);font-size:9px;color:var(--cb-faint);letter-spacing:.1em;text-transform:uppercase;margin-top:3px;}',
'.cb-flex-item{display:grid;grid-template-columns:auto minmax(0,1fr);gap:7px;align-items:start;font-size:11px;color:var(--cb-text);line-height:1.28;}',
'.cb-flex-tag{font-family:var(--cb-mono);font-size:8.5px;color:var(--cb-accent-2);border:1px solid var(--cb-line2);border-radius:6px;padding:2px 4px;white-space:nowrap;}',
'.cb-flex-summary{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}',
'.cb-planet{position:relative;flex:1;min-height:0;}',
'.cb-planet canvas{display:block;}',
'@media(max-width:980px){.cb-right{display:none;}.cb-petals{left:0;right:0;}}',
/* memory stat row (inside the planet panel) */
'.cb-stats{display:flex;gap:14px;justify-content:space-around;padding:11px 12px 13px;border-top:1px solid var(--cb-line2);}',
'.cb-stat{text-align:center;}',
'.cb-stat .n{font-family:var(--cb-disp);font-weight:700;font-size:19px;color:#fff;line-height:1;}',
'.cb-stat .l{font-family:var(--cb-mono);font-size:9px;letter-spacing:.14em;text-transform:uppercase;color:var(--cb-muted);margin-top:6px;}',
'.cb-stat.proj .n{color:var(--cb-accent-2);}',
'.cb-stage-hint{position:absolute;top:18px;right:18px;font-family:var(--cb-mono);font-size:10px;letter-spacing:.1em;',
'  color:var(--cb-faint);text-transform:uppercase;}',
/* projects strip */
'.cb-projects{position:relative;padding:54px clamp(20px,4vw,56px) 70px;border-top:1px solid var(--cb-line2);',
'  background:linear-gradient(180deg,transparent,rgba(0,0,0,.35));}',
'.cb-sec-title{font-family:var(--cb-disp);font-weight:600;font-size:13px;letter-spacing:.28em;text-transform:uppercase;',
'  color:var(--cb-muted);margin:0 0 22px;display:flex;align-items:center;gap:14px;}',
'.cb-sec-title::after{content:"";flex:1;height:1px;background:linear-gradient(90deg,var(--cb-line),transparent);}',
'.cb-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;}',
'@media(max-width:1100px){.cb-grid{grid-template-columns:repeat(2,1fr);}}',
'@media(max-width:680px){.cb-grid{grid-template-columns:1fr;}.cb-chat{left:3vw;right:3vw;width:auto;height:64vh;}}',
'.cb-card{position:relative;border:1px solid var(--cb-line);border-radius:14px;padding:18px 18px 16px;cursor:pointer;',
'  background:linear-gradient(180deg,rgba(255,255,255,.022),rgba(255,255,255,.006));overflow:hidden;',
'  transition:border-color .2s,transform .2s,box-shadow .2s;}',
'.cb-card::before{content:"";position:absolute;left:0;top:0;bottom:0;width:2px;background:linear-gradient(180deg,#fff,var(--cb-accent));opacity:.0;transition:opacity .2s;}',
'.cb-card:hover{border-color:rgba(255,138,61,.5);transform:translateY(-3px);box-shadow:0 14px 40px -22px rgba(255,106,0,.6);}',
'.cb-card:hover::before{opacity:.9;}',
'.cb-card.cb-low-priority{opacity:.58;order:20;}',
'.cb-card.cb-low-priority:hover{opacity:.82;}',
'.cb-card.cb-low-priority .cb-card-block{display:none;}',
'.cb-card.cb-low-priority.cb-expanded .cb-card-block{display:block;}',
'.cb-card.cb-low-priority .cb-low-toggle{display:inline-flex;}',
'.cb-low-toggle{display:none;margin-top:10px;}',
'.cb-card-top{display:flex;align-items:baseline;justify-content:space-between;gap:12px;}',
'.cb-card-name{font-family:var(--cb-disp);font-weight:600;font-size:16px;color:#fff;letter-spacing:.01em;min-width:0;',
'  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}',
'.cb-spark{display:flex;gap:3px;align-items:center;flex:0 0 auto;}',
'.cb-spark i{width:5px;height:5px;border-radius:50%;background:var(--cb-faint);display:block;}',
'.cb-card-block{margin-top:14px;}',
'.cb-block-h{font-family:var(--cb-mono);font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--cb-faint);margin-bottom:7px;}',
'.cb-line-item{display:flex;justify-content:space-between;gap:10px;font-size:12.5px;color:var(--cb-text);padding:2px 0;}',
'.cb-line-item .t{color:var(--cb-muted);font-family:var(--cb-mono);font-size:10.5px;flex:0 0 auto;}',
'.cb-line-item .n{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}',
'.cb-task{display:flex;gap:8px;align-items:flex-start;font-size:12.5px;color:var(--cb-text);padding:2px 0;line-height:1.4;}',
'.cb-task .box{width:13px;height:13px;border:1.4px solid var(--cb-accent);border-radius:3px;flex:0 0 auto;margin-top:2px;transition:background .15s;}',
'.cb-task-do{cursor:pointer;border-radius:6px;margin:0 -4px;padding:2px 4px;transition:background .12s;}',
'.cb-task-do:hover{background:rgba(255,255,255,.05);}',
'.cb-task-do:hover .box{background:var(--cb-accent-dim);}',
'.cb-task-done .box{background:var(--cb-accent);}',
'.cb-task-done .cb-task-txt{text-decoration:line-through;opacity:.5;}',
'.cb-task-add{margin-top:9px;}',
'.cb-task-input{width:100%;box-sizing:border-box;background:rgba(255,255,255,.04);border:1px solid var(--cb-line);border-radius:8px;color:var(--cb-text);font-family:var(--cb-sans);font-size:12px;padding:6px 9px;outline:none;transition:border-color .15s;}',
'.cb-task-input:focus{border-color:var(--cb-accent);box-shadow:0 0 0 2px rgba(255,106,0,.1);}',
'.cb-task-input::placeholder{color:var(--cb-faint);}',
'.cb-empty{font-family:var(--cb-mono);font-size:11px;color:var(--cb-faint);font-style:italic;}',
'.cb-loading{padding:60px;text-align:center;font-family:var(--cb-mono);color:var(--cb-muted);letter-spacing:.1em;}',
/* family cards: groupable + draggable (hold a card and move to reorder) */
'.cb-card.cb-fam{cursor:grab;user-select:none;-webkit-user-select:none;touch-action:manipulation;}',
'.cb-card.cb-fam:active{cursor:grabbing;}',
'.cb-card.cb-dragging{opacity:.9;cursor:grabbing;z-index:6;transform:scale(1.025);border-color:rgba(255,138,61,.75);box-shadow:0 24px 64px -18px rgba(255,106,0,.7);}',
'.cb-grid.cb-drag-active .cb-card:not(.cb-dragging){transition:transform .18s ease;}',
'.cb-grid.cb-drag-active .cb-card:hover{transform:none;box-shadow:none;}',
'.cb-card-head{display:flex;align-items:flex-start;gap:10px;justify-content:space-between;}',
'.cb-drag{flex:0 0 auto;color:var(--cb-faint);font-size:15px;line-height:1;letter-spacing:1px;cursor:grab;padding:2px 2px 6px;opacity:.55;transition:opacity .15s,color .15s;}',
'.cb-card:hover .cb-drag{opacity:.95;color:var(--cb-accent);}',
'.cb-subs{display:flex;flex-wrap:wrap;gap:6px;margin-top:13px;}',
'.cb-sub{font-family:var(--cb-mono);font-size:10.5px;color:var(--cb-text);border:1px solid var(--cb-line);border-radius:999px;padding:4px 10px;background:rgba(255,255,255,.03);cursor:pointer;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;transition:border-color .15s,background .15s,color .15s;}',
'.cb-sub:hover{border-color:rgba(255,138,61,.55);background:rgba(255,138,61,.08);color:#fff;}',
'.cb-sub.cb-active{border-color:var(--cb-accent);background:rgba(255,106,0,.14);color:#fff;}',
'.cb-task[hidden]{display:none;}',
'.cb-tasks-scroll{max-height:230px;overflow:auto;margin:0 -4px;padding:0 4px;}',
'.cb-clients{display:flex;flex-direction:column;gap:7px;}',
'.cb-client{border:1px solid var(--cb-line2);border-radius:8px;background:rgba(0,0,0,.14);padding:7px 8px;}',
'.cb-client strong{display:block;font-family:var(--cb-disp);font-size:12px;color:#fff;margin-bottom:3px;}',
'.cb-client span{display:block;font-size:11.5px;line-height:1.35;color:var(--cb-text);}',
'.cb-client em{display:block;margin-top:3px;font-style:normal;font-family:var(--cb-mono);font-size:10px;line-height:1.35;color:var(--cb-muted);}',
/* white -> fluo-orange gradient on display titles (the accent the user wanted on TEXT) */
'.cb-chat-name,.cb-sec-title,.cb-card-name{background:linear-gradient(90deg,#ffffff 0%,#ffffff 26%,#ffb673 62%,var(--cb-accent) 100%);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;color:transparent;}',
/* todos strip (P2-B) */
'.cb-todos{border-top:1px solid var(--cb-line2);padding:10px 14px;max-height:160px;overflow-y:auto;display:flex;flex-direction:column;gap:4px;}',
'.cb-todos-head{font-family:var(--cb-mono);font-size:9px;letter-spacing:.16em;text-transform:uppercase;color:var(--cb-faint);margin-bottom:4px;}',
'.cb-todo-row{display:flex;align-items:baseline;gap:6px;font-size:11.5px;line-height:1.3;}',
'.cb-todo-dot{width:6px;height:6px;border-radius:50%;flex:0 0 auto;margin-top:4px;}',
'.cb-todo-dot.pending{background:#f5c518;}.cb-todo-dot.in_progress{background:#48c774;}.cb-todo-dot.completed{background:var(--cb-faint);text-decoration:line-through;}.cb-todo-dot.cancelled{background:var(--cb-faint);opacity:.4;}',
'.cb-todo-txt{color:var(--cb-text);flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}',
'.cb-todo-status{font-family:var(--cb-mono);font-size:9px;color:var(--cb-faint);white-space:nowrap;}',
/* tool cards (P2-C) */
'.cb-tool-card{align-self:flex-start;max-width:96%;border:1px solid rgba(100,180,255,.22);border-left:2px solid rgba(100,180,255,.5);border-radius:6px;padding:6px 10px;background:rgba(40,60,80,.28);font-family:var(--cb-mono);font-size:10.5px;line-height:1.35;}',
'.cb-tool-name{color:rgba(140,210,255,.9);font-weight:600;letter-spacing:.08em;}',
'.cb-tool-summary{color:var(--cb-muted);margin-top:2px;font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:340px;}',
''
    ].join('\n');
    var s = el('style'); s.id = 'cb-styles'; s.textContent = css;
    document.head.appendChild(s);
  }

  /* ── DOM scaffold ──────────────────────────────────────────────────────── */
  function build() {
    var host = $('mainBridge');
    if (!host) return false;
    injectFonts();
    injectStyles();
    var root = el('div', 'cb-root');
    root.innerHTML =
      '<div class="cb-stars" aria-hidden="true"></div>' +
      '<div class="cb-hero" id="cbHero">' +
        '<aside class="cb-chat" id="cbChat">' +
          '<div class="cb-chat-head"><span class="cb-dot"></span><div style="flex:1;min-width:0">' +
            '<div class="cb-chat-name">Hermes Prime</div>' +
            '<div class="cb-chat-role"></div>' +
          '</div>' +
            '<span class="cb-quota" id="cbQuotaPill" hidden></span>' +
            '<div class="cb-brainctl" id="cbBrainCtl">' +
              '<select class="cb-brainsel" id="cbBrainSel" aria-label="Modello o brain di Hermes Prime" title="Scegli il modello di Hermes Prime">' +
                '<optgroup label="Claude (inclusi nel piano)">' +
                  '<option value="model:claude-opus-5">Opus 5</option>' +
                  '<option value="model:claude-sonnet-5">Sonnet 5</option>' +
                  '<option value="model:claude-haiku-4-5">Haiku 4.5</option>' +
                  // Fable 5: disabilitato dal 2026-08-01 perche' sul piano Pro non era
                  // incluso e l'API rispondeva 429. Dal 2026-08-05 l'account e' su piano
                  // Max, quindi il gate lato UI e' rimosso: l'entitlement vero lo decide
                  // comunque l'API (se non fosse coperto tornerebbe 429 e bridge_errors
                  // lo mostra come "richiede crediti").
                  '<option value="model:claude-fable-5">Fable 5</option>' +
                '</optgroup>' +
                '<optgroup label="Altro brain">' +
                  '<option value="lead:codex">Codex (locale)</option>' +
                  '<option value="action:auto">AUTO — failover</option>' +
                '</optgroup>' +
              '</select>' +
            '</div>' +
            '<button type="button" class="cb-voicetoggle cb-on" id="cbVoice" aria-label="Voce on/off" title="Voce on/off">' +
              '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5 6 9H2v6h4l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M19 5a9 9 0 0 1 0 14"/></svg></button>' +
          '</div>' +
          '<div class="cb-chat-log" id="cbLog"></div>' +
          '<div class="cb-todos" id="cbTodos" hidden>' +
            '<div class="cb-todos-head">Todo attivi</div>' +
            '<div id="cbTodoList"></div>' +
          '</div>' +
          '<div class="cb-attbar" id="cbAtt"></div>' +
          '<form class="cb-chat-input" id="cbForm" autocomplete="off">' +
            '<textarea id="cbInput" rows="1" placeholder="Parla con Hermes Prime…" aria-label="Messaggio a Hermes Prime"></textarea>' +
            '<span class="cb-live-usage" id="cbLiveUsage" hidden></span>' +
          '</form>' +
        '</aside>' +
        '<div class="cb-actions" id="cbActions">' +
          '<button type="button" class="cb-mic" id="cbMic" aria-label="Parla a voce" title="Parla a voce">' +
            '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2" width="6" height="11" rx="3"/><path d="M5 10a7 7 0 0 0 14 0"/><path d="M12 17v4"/></svg></button>' +
          '<button type="button" class="cb-attach" id="cbAttachBtn" aria-label="Allega foto" title="Allega foto">' +
            '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg></button>' +
          '<input id="cbFile" type="file" accept="image/*" multiple style="display:none">' +
          '<button class="cb-send" type="submit" form="cbForm" id="cbSend" aria-label="Invia" title="Invia">' +
            '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"/><path d="M13 6l6 6-6 6"/></svg></button>' +
          '<button class="cb-stop" type="button" id="cbStop" aria-label="Stop" title="Stop" hidden>' +
            '<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg></button>' +
        '</div>' +
        '<section class="cb-stage" id="cbStage">' +
          '<button class="cb-stage-toggle" id="cbToggle" title="Comprimi chat" aria-label="Comprimi chat">' +
            '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M15 18l-6-6 6-6"/></svg></button>' +
          '<button class="cb-stage-toggle" id="cbMemToggle" title="Comprimi memoria" aria-label="Comprimi memoria">' +
            '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M9 18l6-6-6-6"/></svg></button>' +
          '<div class="cb-petals" id="cbPetals">' +
            '<div class="cb-petal-label"><div class="cb-h">Hermes Prime</div><div class="cb-s">stella viva · agenti in orbita</div></div>' +
          '</div>' +
          '<div class="cb-right" id="cbRight">' +
            '<section class="cb-repo" id="cbRepoPanel">' +
              '<button type="button" class="cb-repo-head" id="cbRepoRefresh" title="Aggiorna stato repository"><span class="cb-mem-dot"></span>Stato repo / Live<span class="cb-repo-refresh">refresh</span></button>' +
              '<div class="cb-repo-list" id="cbRepoList"><div class="cb-empty">caricamento repo</div></div>' +
            '</section>' +
            '<div class="cb-mem" id="cbMem">' +
              '<div class="cb-mem-head"><span class="cb-mem-dot"></span>Memoria · Vault</div>' +
            '<details class="cb-agents" id="cbAgents" open>' +
              '<summary><span>Agenti</span><span class="cb-agents-count" id="cbAgentsCount">--</span></summary>' +
              '<div class="cb-agent-list" id="cbAgentList"><div class="cb-empty">caricamento agenti</div></div>' +
            '</details>' +
            '<section class="cb-flex" id="cbFlex">' +
              '<div class="cb-empty">caricamento work log</div>' +
            '</section>' +
            '<div class="cb-planet" id="cbPlanet"></div>' +
              '<div class="cb-stats" id="cbStats"></div>' +
            '</div>' +
          '</div>' +
        '</section>' +
      '</div>' +
      '<section class="cb-projects">' +
        '<h2 class="cb-sec-title">Projects</h2>' +
        '<div class="cb-grid" id="cbGrid"><div class="cb-loading">caricamento vault…</div></div>' +
      '</section>';
    host.appendChild(root);
    mountPrimeCore($('cbPetals')); // central Hermes Prime: living-star system (voice orb)

    // interactions
    var toggle = $('cbToggle'), hero = $('cbHero');
    if (toggle && hero) toggle.addEventListener('click', function () { hero.classList.toggle('cb-collapsed'); });
    var memToggle = $('cbMemToggle');
    if (memToggle && hero) memToggle.addEventListener('click', function () { hero.classList.toggle('cb-mem-collapsed'); });
    var repoRefresh = $('cbRepoRefresh');
    if (repoRefresh) repoRefresh.addEventListener('click', refreshRepoStatus);
    var form = $('cbForm');
    if (form) form.addEventListener('submit', onPrimeSubmit);
    // Textarea che cresce mentre scrivi e torna piccola all'invio; Invio manda,
    // Shift+Invio va a capo. Risolve anche il cursore che tornava all'inizio.
    var ta = $('cbInput');
    if (ta) {
      var autoGrow = function () {
        ta.style.height = 'auto';
        ta.style.height = Math.min(ta.scrollHeight, Math.round(window.innerHeight * 0.46)) + 'px';
      };
      ta.addEventListener('input', autoGrow);
      ta.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); onPrimeSubmit(ev); }
      });
      window.__cbAutoGrow = autoGrow;
    }
    var mic = $('cbMic');
    if (mic) mic.addEventListener('click', toggleListen);
    var attachBtn = $('cbAttachBtn'), fileInput = $('cbFile');
    if (attachBtn && fileInput) {
      attachBtn.addEventListener('click', function () { fileInput.click(); });
      fileInput.addEventListener('change', function () {
        var list = fileInput.files ? Array.prototype.slice.call(fileInput.files) : [];
        list.forEach(uploadAttachment);
        fileInput.value = ''; // permette di riselezionare lo stesso file
      });
    }
    var pasteInput = $('cbInput');
    if (pasteInput) pasteInput.addEventListener('paste', onPrimePaste);
    var vb = $('cbVoice');
    if (vb) vb.addEventListener('click', function () { userEngaged = true; toggleVoice(); });
    var stop = $('cbStop');
    if (stop) stop.addEventListener('click', cancelPrimeTurn);
    wireBrainControls();
    refreshBrainState();
    primeSay('prime', 'Plancia online. Ti dò il quadro quando vuoi — chiedimi "cosa serve oggi?" e ti briffo. Premi il microfono per parlarmi a voce.');
    BUILT = true;
    return true;
  }

  /* ── Hermes Prime + Voice (Phase 5). Real Claude/Codex delegation: Phase 6. ── */
  var voiceOn = true, userEngaged = false, _ctx = null, _an = null, _raf = null, _cur = null, _rec = null, _ttsActive = false;
  var _listening = false, _micTimer = null;
  var _micStream = null, _micRec = null, _micVadRaf = null, _micChunks = [], _micBusy = false;
  var _primeStreaming = false, _primeStreamId = null, _primeLiveTimer = null;

  // Id modello -> nome umano per l'header (es. claude-fable-5 -> "Fable 5").
  function prettyModelName(id) {
    var raw = String(id || '').trim();
    if (!raw) return '';
    var m = raw.toLowerCase().replace(/^codex:/, '');
    var known = [
      [/^claude-fable-5/, 'Fable 5'],
      [/^claude-opus-5/, 'Opus 5'],
      [/^claude-opus-4-8/, 'Opus 4.8'],
      [/^claude-opus-4-7/, 'Opus 4.7'],
      [/^claude-sonnet-5/, 'Sonnet 5'],
      [/^claude-sonnet-4-6/, 'Sonnet 4.6'],
      [/^claude-haiku-4-5/, 'Haiku 4.5'],
      [/^gpt-5\.5-codex|^codex-5\.5/, 'Codex 5.5'],
      [/^gpt-5\.5/, 'GPT-5.5'],
      [/^gpt-5/, 'GPT-5'],
    ];
    for (var i = 0; i < known.length; i++) {
      if (known[i][0].test(m)) return known[i][1];
    }
    return raw.replace(/^codex:/, ''); // sconosciuto: mostra l'id cosi' com'e'
  }

  function renderBrainState(state) {
    state = state || {};
    var lead = String(state.lead || 'claude').toLowerCase();
    var manual = !!state.manual;
    var sel = $('cbBrainSel');
    if (sel) {
      var model = String(state.model || '').replace(/^codex:/, '');
      if (lead === 'codex') {
        sel.value = 'lead:codex';
      } else {
        var want = 'model:' + model, has = false;
        for (var i = 0; i < sel.options.length; i++) {
          if (sel.options[i].value === want) { has = true; break; }
        }
        // Modello risolto fuori lista (profilo o override manuale): aggiungilo,
        // altrimenti la tendina resterebbe su una voce che non e' quella attiva
        // e mostrerebbe il modello sbagliato.
        if (!has && model) {
          var opt = document.createElement('option');
          opt.value = want;
          opt.textContent = prettyModelName(model) || model;
          sel.getElementsByTagName('optgroup')[0].appendChild(opt);
        }
        if (model) sel.value = want;
      }
      sel.classList.toggle('cb-codex', lead === 'codex');
      sel.title = (manual ? 'Scelta pinnata a mano' : 'Failover automatico attivo')
        + ' — cambia modello o brain di Hermes Prime';
    }
    var role = document.querySelector('.cb-chat-role');
    // Solo il MODELLO vero del capo (es. "Fable 5") — niente chief-of-staff/PIN/voce.
    var brainLabel = prettyModelName(state.model) || lead.toUpperCase();
    if (role) role.textContent = brainLabel;
  }

  function setBrain(payload) {
    var ctl = $('cbBrainCtl'); if (ctl) ctl.classList.add('cb-busy');
    var cfg = window.__HERMES_CONFIG__ || {};
    return fetch(new URL('api/bridge/prime/lead', document.baseURI || location.href).href, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': cfg.csrfToken || '' },
      body: JSON.stringify(payload || {})
    }).then(function (r) {
      if (!r.ok) return r.json().catch(function () { return {}; }).then(function (d) { throw new Error(d.error || ('HTTP ' + r.status)); });
      return r.json();
    }).then(function (state) {
      renderBrainState(state);
      return state;
    }).catch(function (err) {
      sysNote('Switch brain fallito: ' + String(err && err.message || err));
    }).then(function (state) {
      if (ctl) ctl.classList.remove('cb-busy');
      return state;
    });
  }

  function refreshBrainState() { return setBrain({ action: 'get' }); }

  function setPrimeModel(modelId) {
    var ctl = $('cbBrainCtl'); if (ctl) ctl.classList.add('cb-busy');
    var cfg = window.__HERMES_CONFIG__ || {};
    return fetch(new URL('api/bridge/prime/model', document.baseURI || location.href).href, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': cfg.csrfToken || '' },
      body: JSON.stringify({ action: 'set', model: modelId })
    }).then(function (r) {
      if (!r.ok) return r.json().catch(function () { return {}; }).then(function (d) { throw new Error(d.error || ('HTTP ' + r.status)); });
      return r.json();
    }).then(function () {
      // Scegliere un modello Claude implica che il capo sia Claude: se restasse
      // pinnato Codex la tendina mentirebbe (mostra Opus, risponde Codex).
      return setBrain({ action: 'set', lead: 'claude' });
    }).catch(function (err) {
      sysNote('Cambio modello fallito: ' + String(err && err.message || err));
      return refreshBrainState();
    }).then(function (state) {
      if (ctl) ctl.classList.remove('cb-busy');
      return state;
    });
  }

  function wireBrainControls() {
    var sel = $('cbBrainSel');
    if (!sel) return;
    sel.addEventListener('change', function () {
      var v = String(sel.value || '');
      if (v.indexOf('model:') === 0) return setPrimeModel(v.slice(6));
      if (v === 'lead:codex') return setBrain({ action: 'set', lead: 'codex' });
      if (v === 'action:auto') return setBrain({ action: 'auto' });
    });
  }

  function setPrimeStreaming(active, streamId) {
    _primeStreaming = !!active;
    _primeStreamId = active ? (streamId || _primeStreamId || null) : null;
    var stop = $('cbStop'); if (stop) stop.hidden = !_primeStreaming;
  }

  function cancelPrimeTurn() {
    if (!_primeStreaming && !_primeStreamId) return;
    var stop = $('cbStop'); if (stop) stop.disabled = true;
    apiPost('/api/bridge/prime/cancel', { stream_id: _primeStreamId || '' })
      .then(function (d) {
        sysNote((d && d.promoted) ? 'Turno interrotto: ho salvato il parziale.' : 'Stop inviato a Hermes Prime.');
      })
      .catch(function (e) { sysNote('Stop fallito: ' + (e && e.message ? e.message : 'errore')); })
      .then(function () { if (stop) stop.disabled = false; setPrimeStreaming(false, null); });
  }

  function startPrimeLivePolling(node) {
    if (_primeLiveTimer) clearInterval(_primeLiveTimer);
    var lastText = node ? (node.textContent || '') : '';
    var tick = function () {
      api('/api/bridge/prime/live').then(function (data) {
        var pending = data && data.pending_turn;
        if (!data || !data.active || !pending) {
          if (_primeLiveTimer) clearInterval(_primeLiveTimer);
          _primeLiveTimer = null;
          setPrimeStreaming(false, null);
          return;
        }
        setPrimeStreaming(true, pending.stream_id || data.stream_id || null);
        var partial = String(pending.partial_output || '');
        if (node && partial && partial !== lastText) {
          lastText = partial;
          node.textContent = partial;
          node.classList.add('cb-recovered');
        }
      }).catch(function () {});
    };
    tick();
    _primeLiveTimer = setInterval(tick, 2000);
  }

  function formatCompactTokens(n) {
    n = Number(n) || 0;
    if (n >= 1000) return (n / 1000).toFixed(n >= 10000 ? 0 : 1) + 'k';
    return String(n);
  }

  function handlePrimeSlashCommand(text) {
    var raw = String(text || '').trim();
    if (!raw || raw.charAt(0) !== '/') return false;
    var parts = raw.split(/\s+/);
    var cmd = parts[0].toLowerCase();
    var args = raw.slice(parts[0].length).trim();
    if (cmd === '/compact') {
      sysNote('Compressione contesto Prime in corso...');
      apiPost('/api/bridge/prime/compact', {})
        .then(function (d) {
          var before = formatCompactTokens(d && d.before_tokens);
          var after = (d && d.after_tokens_unknown) ? '?' : formatCompactTokens(d && d.after_tokens);
          sysNote('Contesto compresso: ' + before + ' -> ' + after + ' token.');
        })
        .catch(function (e) { sysNote('/compact: ' + (e && e.message ? e.message : 'errore')); });
      return true;
    }
    if (cmd === '/model') {
      var body = args ? { action: 'set', model: args } : { action: 'get' };
      apiPost('/api/bridge/prime/model', body)
        .then(function (d) {
          sysNote('Model Prime: ' + (d.model || '?') + (d.model_provider ? (' @ ' + d.model_provider) : '') + (d.profile ? (' · profilo ' + d.profile) : ''));
        })
        .catch(function (e) { sysNote('/model: ' + (e && e.message ? e.message : 'errore')); });
      return true;
    }
    if (cmd === '/workspace') {
      var payload = args ? { action: 'set', workspace: args } : { action: 'get' };
      apiPost('/api/bridge/prime/workspace', payload)
        .then(function (d) { sysNote('Workspace Prime: ' + (d.workspace || '?')); })
        .catch(function (e) { sysNote('/workspace: ' + (e && e.message ? e.message : 'errore')); });
      return true;
    }
    if (cmd === '/interrupt') {
      cancelPrimeTurn();
      sysNote('Turno interrotto.');
      return true;
    }
    if (cmd === '/compress') {
      return handlePrimeSlashCommand('/compact');
    }
    if (cmd === '/steer') {
      if (!args) { sysNote('/steer: inserisci il nuovo messaggio di ridirezionamento.'); return true; }
      if (_primeStreaming) {
        cancelPrimeTurn();
        setTimeout(function () {
          var inp = $('cbInput');
          if (inp) { inp.value = args; if (window.__cbAutoGrow) window.__cbAutoGrow(); onPrimeSubmit({ preventDefault: function () {} }); }
        }, 200);
      } else {
        var inp = $('cbInput');
        if (inp) { inp.value = args; if (window.__cbAutoGrow) window.__cbAutoGrow(); onPrimeSubmit({ preventDefault: function () {} }); }
      }
      return true;
    }
    if (cmd === '/retry') {
      api('/api/bridge/prime/history').then(function (data) {
        var msgs = (data && data.messages) || [];
        var lastUser = null;
        for (var i = msgs.length - 1; i >= 0; i--) {
          if (msgs[i].role === 'user') { lastUser = msgs[i].content; break; }
        }
        if (!lastUser) { sysNote('/retry: nessun messaggio utente nella storia.'); return; }
        var inp = $('cbInput');
        if (inp) { inp.value = lastUser; if (window.__cbAutoGrow) window.__cbAutoGrow(); onPrimeSubmit({ preventDefault: function () {} }); }
      }).catch(function () { sysNote('/retry: impossibile leggere la storia.'); });
      return true;
    }
    if (cmd === '/goal') {
      if (!args) {
        apiPost('/api/bridge/prime/goal', { action: 'get' })
          .then(function (d) { sysNote('Goal attivo: ' + (d.goal || '(non impostato)')); })
          .catch(function (e) { sysNote('/goal: ' + (e && e.message ? e.message : 'errore')); });
      } else {
        apiPost('/api/bridge/prime/goal', { action: 'set', goal: args })
          .then(function (d) { sysNote('Goal impostato: ' + (d.goal || args)); })
          .catch(function (e) { sysNote('/goal: ' + (e && e.message ? e.message : 'errore')); });
      }
      return true;
    }
    if (cmd === '/tools') {
      if (!args) {
        apiPost('/api/bridge/prime/tools', { action: 'get' })
          .then(function (d) { sysNote('Toolset: ' + (d.toolset || 'lean') + ' · preset disponibili: ' + ((d.presets || []).join(', '))); })
          .catch(function (e) { sysNote('/tools: ' + (e && e.message ? e.message : 'errore')); });
      } else {
        apiPost('/api/bridge/prime/tools', { action: 'set', toolset: args })
          .then(function (d) { sysNote('Toolset impostato: ' + d.toolset + ' (attivo dal prossimo turno)'); })
          .catch(function (e) { sysNote('/tools: ' + (e && e.message ? e.message : 'errore')); });
      }
      return true;
    }
    // Unknown /command → help (NOT sent to Prime)
    sysNote('Comandi disponibili: /compact, /compress, /model, /workspace, /interrupt, /steer <testo>, /retry, /goal [testo], /tools [preset]');
    return true;
  }

  // The central petal-core is the primary Voice Orb; the memory planet on the
  // right echoes the same state so its heart pulses in sync with the voice.
  function setOrb(state, amp) {
    if (window.cbCore) {
      if (state != null) window.cbCore.setState(state);
      if (amp != null) window.cbCore.setAmplitude(amp);
    }
    if (window.cbPlanet) {
      if (state != null) window.cbPlanet.setState(state);
      if (amp != null) window.cbPlanet.setAmplitude(amp);
    }
  }
  function sysNote(text) {
    var log = $('cbLog'); if (!log) return;
    var m = el('div', 'cb-msg cb-from-prime');
    m.innerHTML = '<div class="cb-who">sistema</div><div class="cb-bubble" style="color:var(--cb-muted);font-style:italic">' + esc(text) + '</div>';
    var _sb = nearBottom(log); log.appendChild(m); if (_sb) log.scrollTop = log.scrollHeight;
  }

  // ── Todos panel (P2-B) ──────────────────────────────────────────────────
  function renderTodos(snapshot) {
    var panel = $('cbTodos'), list = $('cbTodoList');
    if (!panel || !list) return;
    var todos = (snapshot && snapshot.todos) || [];
    if (!todos.length) { panel.hidden = true; return; }
    var active = todos.filter(function (t) { return t && t.status !== 'cancelled' && t.status !== 'completed'; });
    if (!active.length) { panel.hidden = true; return; }
    panel.hidden = false;
    list.innerHTML = active.slice(0, 12).map(function (t) {
      var status = String(t.status || 'pending');
      var dot = 'cb-todo-dot ' + status;
      return '<div class="cb-todo-row"><span class="' + dot + '"></span><span class="cb-todo-txt">' + esc(t.content || t.id || '') + '</span><span class="cb-todo-status">' + esc(status) + '</span></div>';
    }).join('');
  }

  function loadPrimeTodos() {
    api('/api/bridge/prime/todos').then(function (d) {
      if (d && d.todo_state) renderTodos(d.todo_state);
    }).catch(function () {});
  }

  // ── Tool cards (P2-C) ──────────────────────────────────────────────────
  function renderToolCard(toolName, summary) {
    var log = $('cbLog'); if (!log) return;
    var card = el('div', 'cb-tool-card');
    card.innerHTML = '<span class="cb-tool-name">' + esc(toolName || 'tool') + '</span>' +
      (summary ? '<div class="cb-tool-summary">' + esc(summary) + '</div>' : '');
    var _sb = nearBottom(log); log.appendChild(card); if (_sb) log.scrollTop = log.scrollHeight;
    return card;
  }

  // delegation cards: update-in-place by id (in_corso -> ok/errore), background-aware
  var _cbTasks = {};
  var _cbActiveAgents = {};
  function taskStateClass(status) {
    if (status === 'ok' || status === 'parziale') return 'cb-deleg-done';
    if (status === 'errore' || status === 'interrotta') return 'cb-deleg-error';
    return 'cb-deleg-running';
  }
  function taskStateLabel(status) {
    if (status === 'in_corso') return 'in corso';
    if (status === 'ok' || status === 'parziale') return 'completato';
    if (status === 'interrotta') return 'interrotta';
    return 'errore';
  }
  function taskSummary(t) {
    if (t && t.summary) return String(t.summary);
    var id = t && t.id ? String(t.id) : 'delega';
    var task = String((t && t.task) || (t && t.task_type) || 'task').replace(/\s+/g, ' ').trim();
    return id + ' - ' + task.slice(0, 72) + ': ' + taskStateLabel(t && t.status);
  }
  function placeTaskCard(card, t) {
    var log = $('cbLog'); if (!log || !card) return;
    var anchorIdx = Number(t && t.anchor_message_index);
    var anchor = Number.isFinite(anchorIdx) ? log.querySelector('[data-cb-msg-index="' + anchorIdx + '"]') : null;
    if (anchor && anchor.parentNode === log) {
      var next = anchor.nextSibling;
      while (next && next.classList && next.classList.contains('cb-deleg') && next.getAttribute('data-anchor-index') === String(anchorIdx)) next = next.nextSibling;
      log.insertBefore(card, next);
    } else if (card.parentNode !== log) {
      log.appendChild(card);
    }
  }
  function renderTask(t) {
    if (!t || !t.id) return;
    var log = $('cbLog'); if (!log) return;
    var prev = _cbTasks[t.id];
    var sl = t.status === 'in_corso' ? '&#8230;' : (t.status === 'ok' || t.status === 'parziale' ? '&#10003;' : '&#10007;');
    var full = String(t.output || '').trim();
    var detail = full ? '<details class="cb-deleg-details"><summary>dettaglio</summary><div class="cb-deleg-full">' + esc(full) + '</div></details>' : '';
    var html = '<b>&#9883; ' + esc(t.agent || 'agente') + '</b> &middot; ' + esc(t.task_type || '') +
      ' <span style="float:right">' + sl + '</span>' +
      '<div class="cb-deleg-summary">' + esc(taskSummary(t)) + '</div>' + detail;
    if (prev && prev.el) { prev.el.innerHTML = html; prev.el.className = 'cb-deleg ' + taskStateClass(t.status); placeTaskCard(prev.el, t); }
    else {
      var c = el('div', 'cb-deleg ' + taskStateClass(t.status)); c.innerHTML = html;
      c.setAttribute('data-task-id', t.id);
      if (t.anchor_message_index != null) c.setAttribute('data-anchor-index', String(t.anchor_message_index));
      var _sb = nearBottom(log); placeTaskCard(c, t); if (_sb) log.scrollTop = log.scrollHeight;
      _cbTasks[t.id] = { el: c, status: t.status };
      // light up the matching planet in the star system
      if (window.cbStar && window.cbStar.flare) window.cbStar.flare((t.agent || '') + ' ' + (t.task_type || ''));
    }
    if (prev && prev.status === 'in_corso' && t.status !== 'in_corso') {
      sysNote('⚡ ' + (t.agent || 'sotto-agente') + ' ha ' + (t.status === 'ok' ? 'finito' : 'fallito') + ' il task.');
    }
    _cbTasks[t.id].status = t.status;
    // Brief automatico: a delega finita, Prime riparte da solo con la sintesi (una volta per task).
    if ((t.status === 'ok' || t.status === 'errore') && !_cbTasks[t.id].briefed) {
      _cbTasks[t.id].briefed = true;
      requestBrief(t);
    }
  }
  function requestBrief(t) {
    setOrb('thinking', 0);
    var ph = pendingBubble(); // riusa la bolla "sto ragionando…" di Hermes Prime
    var turnUi = createPrimeTurnUi(ph);
    var cfg = window.__HERMES_CONFIG__ || {};
    var finish = function (reply) {
      if (turnUi.isClosed()) return;
      reply = String(reply || '').trim();
      try {
        if (ph && ph.parentNode) {
          if (reply) {
            var bub = ph.querySelector('.cb-bubble');
            if (bub) { bub.removeAttribute('style'); renderRich(bub, reply); }
            if (userEngaged) speak(reply);
          } else { ph.parentNode.removeChild(ph); }
        }
      } finally {
        turnUi.close();
        setOrb('idle', 0);
      }
    };
    // Il brief gira in background lato server: qui si fa solo polling leggero,
    // cosi' nessuna connessione resta appesa per minuti (spam "Request timed out").
    var BRIEF_POLL_MS = 3000;
    var BRIEF_MAX_MS = 15 * 60 * 1000;
    var t0 = Date.now();
    var pollStatus = function () {
      if (Date.now() - t0 > BRIEF_MAX_MS) { finish(''); return; }
      api('api/bridge/prime/brief/status?task_id=' + encodeURIComponent(t.id))
        .then(function (s) {
          if (s && s.pending) { setTimeout(pollStatus, BRIEF_POLL_MS); return; }
          finish((s && s.reply) || '');
        })
        .catch(function () { setTimeout(pollStatus, BRIEF_POLL_MS); });
    };
    fetch(new URL('api/bridge/prime/brief', document.baseURI || location.href).href, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': cfg.csrfToken || '' },
      body: JSON.stringify({ task_id: t.id })
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.pending) { setTimeout(pollStatus, BRIEF_POLL_MS); return; }
        finish((d && d.reply) || '');
      })
      .catch(function () { finish(''); });
  }
  var _cbPollTimer = null;
  function syncStarActiveAgents(tasks) {
    if (!window.cbStar || !window.cbStar.setAgentActive) return;
    var next = {};
    (tasks || []).forEach(function (t) {
      if (t && t.status === 'in_corso') {
        var key = (t.agent || t.task_type || '').trim();
        var label = ((t.agent || '') + ' ' + (t.task_type || '')).trim();
        if (key) next[key] = label || key;
      }
    });
    Object.keys(next).forEach(function (name) {
      if (!_cbActiveAgents[name]) window.cbStar.setAgentActive(next[name], true);
    });
    Object.keys(_cbActiveAgents).forEach(function (name) {
      if (!next[name]) window.cbStar.setAgentActive(_cbActiveAgents[name], false);
    });
    _cbActiveAgents = next;
  }
  function pollTasks() {
    api('api/bridge/tasks').then(function (d) {
      var tasks = d && d.tasks ? d.tasks : [];
      if (tasks.length) tasks.forEach(renderTask);
      syncStarActiveAgents(tasks);
    }).catch(function () {});
  }
  function startTaskPolling() { if (!_cbPollTimer) _cbPollTimer = setInterval(pollTasks, 3000); }
  var _cbAgentsTimer = null;
  function pollAgents() {
    api('api/bridge/agents').then(renderAgents).catch(function () {});
  }
  function startAgentsPolling() { if (!_cbAgentsTimer) _cbAgentsTimer = setInterval(pollAgents, 30000); }
  var _cbWorklogTimer = null;
  function pollWorklog() {
    api('api/bridge/worklog?days=14').then(renderWorklog).catch(function () {});
  }
  function startWorklogPolling() { if (!_cbWorklogTimer) _cbWorklogTimer = setInterval(pollWorklog, 60000); }
  var _cbQuotaTimer = null;
  function startQuotaPolling() { if (!_cbQuotaTimer) _cbQuotaTimer = setInterval(pollTokenQuota, 120000); }
  // Strip markdown so the TTS doesn't read "asterisco asterisco" etc.
  function cleanForSpeech(t) {
    return String(t || '')
      .replace(/```[\s\S]*?```/g, ' ')
      .replace(/`([^`]+)`/g, '$1')
      .replace(/\*\*([^*]+)\*\*/g, '$1')
      .replace(/\*([^*]+)\*/g, '$1')
      .replace(/__([^_]+)__/g, '$1')
      .replace(/(^|\s)_([^_]+)_(\s|$)/g, '$1$2$3')
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
      .replace(/^\s{0,3}#{1,6}\s+/gm, '')
      .replace(/^\s*[-*+]\s+/gm, '')
      .replace(/^\s*\d+[.)]\s+/gm, '')
      .replace(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE00}-\u{FE0F}\u{200D}]/gu, ' ')
      .replace(/[•‣◦▪·–—→↑↓←»«]+/g, ' ')
      .replace(/[*_`#>~|]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }
  function createPrimeTurnUi(root) {
    var closed = false;
    function statusNodes() {
      if (!root || typeof root.querySelectorAll !== 'function') return [];
      return Array.prototype.slice.call(root.querySelectorAll('.cb-prime-status'));
    }
    function dedupeStatusNodes() {
      var nodes = statusNodes();
      nodes.slice(1).forEach(function (node) { if (node && node.remove) node.remove(); });
      return nodes.length ? [nodes[0]] : [];
    }
    return {
      isClosed: function () { return closed; },
      updateStatus: function (label, hasReply) {
        if (closed) return false;
        var nodes = dedupeStatusNodes();
        var status = nodes[0] || null;
        var bubble = root && root.querySelector ? root.querySelector('.cb-bubble') : null;
        if (hasReply) {
          if (status) { status.textContent = label; status.hidden = false; }
        } else {
          if (status) status.hidden = true;
          if (bubble) bubble.textContent = label;
        }
        return true;
      },
      close: function () {
        if (closed) return false;
        closed = true;
        statusNodes().forEach(function (node) { if (node && node.remove) node.remove(); });
        if (root && root.setAttribute) root.setAttribute('data-prime-settled', 'true');
        return true;
      }
    };
  }
  function pendingBubble() {
    var log = $('cbLog'); if (!log) return null;
    var m = el('div', 'cb-msg cb-from-prime');
    m.innerHTML = '<div class="cb-who">hermes prime</div><div class="cb-bubble" style="color:var(--cb-muted);font-style:italic">sto ragionando&#8230;</div><div class="cb-prime-status" hidden></div><div class="cb-msg-foot" hidden></div>';
    var _sb = nearBottom(log); log.appendChild(m); if (_sb) log.scrollTop = log.scrollHeight; return m;
  }
  function streamPrimeResponse(response, handlers) {
    if (!response.ok) throw new Error('bridge ' + response.status);
    var contentType = response.headers.get('content-type') || '';
    if (contentType.indexOf('text/event-stream') === -1 || !response.body) {
      throw new Error('bridge did not return an SSE stream');
    }
    var reader = response.body.getReader();
    var decoder = new TextDecoder();
    var buffer = '';
    function dispatch(frame) {
      var event = 'message', data = '';
      frame.split(/\r?\n/).forEach(function (line) {
        if (line.indexOf('event:') === 0) event = line.slice(6).trim();
        else if (line.indexOf('data:') === 0) data += line.slice(5).trimStart();
      });
      if (!data) return;
      var payload = JSON.parse(data);
      if (handlers[event]) handlers[event](payload);
    }
    function pump() {
      return reader.read().then(function (chunk) {
        buffer += decoder.decode(chunk.value || new Uint8Array(), { stream: !chunk.done });
        var frames = buffer.split(/\r?\n\r?\n/);
        buffer = frames.pop() || '';
        frames.forEach(dispatch);
        if (chunk.done) {
          if (buffer.trim()) dispatch(buffer);
          return;
        }
        return pump();
      });
    }
    return pump();
  }
  function _ensureCtx() {
    if (!_ctx) {
      var AC = window.AudioContext || window.webkitAudioContext; if (!AC) return null;
      _ctx = new AC(); _an = _ctx.createAnalyser(); _an.fftSize = 512; _an.connect(_ctx.destination);
    }
    return _ctx;
  }
  // Coda vocale sequenziale: Hermes Prime puo' iniziare a leggere la prima frase
  // appena e' pronta (durante lo streaming) e accodare le successive senza
  // tagliarsi. _ttsActive resta vero per tutta la coda, cosi' l'anti-eco del
  // microfono tiene in pausa la registrazione finche' la voce non finisce.
  var _speakQueue = [];
  function stopSpeak() {
    _speakQueue = [];
    try { if (_cur) { _cur.pause(); _cur = null; } } catch (e) {}
    cancelAnimationFrame(_raf);
    _ttsActive = false;
  }
  function enqueueSpeak(text) {
    text = cleanForSpeech(text); if (!text || !voiceOn) return;
    _speakQueue.push(text);
    if (!_ttsActive) { _ttsActive = true; _drainSpeak(); }
  }
  function _drainSpeak() {
    if (!voiceOn || !_speakQueue.length) { _ttsActive = false; setOrb('idle', 0); return; }
    _playClip(_speakQueue.shift(), _drainSpeak);
  }
  // Avvio one-shot (risposte non in streaming): azzera la coda e parla subito.
  function speak(text) {
    text = String(text || '').trim(); if (!text || !voiceOn) return;
    stopSpeak();
    enqueueSpeak(text);
  }
  function _playClip(text, onDone) {
    setOrb('thinking', 0);
    var cfg = window.__HERMES_CONFIG__ || {};
    fetch(new URL('api/tts', document.baseURI || location.href).href, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': cfg.csrfToken || '' },
      body: JSON.stringify({ text: String(text).slice(0, 4800), voice: 'it-IT-ElsaNeural' })
    }).then(function (r) { if (!r.ok) throw new Error('tts ' + r.status); return r.blob(); })
      .then(function (blob) {
        if (!voiceOn) { onDone(); return; }
        var ctx = _ensureCtx(); var url = URL.createObjectURL(blob);
        var audio = new Audio(url); _cur = audio;
        if (ctx) {
          try { ctx.resume(); } catch (e) {}
          var src = ctx.createMediaElementSource(audio); src.connect(_an);
          var buf = new Uint8Array(_an.frequencyBinCount);
          (function pump() {
            if (_cur !== audio) return;
            _an.getByteTimeDomainData(buf);
            var s = 0; for (var i = 0; i < buf.length; i++) { var v = (buf[i] - 128) / 128; s += v * v; }
            setOrb('speaking', Math.min(1, Math.sqrt(s / buf.length) * 2.3));
            _raf = requestAnimationFrame(pump);
          })();
        }
        setOrb('speaking', 0);
        audio.onended = function () { cancelAnimationFrame(_raf); URL.revokeObjectURL(url); if (_cur === audio) _cur = null; onDone(); };
        return audio.play();
      }).catch(function () { if (_cur) { try { _cur.pause(); } catch (e) {} _cur = null; } onDone(); }); // TTS non disponibile -> passa oltre in silenzio
  }
  // Estrae dal testo accumulato le frasi gia' complete (fino all'ultima
  // punteggiatura di chiusura), cosi' la voce parte appena c'e' una frase pronta
  // invece di aspettare la risposta intera. spokenLen avanza per non ripetere.
  function flushSpokenSentences(full, state, force) {
    if (!userEngaged || !voiceOn) { state.spokenLen = full.length; return; }
    var pending = full.slice(state.spokenLen);
    if (force) {
      var tail = pending.trim();
      if (tail) enqueueSpeak(tail);
      state.spokenLen = full.length;
      return;
    }
    var re = /[.!?…](["')\]]?)(\s|$)|\n/g, lastEnd = -1, m;
    while ((m = re.exec(pending)) !== null) lastEnd = re.lastIndex;
    if (lastEnd <= 0) return;
    var chunk = pending.slice(0, lastEnd).trim();
    if (chunk) enqueueSpeak(chunk);
    state.spokenLen += lastEnd;
  }
  // Microfono di Hermes Prime: registra con MediaRecorder e trascrive con il motore
  // STT locale della WebUI (POST api/transcribe). Niente cloud Google: funziona in
  // Brave/Chromium. Modalita' dialogo continuo: il mic resta acceso, un analyser
  // segmenta le frasi sulla pausa, ogni segmento viene trascritto e inviato, poi
  // si riparte ad ascoltare. Durante il TTS la registrazione e' in pausa (anti-eco).
  function toggleListen() {
    userEngaged = true;
    if (_listening) { stopListen(); return; }
    if (!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder)) {
      sysNote('Microfono non disponibile in questo browser.'); return;
    }
    _listening = true;
    var mic = $('cbMic'); if (mic) mic.classList.add('cb-on');
    setOrb('listening', 0.25);
    startMic();
  }
  function stopListen() {
    _listening = false;
    if (_micVadRaf) { cancelAnimationFrame(_micVadRaf); _micVadRaf = null; }
    if (_micRec && _micRec.state !== 'inactive') { try { _micRec.onstop = null; _micRec.stop(); } catch (e) {} }
    _micRec = null; _micChunks = [];
    if (_micStream) { try { _micStream.getTracks().forEach(function (t) { t.stop(); }); } catch (e) {} _micStream = null; }
    var mic = $('cbMic'); if (mic) mic.classList.remove('cb-on');
    setOrb('idle', 0);
  }
  function startMic() {
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      if (!_listening) { stream.getTracks().forEach(function (t) { t.stop(); }); return; }
      _micStream = stream;
      var ctx = _ensureCtx();
      var analyser = null, data = null;
      if (ctx) {
        try { ctx.resume(); } catch (e) {}
        analyser = ctx.createAnalyser(); analyser.fftSize = 512;
        ctx.createMediaStreamSource(stream).connect(analyser); // non collegato a destination: niente feedback
        data = new Uint8Array(analyser.frequencyBinCount);
      }
      var SPEAK = 0.018, SILENCE_MS = 1500, MIN_SPEAK_MS = 350;
      var spoke = false, speakStart = 0, silenceStart = 0;
      var newRecorder = function () {
        var types = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg'];
        var mime = types.filter(function (t) { return window.MediaRecorder.isTypeSupported && window.MediaRecorder.isTypeSupported(t); })[0] || '';
        var rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
        _micChunks = [];
        rec.ondataavailable = function (e) { if (e.data && e.data.size) _micChunks.push(e.data); };
        rec.onstop = function () {
          var blob = new Blob(_micChunks, { type: (_micChunks[0] && _micChunks[0].type) || 'audio/webm' });
          _micChunks = [];
          if (spoke && blob.size > 1200) transcribeAndSubmit(blob);
          spoke = false;
          if (_listening) { _micRec = newRecorder(); _micRec.start(); }
        };
        return rec;
      };
      _micRec = newRecorder(); _micRec.start();
      var loop = function () {
        if (!_listening) return;
        _micVadRaf = requestAnimationFrame(loop);
        // Durante il TTS (anche tra una frase e l'altra della coda) metti in pausa
        // e azzera, cosi' Hermes non trascrive se stesso
        if (_cur || _ttsActive) {
          if (_micRec && _micRec.state === 'recording') { try { _micRec.pause(); } catch (e) {} }
          spoke = false; silenceStart = 0; return;
        }
        if (_micRec && _micRec.state === 'paused') { try { _micRec.resume(); } catch (e) {} }
        if (!analyser) return;
        analyser.getByteTimeDomainData(data);
        var s = 0; for (var i = 0; i < data.length; i++) { var v = (data[i] - 128) / 128; s += v * v; }
        var rms = Math.sqrt(s / data.length);
        var now = Date.now();
        if (rms > SPEAK) {
          if (!spoke) { spoke = true; speakStart = now; }
          silenceStart = 0;
          setOrb('listening', Math.min(0.6, rms * 4));
        } else if (spoke && now - speakStart > MIN_SPEAK_MS) {
          if (!silenceStart) silenceStart = now;
          else if (now - silenceStart > SILENCE_MS && _micRec && _micRec.state === 'recording') {
            try { _micRec.stop(); } catch (e) {} // chiude il segmento -> onstop trascrive
          }
        }
      };
      _micVadRaf = requestAnimationFrame(loop);
    }).catch(function (err) {
      _listening = false;
      var mic = $('cbMic'); if (mic) mic.classList.remove('cb-on');
      setOrb('idle', 0);
      var name = err && err.name;
      sysNote('Microfono: ' + (name === 'NotAllowedError' ? 'permesso negato dal browser' : (name === 'NotFoundError' ? 'nessun microfono rilevato' : 'impossibile avviare')) + '.');
    });
  }
  function transcribeAndSubmit(blob) {
    if (_micBusy) return; _micBusy = true;
    var ext = (blob.type && blob.type.indexOf('ogg') >= 0) ? 'ogg' : 'webm';
    var form = new FormData();
    form.append('file', new File([blob], 'voice-input.' + ext, { type: blob.type || ('audio/' + ext) }));
    fetch(new URL('api/transcribe', document.baseURI || location.href).href, { method: 'POST', credentials: 'same-origin', body: form })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (!res.ok) { sysNote('Trascrizione: ' + (res.d.error || 'errore lato server') + '.'); return; }
        var text = (res.d.transcript || '').trim(); if (!text) return;
        var inp = $('cbInput'); if (inp) { inp.value = text; if (window.__cbAutoGrow) window.__cbAutoGrow(); }
        var f = $('cbForm'); if (f && f.requestSubmit) f.requestSubmit(); else onPrimeSubmit({ preventDefault: function () {} });
      })
      .catch(function () { sysNote('Trascrizione non riuscita (motore STT locale raggiungibile?).'); })
      .then(function () { _micBusy = false; });
  }
  function toggleVoice() {
    voiceOn = !voiceOn;
    var b = $('cbVoice'); if (b) b.classList.toggle('cb-on', voiceOn);
    refreshBrainState();
    if (!voiceOn) { stopSpeak(); setOrb('idle', 0); }
  }

  function primeSay(who, text, thumbs) {
    var log = $('cbLog'); if (!log) return;
    var m = el('div', 'cb-msg ' + (who === 'user' ? 'cb-from-user' : 'cb-from-prime'));
    var imgs = '';
    if (thumbs && thumbs.length) {
      imgs = thumbs.map(function (u) { return '<img src="' + u + '" alt="">'; }).join('');
    }
    var label;
    if (text && text.trim()) {
      label = (who === 'prime' && typeof window.renderMd === 'function') ? window.renderMd(text) : esc(text);
    } else {
      label = imgs ? '<em style="opacity:.6">foto allegata</em>' : '';
    }
    m.innerHTML = '<div class="cb-who">' + (who === 'user' ? 'tu' : 'hermes prime') + '</div>' +
      '<div class="cb-bubble">' + label + imgs + '</div>' +
      (who === 'prime' ? '<div class="cb-msg-foot" hidden></div>' : '');
    var _sb = nearBottom(log); log.appendChild(m); if (_sb) log.scrollTop = log.scrollHeight;
    if (who === 'prime' && userEngaged) speak(text);
    return m;
  }

  var _cbHistoryLoaded = false;
  function normalizeAttentionPending(payload) {
    if (!payload) return null;
    return payload.pending || payload;
  }
  function appendAttentionCard(kind, pending) {
    var log = $('cbLog'); if (!log || !pending) return null;
    var card = el('div', 'cb-attention-card cb-' + kind + '-card');
    var label = kind === 'approval' ? 'approval richiesta' : 'chiarimento richiesto';
    var title = kind === 'approval'
      ? (pending.description || 'Conferma azione richiesta')
      : (pending.question || 'Serve una scelta');
    var body = kind === 'approval'
      ? (pending.command || pending.pattern_key || '')
      : ((pending.choices_offered || []).join('\n') || '');
    card.innerHTML =
      '<div class="cb-attention-k">' + esc(label) + '</div>' +
      '<div class="cb-attention-title">' + esc(title) + '</div>' +
      (body ? '<div class="cb-attention-body">' + esc(body) + '</div>' : '') +
      '<div class="cb-attention-actions"></div>';
    var _sb = nearBottom(log); log.appendChild(card); if (_sb) log.scrollTop = log.scrollHeight;
    return card;
  }
  function renderBridgeApprovalCard(payload) {
    var pending = normalizeAttentionPending(payload);
    if (!pending) { sysNote('Approval bridge risolta o scaduta.'); return; }
    var card = appendAttentionCard('approval', pending); if (!card) return;
    var actions = card.querySelector('.cb-attention-actions');
    [
      ['once', 'Allow once', ''],
      ['session', 'Allow session', ''],
      ['deny', 'Deny', 'deny']
    ].forEach(function (item) {
      var b = el('button', item[2]); b.type = 'button'; b.textContent = item[1];
      b.addEventListener('click', function () {
        actions.querySelectorAll('button').forEach(function (btn) { btn.disabled = true; });
        apiPost('/api/approval/respond', {
          session_id: 'hermes-prime',
          approval_id: pending.approval_id || '',
          choice: item[0]
        }).then(function () {
          card.querySelector('.cb-attention-k').textContent = 'approval inviata';
        }).catch(function (e) {
          sysNote('Approval: ' + (e && e.message ? e.message : 'errore risposta'));
          actions.querySelectorAll('button').forEach(function (btn) { btn.disabled = false; });
        });
      });
      actions.appendChild(b);
    });
  }
  function bridgeQuestionId(q, idx) {
    return String((q && q.id) || ('question_' + (idx + 1)));
  }
  function bridgeQuestionKey(q, idx) {
    return String((q && (q.question || q.text || q.prompt || q.id || q.header)) || ('Domanda ' + (idx + 1)));
  }
  function bridgeNormalizeOptions(q) {
    var raw = Array.isArray(q && q.options) ? q.options : (Array.isArray(q && q.choices) ? q.choices : []);
    return raw.map(function (option) {
      if (option && typeof option === 'object') {
        return {
          label: String(option.label || option.value || option.text || '').trim(),
          description: String(option.description || option.detail || option.help || '').trim()
        };
      }
      return { label: String(option || '').trim(), description: '' };
    }).filter(function (option) { return option.label; });
  }
  function bridgeStructuredEcho(response) {
    var answers = response && response.answers;
    if (!answers || typeof answers !== 'object') return '';
    return Object.keys(answers).map(function (question) {
      var answer = answers[question];
      var rendered = Array.isArray(answer) ? answer.join(', ') : String(answer || '');
      return question + ': ' + rendered;
    }).join('\n');
  }
  function bridgeResponseHasAnswer(response) {
    var answers = response && response.answers;
    if (!answers || typeof answers !== 'object') return false;
    return Object.keys(answers).some(function (key) {
      var value = answers[key];
      if (Array.isArray(value)) return value.some(function (item) { return String(item || '').trim(); });
      return String(value || '').trim();
    });
  }
  function renderBridgeStructuredQuestions(card, questions, readOnly) {
    var actions = card.querySelector('.cb-attention-actions');
    var selections = {};
    questions.forEach(function (q, idx) {
      var qid = bridgeQuestionId(q, idx);
      var block = el('div', 'cb-question-block');
      block.setAttribute('data-question-id', qid);
      var title = el('div', 'cb-question-title');
      var header = String((q && q.header) || '').trim();
      if (header) title.appendChild(el('span', 'cb-question-chip', esc(header)));
      title.appendChild(el('span', '', esc(bridgeQuestionKey(q, idx))));
      block.appendChild(title);
      bridgeNormalizeOptions(q).forEach(function (option) {
        var b = el('button', 'cb-choice');
        b.type = 'button';
        b.innerHTML = '<span class="cb-option-label">' + esc(option.label) + '</span>' +
          (option.description ? '<span class="cb-option-description">' + esc(option.description) + '</span>' : '');
        if (!readOnly) {
          b.addEventListener('click', function () {
            if (q && q.multiSelect) {
              var arr = Array.isArray(selections[qid]) ? selections[qid].slice() : [];
              var pos = arr.indexOf(option.label);
              if (pos >= 0) arr.splice(pos, 1); else arr.push(option.label);
              selections[qid] = arr;
              b.classList.toggle('selected', pos < 0);
            } else {
              selections[qid] = option.label;
              block.querySelectorAll('.cb-choice').forEach(function (btn) { btn.classList.remove('selected'); });
              b.classList.add('selected');
            }
          });
        }
        block.appendChild(b);
      });
      var row = el('label', 'cb-other-row');
      row.appendChild(el('span', '', 'Altro'));
      var input = el('input', 'cb-attention-input cb-other-input');
      input.type = 'text';
      input.placeholder = 'Risposta libera';
      input.setAttribute('data-question-id', qid);
      if (!readOnly) {
        input.addEventListener('keydown', function (e) {
          if (e.key === 'Enter') {
            e.preventDefault();
            var send = card.querySelector('.cb-clarify-send');
            if (send) send.click();
          }
        });
      }
      row.appendChild(input);
      block.appendChild(row);
      if (q && q.multiSelect) block.appendChild(el('div', 'cb-question-hint', 'Selezione multipla consentita.'));
      actions.appendChild(block);
    });
    return function collect() {
      var answers = {};
      questions.forEach(function (q, idx) {
        var qid = bridgeQuestionId(q, idx);
        var key = bridgeQuestionKey(q, idx);
        var input = null;
        card.querySelectorAll('.cb-other-input').forEach(function (candidate) {
          if (!input && candidate.getAttribute('data-question-id') === qid) input = candidate;
        });
        var other = input ? String(input.value || '').trim() : '';
        var selected = selections[qid];
        if (q && q.multiSelect) {
          var values = Array.isArray(selected) ? selected.slice() : (selected ? [selected] : []);
          if (other) values.push(other);
          answers[key] = values;
        } else {
          answers[key] = other || (Array.isArray(selected) ? (selected[0] || '') : (selected || ''));
        }
      });
      return { answers: answers };
    };
  }
  function renderBridgeClarifyCard(payload) {
    var options = arguments[1] || {};
    var pending = normalizeAttentionPending(payload);
    if (!pending) { sysNote('Clarify bridge risolto o scaduto.'); return; }
    var card = appendAttentionCard('clarify', pending); if (!card) return;
    if (options.readOnly) card.classList.add('cb-readonly');
    var actions = card.querySelector('.cb-attention-actions');
    var selected = [];
    var questions = Array.isArray(pending.questions) ? pending.questions : [];
    var collectStructured = questions.length ? renderBridgeStructuredQuestions(card, questions, !!options.readOnly) : null;
    if (!questions.length) {
      var choices = pending.choices_offered || [];
      choices.filter(Boolean).forEach(function (choice) {
        var b = el('button', 'cb-choice'); b.type = 'button'; b.textContent = choice;
        if (!options.readOnly) {
          b.addEventListener('click', function () {
            selected = [choice];
            actions.querySelectorAll('.cb-choice').forEach(function (btn) { btn.classList.remove('selected'); });
            b.classList.add('selected');
          });
        }
        actions.appendChild(b);
      });
    }
    var input = null;
    if (!questions.length) {
      input = el('input', 'cb-attention-input');
      input.type = 'text';
      input.placeholder = 'Risposta libera';
      card.appendChild(input);
    }
    if (options.readOnly) return card;
    var send = el('button', 'cb-clarify-send'); send.type = 'button'; send.textContent = 'Send';
    send.addEventListener('click', function () {
      var answer = collectStructured ? collectStructured() : (input.value.trim() || selected.join(', '));
      if (collectStructured ? !bridgeResponseHasAnswer(answer) : !answer) {
        var focusTarget = collectStructured ? card.querySelector('.cb-other-input') : input;
        if (focusTarget) focusTarget.focus();
        return;
      }
      send.disabled = true;
      apiPost('/api/clarify/respond', {
        session_id: 'hermes-prime',
        clarify_id: pending.clarify_id || '',
        response: answer
      }).then(function () {
        card.querySelector('.cb-attention-k').textContent = 'chiarimento inviato';
        primeSay('user', collectStructured ? bridgeStructuredEcho(answer) : answer);
      }).catch(function (e) {
        send.disabled = false;
        sysNote('Clarify: ' + (e && e.message ? e.message : 'errore risposta'));
      });
    });
    actions.appendChild(send);
  }
  function loadPrimeHistory() {
    if (_cbHistoryLoaded) return Promise.resolve();
    _cbHistoryLoaded = true;
    return api('/api/bridge/prime/history').then(function (data) {
      var log = $('cbLog'); if (!log) return;
      var pendingClarify = data.pending_clarify || null;
      var pendingClarifyId = pendingClarify && pendingClarify.clarify_id;
      (data.messages || []).forEach(function (m, idx) {
        var node = null;
        if (
          m && m._bridge_clarify_event === 'request' &&
          m._bridge_clarify_payload &&
          m._bridge_clarify_id !== pendingClarifyId
        ) {
          node = renderBridgeClarifyCard({ pending: m._bridge_clarify_payload }, { readOnly: true });
          if (node) node.setAttribute('data-cb-msg-index', String(idx));
          return;
        }
        node = primeSay(m.role === 'user' ? 'user' : 'prime', m.content || '');
        if (node) node.setAttribute('data-cb-msg-index', String(idx));
      });
      if (pendingClarify) renderBridgeClarifyCard({ pending: pendingClarify });
      pollTasks();
      var pending = data.pending_turn;
      if (pending && pending.partial_output) {
        var node = primeSay('prime', pending.partial_output || '');
        if (node) node.classList.add('cb-recovered');
        if (node) startPrimeLivePolling(node.querySelector('.cb-bubble') || node);
      }
      (data.tool_events || []).forEach(function (ev) {
        if (ev && ev.tool) renderToolCard(ev.tool, ev.summary || '');
      });
    }).catch(function () {});
  }
  /* ── Allegati foto per Hermes Prime ─────────────────────────────────────── */
  var pendingAttachments = []; // { name, path, url, type, size }

  function humanSize(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(0) + ' KB';
    return (n / (1024 * 1024)).toFixed(1) + ' MB';
  }
  function typeLabel(a) {
    if (!a.path) return 'caricamento…';
    var sub = String(a.type || '').split('/')[1] || 'immagine';
    var label = sub.toUpperCase();
    if (a.size) label += ' · ' + humanSize(a.size);
    return label;
  }

  function renderAttachments() {
    var bar = $('cbAtt'); if (!bar) return;
    bar.innerHTML = '';
    pendingAttachments.forEach(function (a, i) {
      var chip = el('div', 'cb-chip' + (a.path ? '' : ' cb-chip-up'));
      var thumb = a.url ? '<img src="' + a.url + '" alt="">' : '';
      chip.innerHTML = thumb +
        '<div class="cb-chip-info">' +
          '<span class="cb-chip-name">' + esc(a.name) + '</span>' +
          '<span class="cb-chip-type">' + esc(typeLabel(a)) + '</span>' +
        '</div>' +
        '<button type="button" aria-label="Rimuovi">×</button>';
      chip.querySelector('button').addEventListener('click', function () {
        pendingAttachments.splice(i, 1);
        renderAttachments();
      });
      bar.appendChild(chip);
    });
  }

  function uploadAttachment(file) {
    if (!file || !/^image\//.test(file.type || '')) {
      sysNote('Posso allegare solo immagini.');
      return;
    }
    // Le immagini incollate (Ctrl+V) spesso arrivano senza nome/estensione: il
    // server deduce il MIME dall'estensione, quindi gliene diamo una sensata.
    var name = file.name || '';
    if (!/\.[a-z0-9]{2,5}$/i.test(name)) {
      var ext = (String(file.type || '').split('/')[1] || 'png').replace('jpeg', 'jpg');
      name = (name || ('incolla-' + Date.now())) + '.' + ext;
    }
    var entry = { name: name, path: null, url: null, type: file.type || '', size: file.size || 0 };
    try { entry.url = URL.createObjectURL(file); } catch (_e) {}
    pendingAttachments.push(entry);
    renderAttachments();
    var form = new FormData();
    form.append('file', file, name);
    var cfg = window.__HERMES_CONFIG__ || {};
    fetch(new URL('api/bridge/prime/upload', document.baseURI || location.href).href, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'X-CSRF-Token': cfg.csrfToken || '' },
      body: form
    }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (!res.ok || !res.d || !res.d.path) {
          var idx = pendingAttachments.indexOf(entry);
          if (idx >= 0) pendingAttachments.splice(idx, 1);
          renderAttachments();
          sysNote((res.d && res.d.error) ? ('Allegato: ' + res.d.error) : 'Non sono riuscito a caricare l’immagine.');
          return;
        }
        entry.path = res.d.path;
        entry.name = res.d.filename || entry.name;
        entry.type = res.d.mime || entry.type;
        if (res.d.size) entry.size = res.d.size;
        renderAttachments();
      })
      .catch(function () {
        var idx = pendingAttachments.indexOf(entry);
        if (idx >= 0) pendingAttachments.splice(idx, 1);
        renderAttachments();
        sysNote('Non sono riuscito a caricare l’immagine.');
      });
  }

  function onPrimePaste(e) {
    var dt = e && e.clipboardData;
    if (!dt) return;
    var items = dt.items ? Array.prototype.slice.call(dt.items) : [];
    var imgs = items.filter(function (it) { return it.kind === 'file' && /^image\//.test(it.type || ''); });
    // Fallback: alcuni browser espongono direttamente dt.files
    if (!imgs.length && dt.files && dt.files.length) {
      var files = Array.prototype.slice.call(dt.files).filter(function (f) { return /^image\//.test(f.type || ''); });
      if (files.length) { e.preventDefault(); files.forEach(uploadAttachment); }
      return;
    }
    if (!imgs.length) return; // nessuna immagine: lascia incollare il testo
    e.preventDefault();
    imgs.forEach(function (it) {
      var f = it.getAsFile();
      if (f) uploadAttachment(f);
    });
  }

  function onPrimeSubmit(e) {
    if (e && e.preventDefault) e.preventDefault();
    userEngaged = true;
    var inp = $('cbInput'); if (!inp) return;
    var v = inp.value.trim();
    if (handlePrimeSlashCommand(v)) {
      inp.value = '';
      inp.style.height = 'auto';
      if (window.__cbAutoGrow) window.__cbAutoGrow();
      return;
    }
    var ready = pendingAttachments.filter(function (a) { return !!a.path; });
    var stillUp = pendingAttachments.some(function (a) { return !a.path; });
    if (!v && !ready.length) {
      if (stillUp) sysNote('Aspetta che l’immagine finisca di caricare…');
      return;
    }
    if (stillUp) { sysNote('Aspetta che l’immagine finisca di caricare…'); return; }
    inp.value = '';
    inp.style.height = 'auto';
    if (window.__cbAutoGrow) window.__cbAutoGrow();
    var attachments = ready.map(function (a) { return { name: a.name, path: a.path }; });
    var thumbs = ready.map(function (a) { return a.url; }).filter(Boolean);
    pendingAttachments = [];
    renderAttachments();
    primeSay('user', v, thumbs);
    stopSpeak(); // un nuovo turno interrompe la voce precedente
    setOrb('thinking', 0);
    var ph = pendingBubble();
    var bubble = ph ? ph.querySelector('.cb-bubble') : null;
    var turnUi = createPrimeTurnUi(ph);
    var footLine = ph ? ph.querySelector('.cb-msg-foot') : null;
    var liveUsage = $('cbLiveUsage');
    if (liveUsage) { liveUsage.hidden = true; liveUsage.textContent = ''; }
    var reply = '', settled = false, speech = { spokenLen: 0 }, finalUsage = null;
    setPrimeStreaming(true, null);
    var showUsage = function (usage) {
      if (!_hasBridgeUsage(usage)) return;
      finalUsage = usage;
      var liveText = _formatLiveUsage(usage);
      if (liveUsage && liveText) { liveUsage.textContent = liveText; liveUsage.hidden = false; }
      if (footLine && window._showTokenUsage === true) {
        var badge = _formatAssistantUsageBadge(usage);
        if (badge) { footLine.textContent = badge; footLine.hidden = false; }
      }
    };
    var showStatus = function (state, tool) {
      if (settled) return;
      var labels = {
        started: 'in corso',
        queued: 'in coda\u2026',
        reasoning: 'sto ragionando\u2026',
        tool: 'uso lo strumento ' + (tool || '') + '\u2026',
        responding: 'sta scrivendo\u2026',
        done: '\u2713 completato',
        cancelled: 'interrotto'
      };
      var label = labels[state]; if (!label) return;
      turnUi.updateStatus(label, !!reply);
      if (state === 'done' && bubble && !reply) bubble.textContent = 'Ricevuto.';
    };
    var finish = function (label) {
      if (settled) return;
      settled = true;
      try {
        if (bubble && reply) { bubble.removeAttribute('style'); renderRich(bubble, reply); }
        if (finalUsage) showUsage(finalUsage);
        flushSpokenSentences(reply, speech, true);
      } catch (e) {
        console.error('[Hermes Prime] Finalizzazione risposta fallita; chiudo comunque lo stato in corso.', e);
        // Ultima rete di sicurezza: il testo streamato deve restare visibile anche
        // se una fase accessoria (markdown, usage o TTS) fallisce.
        try { if (bubble && reply) bubble.textContent = reply; } catch (fallbackError) {
          console.error('[Hermes Prime] Anche il fallback a testo grezzo e fallito.', fallbackError);
        }
      } finally {
        turnUi.close();
        if (liveUsage) liveUsage.hidden = true;
        setPrimeStreaming(false, null);
        if (!_ttsActive) setOrb('idle', 0);
      }
    };
    var showToken = function (text) {
      if (settled) return;
      text = String(text || ''); if (!text) return;
      var log = $('cbLog'); var _sb = nearBottom(log);
      reply += text;
      if (bubble) {
        bubble.removeAttribute('style');
        bubble.textContent = reply;
      }
      turnUi.updateStatus('sta scrivendo\u2026', true);
      flushSpokenSentences(reply, speech, false); // legge le frasi gia' complete
      if (_sb && log) log.scrollTop = log.scrollHeight;
    };
    var fail = function (text) {
      if (settled) return;
      settled = true;
      try {
        if (!reply && ph && ph.parentNode) ph.parentNode.removeChild(ph);
        turnUi.close();
        ph = null; bubble = null;
        sysNote(text);
      } finally {
        if (liveUsage) liveUsage.hidden = true;
        setPrimeStreaming(false, null);
        setOrb('idle', 0);
      }
    };
    var cfg = window.__HERMES_CONFIG__ || {};
    fetch(new URL('api/bridge/prime', document.baseURI || location.href).href, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': cfg.csrfToken || '' },
      body: JSON.stringify({ message: v, attachments: attachments })
    }).then(function (r) {
      return streamPrimeResponse(r, {
        status: function (d) { showStatus(d && d.state, d && d.tool); },
        token: function (d) { showToken(d && d.text); },
        usage: function (d) { showUsage(d && d.usage); },
        approval: function (d) { renderBridgeApprovalCard(d); },
        clarify: function (d) { renderBridgeClarifyCard(d); },
        todo: function (d) { renderTodos(d); },
        tool: function (d) { if (d && d.tool) renderToolCard(d.tool, d.summary || ''); },
        done: function (d) {
          if (d && d.usage) showUsage(d.usage);
          if (!reply && d && d.reply) showToken(d.reply);
          if (d && d.cancelled) {
            finish('interrotto');
            if (bubble) bubble.classList.add('cb-recovered');
            return;
          }
          if (!reply) showToken('Ricevuto.');
          finish('\u2713 completato');
          if (d && d.delegations && d.delegations.length) d.delegations.forEach(renderTask);
        },
        error: function (d) {
          var base = (d && d.error) ? d.error : 'Hermes Prime non ha completato la risposta.';
          var br = (d && d.branch) ? (' [' + d.branch + ']') : '';
          var hint = (d && d.hint) ? ('\n↳ ' + d.hint) : '';
          fail('Hermes Prime' + br + ': ' + base + hint);
        }
      });
    }).then(function () {
      // EOF e' un terminale valido anche senza evento `done`: proxy/browser e
      // rami backend eccezionali possono chiudere il body dopo gli ultimi token.
      if (!settled && reply) finish('\u2713 risposta ricevuta');
      else if (!settled) fail('La risposta di Hermes Prime si è interrotta prima del completamento.');
    })
      .catch(function (e) {
        // Ramo trasporto: la fetch/stream SSE e' caduta a livello di rete. Diamo
        // comunque il motivo (perche') invece del generico "non riesco a contattare".
        var partial = reply && reply.length;
        var why = (e && e.message) ? (' (' + e.message + ')') : '';
        fail(partial
          ? 'Connessione con Hermes Prime interrotta a meta risposta [transport_cut]' + why + '. Riprova.'
          : 'Non riesco a contattare Hermes Prime (bridge) [transport_cut]' + why + '. Riprova tra poco.');
      });
  }

  /* ── data render ───────────────────────────────────────────────────────── */
  function renderStats(graph) {
    var c = (graph && graph.counts) || {};
    var stats = $('cbStats'); if (!stats) return;
    var defs = [
      { n: c.projects || 0, l: 'projects', cls: 'proj' },
      { n: c.agents || 0, l: 'agents', cls: '' },
      { n: c.notes || 0, l: 'notes', cls: '' },
      { n: c.nodes || 0, l: 'nodes', cls: '' }
    ];
    stats.innerHTML = defs.map(function (d) {
      return '<div class="cb-stat ' + d.cls + '"><div class="n">' + d.n + '</div><div class="l">' + d.l + '</div></div>';
    }).join('');
    var sub = $('cbCoreSub'); if (sub) sub.textContent = (c.nodes || 0) + ' nodi · core online';
  }

  function sparkDots(activity) {
    var a = activity || [];
    var max = Math.max(1, Math.max.apply(null, a.concat([1])));
    return '<span class="cb-spark">' + a.map(function (v) {
      var t = v / max; // 0..1
      var col = v <= 0 ? 'var(--cb-faint)'
        : 'color-mix(in srgb, #ffffff ' + Math.round((1 - t) * 55) + '%, var(--cb-accent))';
      var op = v <= 0 ? .5 : (.5 + t * .5);
      var glow = v > 0 ? 'box-shadow:0 0 6px ' + 'var(--cb-accent-dim)' + ';' : '';
      return '<i style="background:' + col + ';opacity:' + op.toFixed(2) + ';' + glow + '"></i>';
    }).join('') + '</span>';
  }

  // Saved family order (drag&drop). Array of family ids, persisted locally.
  function loadProjOrder() {
    try { return JSON.parse(localStorage.getItem('cbProjOrder') || '[]') || []; } catch (e) { return []; }
  }
  function saveProjOrder(grid) {
    var ids = Array.prototype.map.call(grid.querySelectorAll('.cb-card'), function (c) {
      return c.getAttribute('data-family-id');
    }).filter(Boolean);
    try { localStorage.setItem('cbProjOrder', JSON.stringify(ids)); } catch (e) {}
  }

  // Latest-changes + up-next blocks shared by family cards and the flat fallback.
  function latestHtml(latest) {
    return (latest || []).map(function (l) {
      return '<div class="cb-line-item"><span class="n">' + esc(l.name) + '</span><span class="t">' + esc(l.rel || '') + '</span></div>';
    }).join('') || '<div class="cb-empty">nessuna modifica recente</div>';
  }
  function tasksHtml(tasks) {
    // tasks are {text, path} (family) or plain strings (legacy flat list).
    var html = (tasks || []).map(function (t) {
      var text = (t && t.text != null) ? t.text : t;
      var path = (t && t.path) ? t.path : '';
      var sub = (t && t.subproject_id) ? t.subproject_id : '';
      return '<div class="cb-task cb-task-do" data-text="' + esc(text) + '" data-path="' + esc(path) + '" data-subproject="' + esc(sub) + '" role="button" tabindex="0" title="Segna come fatto">' +
        '<span class="box"></span><span class="cb-task-txt">' + esc(text) + '</span></div>';
    }).join('') || '<div class="cb-empty">nessun task aperto</div>';
    return '<div class="cb-tasks-scroll">' + html + '</div>' +
      '<div class="cb-task-add"><input type="text" class="cb-task-input" placeholder="+ aggiungi task" aria-label="Aggiungi task"></div>';
  }

  function clientsHtml(clients) {
    clients = Array.isArray(clients) ? clients : [];
    if (!clients.length) return '';
    return '<div class="cb-card-block"><div class="cb-block-h">Clienti attivi</div><div class="cb-clients">' +
      clients.map(function (c) {
        return '<div class="cb-client"><strong>' + esc(c.name || '') + '</strong>' +
          '<span>' + esc(c.status || '') + '</span>' +
          (c.waiting_on ? '<em>In attesa: ' + esc(c.waiting_on) + '</em>' : '') +
          '</div>';
      }).join('') + '</div></div>';
  }

  // Wire task toggle/add on a card. defaultPath is where new tasks are written.
  function wireCardTasks(card, defaultPath) {
    Array.prototype.forEach.call(card.querySelectorAll('.cb-task-do'), function (el) {
      var done = function (e) {
        e.stopPropagation();
        if (el.classList.contains('cb-task-done')) return;
        var text = el.getAttribute('data-text');
        var notePath = el.getAttribute('data-path') || defaultPath;
        el.classList.add('cb-task-done');
        apiPost('api/projects/task', { action: 'toggle', note_path: notePath, text: text, done: true })
          .then(function () {
            el.remove();
            var scroll = card.querySelector('.cb-tasks-scroll');
            if (scroll && !scroll.querySelector('.cb-task-do')) scroll.innerHTML = '<div class="cb-empty">nessun task aperto</div>';
            api('api/bridge/worklog?days=14').then(renderWorklog).catch(function () {});
            setTimeout(refresh, 250);
          })
          .catch(function () { el.classList.remove('cb-task-done'); });
      };
      el.addEventListener('click', done);
      el.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); done(e); } });
    });
    var input = card.querySelector('.cb-task-input');
    if (input && defaultPath) {
      input.addEventListener('pointerdown', function (e) { e.stopPropagation(); });
      input.addEventListener('click', function (e) { e.stopPropagation(); });
      input.addEventListener('keydown', function (e) {
        e.stopPropagation();
        if (e.key !== 'Enter') return;
        e.preventDefault();
        var v = input.value.trim();
        if (!v) return;
        input.disabled = true;
        apiPost('api/projects/task', { action: 'add', note_path: defaultPath, text: v })
          .then(function () { input.value = ''; input.disabled = false; refresh(); })
          .catch(function () { input.disabled = false; });
      });
    }
  }


  function filterCardTasks(card, subprojectId) {
    subprojectId = subprojectId || '';
    Array.prototype.forEach.call(card.querySelectorAll('.cb-sub:not(.cb-low-toggle)'), function (chip) {
      chip.classList.toggle('cb-active', (chip.getAttribute('data-filter') || '') === subprojectId);
    });
    Array.prototype.forEach.call(card.querySelectorAll('.cb-task-do'), function (task) {
      var taskSub = task.getAttribute('data-subproject') || '';
      task.hidden = !!subprojectId && taskSub !== subprojectId;
    });
    var scroll = card.querySelector('.cb-tasks-scroll');
    if (!scroll) return;
    var empty = scroll.querySelector('.cb-filter-empty');
    var visible = Array.prototype.some.call(scroll.querySelectorAll('.cb-task-do'), function (task) { return !task.hidden; });
    if (!visible && subprojectId && !empty) {
      scroll.insertAdjacentHTML('beforeend', '<div class="cb-empty cb-filter-empty">nessun task per questo sottoprogetto</div>');
    } else if ((visible || !subprojectId) && empty) {
      empty.remove();
    }
  }

  function renderFamiliesV3(grid, families) {
    var order = loadProjOrder();
    families = families.slice().sort(function (a, b) {
      var ia = order.indexOf(a.id), ib = order.indexOf(b.id);
      if (ia !== ib) return (ia < 0 ? 999 : ia) - (ib < 0 ? 999 : ib);
      return Number(b.priority || 1) - Number(a.priority || 1);
    });
    grid.innerHTML = families.map(function (f) {
      var children = f.children || [];
      var subs = children.length
        ? '<button type="button" class="cb-sub cb-active" data-filter="">Tutti</button>' + children.map(function (c) {
          return '<button type="button" class="cb-sub" data-filter="' + esc(c.id || '') + '" data-path="' + esc(c.path) + '" title="' + esc(c.name) + '">' + esc(c.name) + '</button>';
        }).join('')
        : '<div class="cb-empty">nessuna sotto-parte</div>';
      var low = Number(f.priority || 1) <= 0;
      return '' +
        '<article class="cb-card cb-fam' + (low ? ' cb-low-priority' : '') + '" data-family-id="' + esc(f.id) + '" data-primary="' + esc(f.primary_path || '') + '" tabindex="0" role="group" aria-roledescription="card progetto trascinabile">' +
          '<div class="cb-card-head">' +
            '<div class="cb-card-top" style="flex:1;min-width:0"><span class="cb-card-name">' + esc(f.name) + '</span>' + sparkDots(f.activity) + '</div>' +
            '<span class="cb-drag" title="Tieni premuto e trascina per riordinare" aria-hidden="true">::</span>' +
          '</div>' +
          '<div class="cb-subs">' + subs + '</div>' +
          '<button type="button" class="cb-sub cb-low-toggle">Espandi card</button>' +
          '<div class="cb-card-block"><div class="cb-block-h">Latest changes</div>' + latestHtml(f.latest) + '</div>' +
          clientsHtml(f.clients_active) +
          '<div class="cb-card-block"><div class="cb-block-h">Up next</div>' + tasksHtml(f.tasks) + '</div>' +
        '</article>';
    }).join('');
    Array.prototype.forEach.call(grid.querySelectorAll('.cb-card.cb-fam'), function (card) {
      Array.prototype.forEach.call(card.querySelectorAll('.cb-sub:not(.cb-low-toggle)'), function (chip) {
        chip.addEventListener('pointerdown', function (e) { e.stopPropagation(); });
        chip.addEventListener('click', function (e) {
          e.stopPropagation();
          var filter = chip.getAttribute('data-filter') || '';
          if (filter && chip.classList.contains('cb-active')) filter = '';
          filterCardTasks(card, filter);
        });
      });
      var lowToggle = card.querySelector('.cb-low-toggle');
      if (lowToggle) {
        lowToggle.addEventListener('pointerdown', function (e) { e.stopPropagation(); });
        lowToggle.addEventListener('click', function (e) {
          e.stopPropagation();
          var expanded = card.classList.toggle('cb-expanded');
          lowToggle.textContent = expanded ? 'Comprimi card' : 'Espandi card';
        });
      }
      wireCardTasks(card, card.getAttribute('data-primary'));
    });
    enableDragReorder(grid);
  }

  // Drag-to-reorder the family cards (hold a card and move). Pointer-based so it
  // works with touch + mouse, delegated on the grid so it survives re-renders.
  function enableDragReorder(grid) {
    if (grid.__cbDragBound) return;
    grid.__cbDragBound = true;
    var dragging = null, pid = null, startX = 0, startY = 0, moved = false;
    var IGNORE = '.cb-task-do,.cb-task-input,.cb-sub,a,button';
    grid.addEventListener('pointerdown', function (e) {
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      var card = e.target.closest && e.target.closest('.cb-card.cb-fam');
      if (!card) return;
      if (e.target.closest(IGNORE)) return; // let tasks/chips/input do their thing
      dragging = card; pid = e.pointerId; startX = e.clientX; startY = e.clientY; moved = false;
    });
    grid.addEventListener('pointermove', function (e) {
      if (!dragging) return;
      if (!moved) {
        if (Math.abs(e.clientX - startX) + Math.abs(e.clientY - startY) < 6) return;
        moved = true;
        dragging.classList.add('cb-dragging');
        grid.classList.add('cb-drag-active');
        try { dragging.setPointerCapture(pid); } catch (err) {}
      }
      e.preventDefault();
      var cards = grid.querySelectorAll('.cb-card.cb-fam');
      for (var i = 0; i < cards.length; i++) {
        var c = cards[i]; if (c === dragging) continue;
        var r = c.getBoundingClientRect();
        if (e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom) {
          var before = (e.clientY - r.top) < (r.height / 2);
          grid.insertBefore(dragging, before ? c : c.nextSibling);
          break;
        }
      }
    });
    var end = function () {
      if (!dragging) return;
      try { dragging.releasePointerCapture(pid); } catch (err) {}
      dragging.classList.remove('cb-dragging');
      grid.classList.remove('cb-drag-active');
      if (moved) saveProjOrder(grid);
      dragging = null; moved = false; pid = null;
    };
    grid.addEventListener('pointerup', end);
    grid.addEventListener('pointercancel', end);
  }

  function renderProjects(data) {
    var grid = $('cbGrid'); if (!grid) return;
    var families = (data && data.families) || [];
    if (families.length) { renderFamiliesV3(grid, families); return; }
    // Fallback: flat per-note cards (legacy backend without families).
    var projects = (data && data.projects) || [];
    if (!projects.length) {
      grid.innerHTML = '<div class="cb-loading">Nessun progetto in 01-Projects.</div>';
      return;
    }
    grid.innerHTML = projects.map(function (p) {
      return '' +
        '<article class="cb-card" data-project-id="' + esc(p.id) + '" data-path="' + esc(p.path) + '" tabindex="0" role="button">' +
          '<div class="cb-card-top"><span class="cb-card-name">' + esc(p.name) + '</span>' + sparkDots(p.activity) + '</div>' +
          '<div class="cb-card-block"><div class="cb-block-h">Latest changes</div>' + latestHtml(p.latest) + '</div>' +
          '<div class="cb-card-block"><div class="cb-block-h">Up next</div>' + tasksHtml(p.tasks) + '</div>' +
        '</article>';
    }).join('');
    Array.prototype.forEach.call(grid.querySelectorAll('.cb-card'), function (card) {
      var path = card.getAttribute('data-path');
      var go = function () { focusProject(card.getAttribute('data-project-id'), path); };
      card.addEventListener('click', go);
      card.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
      wireCardTasks(card, path);
    });
  }

  function renderFamilies(grid, families) {
    var order = loadProjOrder();
    families = families.slice().sort(function (a, b) {
      var ia = order.indexOf(a.id), ib = order.indexOf(b.id);
      return (ia < 0 ? 999 : ia) - (ib < 0 ? 999 : ib);
    });
    grid.innerHTML = families.map(function (f) {
      var subs = (f.children || []).map(function (c) {
        return '<button type="button" class="cb-sub" data-id="' + esc(c.id || '') + '" data-path="' + esc(c.path) + '" title="' + esc(c.name) + '">' + esc(c.name) + '</button>';
      }).join('') || '<div class="cb-empty">nessuna sotto-parte</div>';
      return '' +
        '<article class="cb-card cb-fam" data-family-id="' + esc(f.id) + '" data-primary="' + esc(f.primary_path || '') + '" tabindex="0" role="group" aria-roledescription="card progetto trascinabile">' +
          '<div class="cb-card-head">' +
            '<div class="cb-card-top" style="flex:1;min-width:0"><span class="cb-card-name">' + esc(f.name) + '</span>' + sparkDots(f.activity) + '</div>' +
            '<span class="cb-drag" title="Tieni premuto e trascina per riordinare" aria-hidden="true">∷∷</span>' +
          '</div>' +
          '<div class="cb-subs">' + subs + '</div>' +
          '<div class="cb-card-block"><div class="cb-block-h">Latest changes</div>' + latestHtml(f.latest) + '</div>' +
          '<div class="cb-card-block"><div class="cb-block-h">Up next</div>' + tasksHtml(f.tasks) + '</div>' +
        '</article>';
    }).join('');
    Array.prototype.forEach.call(grid.querySelectorAll('.cb-card.cb-fam'), function (card) {
      // Sub-project chip -> focus that specific note (drill into the sub-part).
      Array.prototype.forEach.call(card.querySelectorAll('.cb-sub'), function (chip) {
        chip.addEventListener('pointerdown', function (e) { e.stopPropagation(); });
        chip.addEventListener('click', function (e) {
          e.stopPropagation();
          focusProject(chip.getAttribute('data-id'), chip.getAttribute('data-path'));
        });
      });
      wireCardTasks(card, card.getAttribute('data-primary'));
    });
    enableDragReorder(grid);
  }

  function renderAgents(data) {
    var list = $('cbAgentList'), count = $('cbAgentsCount');
    if (!list) return;
    var agents = (data && data.agents) || [];
    function stateClass(s) {
      if (s === 'attivo' || s === 'vivo') return 'cb-live';
      if (s === 'in_attesa') return 'cb-waiting';
      return '';
    }
    if (count) {
      var present = agents.filter(function (a) {
        return a && (a.state === 'attivo' || a.state === 'vivo' || a.state === 'in_attesa');
      }).length;
      count.textContent = present + '/' + agents.length + ' attivi';
    }
    if (!agents.length) {
      list.innerHTML = '<div class="cb-empty">nessun agente trovato</div>';
      return;
    }
    list.innerHTML = agents.map(function (a) {
      var last = a.last_used && a.last_used.rel ? a.last_used.rel : 'mai';
      var status = a.status_label || ((a.state === 'attivo') ? 'live / task attivo' : 'idle / nessun task attivo');
      return '' +
        '<div class="cb-agent-row">' +
          '<span class="cb-agent-state ' + stateClass(a.state) + '"></span>' +
          '<div style="min-width:0">' +
            '<div class="cb-agent-name">' + esc(a.name || 'Agente') + '</div>' +
            '<div class="cb-agent-role">' + esc(a.role || '') + '</div>' +
            '<div class="cb-agent-role">' + esc(status) + '</div>' +
          '</div>' +
          '<div class="cb-agent-meta">' + esc(a.model || 'auto') + '<br>' + esc(last) + '</div>' +
        '</div>';
    }).join('');
  }

  function renderWorklog(data) {
    var box = $('cbFlex');
    if (!box) return;
    if (!data || data.ok === false) {
      box.innerHTML = '<div class="cb-empty">work log non disponibile</div>';
      return;
    }
    var today = Number(data.today_count || 0);
    var yesterday = Number(data.yesterday_count || 0);
    var record = Number(data.record_count || 0);
    var streak = Number(data.streak_days || 0);
    var totals = data.totals_by_project || {};
    var isRecord = record > 0 && today >= record;
    var projects = ['Hermes', 'VisionBuilts', 'Rap'];
    var recent = (data.recent || []).slice(0, 8);
    var lastDate = '';
    var recentHtml = recent.length ? recent.map(function (r) {
      var day = r.date || '';
      var head = day !== lastDate ? '<div class="cb-flex-day">' + esc(day) + '</div>' : '';
      lastDate = day;
      return head +
        '<div class="cb-flex-item">' +
          '<span class="cb-flex-tag">' + esc(r.project || 'Altro') + '</span>' +
          '<span class="cb-flex-summary">' + esc(r.summary || r.type || '') + '</span>' +
        '</div>';
    }).join('') : '<div class="cb-empty">nessun evento recente</div>';
    box.innerHTML =
      '<div class="cb-flex-top">' +
        '<div>' +
          '<div class="cb-flex-num">' + today + '</div>' +
          '<div class="cb-flex-label">oggi</div>' +
          '<div class="cb-flex-delta">ieri ' + yesterday + ' &rarr; oggi ' + today + '</div>' +
        '</div>' +
        '<div>' +
          '<div class="cb-flex-badges">' +
            '<span class="cb-flex-badge ' + (isRecord ? 'hot' : '') + '">' + (isRecord ? '&#128293; ' : '') + 'record ' + record + '</span>' +
            '<span class="cb-flex-badge">streak ' + streak + 'g</span>' +
          '</div>' +
          '<div class="cb-flex-projects">' +
            projects.map(function (p) {
              return '<div class="cb-flex-proj"><div class="n">' + Number(totals[p] || 0) + '</div><div class="l">' + esc(p) + '</div></div>';
            }).join('') +
          '</div>' +
        '</div>' +
      '</div>' +
      '<div class="cb-flex-recent">' + recentHtml + '</div>';
  }

  function renderRepoStatus(data) {
    var list = $('cbRepoList');
    if (!list) return;
    var repos = data && Array.isArray(data.repos) ? data.repos : [];
    var rows = repos.map(function (repo) {
      if (!repo || repo.status !== 'ok') {
        return '<div class="cb-repo-row"><div class="cb-repo-main"><span class="cb-repo-name">' + esc(repo && repo.name || 'Repo') + '</span><span class="cb-repo-branch">n/d</span></div><span class="cb-repo-badge error">n/d</span><span class="cb-repo-dirty">--</span><span class="cb-repo-headhash">--</span></div>';
      }
      var ahead = Number(repo.ahead || 0), behind = Number(repo.behind || 0), dirty = Number(repo.dirty || 0);
      var level = behind > 0 ? 'error' : ((ahead > 0 || dirty > 0) ? 'warn' : 'ok');
      var head = repo.head || {};
      return '<div class="cb-repo-row">' +
        '<div class="cb-repo-main"><span class="cb-repo-name">' + esc(repo.name) + '</span><span class="cb-repo-branch" title="' + esc(repo.branch || '') + '">' + esc(repo.branch || 'n/d') + '</span></div>' +
        '<span class="cb-repo-badge ' + level + '">↑' + ahead + ' ↓' + behind + '</span>' +
        '<span class="cb-repo-dirty" title="File modificati">Δ' + dirty + '</span>' +
        '<span class="cb-repo-headhash" title="' + esc(head.subject || '') + '">' + esc(head.hash || '--') + '</span>' +
      '</div>';
    });
    var workflow = data && data.workflow;
    if (workflow) {
      rows.push('<div class="cb-repo-row cb-repo-workflow"><span class="cb-repo-name">Workflow n8n ' + esc(workflow.id || '') + '</span><span>' + esc(workflow.label || '') + '</span></div>');
    }
    list.innerHTML = rows.length ? rows.join('') : '<div class="cb-empty">stato repo n/d</div>';
  }

  var _repoStatusTimer = null;
  function refreshRepoStatus() {
    return api('/api/repo-status').then(renderRepoStatus).catch(function () {
      var list = $('cbRepoList');
      if (list) list.innerHTML = '<div class="cb-empty">stato repo n/d</div>';
    });
  }

  function startRepoStatusRefresh() {
    refreshRepoStatus();
    if (_repoStatusTimer) clearInterval(_repoStatusTimer);
    _repoStatusTimer = setInterval(refreshRepoStatus, 60000);
  }

  // Phase 4 will fly the planet camera here; for now scroll to the stage and pulse.
  function focusProject(id, path) {
    try { document.dispatchEvent(new CustomEvent('cb:focus-project', { detail: { id: id, path: path } })); } catch (e) {}
    var root = document.querySelector('.cb-root');
    if (root) root.scrollTo({ top: 0, behavior: reduceMotion ? 'auto' : 'smooth' });
    if (window.cbCore && window.cbCore.pulse) window.cbCore.pulse();
  }

  function mountPlanet(g, tries) {
    tries = tries || 0;
    var pc = $('cbPlanet'); if (!pc || pc.getAttribute('data-mounted')) return;
    if (typeof window.cbInitPlanet === 'function') {
      try {
        window.cbInitPlanet(pc, g);
        pc.setAttribute('data-mounted', '1');
      } catch (e) { /* planet failed to mount; memory panel stays empty */ }
    } else if (tries < 40) {
      setTimeout(function () { mountPlanet(g, tries + 1); }, 250); // wait for three.js module to load
    }
  }

  function refresh() {
    refreshBrainState();
    startRepoStatusRefresh();
    api('api/vault/graph').then(function (g) { renderStats(g); mountPlanet(g); }).catch(function () {});
    api('api/projects/overview').then(renderProjects).catch(function () {
      var grid = $('cbGrid'); if (grid) grid.innerHTML = '<div class="cb-loading">vault non disponibile.</div>';
    });
    api('api/bridge/agents').then(renderAgents).catch(function () {
      var list = $('cbAgentList'); if (list) list.innerHTML = '<div class="cb-empty">agenti non disponibili</div>';
    });
    api('api/bridge/worklog?days=14').then(renderWorklog).catch(function () {
      var box = $('cbFlex'); if (box) box.innerHTML = '<div class="cb-empty">work log non disponibile</div>';
    });
  }

  /* ── Hermes Prime petal-core (canvas 2D) ───────────────────────────────────
   * The luminous heart of the bridge: Hermes Prime at the center, the sub-agents
   * radiating as glowing petals with sparks that flow outward from the core
   * (the "petali" metaphor). Doubles as the Voice Orb: reacts to setState/
   * setAmplitude exactly like the planet did, so voice still drives the center.
   * Palette: incandescent white core -> fluo orange leaves; cool tint on listen.
   * ──────────────────────────────────────────────────────────────────────── */
  var WHITE = [255, 255, 255], CREAM = [255, 247, 205], GOLD = [255, 224, 70];
  var ORANGE = [255, 106, 0], COOL = [127, 208, 255];
  function _mix(a, b, t) {
    t = t < 0 ? 0 : (t > 1 ? 1 : t);
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
  }
  function _rgba(c, a) { return 'rgba(' + (c[0] | 0) + ',' + (c[1] | 0) + ',' + (c[2] | 0) + ',' + a + ')'; }

  function initPrimeCore(host) {
    if (!host || host.getAttribute('data-core')) return;
    host.setAttribute('data-core', '1');
    var canvas = el('canvas');
    host.insertBefore(canvas, host.firstChild); // sits under the .cb-petal-label
    var ctx = canvas.getContext('2d');
    var DPR = Math.min(2, window.devicePixelRatio || 1), W = 1, H = 1;
    function size() {
      var r = host.getBoundingClientRect();
      W = Math.max(1, r.width); H = Math.max(1, r.height);
      canvas.width = Math.round(W * DPR); canvas.height = Math.round(H * DPR);
      canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
    }
    size();
    try { new ResizeObserver(size).observe(host); } catch (e) { window.addEventListener('resize', size); }

    // The agents as "organs" around the Hermes Prime heart: each one fixed in
    // place (so it stays distinct and recognisable) and wired to the heart by a
    // living vessel with pulses flowing in and out, like a circulatory system.
    var AGENTS = [
      { name: 'codice',       ang: -1.05, dist: 0.96, size: 0.135, tint: 0.10 },
      { name: 'ricerca',      ang: -0.18, dist: 1.02, size: 0.115, tint: 0.45 },
      { name: 'ragionamento', ang:  0.72, dist: 0.90, size: 0.150, tint: 0.00 },
      { name: 'semplice',     ang:  1.95, dist: 0.98, size: 0.100, tint: 0.62 },
      { name: 'voce',         ang:  2.75, dist: 0.92, size: 0.120, tint: 0.30 }
    ];
    AGENTS.forEach(function (a, i) { a.phase = i * 1.3; });

    var mode = 'idle', amp = 0, pulse = 0, t = 0;
    window.cbCore = {
      setState: function (s) { mode = s || 'idle'; },
      setAmplitude: function (a) { amp = a < 0 ? 0 : (a > 1 ? 1 : a); },
      pulse: function () { pulse = 0.7; }
    };

    function qbez(ax, ay, bx, by, c2x, c2y, s) {
      var u = 1 - s;
      return [u * u * ax + 2 * u * s * bx + s * s * c2x, u * u * ay + 2 * u * s * by + s * s * c2y];
    }
    // double-beat (lub-dub) envelope, ~0..1
    function heartbeat(time, bpm) {
      var period = 60 / bpm, x = (time % period) / period;
      var lub = Math.exp(-Math.pow((x - 0.10) / 0.045, 2));
      var dub = 0.55 * Math.exp(-Math.pow((x - 0.26) / 0.05, 2));
      return lub + dub;
    }

    var running = true;
    function frame() {
      if (!running) return;
      requestAnimationFrame(frame);
      t += 0.016;
      var cx = W / 2, cy = H / 2, base = Math.min(W, H) * 0.5;
      if (base < 2) return;
      ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
      ctx.clearRect(0, 0, W, H);

      var bpm = mode === 'speaking' ? 96 : (mode === 'listening' ? 78 : (mode === 'thinking' ? 66 : 60));
      var beat = reduceMotion ? 0.35 : heartbeat(t, bpm);
      var bright = 1, cool = 0;
      if (mode === 'speaking') bright = 1 + amp * 0.7;
      else if (mode === 'listening') { cool = 0.7; bright = 1.08; }
      else if (mode === 'thinking') bright = 0.66;
      pulse *= 0.92; bright += pulse;
      var beatBright = bright * (0.86 + beat * 0.5);

      var Rx = base * 0.78, Ry = base * 0.62;
      var coreR = base * 0.14 * (1 + beat * 0.16 + (mode === 'speaking' ? amp * 0.18 : 0));

      ctx.globalCompositeOperation = 'lighter';
      var labels = [];

      AGENTS.forEach(function (ag) {
        var bob = reduceMotion ? 0 : Math.sin(t * 1.1 + ag.phase) * 0.025;
        var ang = ag.ang + bob;
        var ox = cx + Math.cos(ang) * Rx * ag.dist;
        var oy = cy + Math.sin(ang) * Ry * ag.dist;
        var col = _mix(_mix(GOLD, ORANGE, ag.tint), COOL, cool * 0.5);
        // vessel (artery) heart -> organ, gently arced
        var hx = cx + Math.cos(ang) * coreR, hy = cy + Math.sin(ang) * coreR;
        var mx = (hx + ox) / 2, my = (hy + oy) / 2;
        var nx = -(oy - hy), ny = (ox - hx), nl = Math.sqrt(nx * nx + ny * ny) || 1;
        var arc = base * 0.12 * Math.sin(ag.phase);
        var c2x = mx + nx / nl * arc, c2y = my + ny / nl * arc;
        ctx.beginPath();
        ctx.moveTo(hx, hy); ctx.quadraticCurveTo(c2x, c2y, ox, oy);
        ctx.lineWidth = base * 0.012; ctx.strokeStyle = _rgba(col, 0.10 * beatBright); ctx.stroke();
        ctx.lineWidth = base * 0.004; ctx.strokeStyle = _rgba(_mix(col, WHITE, 0.4), 0.16 * beatBright); ctx.stroke();
        // pulses along the vessel (two flow outward, one returns to the heart)
        if (!reduceMotion) {
          for (var k = 0; k < 3; k++) {
            var s = ((t * (mode === 'speaking' ? 0.5 + amp * 0.4 : 0.32) + ag.phase * 0.3 + k * 0.37) % 1 + 1) % 1;
            if (k === 2) s = 1 - s;
            var pp = qbez(hx, hy, c2x, c2y, ox, oy, s);
            var fade = Math.sin(s * Math.PI), pr = base * 0.011 * (1 + beat * 0.4);
            var pg = ctx.createRadialGradient(pp[0], pp[1], 0, pp[0], pp[1], pr * 3.5);
            pg.addColorStop(0, _rgba(_mix(col, WHITE, 0.5), 0.6 * fade * beatBright));
            pg.addColorStop(1, _rgba(col, 0));
            ctx.fillStyle = pg; ctx.beginPath(); ctx.arc(pp[0], pp[1], pr * 3.5, 0, 7); ctx.fill();
          }
        }
        // organ body (membrane glow + ring + nucleus)
        var orad = base * ag.size * (1 + beat * 0.12 + (reduceMotion ? 0 : Math.sin(t * 1.6 + ag.phase) * 0.04));
        var og = ctx.createRadialGradient(ox, oy, 0, ox, oy, orad);
        og.addColorStop(0, _rgba(_mix(WHITE, col, 0.35), 0.95 * beatBright));
        og.addColorStop(0.4, _rgba(col, 0.5 * beatBright));
        og.addColorStop(1, _rgba(col, 0));
        ctx.fillStyle = og; ctx.beginPath(); ctx.arc(ox, oy, orad, 0, 7); ctx.fill();
        ctx.lineWidth = Math.max(1, base * 0.005);
        ctx.strokeStyle = _rgba(_mix(col, WHITE, 0.3), 0.4 * beatBright);
        ctx.beginPath(); ctx.arc(ox, oy, orad * 0.62, 0, 7); ctx.stroke();
        ctx.fillStyle = _rgba(WHITE, 0.9 * beatBright);
        ctx.beginPath(); ctx.arc(ox, oy, orad * 0.16, 0, 7); ctx.fill();
        labels.push({ x: ox, y: oy + orad + base * 0.05, name: ag.name });
      });

      // heart core: nebula + incandescent heart
      var c0 = _mix(WHITE, COOL, cool * 0.5);
      var ng = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreR * 4.4);
      ng.addColorStop(0, _rgba(c0, Math.min(1, 0.95 * beatBright)));
      ng.addColorStop(0.16, _rgba(_mix(CREAM, COOL, cool * 0.5), 0.85 * beatBright));
      ng.addColorStop(0.42, _rgba(_mix(GOLD, COOL, cool), 0.34 * beatBright));
      ng.addColorStop(0.72, _rgba(_mix(ORANGE, COOL, cool), 0.1 * beatBright));
      ng.addColorStop(1, _rgba(ORANGE, 0));
      ctx.fillStyle = ng; ctx.beginPath(); ctx.arc(cx, cy, coreR * 4.4, 0, 7); ctx.fill();

      var cg = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreR);
      cg.addColorStop(0, _rgba(WHITE, Math.min(1, beatBright)));
      cg.addColorStop(0.5, _rgba(_mix(CREAM, COOL, cool), 0.94 * beatBright));
      cg.addColorStop(1, _rgba(_mix(GOLD, ORANGE, 0.3), 0));
      ctx.fillStyle = cg; ctx.beginPath(); ctx.arc(cx, cy, coreR, 0, 7); ctx.fill();

      // organ labels (crisp, on top)
      ctx.globalCompositeOperation = 'source-over';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.font = '600 ' + Math.max(8, Math.round(base * 0.03)) + 'px "IBM Plex Mono", ui-monospace, monospace';
      labels.forEach(function (l) {
        ctx.fillStyle = _rgba([182, 186, 198], 0.82);
        ctx.fillText(l.name, l.x, l.y);
      });
    }
    frame();
  }

  // Prefer the three.js living-star system; fall back to the canvas core if the
  // module hasn't loaded yet or WebGL is unavailable.
  function mountPrimeCore(host, tries) {
    tries = tries || 0;
    if (!host) return;
    if (typeof window.cbInitStar === 'function') {
      try { window.cbInitStar(host); return; } catch (e) { /* fall back to canvas */ }
      initPrimeCore(host); return;
    }
    if (tries < 40) { setTimeout(function () { mountPrimeCore(host, tries + 1); }, 200); return; }
    initPrimeCore(host); // star module never arrived -> canvas fallback
  }

  /* ── entry point (called by switchPanel) ───────────────────────────────── */
  window.loadCommandBridge = function () {
    if (!BUILT) { if (!build()) return Promise.resolve(); }
    loadPrimeHistory();
    loadPrimeTodos();
    refresh();
    startTaskPolling();
    startAgentsPolling();
    startWorklogPolling();
    startQuotaPolling();
    pollTokenQuota();
    return Promise.resolve();
  };
})();
