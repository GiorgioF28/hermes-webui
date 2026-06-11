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
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[c];
    });
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
'#mainBridge{padding:0!important;}',
'.cb-root{',
'  --cb-bg:#06070b; --cb-bg2:#0a0c12; --cb-core:#ffffff;',
'  --cb-accent:#ff6a00; --cb-accent-2:#ff9248; --cb-accent-dim:rgba(255,106,0,.22);',
'  --cb-text:#e9e9ee; --cb-muted:#7d808c; --cb-faint:#494c58;',
'  --cb-line:rgba(255,255,255,.07); --cb-line2:rgba(255,255,255,.04);',
'  --cb-disp:"Chakra Petch",system-ui,sans-serif;',
'  --cb-mono:"IBM Plex Mono",ui-monospace,monospace;',
'  --cb-sans:"IBM Plex Sans",system-ui,sans-serif;',
'  position:relative;height:100%;width:100%;overflow-y:auto;overflow-x:hidden;',
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
'.cb-hero{position:relative;min-height:100%;display:grid;grid-template-columns:minmax(300px,30%) 1fr;}',
'.cb-hero.cb-collapsed{grid-template-columns:0 1fr;}',
/* left: Hermes Prime */
'.cb-chat{position:relative;display:flex;flex-direction:column;min-width:0;border-right:1px solid var(--cb-line);',
'  background:linear-gradient(180deg,rgba(255,255,255,.015),transparent 40%);backdrop-filter:blur(2px);}',
'.cb-collapsed .cb-chat{opacity:0;pointer-events:none;}',
'.cb-chat-head{display:flex;align-items:center;gap:10px;padding:18px 20px 14px;border-bottom:1px solid var(--cb-line2);}',
'.cb-dot{width:9px;height:9px;border-radius:50%;background:var(--cb-accent);box-shadow:0 0 10px var(--cb-accent);flex:0 0 auto;}',
'.cb-chat-name{font-family:var(--cb-disp);font-weight:700;letter-spacing:.14em;font-size:13px;text-transform:uppercase;}',
'.cb-chat-role{font-family:var(--cb-mono);font-size:10px;color:var(--cb-muted);letter-spacing:.08em;margin-top:1px;}',
'.cb-chat-log{flex:1;min-height:0;overflow-y:auto;padding:18px 20px;display:flex;flex-direction:column;gap:14px;}',
'.cb-msg{max-width:92%;font-size:13.5px;line-height:1.5;}',
'.cb-msg .cb-who{font-family:var(--cb-mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--cb-faint);margin-bottom:4px;}',
'.cb-msg.cb-from-prime{align-self:flex-start;}',
'.cb-msg.cb-from-user{align-self:flex-end;text-align:right;color:#fff;}',
'.cb-msg.cb-from-prime .cb-bubble{color:var(--cb-text);}',
'.cb-deleg{align-self:flex-start;max-width:96%;border:1px solid var(--cb-accent-dim);border-left:2px solid var(--cb-accent);',
'  border-radius:8px;padding:9px 12px;background:rgba(255,106,0,.05);font-family:var(--cb-mono);font-size:11px;}',
'.cb-deleg b{color:var(--cb-accent-2);font-weight:600;}',
'.cb-chat-input{display:flex;gap:8px;padding:14px 16px;border-top:1px solid var(--cb-line2);}',
'.cb-chat-input input{flex:1;background:rgba(255,255,255,.04);border:1px solid var(--cb-line);border-radius:10px;',
'  color:var(--cb-text);font-family:var(--cb-sans);font-size:13.5px;padding:11px 14px;outline:none;}',
'.cb-chat-input input:focus{border-color:var(--cb-accent);box-shadow:0 0 0 3px rgba(255,106,0,.12);}',
'.cb-send{background:var(--cb-accent);border:none;color:#1a0c00;font-weight:700;border-radius:10px;width:42px;cursor:pointer;',
'  font-family:var(--cb-disp);display:flex;align-items:center;justify-content:center;transition:filter .15s;}',
'.cb-send:hover{filter:brightness(1.12);}',
/* center stage */
'.cb-stage{position:relative;display:flex;flex-direction:column;align-items:center;justify-content:center;min-width:0;overflow:hidden;}',
'.cb-stage-toggle{position:absolute;top:16px;left:16px;z-index:5;background:rgba(255,255,255,.04);border:1px solid var(--cb-line);',
'  color:var(--cb-muted);width:34px;height:34px;border-radius:9px;cursor:pointer;display:flex;align-items:center;justify-content:center;}',
'.cb-stage-toggle:hover{color:var(--cb-text);border-color:var(--cb-accent);}',
/* the core / orb placeholder */
'.cb-core{position:relative;width:min(46vh,420px);height:min(46vh,420px);display:flex;align-items:center;justify-content:center;}',
'.cb-core-glow{position:absolute;inset:0;border-radius:50%;',
'  background:radial-gradient(circle at 50% 50%,#fff 0%,#ffe9d5 9%,var(--cb-accent-2) 26%,var(--cb-accent) 42%,rgba(255,106,0,.25) 60%,transparent 75%);',
'  filter:blur(2px);animation:cb-breathe 5.2s ease-in-out infinite;}',
'.cb-core-ring{position:absolute;inset:0;border-radius:50%;border:1px solid rgba(255,170,110,.18);',
'  box-shadow:inset 0 0 60px rgba(255,106,0,.12);}',
'.cb-core-ring.r2{inset:-9%;border-color:rgba(255,255,255,.05);}',
'.cb-core-ring.r3{inset:-20%;border-color:rgba(255,255,255,.035);}',
'.cb-core-label{position:relative;z-index:2;text-align:center;mix-blend-mode:screen;}',
'.cb-core-label .cb-h{font-family:var(--cb-disp);font-weight:700;font-size:15px;letter-spacing:.34em;color:#fff;text-transform:uppercase;}',
'.cb-core-label .cb-s{font-family:var(--cb-mono);font-size:10px;letter-spacing:.16em;color:rgba(255,255,255,.7);margin-top:5px;}',
'@keyframes cb-breathe{0%,100%{transform:scale(1);opacity:.92;}50%{transform:scale(1.045);opacity:1;}}',
/* stage stat ring */
'.cb-stats{position:absolute;bottom:28px;left:50%;transform:translateX(-50%);display:flex;gap:30px;}',
'.cb-stat{text-align:center;}',
'.cb-stat .n{font-family:var(--cb-disp);font-weight:700;font-size:22px;color:#fff;line-height:1;}',
'.cb-stat .l{font-family:var(--cb-mono);font-size:9.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--cb-muted);margin-top:6px;}',
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
'@media(max-width:680px){.cb-grid{grid-template-columns:1fr;}.cb-hero{grid-template-columns:1fr;}.cb-chat{display:none;}}',
'.cb-card{position:relative;border:1px solid var(--cb-line);border-radius:14px;padding:18px 18px 16px;cursor:pointer;',
'  background:linear-gradient(180deg,rgba(255,255,255,.022),rgba(255,255,255,.006));overflow:hidden;',
'  transition:border-color .2s,transform .2s,box-shadow .2s;}',
'.cb-card::before{content:"";position:absolute;left:0;top:0;bottom:0;width:2px;background:linear-gradient(180deg,#fff,var(--cb-accent));opacity:.0;transition:opacity .2s;}',
'.cb-card:hover{border-color:rgba(255,138,61,.5);transform:translateY(-3px);box-shadow:0 14px 40px -22px rgba(255,106,0,.6);}',
'.cb-card:hover::before{opacity:.9;}',
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
'.cb-task .box{width:13px;height:13px;border:1.4px solid var(--cb-accent);border-radius:3px;flex:0 0 auto;margin-top:2px;}',
'.cb-empty{font-family:var(--cb-mono);font-size:11px;color:var(--cb-faint);font-style:italic;}',
'.cb-loading{padding:60px;text-align:center;font-family:var(--cb-mono);color:var(--cb-muted);letter-spacing:.1em;}',
(reduceMotion ? '.cb-core-glow{animation:none!important;}' : '')
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
          '<div class="cb-chat-head"><span class="cb-dot"></span><div>' +
            '<div class="cb-chat-name">Hermes Prime</div>' +
            '<div class="cb-chat-role">chief of staff · briefing mode</div>' +
          '</div></div>' +
          '<div class="cb-chat-log" id="cbLog"></div>' +
          '<form class="cb-chat-input" id="cbForm" autocomplete="off">' +
            '<input id="cbInput" placeholder="Parla con Hermes Prime…" aria-label="Messaggio a Hermes Prime">' +
            '<button class="cb-send" type="submit" aria-label="Invia">&#8594;</button>' +
          '</form>' +
        '</aside>' +
        '<section class="cb-stage" id="cbStage">' +
          '<button class="cb-stage-toggle" id="cbToggle" title="Comprimi pannello" aria-label="Comprimi pannello">' +
            '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M15 18l-6-6 6-6"/></svg></button>' +
          '<div class="cb-stage-hint">Vault Planet · anteprima</div>' +
          '<div class="cb-core">' +
            '<div class="cb-core-ring r3"></div><div class="cb-core-ring r2"></div><div class="cb-core-ring"></div>' +
            '<div class="cb-core-glow"></div>' +
            '<div class="cb-core-label"><div class="cb-h">Hermes</div><div class="cb-s" id="cbCoreSub">core online</div></div>' +
          '</div>' +
          '<div class="cb-stats" id="cbStats"></div>' +
        '</section>' +
      '</div>' +
      '<section class="cb-projects">' +
        '<h2 class="cb-sec-title">Projects</h2>' +
        '<div class="cb-grid" id="cbGrid"><div class="cb-loading">caricamento vault…</div></div>' +
      '</section>';
    host.appendChild(root);

    // interactions
    var toggle = $('cbToggle'), hero = $('cbHero');
    if (toggle && hero) toggle.addEventListener('click', function () { hero.classList.toggle('cb-collapsed'); });
    var form = $('cbForm');
    if (form) form.addEventListener('submit', onPrimeSubmit);
    primeSay('prime', 'Plancia online. Ti dò il quadro quando vuoi — chiedimi "cosa serve oggi?" e ti briffo. Voce e deleghe arrivano a breve.');
    BUILT = true;
    return true;
  }

  /* ── Hermes Prime (Phase 3 preview — full agent wiring lands in Phase 6) ── */
  function primeSay(who, text) {
    var log = $('cbLog'); if (!log) return;
    var m = el('div', 'cb-msg ' + (who === 'user' ? 'cb-from-user' : 'cb-from-prime'));
    m.innerHTML = '<div class="cb-who">' + (who === 'user' ? 'tu' : 'hermes prime') + '</div><div class="cb-bubble">' + esc(text) + '</div>';
    log.appendChild(m); log.scrollTop = log.scrollHeight;
  }
  function onPrimeSubmit(e) {
    e.preventDefault();
    var inp = $('cbInput'); if (!inp) return;
    var v = inp.value.trim(); if (!v) return;
    inp.value = '';
    primeSay('user', v);
    setTimeout(function () {
      primeSay('prime', 'Ricevuto. La voce e le deleghe vere (Claude/Codex) arrivano nella prossima fase — per ora questo è il preview della plancia.');
    }, 360);
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

  function renderProjects(data) {
    var grid = $('cbGrid'); if (!grid) return;
    var projects = (data && data.projects) || [];
    if (!projects.length) {
      grid.innerHTML = '<div class="cb-loading">Nessun progetto in 01-Projects.</div>';
      return;
    }
    grid.innerHTML = projects.map(function (p) {
      var latest = (p.latest || []).map(function (l) {
        return '<div class="cb-line-item"><span class="n">' + esc(l.name) + '</span><span class="t">' + esc(l.rel || '') + '</span></div>';
      }).join('') || '<div class="cb-empty">nessuna modifica recente</div>';
      var tasks = (p.tasks || []).map(function (t) {
        return '<div class="cb-task"><span class="box"></span><span>' + esc(t) + '</span></div>';
      }).join('') || '<div class="cb-empty">nessun task aperto</div>';
      return '' +
        '<article class="cb-card" data-project-id="' + esc(p.id) + '" data-path="' + esc(p.path) + '" tabindex="0" role="button">' +
          '<div class="cb-card-top"><span class="cb-card-name">' + esc(p.name) + '</span>' + sparkDots(p.activity) + '</div>' +
          '<div class="cb-card-block"><div class="cb-block-h">Latest changes</div>' + latest + '</div>' +
          '<div class="cb-card-block"><div class="cb-block-h">Up next</div>' + tasks + '</div>' +
        '</article>';
    }).join('');
    Array.prototype.forEach.call(grid.querySelectorAll('.cb-card'), function (card) {
      var go = function () { focusProject(card.getAttribute('data-project-id'), card.getAttribute('data-path')); };
      card.addEventListener('click', go);
      card.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
    });
  }

  // Phase 4 will fly the planet camera here; for now scroll to the stage and pulse.
  function focusProject(id, path) {
    try { document.dispatchEvent(new CustomEvent('cb:focus-project', { detail: { id: id, path: path } })); } catch (e) {}
    var root = document.querySelector('.cb-root');
    if (root) root.scrollTo({ top: 0, behavior: reduceMotion ? 'auto' : 'smooth' });
    var glow = document.querySelector('.cb-core-glow');
    if (glow && !reduceMotion) { glow.style.filter = 'blur(2px) brightness(1.5)'; setTimeout(function () { glow.style.filter = ''; }, 420); }
  }

  function refresh() {
    api('api/vault/graph').then(renderStats).catch(function () {});
    api('api/projects/overview').then(renderProjects).catch(function () {
      var grid = $('cbGrid'); if (grid) grid.innerHTML = '<div class="cb-loading">vault non disponibile.</div>';
    });
  }

  /* ── entry point (called by switchPanel) ───────────────────────────────── */
  window.loadCommandBridge = function () {
    if (!BUILT) { if (!build()) return Promise.resolve(); }
    refresh();
    return Promise.resolve();
  };
})();
