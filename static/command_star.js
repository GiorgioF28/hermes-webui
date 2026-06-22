/* ──────────────────────────────────────────────────────────────────────────
 * Hermes Prime — Living Star system (three.js).
 * Hermes Prime (the Orchestratore) IS the star; the worker agents are distinct
 * planets orbiting it. A black hole beside the Librarian planet is the Obsidian
 * memory of the system. Exposes window.cbInitStar(container) and acts as the
 * Voice Orb via window.cbStar / window.cbCore (setState / setAmplitude / pulse).
 *
 * Readable heartbeat: the star core CONTRACTS like a muscle, flashes brighter,
 * and emits an expanding shockwave shell on every beat — not just a glow change.
 * ────────────────────────────────────────────────────────────────────────── */
import * as THREE from './vendor/three/three.module.js';
import { OrbitControls } from './vendor/three/OrbitControls.js';

const mountedStar = new WeakSet();

function reduceMotion() {
  try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { return false; }
}

// double-beat (lub-dub) envelope, ~0..1
function heartbeat(phase) {
  var x = phase % 1;
  var lub = Math.exp(-Math.pow((x - 0.10) / 0.045, 2));
  var dub = 0.55 * Math.exp(-Math.pow((x - 0.26) / 0.05, 2));
  return lub + dub;
}

function radialTexture(stops) {
  const s = 128, c = document.createElement('canvas'); c.width = c.height = s;
  const g = c.getContext('2d');
  const grd = g.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
  stops.forEach(function (st) { grd.addColorStop(st[0], st[1]); });
  g.fillStyle = grd; g.fillRect(0, 0, s, s);
  const t = new THREE.CanvasTexture(c); t.needsUpdate = true; return t;
}

// procedural planet skins — each agent reads as a distinct world
function planetTexture(kind) {
  const S = 256, c = document.createElement('canvas'); c.width = c.height = S;
  const g = c.getContext('2d');
  function blob(x, y, r, col) { g.fillStyle = col; g.beginPath(); g.arc(x, y, r, 0, 7); g.fill(); }
  function rnd(a, b) { return a + Math.random() * (b - a); }
  if (kind === 'social') {            // habitable: oceans, green land, clouds
    g.fillStyle = '#0b3f74'; g.fillRect(0, 0, S, S);
    for (let i = 0; i < 14; i++) blob(rnd(0, S), rnd(0, S), rnd(14, 40), '#2f8f3a');
    g.globalAlpha = 0.5;
    for (let i = 0; i < 11; i++) blob(rnd(0, S), rnd(0, S), rnd(10, 28), '#eaf6ff');
    g.globalAlpha = 1;
  } else if (kind === 'tech') {        // technological: dark grid + glowing nodes
    g.fillStyle = '#070b12'; g.fillRect(0, 0, S, S);
    g.strokeStyle = 'rgba(70,200,255,.4)'; g.lineWidth = 1;
    for (let i = 0; i <= S; i += 16) {
      g.beginPath(); g.moveTo(i, 0); g.lineTo(i, S); g.stroke();
      g.beginPath(); g.moveTo(0, i); g.lineTo(S, i); g.stroke();
    }
    for (let i = 0; i < 46; i++) blob(Math.round(rnd(0, 16)) * 16, Math.round(rnd(0, 16)) * 16, rnd(1.5, 3), 'rgba(130,235,255,.95)');
  } else if (kind === 'green') {        // lush all-green world
    g.fillStyle = '#0e3a18'; g.fillRect(0, 0, S, S);
    const greens = ['#1f7a30', '#2fae45', '#176a28', '#43c95a'];
    for (let i = 0; i < 26; i++) blob(rnd(0, S), rnd(0, S), rnd(12, 38), greens[i % greens.length]);
  } else {                             // exotic: fire / water / gas / green / earth
    const biomes = ['#ff5a1f', '#1f6dff', '#9b4dff', '#3fae4a', '#8a5a2b', '#ffcf3f'];
    g.fillStyle = '#1a1320'; g.fillRect(0, 0, S, S);
    for (let i = 0; i < 22; i++) blob(rnd(0, S), rnd(0, S), rnd(18, 46), biomes[i % biomes.length]);
  }
  const t = new THREE.CanvasTexture(c); t.needsUpdate = true; return t;
}

