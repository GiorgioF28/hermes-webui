# Command Bridge — Handoff per Codex (dove mettere le mani)

> Scopo di questo file: dare a Codex (o a qualsiasi dev/agent) il contesto e la
> **mappa esatta** del Command Bridge, così può applicare fix mirati senza
> ri-esplorare tutto. Branch attivo: `giorgio/persistent-cli-bridge`.
> Ultimo aggiornamento: 2026-06-12.

---

## 1. Cosa stiamo costruendo

Una **home/opening section** della Hermes WebUI chiamata **Command Bridge**: la
"plancia di comando" di Hermes. Tre zone:

1. **Sinistra** — chat con **Hermes Prime** (l'agente capo / chief of staff), con voce.
2. **Centro** — la **Vault Planet**: il vault Obsidian renderizzato come un globo
   trasparente 3D (three.js) con un **albero radiale** dentro (core bianco→foglie
   arancio). Il **core È il Voice Orb**: pulsa con la voce di Hermes.
3. **Sotto (scroll)** — **Projects Strip**: card dei progetti (3 per riga) con
   ultime modifiche + task "up next", dati reali dal vault.

Estetica: near-black "starship bridge", core **giallo** incandescente, **gradiente
bianco→arancio fluo sui titoli** (orizzontale: prime lettere bianche, ultime
arancio), font **Chakra Petch** (display) + **IBM Plex Mono/Sans** (dati/testo).

Stack: **Python + vanilla JS, niente bundler, niente React.** Il 3D è **three.js
vendored** (non react-three-fiber).

---

## 2. Ambiente / come girare e testare

- WebUI repo: `C:\Users\giorg\Documents\Hermes setup\hermes-webui`
- Agent repo: `C:\Users\giorg\AppData\Local\hermes\hermes-agent` (origin/main)
- **venv del server** (gestito con uv, NO pip): `C:\Users\giorg\AppData\Local\hermes\hermes-agent\venv`
  - install pacchetti: `uv pip install --python "<venv>\Scripts\python.exe" <pkg>`
- Avvio: `scripts\serve-hermes-webui.cmd` → `start.ps1 -Port 8788` (porta **8788**).
  - `start.ps1` lancia con **`-X utf8`** (obbligatorio su Windows: senza, crash cp1252).
- **Modifiche a file `.py` (backend) → serve RIAVVIARE il server.**
  Modifiche a `static/*.js|css|html` → basta **Ctrl+F5** nel browser.
- Test rapido endpoint: `GET http://127.0.0.1:8788/api/vault/graph` deve dare JSON.

---

## 3. Mappa file del Command Bridge (DOVE mettere le mani)

### Backend (`api/`)
| File | Cosa fa | Funzioni chiave |
|---|---|---|
| `api/vault_graph.py` | Costruisce il grafo del vault (nodi/edge) per il pianeta. Legge `<workspace>/obsidian-vault` via fs. Cache mtime/TTL. | `build_graph(vault_path)`, `get_vault_graph(vault_path)`. Progetti = `01-Projects/`, agenti = `06-Agents/`. |
| `api/projects_overview.py` | Dati per le card progetti (task aperti, ultime modifiche, attività 14gg). | `build_projects_overview(vault_path)`, `parse_open_tasks(body)`. CONFIG in cima (heading TODO/Next, ecc.). |
| `api/routes.py` | Registra le route + handler. | Route registrate in `handle_get` (cerca `"/api/vault/graph"` e `"/api/projects/overview"`). Handler: `_handle_vault_graph`, `_handle_projects_overview`. **TTS**: `_handle_tts` (cerca `def _handle_tts`); la **allowlist voci** è il set `allowed = {...}` lì dentro (ci ho aggiunto le voci `it-IT-*`). |

### Frontend (`static/`)
| File | Cosa fa |
|---|---|
| `static/command_bridge.js` | **IL FILE PRINCIPALE.** Costruisce tutta la UI del bridge (shell 3 zone, chat Hermes Prime, projects strip), **inietta CSS e font**, e contiene **tutta la logica voce (Phase 5)**. Entry point globale: `window.loadCommandBridge()`. |
| `static/command_planet.js` | **Modulo ES** (`<script type="module">`). Il **pianeta 3D** (three.js): sfera, layout albero-radiale, nodi instanced, edge, OrbitControls, **core/orb**. Espone `window.cbInitPlanet(container, graph)` e `window.cbPlanet = {setAmplitude(a), setState(s)}`. |
| `static/vendor/three/three.module.js` | three.js 0.160 vendored (1.2MB). |
| `static/vendor/three/OrbitControls.js` | OrbitControls (import riscritto da `'three'` → `'./three.module.js'`). |

### Integrazione (modifiche dentro file esistenti, NON riscrivere il resto)
- `static/index.html`: nav tab `data-panel="bridge"` (icona radar, vicino a "command"); contenitore `<div id="mainBridge" class="main-view">`; `<div id="panelBridge" class="panel-view">` (vuoto); gli `<script>` di `command_bridge.js` (defer) e `command_planet.js` (module).
- `static/panels.js`: dentro `switchPanel()` — `'bridge'` è nell'array `showing-*`, e c'è `if (nextPanel === 'bridge') await loadCommandBridge();`.
- `static/style.css`: `main.main.showing-bridge > #mainBridge{display:flex;}` + `:not(.showing-bridge)` aggiunto al selettore catch-all di `#mainChat`.

> NOTA STILE: **il CSS del bridge NON è in style.css** — è **iniettato da `command_bridge.js`** (funzione `injectStyles()`, dentro un grande array di stringhe `css`). I fix di stile (colori, spaziature, gradiente, dimensioni core, ecc.) si fanno **lì**. I font sono caricati da `injectFonts()`.

---

## 4. Struttura Phase 5 — la VOCE (tutto in `static/command_bridge.js`)

Variabili di stato (in cima alla sezione "Hermes Prime + Voice"):
`voiceOn` (voce on/off), `userEngaged` (true dopo la prima interazione — serve per
la regola autoplay del browser), `_ctx`/`_an` (AudioContext + AnalyserNode),
`_cur` (Audio corrente), `_rec` (SpeechRecognition corrente).

Funzioni (cerca per nome nel file):
| Funzione | Cosa fa | Dove agire per fix |
|---|---|---|
| `setOrb(state, amp)` | Inoltra stato/ampiezza al pianeta via `window.cbPlanet`. | — |
| `sysNote(text)` | Aggiunge una riga "sistema" in chat (NON la legge a voce). | messaggi di errore mic |
| `speak(text)` | **TTS**: POST `api/tts` `{text, voice:'it-IT-ElsaNeural'}` → blob → `new Audio()` → `MediaElementSource → AnalyserNode` → loop `pump()` che calcola l'RMS e fa `setOrb('speaking', rms)`. A fine audio → `setOrb('idle')`. | **cambiare voce** = la stringa `voice:'it-IT-ElsaNeural'`; **sensibilità pulsazione** = il fattore `*2.3` in `pump()`. Voci IT valide: Elsa, Isabella, Diego, Giuseppe (devono stare nell'allowlist di `_handle_tts`). |
| `toggleListen()` | **STT**: `webkitSpeechRecognition` lang `it-IT`. `interimResults` mostra il testo mentre parli; a `onend` se c'è testo finale fa submit. `onerror` → `sysNote()` col motivo. | il mic che "si stacca" è quasi sempre `error: network` (Chrome STT usa il **cloud Google**, serve internet). Per STT affidabile offline → **Whisper lato server** (da fare). |
| `toggleVoice()` | Muta/riattiva la voce (`voiceOn`), aggiorna il tasto altoparlante e il testo "voce attiva/muta". | — |
| `primeSay(who, text)` | Aggiunge un messaggio in chat; se `who==='prime'` e `userEngaged` → `speak(text)`. | qui passa ogni risposta di Hermes |
| `onPrimeSubmit(e)` | Invio: `primeSay('user')`, `setOrb('thinking')`, poi **fetch `POST api/bridge/prime` `{message}`** → `primeSay('prime', reply)` (che la legge a voce). | per la **delega** (step 2): tool/handling qui o nel backend `_hermes_prime_reply`. |

