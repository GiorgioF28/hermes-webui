# Hermes "Command Bridge" — Recon + Implementation Plan

> Phase 0 deliverable. Grounded in the real codebase (no assumptions).

## Recon findings (the real stack)

- **Frontend: pure vanilla JS, NO bundler, NO React.** `package.json` says so explicitly. Scripts are plain `<script defer>` in `static/index.html` (`panels.js`, `ui.js`, `messages.js`, …). `static/vendor/` holds vendored libs (smd, katex); CDN used for prism/xterm.
  - ⇒ **3D = plain `three.js`, vendored into `static/vendor/three/`** (NOT react-three-fiber).
- **Views = "panels".** Nav buttons `data-panel="X"` call `switchPanel('X')` (`panels.js:205`). A panel = a `.panel-view` element with id `panel<Name>`; `<main>` gets `showing-<name>` class for layout; lazy-loaded via `load<X>()`.
  - **There is already a `command` panel** with `loadCommandCenter()` and `main.showing-command`. ⇒ The Command Bridge **becomes the `command` panel** (rebuild its content + layout).
- **Vault:** `<DEFAULT_WORKSPACE>/obsidian-vault` (default workspace = `C:\Users\giorg\Documents\Hermes setup`). Read by **direct fs** (per spec). `_local_workspace_context`/`_obsidian_memory_context` already walk it — reuse patterns.
- **Backend:** `http.server.ThreadingHTTPServer` (sync threads). Routes dispatched in `api/routes.py` (`handle_get`/POST path matching). SSE already exists (`/api/chat/stream`, `/api/clarify/stream`). New endpoints are plain handler funcs added to the path dispatch.
- **Voice:** `/api/tts` exists (Edge TTS, server-side). **No STT endpoint** ⇒ browser `SpeechRecognition` fallback behind a `VoiceProvider` interface. TTS audio piped through `WebAudio AnalyserNode` for the orb.
- **Agents:** model providers in `api/providers.py` (claude-code, codex-cli) + the `profiles` panel (agent profiles). "Agents" family in the planet = configured providers/profiles.
- **Bridge delegation:** the persistent Claude/Codex bridges (`_run_claude_code_streaming`, `_run_codex_cli_streaming`) + `/api/chat/start` are how sub-agent tasks get created. Hermes Prime delegates by creating a session/turn there and surfacing a status card.

## Files to create / modify

**Backend (`api/`):**
- Create `api/vault_graph.py` — walk vault → `{nodes, edges}`; mtime-cached; wikilink parse (cap 1MB, cache). Projects = top-level project folders; agents = providers/profiles.
- Create `api/command_bridge.py` — `/api/vault/graph` and `/api/projects/overview` handlers + Hermes Prime support (persona load, delegation endpoint).
- Modify `api/routes.py` — register the new GET/POST paths in the dispatch.
- Create `prompts/hermes-prime.md` — editable chief-of-staff persona.
- Config object for Hermes Prime permissions (read-only vault default; delegation allowed; writes need confirm).

**Frontend (`static/`):**
- Vendor `static/vendor/three/three.module.js` (+ a small OrbitControls).
- Create `static/command_bridge.js` — the whole view: layout, planet (three.js), orb (shader), voice pipeline (`VoiceProvider`), projects strip, Hermes Prime chat. Loaded by `loadCommandCenter()`.
- Modify `static/index.html` — rebuild `panelCommand` / the `showing-command` main region into the 3-zone Command Bridge shell; add `<script>` for command_bridge.js + three import.
- Modify `static/style.css` — Command Bridge styles (dark instrument aesthetic, display+body type pairing).

## Data flow

```
vault (fs) ──walk──> api/vault_graph.py ──cache(mtime)──> GET /api/vault/graph ──> command_bridge.js
                                                          GET /api/projects/overview ──> Projects Strip + node detail cards
mic ─> VoiceProvider.stt ─> Hermes Prime (system prompt + tools) ─> delegate -> /api/chat/start (sub-agent)
                                                                  └─> TTS (/api/tts) ─> <audio> ─> AnalyserNode ─> Orb amplitude
```

## Layout algorithm (inside-sphere radial tree)

- Core at origin. `r(depth) = R * (depth/maxDepth)^0.85`, R ≈ 0.9 × sphere radius.
- Each top-level branch gets a cone (solid-angle wedge) sized by subtree size; recursively subdivide among children. Deterministic — no physics at load.
- Post-pass: few iterations of sibling-only repulsion at same depth to clear residual overlap. No per-frame global force sim.
- Perf: `InstancedMesh` for nodes; batched `Line2`/merged geometry for edges; labels only depth ≤ 2 (more on hover/zoom). Target 60fps @ 2k nodes.

## Implementation order (matches the prompt)

1. **Phase 0** — recon + this plan. ✅
2. **Phase 1 — `GET /api/vault/graph`** (real data, mtime cache). ← starting now.
3. **Phase 2 — `GET /api/projects/overview`** (latest changes + up-next task parsing).
4. **Phase 3 — Command Bridge shell + Projects Strip** (no 3D yet: 3-zone layout, real project cards, card→(later)planet link).
5. **Phase 4 — Vault Planet** (three.js: sphere shell, radial-tree layout, instanced nodes, edges, interaction, LOD labels).
6. **Phase 5 — Voice Orb** (shader sphere, idle/listening/thinking/speaking, AnalyserNode-driven).
7. **Phase 6 — Hermes Prime** (persona file, permissions config, STT→agent→TTS pipeline, delegation cards in chat).
8. **Phase 7 — polish** (mobile stacking, reduced-motion, focus states, perf pass).

Each phase commits independently and keeps Hermes fully working (the Command Bridge is additive to the existing `command` panel).

## Honest scope note

This is a large, multi-session feature. It is built phase-by-phase; every phase is independently useful and shippable. No mock data in the final result — the graph and project cards read the real vault from Phase 1 onward.
