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
  // Scroll to bottom only if the user is already near it, so reading older
  // messages isn't interrupted while Prime keeps writing.
  function nearBottom(log) { return !log || (log.scrollHeight - log.scrollTop - log.clientHeight) < 64; }
  // Render Prime text as markdown (reuses the main chat renderer); plain fallback.
  function renderRich(node, text) {
    if (!node) return;
    if (typeof window.renderMd === 'function') {
      try { node.innerHTML = window.renderMd(String(text || '')); return; } catch (e) {}
    }
    node.textContent = String(text || '');
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
/* the bridge is a full-bleed stage: hide the (empty) 300px sidebar panel */
'.layout:has(main.main.showing-bridge) .sidebar{width:0!important;min-width:0!important;opacity:0!important;pointer-events:none;border:none!important;overflow:hidden;}',
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
'.cb-hero{position:relative;min-height:100%;}',
/* Hermes Prime = glass console on the LEFT; the planet stays centered behind/right of it */
'.cb-chat{position:absolute;left:22px;top:22px;bottom:22px;z-index:3;display:flex;flex-direction:column;width:min(430px,40vw);',
'  min-width:0;border:1px solid var(--cb-line);border-radius:18px;overflow:hidden;',
'  background:linear-gradient(180deg,rgba(8,9,14,.66),rgba(6,7,11,.86));backdrop-filter:blur(11px);',
'  box-shadow:0 30px 90px -30px rgba(0,0,0,.9),inset 0 0 0 1px rgba(255,255,255,.02);}',
'.cb-hero.cb-collapsed .cb-chat{opacity:0;pointer-events:none;transform:translateX(-10px);}',
'.cb-chat-head{display:flex;align-items:center;gap:10px;padding:18px 20px 14px;border-bottom:1px solid var(--cb-line2);}',
'.cb-dot{width:9px;height:9px;border-radius:50%;background:var(--cb-accent);box-shadow:0 0 10px var(--cb-accent);flex:0 0 auto;}',
'.cb-chat-name{font-family:var(--cb-disp);font-weight:700;letter-spacing:.14em;font-size:13px;text-transform:uppercase;}',
'.cb-chat-role{font-family:var(--cb-mono);font-size:10px;color:var(--cb-muted);letter-spacing:.08em;margin-top:1px;}',
'.cb-voicetoggle{background:transparent;border:1px solid var(--cb-line);color:var(--cb-faint);border-radius:8px;width:32px;height:32px;cursor:pointer;display:flex;align-items:center;justify-content:center;flex:0 0 auto;transition:.15s;}',
'.cb-voicetoggle:hover{color:var(--cb-text);}',
'.cb-voicetoggle.cb-on{color:var(--cb-accent);border-color:var(--cb-accent-dim);}',
'.cb-chat-log{flex:1;min-height:0;overflow-y:auto;padding:18px 20px;display:flex;flex-direction:column;gap:14px;}',
'.cb-msg{max-width:92%;font-size:13.5px;line-height:1.5;}',
'.cb-msg .cb-who{font-family:var(--cb-mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--cb-faint);margin-bottom:4px;}',
'.cb-msg.cb-from-prime{align-self:flex-start;}',
'.cb-msg.cb-from-user{align-self:flex-end;text-align:right;color:#fff;}',
'.cb-msg.cb-from-prime .cb-bubble{color:var(--cb-text);}',
'.cb-prime-status{margin-top:5px;font-family:var(--cb-mono);font-size:9.5px;letter-spacing:.06em;color:var(--cb-faint);}',
'.cb-deleg{align-self:flex-start;max-width:96%;border:1px solid var(--cb-accent-dim);border-left:2px solid var(--cb-accent);',
'  border-radius:8px;padding:9px 12px;background:rgba(255,106,0,.05);font-family:var(--cb-mono);font-size:11px;}',
'.cb-deleg b{color:var(--cb-accent-2);font-weight:600;}',
'.cb-chat-input{display:flex;gap:8px;padding:14px 16px;border-top:1px solid var(--cb-line2);align-items:flex-end;}',
'.cb-chat-input input,.cb-chat-input textarea{flex:1;min-width:0;background:rgba(255,255,255,.04);border:1px solid var(--cb-line);border-radius:10px;',
'  color:var(--cb-text);font-family:var(--cb-sans);font-size:13.5px;line-height:1.45;padding:10px 14px;outline:none;}',
'.cb-chat-input textarea{resize:none;display:block;min-height:42px;max-height:40vh;overflow-y:auto;}',
'.cb-chat-input .cb-send,.cb-chat-input .cb-mic,.cb-chat-input .cb-attach{height:42px;flex:0 0 auto;}',
'.cb-chat-input input:focus,.cb-chat-input textarea:focus{border-color:var(--cb-accent);box-shadow:0 0 0 3px rgba(255,106,0,.12);}',
'.cb-send{background:var(--cb-accent);border:none;color:#1a0c00;font-weight:700;border-radius:10px;width:42px;cursor:pointer;',
'  font-family:var(--cb-disp);display:flex;align-items:center;justify-content:center;transition:filter .15s;}',
'.cb-mic{background:rgba(255,255,255,.05);border:1px solid var(--cb-line);color:var(--cb-muted);border-radius:10px;width:42px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:.15s;flex:0 0 auto;}',
'.cb-mic:hover{color:var(--cb-text);border-color:var(--cb-accent);}',
'.cb-mic.cb-on{color:#1a0c00;background:var(--cb-accent);border-color:var(--cb-accent);animation:cb-micpulse 1.1s ease-in-out infinite;}',
'@keyframes cb-micpulse{0%,100%{box-shadow:0 0 0 0 rgba(255,106,0,.5);}50%{box-shadow:0 0 0 6px rgba(255,106,0,0);}}',
'.cb-send:hover{filter:brightness(1.12);}',
'.cb-attach{background:rgba(255,255,255,.05);border:1px solid var(--cb-line);color:var(--cb-muted);border-radius:10px;width:42px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:.15s;flex:0 0 auto;}',
'.cb-attach:hover{color:var(--cb-text);border-color:var(--cb-accent);}',
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
'.cb-msg.cb-from-user .cb-bubble img{max-width:160px;border-radius:8px;margin-top:6px;display:inline-block;}',
/* center stage */
'.cb-stage{position:absolute;inset:0;z-index:1;display:flex;align-items:center;justify-content:center;overflow:hidden;}',
'.cb-planet{position:absolute;inset:0;z-index:1;}',
'.cb-planet canvas{display:block;}',
'.cb-stage-toggle{position:absolute;top:16px;left:16px;z-index:5;background:rgba(255,255,255,.04);border:1px solid var(--cb-line);',
'  color:var(--cb-muted);width:34px;height:34px;border-radius:9px;cursor:pointer;display:flex;align-items:center;justify-content:center;}',
'.cb-stage-toggle:hover{color:var(--cb-text);border-color:var(--cb-accent);}',
/* the core / orb placeholder */
'.cb-core{position:relative;width:min(50vh,480px);height:min(50vh,480px);display:flex;align-items:center;justify-content:center;}',
'.cb-core-glow{position:absolute;inset:0;border-radius:50%;',
'  background:radial-gradient(circle at 50% 50%,rgba(255,255,255,.92) 0%,rgba(255,246,200,.58) 10%,rgba(255,224,70,.36) 27%,rgba(255,202,20,.20) 45%,rgba(255,196,20,.07) 63%,transparent 75%);',
'  filter:blur(3px);animation:cb-breathe 5.2s ease-in-out infinite;}',
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
'@media(max-width:680px){.cb-grid{grid-template-columns:1fr;}.cb-chat{width:94vw;height:64vh;}}',
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
/* white -> fluo-orange gradient on display titles (the accent the user wanted on TEXT) */
'.cb-chat-name,.cb-sec-title,.cb-card-name{background:linear-gradient(90deg,#ffffff 0%,#ffffff 26%,#ffb673 62%,var(--cb-accent) 100%);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;color:transparent;}',
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
          '<div class="cb-chat-head"><span class="cb-dot"></span><div style="flex:1;min-width:0">' +
            '<div class="cb-chat-name">Hermes Prime</div>' +
            '<div class="cb-chat-role">chief of staff · voce attiva</div>' +
          '</div>' +
            '<button type="button" class="cb-voicetoggle cb-on" id="cbVoice" aria-label="Voce on/off" title="Voce on/off">' +
              '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5 6 9H2v6h4l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M19 5a9 9 0 0 1 0 14"/></svg></button>' +
          '</div>' +
          '<div class="cb-chat-log" id="cbLog"></div>' +
          '<div class="cb-attbar" id="cbAtt"></div>' +
          '<form class="cb-chat-input" id="cbForm" autocomplete="off">' +
            '<button type="button" class="cb-mic" id="cbMic" aria-label="Parla a voce" title="Parla a voce">' +
              '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2" width="6" height="11" rx="3"/><path d="M5 10a7 7 0 0 0 14 0"/><path d="M12 17v4"/></svg></button>' +
            '<button type="button" class="cb-attach" id="cbAttachBtn" aria-label="Allega foto" title="Allega foto">' +
              '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg></button>' +
            '<input id="cbFile" type="file" accept="image/*" multiple style="display:none">' +
            '<textarea id="cbInput" rows="1" placeholder="Parla con Hermes Prime…" aria-label="Messaggio a Hermes Prime"></textarea>' +
            '<button class="cb-send" type="submit" aria-label="Invia">&#8594;</button>' +
          '</form>' +
        '</aside>' +
        '<section class="cb-stage" id="cbStage">' +
          '<button class="cb-stage-toggle" id="cbToggle" title="Comprimi pannello" aria-label="Comprimi pannello">' +
            '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M15 18l-6-6 6-6"/></svg></button>' +
          '<div class="cb-stage-hint">Vault Planet · anteprima</div>' +
          '<div class="cb-planet" id="cbPlanet"></div>' +
          '<div class="cb-core">' +
            '<div class="cb-core-ring r3"></div><div class="cb-core-ring r2"></div><div class="cb-core-ring"></div>' +
            '<div class="cb-core-glow"></div>' +
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
    // Textarea che cresce mentre scrivi e torna piccola all'invio; Invio manda,
    // Shift+Invio va a capo. Risolve anche il cursore che tornava all'inizio.
    var ta = $('cbInput');
    if (ta) {
      var autoGrow = function () {
        ta.style.height = 'auto';
        ta.style.height = Math.min(ta.scrollHeight, Math.round(window.innerHeight * 0.4)) + 'px';
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
    primeSay('prime', 'Plancia online. Ti dò il quadro quando vuoi — chiedimi "cosa serve oggi?" e ti briffo. Premi il microfono per parlarmi a voce.');
    BUILT = true;
    return true;
  }

  /* ── Hermes Prime + Voice (Phase 5). Real Claude/Codex delegation: Phase 6. ── */
  var voiceOn = true, userEngaged = false, _ctx = null, _an = null, _raf = null, _cur = null, _rec = null, _ttsActive = false;
  var _listening = false, _micTimer = null;
  var _micStream = null, _micRec = null, _micVadRaf = null, _micChunks = [], _micBusy = false;

  function setOrb(state, amp) {
    if (!window.cbPlanet) return;
    if (state != null) window.cbPlanet.setState(state);
    if (amp != null) window.cbPlanet.setAmplitude(amp);
  }
  function sysNote(text) {
    var log = $('cbLog'); if (!log) return;
    var m = el('div', 'cb-msg cb-from-prime');
    m.innerHTML = '<div class="cb-who">sistema</div><div class="cb-bubble" style="color:var(--cb-muted);font-style:italic">' + esc(text) + '</div>';
    var _sb = nearBottom(log); log.appendChild(m); if (_sb) log.scrollTop = log.scrollHeight;
  }
  // delegation cards: update-in-place by id (in_corso -> ok/errore), background-aware
  var _cbTasks = {};
  function renderTask(t) {
    if (!t || !t.id) return;
    var log = $('cbLog'); if (!log) return;
    var prev = _cbTasks[t.id];
    var sl = t.status === 'in_corso' ? '&#8230;' : (t.status === 'ok' ? '&#10003;' : '&#10007;');
    var body = t.status === 'in_corso'
      ? '<span style="color:var(--cb-muted);font-style:italic">il sotto-agente sta lavorando&#8230;</span>'
      : '<div style="color:var(--cb-text);font-family:var(--cb-sans);font-size:12.5px;line-height:1.45">' + esc((t.output || '').slice(0, 600)) + '</div>';
    var html = '<b>&#9883; ' + esc(t.agent || 'agente') + '</b> &middot; ' + esc(t.task_type || '') +
      ' <span style="float:right">' + sl + '</span>' +
      '<div style="margin-top:6px;color:var(--cb-muted);font-family:var(--cb-sans);font-size:11.5px">' + esc((t.task || '').slice(0, 120)) + '</div>' +
      '<div style="margin-top:6px">' + body + '</div>';
    if (prev && prev.el) { prev.el.innerHTML = html; }
    else {
      var c = el('div', 'cb-deleg'); c.innerHTML = html;
      var _sb = nearBottom(log); log.appendChild(c); if (_sb) log.scrollTop = log.scrollHeight;
      _cbTasks[t.id] = { el: c, status: t.status };
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
    var cfg = window.__HERMES_CONFIG__ || {};
    fetch(new URL('api/bridge/prime/brief', document.baseURI || location.href).href, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': cfg.csrfToken || '' },
      body: JSON.stringify({ task_id: t.id })
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        var reply = String((d && d.reply) || '').trim();
        if (ph && ph.parentNode) {
          if (reply) {
            var bub = ph.querySelector('.cb-bubble');
            if (bub) { bub.removeAttribute('style'); renderRich(bub, reply); }
            if (userEngaged) speak(reply);
          } else { ph.parentNode.removeChild(ph); }
        }
        setOrb('idle', 0);
      })
      .catch(function () { if (ph && ph.parentNode) ph.parentNode.removeChild(ph); setOrb('idle', 0); });
  }
  var _cbPollTimer = null;
  function pollTasks() {
    api('api/bridge/tasks').then(function (d) {
      if (d && d.tasks) d.tasks.forEach(renderTask);
    }).catch(function () {});
  }
  function startTaskPolling() { if (!_cbPollTimer) _cbPollTimer = setInterval(pollTasks, 3000); }
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
  function pendingBubble() {
    var log = $('cbLog'); if (!log) return null;
    var m = el('div', 'cb-msg cb-from-prime');
    m.innerHTML = '<div class="cb-who">hermes prime</div><div class="cb-bubble" style="color:var(--cb-muted);font-style:italic">sto ragionando&#8230;</div><div class="cb-prime-status">in corso</div>';
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
    var role = document.querySelector('.cb-chat-role'); if (role) role.textContent = 'chief of staff · ' + (voiceOn ? 'voce attiva' : 'voce muta');
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
      '<div class="cb-bubble">' + label + imgs + '</div>';
    var _sb = nearBottom(log); log.appendChild(m); if (_sb) log.scrollTop = log.scrollHeight;
    if (who === 'prime' && userEngaged) speak(text);
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
    var statusLine = ph ? ph.querySelector('.cb-prime-status') : null;
    var reply = '', settled = false, speech = { spokenLen: 0 };
    var showStatus = function (state, tool) {
      var labels = {
        queued: 'in coda\u2026',
        reasoning: 'sto ragionando\u2026',
        tool: 'uso lo strumento ' + (tool || '') + '\u2026',
        responding: 'sta scrivendo\u2026',
        done: '\u2713 completato'
      };
      var label = labels[state]; if (!label) return;
      if (statusLine) statusLine.textContent = label;
      if (!reply && bubble) bubble.textContent = label;
      if (state === 'done' && bubble && !reply) bubble.textContent = 'Ricevuto.';
    };
    var finish = function (label) {
      if (settled) return;
      settled = true;
      if (statusLine) statusLine.textContent = label || '\u2713 completato';
      if (bubble && reply) { bubble.removeAttribute('style'); renderRich(bubble, reply); }
      flushSpokenSentences(reply, speech, true);
      if (!_ttsActive) setOrb('idle', 0);
    };
    var showToken = function (text) {
      text = String(text || ''); if (!text) return;
      var log = $('cbLog'); var _sb = nearBottom(log);
      reply += text;
      if (bubble) {
        bubble.removeAttribute('style');
        bubble.textContent = reply;
      }
      flushSpokenSentences(reply, speech, false); // legge le frasi gia' complete
      if (_sb && log) log.scrollTop = log.scrollHeight;
    };
    var fail = function (text) {
      if (settled) return;
      settled = true;
      if (!reply && ph && ph.parentNode) ph.parentNode.removeChild(ph);
      ph = null; bubble = null; statusLine = null;
      sysNote(text);
      setOrb('idle', 0);
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
        done: function (d) {
          if (!reply && d && d.reply) showToken(d.reply);
          if (!reply) showToken('Ricevuto.');
          finish('\u2713 completato');
          if (d && d.delegations && d.delegations.length) d.delegations.forEach(renderTask);
        },
        error: function (d) {
          fail((d && d.error) ? ('Hermes Prime: ' + d.error) : 'Hermes Prime non ha completato la risposta.');
        }
      });
    }).then(function () {
      if (!settled && reply) finish('\u2713 risposta ricevuta');
      else if (!settled) fail('La risposta di Hermes Prime si è interrotta prima del completamento.');
    })
      .catch(function () {
        fail('Non riesco a contattare Hermes Prime (bridge). Riprova tra poco.');
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

  function mountPlanet(g, tries) {
    tries = tries || 0;
    var pc = $('cbPlanet'); if (!pc || pc.getAttribute('data-mounted')) return;
    if (typeof window.cbInitPlanet === 'function') {
      try {
        window.cbInitPlanet(pc, g);
        pc.setAttribute('data-mounted', '1');
        var core = document.querySelector('.cb-core'); if (core) core.style.display = 'none';
        var hint = document.querySelector('.cb-stage-hint'); if (hint) hint.textContent = 'Vault Planet';
      } catch (e) { /* keep CSS core fallback */ }
    } else if (tries < 40) {
      setTimeout(function () { mountPlanet(g, tries + 1); }, 250); // wait for three.js module to load
    }
  }

  function refresh() {
    api('api/vault/graph').then(function (g) { renderStats(g); mountPlanet(g); }).catch(function () {});
    api('api/projects/overview').then(renderProjects).catch(function () {
      var grid = $('cbGrid'); if (grid) grid.innerHTML = '<div class="cb-loading">vault non disponibile.</div>';
    });
  }

  /* ── entry point (called by switchPanel) ───────────────────────────────── */
  window.loadCommandBridge = function () {
    if (!BUILT) { if (!build()) return Promise.resolve(); }
    refresh();
    startTaskPolling();
    return Promise.resolve();
  };
})();