UI relativa (dentro `build()` → la stringa HTML): header chat con `#cbVoice`
(tasto altoparlante), input row con `#cbMic` (mic) + `#cbInput` + `.cb-send`.
Wiring dei listener subito dopo `host.appendChild(root)` in `build()`.

Pianeta/orb (`static/command_planet.js`): `window.cbPlanet.setState(s)` accetta
`'idle'|'listening'|'thinking'|'speaking'`; `setAmplitude(0..1)`. Il loop
`animate()` usa questi per scalare/illuminare il `core` (sprite glow) e il
`coreDot`. Per ritoccare il comportamento dell'orb (quanto pulsa, colori stato) →
**lì nel blocco `animate()`** e nelle costanti `CW`/`CCOOL`/glow.

---

## 5. Stato per fase (dove siamo)

- **Phase 0-2 ✅** recon + plan (`docs/COMMAND-BRIDGE-PLAN.md`) + endpoint `vault/graph` e `projects/overview` (con test in `tests/test_vault_graph.py`, `tests/test_projects_overview.py`).
- **Phase 3 ✅** shell 3 zone + Projects Strip.
- **Phase 4 ✅** Vault Planet 3D (three.js) funzionante: sfera, albero radiale, nodi, edge, orbit controls, core.
- **Phase 5 ✅** Voice Orb: TTS Elsa → pulsazione core, STT mic it-IT, 4 stati, tasto mute. **TTS verificato funzionante** (endpoint dà audio/mpeg). STT dipende da Chrome+internet.
- **Phase 6 — FATTO (chief + delega ASINCRONA).** Hermes Prime risponde via `POST /api/bridge/prime` (sessione persistente `hermes-prime`, persona, legge il vault) e **delega in background** ai sotto-agenti (tool `delega` → ritorna subito, Prime continua a chattare; routing **Codex**/Sonnet/Opus per tipo; UI fa polling `GET /api/bridge/tasks` e mostra card `in_corso`→`fatto` + "⚡ ha finito"). Testato end-to-end. **DA FARE:** **streaming** risposta chief (SSE), sotto-agenti **persistenti**, agenti↔progetti, **Discord**.
- **Phase 4-bis ⏳** rifiniture pianeta: hover/click nodo → card dettaglio + camera dolly, **label LOD**, click su card progetto → vola al nodo, distinzione visiva progetti vs agenti.
- **Phase 7 ⏳** polish (mobile/bottom-sheet, reduced-motion, perf 2k nodi, rendere il bridge la landing di default).

