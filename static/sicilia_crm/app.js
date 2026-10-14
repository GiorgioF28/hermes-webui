(() => {
  'use strict';

  const $ = (selector) => document.querySelector(selector);
  const app = $('.app-shell');
  const map = $('#map');
  const world = $('#map-world');
  const markers = $('#map-markers');
  const state = { records: [], demo: false, selected: null, query: '', csrf: '', center: { lat: 37.45, lon: 14.05 }, zoom: 7,
    drag: null, tilesFailed: false, pin: null, fix: null, recognition: null, transcript: '', pendingRequestId: null, saveFailed: false };
  const fakeRecords = [
    { id: 'demo-1', name: 'Esempio Contatto A', address: 'Via dimostrativa 12 · Catania', outcome: 'potenziale', created_at: '2026-10-12T09:35:00Z', latitude: 37.5079, longitude: 15.0830, location_source: 'declared', phone: '', floor: '', staircase: '', unit: '', notes: 'Record sintetico illustrativo.', author: 'demo' },
    { id: 'demo-2', name: 'Esempio Contatto B', address: 'Indirizzo sintetico · Palermo', outcome: 'assente', created_at: '2026-10-10T14:10:00Z', latitude: 38.1157, longitude: 13.3615, location_source: 'declared', phone: '', floor: '', staircase: '', unit: '', notes: 'Record sintetico illustrativo.', author: 'demo' },
  ];
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
  const visibleRecords = () => (state.demo ? [...fakeRecords, ...state.records] : state.records).filter((r) => {
    const q = state.query.trim().toLocaleLowerCase('it');
    return !q || `${r.name} ${r.address} ${r.phone}`.toLocaleLowerCase('it').includes(q);
  });

  async function api(path, options = {}) {
    const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
    if (options.method === 'POST' && state.csrf) headers['X-CSRF-Token'] = state.csrf;
    const response = await fetch(path, { credentials: 'same-origin', ...options, headers });
    let body;
    try { body = await response.json(); } catch { body = {}; }
    if (!response.ok) throw new Error(body.error || `Richiesta non riuscita (${response.status})`);
    return body;
  }

  function project(lat, lon, zoom = state.zoom) {
    const scale = 256 * (2 ** zoom);
    const sin = Math.sin(Math.max(-85.0511, Math.min(85.0511, lat)) * Math.PI / 180);
    return { x: (lon + 180) / 360 * scale, y: (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * scale };
  }
  function unproject(x, y, zoom = state.zoom) {
    const scale = 256 * (2 ** zoom);
    const lon = x / scale * 360 - 180;
    const n = Math.PI - 2 * Math.PI * y / scale;
    const lat = 180 / Math.PI * Math.atan(Math.sinh(n));
    return { lat, lon };
  }
  function renderMap() {
    const rect = map.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const centerPx = project(state.center.lat, state.center.lon);
    const left = centerPx.x - rect.width / 2;
    const top = centerPx.y - rect.height / 2;
    const minX = Math.floor(left / 256);
    const maxX = Math.floor((left + rect.width) / 256);
    const minY = Math.max(0, Math.floor(top / 256));
    const maxY = Math.min((2 ** state.zoom) - 1, Math.floor((top + rect.height) / 256));
    const tiles = [];
    for (let y = minY; y <= maxY; y += 1) for (let x = minX; x <= maxX; x += 1) {
      const wrappedX = ((x % (2 ** state.zoom)) + (2 ** state.zoom)) % (2 ** state.zoom);
      tiles.push(`<img class="tile" referrerpolicy="origin" draggable="false" alt="" src="https://tile.openstreetmap.org/${state.zoom}/${wrappedX}/${y}.png" style="left:${Math.round(x * 256 - left)}px;top:${Math.round(y * 256 - top)}px">`);
    }
    world.innerHTML = tiles.join('');
    world.querySelectorAll('img').forEach((img) => {
      img.addEventListener('load', () => { $('#tile-status').hidden = true; state.tilesFailed = false; });
      img.addEventListener('error', () => {
        state.tilesFailed = true;
        const status = $('#tile-status'); status.hidden = false;
        status.textContent = 'Cartografia non disponibile · elenco e scheda restano utilizzabili';
      });
    });
    markers.setAttribute('viewBox', `0 0 ${rect.width} ${rect.height}`);
    const pinRecords = visibleRecords().filter((r) => Number.isFinite(Number(r.pin_latitude ?? r.latitude)) && Number.isFinite(Number(r.pin_longitude ?? r.longitude)));
    markers.innerHTML = pinRecords.map((record, index) => {
      const p = project(Number(record.pin_latitude ?? record.latitude), Number(record.pin_longitude ?? record.longitude));
      const x = p.x - left;
      const y = p.y - top;
      if (x < -30 || x > rect.width + 30 || y < -35 || y > rect.height + 30) return '';
      return `<g class="map-marker" data-record-id="${esc(record.id)}" role="button" tabindex="0" aria-label="Apri ${esc(record.name)}" transform="translate(${x.toFixed(1)} ${y.toFixed(1)})"><circle r="13"></circle><text y=".5">${index + 1}</text></g>`;
    }).join('');
    $('#map-empty').hidden = visibleRecords().length > 0;
    $('#record-count').textContent = `${visibleRecords().length} ${visibleRecords().length === 1 ? 'record' : 'record'}`;
  }

  function relativeDate(value) {
    if (!value) return '';
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return '';
    return new Intl.DateTimeFormat('it-IT', { day: '2-digit', month: 'short' }).format(date);
  }
  function renderList() {
    const list = $('#record-list');
    const rows = visibleRecords();
    if (state.selected && !rows.some((r) => r.id === state.selected)) state.selected = null;
    list.innerHTML = rows.length ? rows.map((record, index) => `<button type="button" class="record-row ${state.selected === record.id ? 'selected' : ''}" data-record-id="${esc(record.id)}"><span class="record-avatar ${String(record.id).startsWith('demo-') ? 'demo-badge' : ''}">${esc((record.name || '?').trim().slice(0, 1).toUpperCase())}</span><span class="record-copy"><strong>${esc(record.name || 'Nome assente')}</strong><span>${esc(record.address || 'Indirizzo non indicato')} · ${esc(outcomeLabel(record.outcome))}</span></span><time class="record-meta">${String(record.id).startsWith('demo-') ? 'DEMO' : esc(relativeDate(record.created_at))}</time></button>`).join('') : `<div class="list-empty"><span class="empty-illustration">⌖</span><strong>${state.query ? 'Nessun risultato' : 'Ancora nessun record'}</strong><span>${state.query ? 'Prova con un nome o un indirizzo diverso.' : 'I tuoi record salvati appariranno qui. Puoi iniziare con un inserimento manuale.'}</span></div>`;
    $('#list-caption').textContent = state.demo ? 'Demo sintetica separata · nessun dato demo salvato' : 'Dati personali · nessun filtro';
    $('#connection-state').textContent = state.demo ? 'Demo sintetica attiva · i record demo non vengono salvati' : 'Dati caricati dal CRM';
    renderDetail();
    renderMap();
  }
  function outcomeLabel(value) { return ({ assente: 'Assente', non_interessato: 'Non interessato', potenziale: 'Potenziale', altro: 'Altro', non_indicato: 'Esito non indicato' })[value] || 'Esito non indicato'; }
  function showDetail(record) {
    state.selected = record.id;
    document.querySelectorAll('.record-row').forEach((row) => row.classList.toggle('selected', row.dataset.recordId === record.id));
    const details = $('#record-detail');
    details.hidden = false;
    const pinLat = record.pin_latitude ?? record.latitude; const pinLon = record.pin_longitude ?? record.longitude;
    const originalFix = record.location_source === 'pin_correction' && record.captured_at && Number.isFinite(Number(record.latitude));
    details.innerHTML = `<div class="detail-heading"><h3>Dettaglio record</h3><button class="detail-close" type="button">Chiudi</button></div><div class="detail-grid"><div class="detail-item"><small>Nome</small><span>${esc(record.name || '—')}</span></div><div class="detail-item"><small>Esito visita</small><span>${esc(outcomeLabel(record.outcome))}</span></div><div class="detail-item"><small>Recapito</small><span>${esc(record.phone || 'Non indicato')}</span></div><div class="detail-item"><small>Indirizzo</small><span>${esc(record.address || 'Non indicato')}</span></div><div class="detail-item"><small>Scala · interno · piano</small><span>${esc([record.staircase, record.unit, record.floor].filter(Boolean).join(' · ') || 'Non specificati')}</span></div><div class="detail-item"><small>Modello aspirapolvere</small><span>${esc(record.vacuum_model || 'Non indicato')}</span></div><div class="detail-item"><small>Posizione / pin</small><span>${Number.isFinite(Number(pinLat)) ? `${Number(pinLat).toFixed(5)}, ${Number(pinLon).toFixed(5)} · pin ${esc(record.location_source || 'non indicato')}${originalFix ? ` · fix originale ${Number(record.latitude).toFixed(5)}, ${Number(record.longitude).toFixed(5)}` : ''}` : 'Non acquisita'}</span></div><div class="detail-item"><small>Autore · salvato</small><span>${esc(record.author || '—')} · ${esc(relativeDate(record.created_at))}</span></div><div class="detail-item"><small>Appuntamento</small><span>${esc(record.appointment_at || 'Nessuno indicato')}</span></div><div class="detail-item"><small>Note</small><span>${esc(record.notes || 'Nessuna')}</span></div></div>`;
    details.querySelector('.detail-close').addEventListener('click', () => { state.selected = null; details.hidden = true; renderList(); });
  }
  function renderDetail() {
    const row = visibleRecords().find((r) => r.id === state.selected);
    if (row) showDetail(row);
    else $('#record-detail').hidden = true;
  }

  async function refreshRecords() {
    $('#connection-state').textContent = 'Caricamento record…';
    try {
      const result = await api('/api/sicilia-crm/records');
      state.records = Array.isArray(result.records) ? result.records : [];
      state.csrf = typeof result.csrf_token === 'string' ? result.csrf_token : '';
      renderList();
    } catch (error) {
      $('#connection-state').textContent = `CRM non disponibile: ${error.message}`;
      $('#record-list').innerHTML = `<div class="list-empty"><strong>Connessione al CRM non riuscita</strong><span>${esc(error.message)}. La scheda manuale resta disponibile, ma il salvataggio richiede l'API integrata e autenticata.</span></div>`;
      renderMap();
    }
  }

  const formDialog = $('#record-dialog');
  const form = $('#record-form');
  function resetForm() {
    form.reset();
    state.pin = null;
    state.fix = null;
    state.pendingRequestId = null;
    state.saveFailed = false;
    $('#captured-fix').hidden = true;
    $('#declared-note').hidden = true;
    $('#correction-field').hidden = true;
    $('#location-state').textContent = 'Nessuna posizione acquisita';
    $('#gps-message').textContent = 'La posizione viene acquisita una sola volta, solo dopo aver premuto il pulsante.';
    $('#form-error').hidden = true;
  }
  function openRecord(draft = null) {
    resetForm();
    state.pendingRequestId = crypto.randomUUID();
    if (draft) Object.entries(draft).forEach(([key, value]) => {
      const field = form.elements.namedItem(key);
      if (field && typeof value === 'string') field.value = value;
    });
    formDialog.showModal();
  }
  function maybeDeclaredPin() {
    const lat = form.elements.namedItem('pin_latitude').value.trim();
    const lon = form.elements.namedItem('pin_longitude').value.trim();
    if (!lat && !lon) return;
    const latNum = Number(lat.replace(',', '.'));
    const lonNum = Number(lon.replace(',', '.'));
    if (Number.isFinite(latNum) && Number.isFinite(lonNum) && latNum >= -90 && latNum <= 90 && lonNum >= -180 && lonNum <= 180) {
      state.pin = { lat: latNum, lon: lonNum };
      $('#declared-note').hidden = !state.fix;
      $('#correction-field').hidden = !state.fix;
      $('#location-state').textContent = state.fix ? 'Fix registrato · pin corretto/dichiarato' : 'Pin dichiarato manualmente';
    }
  }
  function setPin(lat, lon, source = 'declared') {
    state.pin = { lat, lon, source };
    form.elements.namedItem('pin_latitude').value = lat.toFixed(6);
    form.elements.namedItem('pin_longitude').value = lon.toFixed(6);
    $('#declared-note').hidden = !state.fix;
    $('#correction-field').hidden = !state.fix;
    $('#location-state').textContent = state.fix ? 'Fix originale conservato · pin corretto' : 'Pin dichiarato sulla mappa';
    $('#gps-message').textContent = state.fix ? 'Il punto dichiarato è distinto dal fix originale acquisito.' : 'Pin indicato manualmente sulla mappa; non è un fix GPS.';
  }

  $('#new-record').addEventListener('click', () => openRecord());
  $('.close-dialog').addEventListener('click', () => formDialog.close());
  $('.cancel-dialog').addEventListener('click', () => formDialog.close());
  formDialog.addEventListener('close', resetForm);
  form.elements.namedItem('pin_latitude').addEventListener('input', maybeDeclaredPin);
  form.elements.namedItem('pin_longitude').addEventListener('input', maybeDeclaredPin);
  form.addEventListener('input', () => {
    if (state.saveFailed) {
      // An edit after an ambiguous network failure starts a new explicit save;
      // a plain retry without edits reuses the idempotency key below.
      state.pendingRequestId = crypto.randomUUID();
      state.saveFailed = false;
    }
  });
  $('#capture-location').addEventListener('click', () => {
    const message = $('#gps-message');
    if (!window.isSecureContext) { message.textContent = 'Geolocalizzazione non disponibile: serve un contesto HTTPS o localhost. Puoi inserire le coordinate manualmente.'; return; }
    if (!navigator.geolocation) { message.textContent = 'Questo browser non supporta la geolocalizzazione. Puoi inserire le coordinate manualmente.'; return; }
    message.textContent = 'Richiesta posizione in corso…';
    navigator.geolocation.getCurrentPosition((position) => {
      state.fix = { latitude: position.coords.latitude, longitude: position.coords.longitude,
        accuracy_m: position.coords.accuracy, altitude_m: Number.isFinite(position.coords.altitude) ? position.coords.altitude : null,
        altitude_accuracy_m: Number.isFinite(position.coords.altitudeAccuracy) ? position.coords.altitudeAccuracy : null,
        captured_at: new Date(position.timestamp).toISOString() };
      if (!state.pin) setPin(state.fix.latitude, state.fix.longitude, 'device');
      $('#location-state').textContent = 'Fix una tantum acquisito';
      $('#captured-fix').hidden = false;
      $('#captured-fix').textContent = `Fix originale: ${state.fix.latitude.toFixed(6)}, ${state.fix.longitude.toFixed(6)} · precisione ${Math.round(state.fix.accuracy_m)} m · ${state.fix.altitude_m === null ? 'altitudine non disponibile' : `altitudine ${Math.round(state.fix.altitude_m)} m (non usata per dedurre il piano)`}`;
      message.textContent = 'Posizione acquisita una volta. Nessun tracciamento attivo.';
    }, (error) => {
      const messages = { 1: 'Permesso posizione negato. Puoi inserire un pin dichiarato o continuare senza posizione.', 2: 'Posizione non disponibile. Puoi inserire un pin dichiarato o continuare senza posizione.', 3: 'Tempo scaduto durante la richiesta posizione. Riprova oppure inserisci le coordinate manualmente.' };
      message.textContent = messages[error.code] || 'Geolocalizzazione non riuscita. Puoi inserire la posizione manualmente.';
    }, { enableHighAccuracy: true, timeout: 12000, maximumAge: 0 });
  });

  function romeIso(localValue) {
    if (!localValue) return '';
    const [datePart, timePart] = localValue.split('T');
    const probe = new Date(`${datePart}T${timePart}:00Z`);
    const offsetText = new Intl.DateTimeFormat('en', { timeZone: 'Europe/Rome', timeZoneName: 'shortOffset' }).formatToParts(probe).find((p) => p.type === 'timeZoneName')?.value || 'GMT+1';
    const match = offsetText.match(/GMT([+-])(\d{1,2})(?::(\d{2}))?/);
    const offset = match ? `${match[1]}${String(match[2]).padStart(2, '0')}:${match[3] || '00'}` : '+01:00';
    return `${datePart}T${timePart}:00${offset}`;
  }
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const errorBox = $('#form-error');
    errorBox.hidden = true;
    const save = $('#save-record'); save.disabled = true; save.textContent = 'Salvataggio…';
    const data = new FormData(form);
    const pinLat = data.get('pin_latitude') ? Number(String(data.get('pin_latitude')).replace(',', '.')) : null;
    const pinLon = data.get('pin_longitude') ? Number(String(data.get('pin_longitude')).replace(',', '.')) : null;
    const isCorrected = !!(state.fix && state.pin && (state.pin.lat !== state.fix.latitude || state.pin.lon !== state.fix.longitude));
    const payload = {
      client_request_id: state.pendingRequestId || crypto.randomUUID(), name: data.get('name'), phone: data.get('phone'), address: data.get('address'),
      staircase: data.get('staircase'), unit: data.get('unit'), floor: data.get('floor'), outcome: data.get('outcome'),
      vacuum_model: data.get('vacuum_model'), notes: data.get('notes'), appointment_at: romeIso(data.get('appointment_at')),
      location_source: state.fix ? (isCorrected ? 'pin_correction' : 'device') : (state.pin ? 'declared' : 'none'),
      latitude: state.fix?.latitude ?? pinLat, longitude: state.fix?.longitude ?? pinLon,
      accuracy_m: state.fix?.accuracy_m ?? null, altitude_m: state.fix?.altitude_m ?? null,
      altitude_accuracy_m: state.fix?.altitude_accuracy_m ?? null, captured_at: state.fix?.captured_at ?? '',
      declared_latitude: isCorrected ? pinLat : (state.fix ? null : pinLat),
      declared_longitude: isCorrected ? pinLon : (state.fix ? null : pinLon), correction_reason: data.get('correction_reason'),
    };
    if (!payload.name.trim()) { errorBox.textContent = 'Inserisci il nome del contatto.'; errorBox.hidden = false; save.disabled = false; save.textContent = 'Salva record'; return; }
    try {
      const result = await api('/api/sicilia-crm/records', { method: 'POST', body: JSON.stringify(payload) });
      const previous = state.records.find((record) => record.id === result.record.id);
      if (!previous) state.records.unshift(result.record);
      state.selected = result.record.id;
      state.saveFailed = false;
      formDialog.close();
      renderList();
    } catch (error) {
      state.saveFailed = true;
      errorBox.textContent = `Salvataggio non riuscito: ${error.message}. Il modulo resta aperto; puoi riprovare senza perdere i dati.`;
      errorBox.hidden = false;
    } finally { save.disabled = false; save.textContent = 'Salva record'; }
  });

  const aiDialog = $('#ai-dialog');
  $('#open-ai').addEventListener('click', () => aiDialog.showModal());
  $('.close-ai').addEventListener('click', () => stopSpeech(true));
  $('.cancel-ai').addEventListener('click', () => stopSpeech(true));
  aiDialog.addEventListener('close', () => { stopSpeech(true); $('#ai-error').hidden = true; });
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (SpeechRecognition) {
    const recognition = new SpeechRecognition(); state.recognition = recognition;
    recognition.lang = 'it-IT'; recognition.interimResults = true; recognition.continuous = true;
    recognition.onstart = () => { $('#mic-state').textContent = 'Registrazione attiva · parla ora'; $('#ai-orb').classList.add('listening'); $('#start-voice').disabled = true; $('#stop-voice').disabled = false; };
    recognition.onresult = (event) => {
      let value = $('#transcript').value;
      const start = Number($('#transcript').dataset.voiceStart || value.length);
      value = value.slice(0, start);
      for (let i = 0; i < event.results.length; i += 1) value += event.results[i][0].transcript;
      $('#transcript').value = value.slice(0, 2000);
    };
    recognition.onerror = (event) => { $('#mic-state').textContent = event.error === 'not-allowed' ? 'Permesso microfono negato · puoi scrivere il testo.' : `Ascolto interrotto (${event.error}) · trascrizione preservata.`; };
    recognition.onend = () => { $('#mic-state').textContent = 'Microfono inattivo. Puoi correggere la trascrizione.'; $('#ai-orb').classList.remove('listening'); $('#start-voice').disabled = false; $('#stop-voice').disabled = true; };
    $('#speech-support').textContent = 'Voce disponibile';
    $('#start-voice').addEventListener('click', () => { $('#transcript').dataset.voiceStart = String($('#transcript').value.length); try { recognition.start(); } catch { $('#mic-state').textContent = 'Microfono già attivo.'; } });
    $('#stop-voice').addEventListener('click', () => stopSpeech(false));
  } else {
    $('#speech-support').textContent = 'Voce non supportata · usa il testo';
    $('#start-voice').hidden = true; $('#stop-voice').hidden = true;
  }
  function stopSpeech(close) {
    try { state.recognition?.stop(); } catch { /* no active capture */ }
    if (close && aiDialog.open) aiDialog.close();
  }
  $('#make-draft').addEventListener('click', async () => {
    const text = $('#transcript').value.trim();
    const error = $('#ai-error'); error.hidden = true;
    if (!text) { error.textContent = 'Scrivi o trascrivi i dettagli prima di creare la bozza.'; error.hidden = false; return; }
    stopSpeech(false);
    const button = $('#make-draft'); button.disabled = true; button.textContent = 'Elaborazione…';
    try {
      const result = await api('/api/sicilia-crm/drafts', { method: 'POST', body: JSON.stringify({ transcript: text }) });
      aiDialog.close();
      openRecord(result.draft);
      if (result.draft.clarification) {
        $('#form-error').textContent = `Chiarimento richiesto: ${result.draft.clarification}`;
        $('#form-error').hidden = false;
      }
    } catch (failure) {
      error.textContent = `AI non disponibile: ${failure.message}. Il testo resta qui; puoi copiare i dati nel modulo manuale.`;
      error.hidden = false;
    } finally { button.disabled = false; button.textContent = 'Crea bozza'; }
  });

  $('#search').addEventListener('input', (event) => { state.query = event.target.value; renderList(); });
  $('#record-list').addEventListener('click', (event) => {
    const row = event.target.closest('.record-row');
    if (row) { const record = visibleRecords().find((item) => item.id === row.dataset.recordId); if (record) showDetail(record); }
  });
  $('#refresh').addEventListener('click', refreshRecords);
  $('#demo-toggle').addEventListener('click', (event) => {
    state.demo = !state.demo; app.classList.toggle('demo-mode', state.demo);
    event.currentTarget.setAttribute('aria-pressed', String(state.demo));
    event.currentTarget.textContent = state.demo ? 'Nascondi demo sintetica' : 'Mostra demo sintetica';
    renderList();
  });
  $('#zoom-in').addEventListener('click', () => { state.zoom = Math.min(18, state.zoom + 1); renderMap(); });
  $('#zoom-out').addEventListener('click', () => { state.zoom = Math.max(3, state.zoom - 1); renderMap(); });
  $('#center-map').addEventListener('click', () => { state.center = { lat: 37.45, lon: 14.05 }; state.zoom = 7; renderMap(); });
  $('#map-add-pin').addEventListener('click', () => {
    if (!state.pin) {
      const r = map.getBoundingClientRect();
      const center = project(state.center.lat, state.center.lon);
      setPin(...Object.values(unproject(center.x, center.y)));
      return;
    }
    const point = state.pin; setPin(point.lat, point.lon);
  });
  map.addEventListener('pointerdown', (event) => { if (event.target.closest('.map-attribution,.map-pin-action')) return; state.drag = { x: event.clientX, y: event.clientY, center: { ...project(state.center.lat, state.center.lon) }, moved: false }; map.setPointerCapture(event.pointerId); });
  map.addEventListener('pointermove', (event) => {
    if (!state.drag) return;
    const dx = event.clientX - state.drag.x; const dy = event.clientY - state.drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) state.drag.moved = true;
    if (state.drag.moved) { state.center = unproject(state.drag.center.x - dx, state.drag.center.y - dy); renderMap(); }
  });
  map.addEventListener('pointerup', (event) => {
    if (!state.drag) return;
    const wasMoved = state.drag.moved;
    if (!wasMoved && formDialog.open && !event.target.closest('.map-attribution,.map-pin-action')) {
      const rect = map.getBoundingClientRect();
      const center = project(state.center.lat, state.center.lon);
      const point = unproject(center.x + (event.clientX - rect.left - rect.width / 2), center.y + (event.clientY - rect.top - rect.height / 2));
      setPin(point.lat, point.lon);
    } else if (!wasMoved && event.target.closest('.map-marker')) {
      const record = visibleRecords().find((r) => r.id === event.target.closest('.map-marker').dataset.recordId);
      if (record) showDetail(record);
    }
    state.drag = null;
  });
  markers.addEventListener('click', (event) => {
    const marker = event.target.closest('.map-marker');
    if (marker) { const record = visibleRecords().find((r) => r.id === marker.dataset.recordId); if (record) showDetail(record); }
  });
  markers.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') event.target.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
  map.addEventListener('click', () => { if (formDialog.open) { $('#map-add-pin').hidden = false; $('#map-add-pin').textContent = 'Imposta pin dal centro mappa'; } });
  window.addEventListener('resize', renderMap);

  renderMap();
  refreshRecords();
})();
