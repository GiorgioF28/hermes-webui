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
'.cb-deleg{align-self:flex-start;max-width:96%;border:1px solid var(--cb-accent-dim);border-left:2px solid var(--cb-accent);',
'  border-radius:8px;padding:9px 12px;background:rgba(255,106,0,.05);font-family:var(--cb-mono);font-size:11px;}',
'.cb-deleg b{color:var(--cb-accent-2);font-weight:600;}',
'.cb-chat-input{display:flex;gap:8px;padding:14px 16px;border-top:1px solid var(--cb-line2);}',
'.cb-chat-input input{flex:1;background:rgba(255,255,255,.04);border:1px solid var(--cb-line);border-radius:10px;',
'  color:var(--cb-text);font-family:var(--cb-sans);font-size:13.5px;padding:11px 14px;outline:none;}',
'.cb-chat-input input:focus{border-color:var(--cb-accent);box-shadow:0 0 0 3px rgba(255,106,0,.12);}',
'.cb-send{background:var(--cb-accent);border:none;color:#1a0c00;font-weight:700;border-radius:10px;width:42px;cursor:pointer;',
'  font-family:var(--cb-disp);display:flex;align-items:center;justify-content:center;transition:filter .15s;}',
'.cb-mic{background:rgba(255,255,255,.05);border:1px solid var(--cb-line);color:var(--cb-muted);border-radius:10px;width:42px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:.15s;flex:0 0 auto;}',
'.cb-mic:hover{color:var(--cb-text);border-color:var(--cb-accent);}',
'.cb-mic.cb-on{color:#1a0c00;background:var(--cb-accent);border-color:var(--cb-accent);animation:cb-micpulse 1.1s ease-in-out infinite;}',
'@keyframes cb-micpulse{0%,100%{box-shadow:0 0 0 0 rgba(255,106,0,.5);}50%{box-shadow:0 0 0 6px rgba(255,106,0,0);}}',
'.cb-send:hover{filter:brightness(1.12);}',
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
          '<form class="cb-chat-input" id="cbForm" autocomplete="off">' +
            '<button type="button" class="cb-mic" id="cbMic" aria-label="Parla a voce" title="Parla a voce">' +
              '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2" width="6" height="11" rx="3"/><path d="M5 10a7 7 0 0 0 14 0"/><path d="M12 17v4"/></svg></button>' +
            '<input id="cbInput" placeholder="Parla con Hermes Prime…" aria-label="Messaggio a Hermes Prime">' +
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
    var mic = $('cbMic');
    if (mic) mic.addEventListener('click', toggleListen);
    var vb = $('cbVoice');
    if (vb) vb.addEventListener('click', function () { userEngaged = true; toggleVoice(); });
    primeSay('prime', 'Plancia online. Ti dò il quadro quando vuoi — chiedimi "cosa serve oggi?" e ti briffo. Premi il microfono per parlarmi a voce.');
    BUILT = true;
    return true;
  }

  /* ── Hermes Prime + Voice (Phase 5). Real Claude/Codex delegation: Phase 6. ── */
  var voiceOn = true, userEngaged = false, _ctx = null, _an = null, _raf = null, _cur = null, _rec = null;

  function setOrb(state, amp) {
    if (!window.cbPlanet) return;
    if (state != null) window.cbPlanet.setState(state);
    if (amp != null) window.cbPlanet.setAmplitude(amp);
  }
  function _ensureCtx() {
    if (!_ctx) {
      var AC = window.AudioContext || window.webkitAudioContext; if (!AC) return null;
      _ctx = new AC(); _an = _ctx.createAnalyser(); _an.fftSize = 512; _an.connect(_ctx.destination);
    }
    return _ctx;
  }
  function speak(text) {
    text = String(text || '').trim(); if (!text || !voiceOn) return;
    try { if (_cur) { _cur.pause(); _cur = null; } } catch (e) {}
    setOrb('thinking', 0);
    var cfg = window.__HERMES_CONFIG__ || {};
    fetch(new URL('api/tts', document.baseURI || location.href).href, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': cfg.csrfToken || '' },
      body: JSON.stringify({ text: text.slice(0, 4800), voice: 'it-IT-ElsaNeural' })
    }).then(function (r) { if (!r.ok) throw new Error('tts ' + r.status); return r.blob(); })
      .then(function (blob) {
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
        audio.onended = function () { cancelAnimationFrame(_raf); setOrb('idle', 0); URL.revokeObjectURL(url); if (_cur === audio) _cur = null; };
        return audio.play();
      }).catch(function () { setOrb('idle', 0); }); // TTS unavailable (edge-tts not installed) -> silent
  }
  function toggleListen() {
    userEngaged = true;
    var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    var mic = $('cbMic');
    if (!SR) { if (mic) mic.title = 'Riconoscimento vocale non supportato dal browser'; return; }
    if (_rec) { try { _rec.stop(); } catch (e) {} _rec = null; return; }
    var rec = new SR(); _rec = rec; rec.lang = 'it-IT'; rec.interimResults = false; rec.maxAlternatives = 1;
    if (mic) mic.classList.add('cb-on');
    setOrb('listening', 0.25);
    rec.onresult = function (e) {
      var t = e.results[0][0].transcript; var inp = $('cbInput');
      if (inp) inp.value = t;
      var form = $('cbForm');
      if (form && form.requestSubmit) form.requestSubmit(); else onPrimeSubmit({ preventDefault: function () {} });
    };
    var done = function () { if (mic) mic.classList.remove('cb-on'); if (_rec === rec) { setOrb('idle', 0); _rec = null; } };
    rec.onend = done; rec.onerror = done;
    try { rec.start(); } catch (e) { done(); }
  }
  function toggleVoice() {
    voiceOn = !voiceOn;
    var b = $('cbVoice'); if (b) b.classList.toggle('cb-on', voiceOn);
    var role = document.querySelector('.cb-chat-role'); if (role) role.textContent = 'chief of staff · ' + (voiceOn ? 'voce attiva' : 'voce muta');
    if (!voiceOn) { try { if (_cur) { _cur.pause(); _cur = null; } } catch (e) {} cancelAnimationFrame(_raf); setOrb('idle', 0); }
  }

  function primeSay(who, text) {
    var log = $('cbLog'); if (!log) return;
    var m = el('div', 'cb-msg ' + (who === 'user' ? 'cb-from-user' : 'cb-from-prime'));
    m.innerHTML = '<div class="cb-who">' + (who === 'user' ? 'tu' : 'hermes prime') + '</div><div class="cb-bubble">' + esc(text) + '</div>';
    log.appendChild(m); log.scrollTop = log.scrollHeight;
    if (who === 'prime' && userEngaged) speak(text);
  }
  function onPrimeSubmit(e) {
    if (e && e.preventDefault) e.preventDefault();
    userEngaged = true;
    var inp = $('cbInput'); if (!inp) return;
    var v = inp.value.trim(); if (!v) return;
    inp.value = '';
    primeSay('user', v);
    setOrb('thinking', 0);
    setTimeout(function () {
      primeSay('prime', 'Ricevuto. Nella prossima fase collego davvero Claude e Codex e inizio a delegare ai sotto-agenti. Per ora ti parlo e il core del pianeta pulsa con la mia voce.');
    }, 450);
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
    return Promise.resolve();
  };
})();