---

## 6. Cose da fixare / decisioni prese (per Codex)

**Fix UI/voce (zone già indicate sopra):**
- Ritocchi colore/spaziatura/gradiente/dimensioni → `injectStyles()` in `command_bridge.js`.
- Sensibilità/visual dell'orb → `animate()` in `command_planet.js`.
- Voce/sensibilità TTS → `speak()` in `command_bridge.js`.
- STT che si stacca → è `network` (Chrome cloud). Soluzione robusta: STT server-side (Whisper) — nuovo endpoint + sostituire `toggleListen()`.

**Phase 6 — architettura DECISA con l'utente (importante):**
- **Agenti IBRIDI**: chief + agenti principali **persistenti** (sessione viva + propri MD di memoria nel vault), task secondari/una-tantum **usa-e-getta**.
- **Routing modelli: AUTOMATICO per tipo di task** adesso (codice→Codex, ragionamento→Opus, semplice→Sonnet). In futuro **modello fisso per agente** configurabile (l'utente vuole poter mettere modelli locali su GPU).
- Le **deleghe** devono comparire in chat come **card evento** (agente, task, stato) — la voce resta alto livello, lo schermo porta il dettaglio.
- **Risposta vera: GIÀ FATTA (step 1).** Backend in `api/routes.py`: `_handle_bridge_prime` (route `POST /api/bridge/prime`), `_hermes_prime_reply` (turno via `_get_claude_registry()`, sessione persistente `"hermes-prime"`), `_hermes_prime_system_prompt` (carica `prompts/hermes-prime.md` + `_local_workspace_context`). Frontend: `onPrimeSubmit()` in `command_bridge.js` fa la fetch.
- **STEP 2 (delega) — FATTO, ASINCRONA.** `api/prime_delegation.py`: tool `delega(task_type, task)` agganciato alla sessione `hermes-prime` nel factory di `_get_claude_registry()` (`api/routes.py`, cerca `if session_id == "hermes-prime"`).
  - **Async/non-bloccante**: `delega` crea un task in `_BG_TASKS` (status `in_corso`), lancia il worker con `asyncio.ensure_future(_run_and_store(...))` e **torna subito** a Prime ("delega avviata"). Il worker gira in background sul loop del registry → Prime resta libero di parlare.
  - **Routing modello** (`_model_for`): `codice`→**Codex CLI** (`_run_codex_worker`/`_codex_exec_blocking`, subprocess), `semplice`→Sonnet, altro→Opus.
  - **Polling UI**: `GET /api/bridge/tasks` (`_handle_bridge_tasks` → `get_background_tasks()`). Frontend `command_bridge.js`: `pollTasks()` ogni 3s, `renderTask()` aggiorna la card per id (`in_corso`→`ok`/`errore`) + "⚡ ha finito" (+ voce). `_hermes_prime_reply` ritorna `delegations: get_background_tasks()` per mostrare subito la card "in corso".

### DA FARE PROSSIMO (Prime/Codex — dare questi task a un sotto-agente)
1. **STREAMING della risposta del chief (PRIORITÀ).** Ora `/api/bridge/prime` è **sincrono**: la risposta di Prime appare tutta insieme dopo ~10s (Opus che ragiona). Convertire in **SSE/streaming** così le parole appaiono man mano. Backend: nuovo endpoint streaming sulla sessione `hermes-prime` con `include_partial_messages=True`, mandare i `token` via SSE (riusare il parser `_handle_claude_stream_event` in `routes.py`). Frontend: consumare l'SSE in `onPrimeSubmit` e accodare i token nel bubble di Prime (vedi come fa la chat principale: `/api/chat/start` + SSE `/api/chat/stream` in `messages.js`).
2. **Sotto-agenti persistenti** (ibrido): per gli agenti principali riusare la sessione con un **id stabile per agente** invece del client effimero in `_run_worker` (mantengono memoria). Attenzione loop: dentro il tool (già async) `await` diretto, non `run_turn` (blocca).
3. **Agenti legati ai progetti**: ogni agente prende i suoi **MD di memoria** da `06-Agents/` + pagina-progetto (obsidian-wiki) nel system prompt del worker.
4. **Discord**: bot + canale; instradare messaggi agente↔canale. Hermes ha già **gateway messaggi** + canali (CLI/Telegram/Discord/Slack — impostazioni "external sessions", `api/gateway_chat.py`). L'utente imposta il bot; lato codice collegare le sessioni agente al canale.

**Nota memoria persistente agenti:** ogni agente avrà bisogno di MD specifici del
vault come contesto/memoria — va passato come system prompt/contesto alla sua
sessione bridge (come già fa il bridge Claude col contesto Obsidian, vedi
`_claude_session_system_prompt` in `api/routes.py`).

---

## 7. Commit recenti (branch `giorgio/persistent-cli-bridge`)
- `feat(command-bridge): Phase 1 — vault graph endpoint`
- `feat(command-bridge): Phase 2 — /api/projects/overview`
- `feat(command-bridge): Phase 3 — 3-zone shell + Projects Strip`
- `feat(command-bridge): Phase 4 — real 3D Vault Planet`
- `fix(command-bridge): center console, hide sidebar, yellow core, horizontal gradient`
- `feat(command-bridge): Phase 5 — Voice Orb (Elsa TTS, mic STT, core pulse, states)`
- `fix(command-bridge): STT shows interim text + reports error reason`

Vedi anche: `docs/COMMAND-BRIDGE-PLAN.md` (piano completo),
`docs/GUIDA-BRIDGE-HERMES.md` (bridge Claude/Codex, UTF-8, update, troubleshooting),
`docs/OBSIDIAN-WIKI-MEMORY-PLAN.md` (secondo cervello Obsidian con obsidian-wiki).