function labelSprite(text) {
  const fs = 34, pad = 8, probe = document.createElement('canvas').getContext('2d');
  probe.font = '600 ' + fs + 'px "IBM Plex Mono", monospace';
  const w = Math.ceil(probe.measureText(text.toUpperCase()).width) + pad * 2;
  const c = document.createElement('canvas'); c.width = w; c.height = fs + pad * 2;
  const g = c.getContext('2d');
  g.font = '600 ' + fs + 'px "IBM Plex Mono", monospace';
  g.textAlign = 'center'; g.textBaseline = 'middle';
  g.fillStyle = 'rgba(222,226,236,.92)';
  g.fillText(text.toUpperCase(), c.width / 2, c.height / 2);
  const t = new THREE.CanvasTexture(c); t.needsUpdate = true;
  const m = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, transparent: true, depthWrite: false, depthTest: false }));
  m.userData.aspect = c.width / c.height;
  return m;
}

window.cbInitStar = function (container) {
  if (!container || mountedStar.has(container)) return;
  mountedStar.add(container);

  const w = container.clientWidth || 600, h = container.clientHeight || 600;
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  renderer.setSize(w, h);
  renderer.domElement.style.pointerEvents = 'auto'; // allow drag-orbit; rest passes through
  container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, w / h, 1, 6000);

  const starLight = new THREE.PointLight(0xfff0d0, 2.6, 0, 0);
  scene.add(starLight);
  scene.add(new THREE.AmbientLight(0x18243c, 0.7));

  // ── Star = Hermes Prime ───────────────────────────────────────────────
  const R = 26;
  const starCore = new THREE.Mesh(new THREE.IcosahedronGeometry(R, 3), new THREE.MeshBasicMaterial({ color: 0xffffff }));
  scene.add(starCore);
  const glow = new THREE.Sprite(new THREE.SpriteMaterial({
    map: radialTexture([[0, 'rgba(255,255,255,1)'], [0.16, 'rgba(255,244,200,.95)'], [0.4, 'rgba(255,200,40,.5)'], [0.7, 'rgba(255,140,20,.12)'], [1, 'rgba(255,140,20,0)']]),
    transparent: true, blending: THREE.AdditiveBlending, depthWrite: false
  }));
  glow.scale.setScalar(R * 9); scene.add(glow);
  const corona = new THREE.Sprite(new THREE.SpriteMaterial({
    map: radialTexture([[0, 'rgba(255,180,80,.6)'], [0.5, 'rgba(255,120,20,.16)'], [1, 'rgba(255,120,20,0)']]),
    transparent: true, blending: THREE.AdditiveBlending, depthWrite: false
  }));
  corona.scale.setScalar(R * 15); scene.add(corona);

  // heartbeat ripples: a thin, slightly wavy ring that expands out to ~the Social
  // orbit and fades — a subtle pulse, not a bright blob that lingers behind.
  const RING_MAX = 108; // reaches the Social orbit
  const rings = [];
  const ringGeo = (function () {
    const seg = 160, pts = [];
    for (let i = 0; i <= seg; i++) {
      const a = i / seg * Math.PI * 2, rr = 1 + 0.05 * Math.sin(a * 5);
      pts.push(new THREE.Vector3(Math.cos(a) * rr, 0, Math.sin(a) * rr));
    }
    return new THREE.BufferGeometry().setFromPoints(pts);
  })();
  function spawnRing(power) {
    let r = null;
    for (let i = 0; i < rings.length; i++) if (!rings[i].visible) { r = rings[i]; break; }
    if (!r) {
      r = new THREE.LineLoop(ringGeo, new THREE.LineBasicMaterial({
        color: 0xffce9a, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false
      }));
      scene.add(r); rings.push(r);
    }
    r.visible = true; r.userData.life = 0; r.userData.power = power || 1;
    // tiny random tilt so the ripple isn't a perfectly flat, rigid circle
    r.rotation.set((Math.random() - 0.5) * 0.18, Math.random() * Math.PI, (Math.random() - 0.5) * 0.18);
  }

  // ── Planets (the worker agents) ───────────────────────────────────────
  const planets = [];
  function makePlanet(o) {
    const grp = new THREE.Group();
    const skin = planetTexture(o.kind);
    const mat = new THREE.MeshStandardMaterial({
      map: skin, emissive: 0xffffff, emissiveMap: skin, emissiveIntensity: 0.22,
      roughness: 1, metalness: o.metalness || 0
    });
    const mesh = new THREE.Mesh(new THREE.SphereGeometry(o.size, 44, 32), mat);
    grp.add(mesh);
    if (o.atmo) {
      const atmo = new THREE.Mesh(new THREE.SphereGeometry(o.size * 1.13, 36, 26),
        new THREE.MeshBasicMaterial({ color: o.atmo, transparent: true, opacity: 0.16, side: THREE.BackSide, blending: THREE.AdditiveBlending, depthWrite: false }));
      grp.add(atmo);
    }
    const label = labelSprite(o.label);
    const lh = o.size * 0.95; label.scale.set(lh * label.userData.aspect, lh, 1);
    label.position.set(0, -o.size - lh, 0);
    grp.add(label);
    scene.add(grp);
    // faint orbit ring
    const pts = [];
    for (let i = 0; i <= 128; i++) { const a = i / 128 * Math.PI * 2; pts.push(new THREE.Vector3(Math.cos(a) * o.orbit, 0, Math.sin(a) * o.orbit)); }
    const ring = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(pts),
      new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.06 }));
    ring.rotation.x = o.incl || 0;
    scene.add(ring);
    const p = { name: o.name, grp: grp, mesh: mesh, orbit: o.orbit, angle: o.phase || 0,
                speed: o.speed, incl: o.incl || 0, size: o.size, flare: 0 };
    planets.push(p);
    return p;
  }

  makePlanet({ name: 'programmatore', label: 'Programmatore', kind: 'tech',   size: 8,  orbit: 70,  speed: 0.10,  phase: 0.4, metalness: 0.55, atmo: 0x3fd0ff, incl: 0.05 });
  makePlanet({ name: 'social',        label: 'Social',        kind: 'social', size: 10, orbit: 108, speed: 0.072, phase: 2.1, atmo: 0x6fc3ff, incl: 0.12 });
  makePlanet({ name: 'ricercatore',   label: 'Ricercatore',   kind: 'exotic', size: 11, orbit: 150, speed: 0.055, phase: 3.7, atmo: 0xb070ff, incl: 0.18 });
  const lib = makePlanet({ name: 'librarian', label: 'Librarian', kind: 'green', size: 9, orbit: 196, speed: 0.04, phase: 5.2, atmo: 0x4fe06a, incl: 0.08 });

  // ── Black hole beside Librarian = Obsidian memory ─────────────────────
  const holeGrp = new THREE.Group();
  holeGrp.rotation.set(0.9, 0.3, 0); // tilt so the disk reads as a disk
  scene.add(holeGrp);
  holeGrp.add(new THREE.Mesh(new THREE.SphereGeometry(5, 28, 20), new THREE.MeshBasicMaterial({ color: 0x000000 })));
  const photon = new THREE.Mesh(new THREE.TorusGeometry(7, 0.5, 12, 64),
    new THREE.MeshBasicMaterial({ color: 0xffe6b0, transparent: true, opacity: 0.9, blending: THREE.AdditiveBlending, depthWrite: false }));
  holeGrp.add(photon);
  const disk = new THREE.Mesh(new THREE.TorusGeometry(11, 3.2, 2, 80),
    new THREE.MeshBasicMaterial({ color: 0xff8a2a, transparent: true, opacity: 0.4, blending: THREE.AdditiveBlending, depthWrite: false }));
  holeGrp.add(disk);
  const lensing = new THREE.Sprite(new THREE.SpriteMaterial({
    map: radialTexture([[0, 'rgba(120,90,200,0)'], [0.55, 'rgba(120,90,200,.18)'], [0.75, 'rgba(200,150,255,.10)'], [1, 'rgba(120,90,200,0)']]),
    transparent: true, blending: THREE.AdditiveBlending, depthWrite: false
  }));
  lensing.scale.setScalar(46); holeGrp.add(lensing);

  // ── camera framing ───────────────────────────────────────────────────────
  // The system reads as a WIDE, short ellipse (orbits are nearly flat to the
  // camera), so we fit it by WIDTH, not by a circle radius. This way it shrinks
  // to fit when the memory panel is open (narrower stage) and grows back when
  // the memory is collapsed — no more spilling over the memory card.
  const ZOOM = 1.0;
  function frameAll() {
    const tan = Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2);
    const horizR = 230, vertR = 125; // horizontal reach vs (short) vertical reach
    const distH = horizR / (tan * camera.aspect);
    const distV = vertR / tan;
    const dist = Math.max(distH, distV) * 1.1 / ZOOM;
    camera.position.set(0, dist * 0.30, dist);
    camera.lookAt(0, 0, 0);
  }
  frameAll();

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.dampingFactor = 0.08;
  controls.enablePan = false;
  controls.enableZoom = false; // fixed scale — no jarring far/near zoom
  controls.autoRotate = !reduceMotion(); controls.autoRotateSpeed = 0.35;
  let interacting = false;
  controls.addEventListener('start', function () { interacting = true; controls.autoRotate = false; });
  controls.addEventListener('end', function () { interacting = false; if (!reduceMotion()) setTimeout(function () { if (!interacting) controls.autoRotate = true; }, 2500); });

  function resize() {
    const ww = container.clientWidth, hh = container.clientHeight;
    if (!ww || !hh) return;
    camera.aspect = ww / hh; camera.updateProjectionMatrix();
    renderer.setSize(ww, hh); frameAll();
  }
  try { new ResizeObserver(resize).observe(container); } catch (e) { window.addEventListener('resize', resize); }

  // ── Voice Orb interface ───────────────────────────────────────────────
  let mode = 'idle', amp = 0, manualPulse = 0, beatPhase = 0;
  const COOL = new THREE.Color(0x9fd6ff), WARM = new THREE.Color(0xffffff);
  function flareAgent(name) {
    if (!name) return;
    const q = String(name).toLowerCase();
    planets.forEach(function (p) {
      if (q.indexOf(p.name) >= 0 ||
          (p.name === 'programmatore' && /program|cod/.test(q)) ||
          (p.name === 'ricercatore' && /ricerc|research/.test(q)) ||
          (p.name === 'librarian' && /librar|memor/.test(q)) ||
          (p.name === 'social' && /social|client/.test(q))) {
        p.flare = 1;
      }
    });
  }
  window.cbStar = window.cbCore = {
    setState: function (s) { mode = s || 'idle'; },
    setAmplitude: function (a) { amp = a < 0 ? 0 : (a > 1 ? 1 : a); },
    pulse: function () { manualPulse = 1; spawnRing(1.5); },
    flare: flareAgent
  };

  let running = true;
  const clock = new THREE.Clock();
  function animate() {
    if (!running) return;
    requestAnimationFrame(animate);
    const dt = Math.min(0.05, clock.getDelta());
    const t = clock.getElapsedTime();

    const bpm = mode === 'speaking' ? 96 : (mode === 'listening' ? 78 : (mode === 'thinking' ? 66 : 60));
    const period = 60 / bpm;
    if (!reduceMotion()) {
      beatPhase += dt / period;
      if (beatPhase >= 1) { beatPhase -= 1; spawnRing(mode === 'speaking' ? 1 + amp * 0.4 : 0.92); }
    }
    const beat = reduceMotion() ? 0.3 : heartbeat(beatPhase);

    let bright = 1, cool = 0;
    if (mode === 'speaking') bright = 1 + amp * 0.7;
    else if (mode === 'listening') { cool = 0.7; bright = 1.08; }
    else if (mode === 'thinking') bright = 0.66;
    manualPulse *= 0.9; bright += manualPulse;

    // star: muscle contraction (shrinks on beat) + brightness flash
    const contract = 1 - beat * 0.16;
    starCore.scale.setScalar(contract);
    const tint = WARM.clone().lerp(COOL, cool * 0.6);
    starCore.material.color.copy(tint);
    glow.material.color.copy(tint);
    glow.scale.setScalar(R * (8.6 + beat * 0.4));
    glow.material.opacity = Math.min(1, 0.82 * bright);
    corona.scale.setScalar(R * (14 + beat * 0.7));
    corona.material.opacity = Math.min(1, 0.48 * bright);
    starLight.intensity = 2.6 * bright;
    starLight.color.copy(tint);

    // heartbeat ripples: thin wavy ring expanding to ~Social, fading as it goes
    for (let i = 0; i < rings.length; i++) {
      const r = rings[i]; if (!r.visible) continue;
      r.userData.life += dt / 0.95;
      const k = r.userData.life;
      if (k >= 1) { r.visible = false; continue; }
      const rad = R + k * (RING_MAX * r.userData.power - R);
      r.scale.set(rad, rad, rad);
      r.material.opacity = 0.3 * (1 - k);
    }

    // planets orbit + spin; flare lights the planet when it works
    planets.forEach(function (p) {
      if (!reduceMotion()) p.angle += p.speed * dt;
      const px = Math.cos(p.angle) * p.orbit, pz = Math.sin(p.angle) * p.orbit;
      // incline the orbit about the X axis so the planet sits on its ring
      p.grp.position.set(px, -pz * Math.sin(p.incl), pz * Math.cos(p.incl));
      if (!reduceMotion()) p.mesh.rotation.y += dt * 0.25;
      if (p.flare > 0) {
        p.flare = Math.max(0, p.flare - dt * 0.6);
        p.mesh.material.emissiveIntensity = 0.22 + p.flare * 0.9;
        const s = 1 + p.flare * 0.18; p.mesh.scale.setScalar(s);
      }
    });

    // black hole orbits just outside the Librarian planet
    holeGrp.position.set(lib.grp.position.x * 1.16, lib.grp.position.y, lib.grp.position.z * 1.16);
    if (!reduceMotion()) { disk.rotation.z += dt * 0.6; photon.rotation.z -= dt * 0.4; }

    controls.update();
    renderer.render(scene, camera);
  }
  animate();
};
