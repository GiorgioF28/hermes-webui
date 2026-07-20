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

function clamp01(v) { return v < 0 ? 0 : (v > 1 ? 1 : v); }
function mix(a, b, t) { return a + (b - a) * t; }
function smoothstep(t) { return t * t * (3 - 2 * t); }
function hash2(x, y, seed) {
  const n = Math.sin(x * 127.1 + y * 311.7 + seed * 74.7) * 43758.5453123;
  return n - Math.floor(n);
}
function valueNoise(x, y, seed) {
  const xi = Math.floor(x), yi = Math.floor(y), xf = x - xi, yf = y - yi;
  const u = smoothstep(xf), v = smoothstep(yf);
  const a = hash2(xi, yi, seed), b = hash2(xi + 1, yi, seed);
  const c = hash2(xi, yi + 1, seed), d = hash2(xi + 1, yi + 1, seed);
  return mix(mix(a, b, u), mix(c, d, u), v);
}
function fbm(x, y, seed, octaves) {
  let sum = 0, amp = 0.5, freq = 1, norm = 0;
  for (let i = 0; i < octaves; i++) {
    sum += valueNoise(x * freq, y * freq, seed + i * 19.19) * amp;
    norm += amp; amp *= 0.5; freq *= 2;
  }
  return sum / norm;
}
function canvasTexture(canvas, srgb) {
  const t = new THREE.CanvasTexture(canvas);
  t.wrapS = THREE.RepeatWrapping;
  t.wrapT = THREE.ClampToEdgeWrapping;
  if (srgb !== false) t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 4;
  t.needsUpdate = true;
  return t;
}
function grayTexture(values, width, height) {
  const c = document.createElement('canvas'); c.width = width; c.height = height;
  const g = c.getContext('2d'), img = g.createImageData(width, height);
  for (let i = 0; i < values.length; i++) {
    const v = Math.round(clamp01(values[i]) * 255), j = i * 4;
    img.data[j] = v; img.data[j + 1] = v; img.data[j + 2] = v; img.data[j + 3] = 255;
  }
  g.putImageData(img, 0, 0);
  return canvasTexture(c, false);
}
function putPixel(data, idx, r, g, b, a) {
  data[idx] = Math.round(clamp01(r) * 255);
  data[idx + 1] = Math.round(clamp01(g) * 255);
  data[idx + 2] = Math.round(clamp01(b) * 255);
  data[idx + 3] = a == null ? 255 : a;
}
function drawSoftSpot(g, x, y, rx, ry, colorStops) {
  const grd = g.createRadialGradient(x, y, 0, x, y, Math.max(rx, ry));
  colorStops.forEach(function (st) { grd.addColorStop(st[0], st[1]); });
  g.save(); g.translate(x, y); g.scale(rx / Math.max(rx, ry), ry / Math.max(rx, ry));
  g.fillStyle = grd; g.beginPath(); g.arc(0, 0, Math.max(rx, ry), 0, Math.PI * 2); g.fill(); g.restore();
}

function solarTexture() {
  const W = 1024, H = 512, c = document.createElement('canvas'); c.width = W; c.height = H;
  const g = c.getContext('2d'), img = g.createImageData(W, H), bump = new Array(W * H);
  for (let y = 0; y < H; y++) {
    const lat = Math.abs(y / H - 0.5) * 2;
    for (let x = 0; x < W; x++) {
      const i = y * W + x, n = fbm(x / 20, y / 20, 11, 5), gran = fbm(x / 5.2, y / 5.2, 31, 3);
      const fil = Math.pow(fbm(x / 85 + n * 2.8, y / 18, 71, 4), 2.2);
      const heat = 0.38 + n * 0.38 + gran * 0.18 + fil * 0.16 - lat * 0.09;
      bump[i] = heat;
      putPixel(img.data, i * 4, mix(0.38, 0.98, heat), mix(0.045, 0.24, heat), mix(0.018, 0.055, heat));
    }
  }
  g.putImageData(img, 0, 0);
  [[245, 170, 34, 18], [420, 248, 52, 29], [610, 142, 30, 17], [770, 322, 46, 23], [900, 220, 26, 14]].forEach(function (s, idx) {
    const wob = 1 + fbm(s[0] / 20, s[1] / 20, 93 + idx, 3) * 0.3;
    drawSoftSpot(g, s[0], s[1], s[2] * 1.75, s[3] * 1.5 * wob, [
      [0, 'rgba(18,5,3,.94)'], [0.36, 'rgba(42,10,7,.88)'], [0.7, 'rgba(91,26,14,.52)'], [1, 'rgba(120,42,20,0)']
    ]);
  });
  g.globalCompositeOperation = 'screen';
  g.strokeStyle = 'rgba(255,154,70,.18)'; g.lineWidth = 2;
  for (let i = 0; i < 18; i++) {
    const y = 40 + i * 24 + Math.sin(i * 1.7) * 14;
    g.beginPath();
    for (let x = 0; x <= W; x += 14) {
      const yy = y + Math.sin(x * 0.018 + i) * 9 + (fbm(x / 60, i * 9, 141, 3) - 0.5) * 16;
      if (x === 0) g.moveTo(x, yy); else g.lineTo(x, yy);
    }
    g.stroke();
  }
  g.globalCompositeOperation = 'source-over';
  return { map: canvasTexture(c), emissiveMap: canvasTexture(c), bumpMap: grayTexture(bump, W, H) };
}

function planetMaps(kind) {
  const W = 1024, H = 512, c = document.createElement('canvas'), eg = document.createElement('canvas');
  c.width = eg.width = W; c.height = eg.height = H;
  const g = c.getContext('2d'), emg = eg.getContext('2d');
  const img = g.createImageData(W, H), emi = emg.createImageData(W, H);
  const bump = new Array(W * H), rough = new Array(W * H);
  const seed = kind === 'tech' ? 5 : (kind === 'social' ? 17 : (kind === 'green' ? 29 : 41));
  for (let y = 0; y < H; y++) {
    const v = y / H, lat = Math.abs(v - 0.5) * 2;
    for (let x = 0; x < W; x++) {
      const u = x / W, i = y * W + x, j = i * 4, n = fbm(u * 10, v * 5, seed, 5);
      let r = 0, gg = 0, b = 0, e = 0, h = n, rf = 0.9;
      if (kind === 'tech') {
        const base = 0.018 + n * 0.055;
        r = base * 0.35; gg = base * 0.95; b = base * 1.55; rf = 0.62; h = 0.18 + n * 0.25;
      } else if (kind === 'social') {
        const wave = 0.5 + 0.5 * Math.sin(v * 58 + fbm(u * 9, v * 12, seed + 2, 4) * 5 + Math.sin(u * 25) * 0.7);
        r = 0.36 + wave * 0.45 + n * 0.11; gg = 0.08 + wave * 0.18; b = 0.22 + (1 - wave) * 0.42 + n * 0.09;
        h = 0.35 + wave * 0.22; rf = 0.88;
      } else if (kind === 'green') {
        const land = fbm(u * 5.6 + fbm(u * 13, v * 7, seed + 4, 4) * 1.8, v * 3.2, seed + 5, 6);
        const coast = clamp01((land - 0.48) * 9), forest = fbm(u * 34, v * 18, seed + 6, 4);
        r = mix(0.012, 0.06 + forest * 0.08, coast); gg = mix(0.075 + n * 0.06, 0.22 + forest * 0.28, coast); b = mix(0.18 + n * 0.1, 0.055 + forest * 0.05, coast);
        h = 0.25 + coast * 0.55 + forest * 0.18; rf = mix(0.82, 0.95, coast);
      } else {
        const bands = 0.5 + 0.5 * Math.sin(v * 86 + fbm(u * 12, v * 12, seed + 8, 5) * 7), shear = fbm(u * 26 + bands * 1.6, v * 11, seed + 9, 5);
        r = 0.18 + bands * 0.22 + shear * 0.09; gg = 0.16 + (1 - bands) * 0.35 + n * 0.08; b = 0.28 + bands * 0.42 + shear * 0.12;
        h = 0.38 + bands * 0.25 + shear * 0.18; rf = 0.96;
      }
      const shade = 1 - lat * 0.18;
      putPixel(img.data, j, r * shade, gg * shade, b * shade);
      putPixel(emi.data, j, e, e, e);
      bump[i] = h; rough[i] = rf;
    }
  }
  g.putImageData(img, 0, 0); emg.putImageData(emi, 0, 0);
  if (kind === 'tech') {
    emg.globalCompositeOperation = 'lighter';
    for (let i = 0; i < 240; i++) {
      const x = Math.floor(hash2(i, 2, seed) * W / 24) * 24, y = Math.floor(hash2(i, 7, seed) * H / 18) * 18;
      const len = 24 + Math.floor(hash2(i, 11, seed) * 96);
      emg.strokeStyle = 'rgba(90,225,255,.72)'; emg.lineWidth = hash2(i, 13, seed) > 0.72 ? 2 : 1;
      emg.beginPath(); emg.moveTo(x, y);
      if (hash2(i, 17, seed) > 0.5) emg.lineTo((x + len) % W, y); else emg.lineTo(x, (y + len) % H);
      emg.stroke();
      drawSoftSpot(emg, x, y, 7, 7, [[0, 'rgba(178,250,255,.95)'], [0.45, 'rgba(80,220,255,.36)'], [1, 'rgba(80,220,255,0)']]);
    }
    g.drawImage(eg, 0, 0);
  } else if (kind === 'exotic') {
    drawSoftSpot(g, 670, 255, 92, 40, [[0, 'rgba(250,130,210,.82)'], [0.42, 'rgba(115,30,140,.78)'], [0.78, 'rgba(70,210,205,.32)'], [1, 'rgba(70,210,205,0)']]);
    drawSoftSpot(g, 700, 255, 33, 16, [[0, 'rgba(25,7,46,.75)'], [0.72, 'rgba(250,210,255,.18)'], [1, 'rgba(250,210,255,0)']]);
  } else if (kind === 'social') {
    g.globalAlpha = 0.28; g.fillStyle = '#ffd6f5';
    for (let i = 0; i < 22; i++) {
      g.beginPath();
      const yy = 18 + i * 23;
      for (let x = 0; x <= W; x += 18) {
        const y = yy + Math.sin(x * 0.014 + i) * 10;
        if (x === 0) g.moveTo(x, y); else g.lineTo(x, y);
      }
      g.lineTo(W, yy + 14); g.lineTo(0, yy + 14); g.closePath(); g.fill();
    }
    g.globalAlpha = 1;
  } else if (kind === 'green') {
    g.globalCompositeOperation = 'screen';
    for (let i = 0; i < 34; i++) drawSoftSpot(g, hash2(i, 1, seed) * W, hash2(i, 3, seed) * H, 30 + hash2(i, 5, seed) * 90, 5 + hash2(i, 7, seed) * 12, [[0, 'rgba(150,255,170,.13)'], [1, 'rgba(150,255,170,0)']]);
    g.globalCompositeOperation = 'source-over';
  }
  return { map: canvasTexture(c), emissiveMap: canvasTexture(eg), bumpMap: grayTexture(bump, W, H), roughnessMap: grayTexture(rough, W, H) };
}

function planetTexture(kind) {
  return planetMaps(kind).map;
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

  const starLight = new THREE.PointLight(0xff8a38, 1.65, 0, 0);
  scene.add(starLight);
  scene.add(new THREE.AmbientLight(0x24304a, 0.78));

  // ── Star = Hermes Prime ───────────────────────────────────────────────
  const R = 26;
  const solar = solarTexture();
  const starCore = new THREE.Mesh(new THREE.SphereGeometry(R, 72, 48), new THREE.MeshStandardMaterial({
    map: solar.map,
    emissiveMap: solar.emissiveMap,
    emissive: 0xff4a20,
    emissiveIntensity: 0.68,
    bumpMap: solar.bumpMap,
    bumpScale: 1.25,
    color: 0xff5a22,
    roughness: 1,
    metalness: 0
  }));
  scene.add(starCore);
  const glow = new THREE.Sprite(new THREE.SpriteMaterial({
    map: radialTexture([[0, 'rgba(255,76,28,.52)'], [0.2, 'rgba(255,86,34,.34)'], [0.48, 'rgba(255,74,22,.15)'], [0.78, 'rgba(190,34,12,.05)'], [1, 'rgba(120,20,10,0)']]),
    transparent: true, blending: THREE.AdditiveBlending, depthWrite: false
  }));
  glow.scale.setScalar(R * 7.2); scene.add(glow);
  const corona = new THREE.Sprite(new THREE.SpriteMaterial({
    map: radialTexture([[0, 'rgba(255,126,45,.32)'], [0.46, 'rgba(224,58,18,.12)'], [1, 'rgba(160,24,8,0)']]),
    transparent: true, blending: THREE.AdditiveBlending, depthWrite: false
  }));
  corona.scale.setScalar(R * 11.5); scene.add(corona);

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
    const maps = planetMaps(o.kind);
    const mat = new THREE.MeshStandardMaterial({
      map: maps.map,
      emissive: o.emissive || 0xffffff,
      emissiveMap: maps.emissiveMap,
      emissiveIntensity: o.kind === 'tech' ? 0.72 : 0.1,
      bumpMap: maps.bumpMap,
      bumpScale: o.bumpScale || 0.7,
      roughnessMap: maps.roughnessMap,
      roughness: 0.9,
      metalness: o.metalness || 0
    });
    const mesh = new THREE.Mesh(new THREE.SphereGeometry(o.size, 72, 48), mat);
    grp.add(mesh);
    let activeAura = null;
    if (o.atmo) {
      const atmo = new THREE.Mesh(new THREE.SphereGeometry(o.size * 1.15, 48, 32),
        new THREE.MeshBasicMaterial({ color: o.atmo, transparent: true, opacity: o.atmoOpacity || 0.16, side: THREE.BackSide, blending: THREE.AdditiveBlending, depthWrite: false }));
      grp.add(atmo);
    }
    activeAura = new THREE.Mesh(new THREE.SphereGeometry(o.size * 1.42, 48, 32),
      new THREE.MeshBasicMaterial({ color: o.atmo || 0xffffff, transparent: true, opacity: 0, side: THREE.BackSide, blending: THREE.AdditiveBlending, depthWrite: false }));
    grp.add(activeAura);
    if (o.rings) {
      const ring = new THREE.Mesh(new THREE.RingGeometry(o.size * 1.34, o.size * 1.88, 96),
        new THREE.MeshBasicMaterial({ color: o.rings, transparent: true, opacity: 0.22, side: THREE.DoubleSide, blending: THREE.AdditiveBlending, depthWrite: false }));
      ring.rotation.x = Math.PI * 0.52; ring.rotation.y = Math.PI * 0.12; grp.add(ring);
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
      new THREE.LineBasicMaterial({ color: o.atmo || 0xffffff, transparent: true, opacity: 0.075 }));
    ring.rotation.x = o.incl || 0;
    scene.add(ring);
    const p = { name: o.name, grp: grp, mesh: mesh, orbit: o.orbit, angle: o.phase || 0,
                speed: o.speed, incl: o.incl || 0, size: o.size, flare: 0, active: false,
                activeLevel: 0, aura: activeAura, spin: o.spin || 0.25,
                baseEmissive: o.kind === 'tech' ? 0.72 : 0.1 };
    planets.push(p);
    return p;
  }

  makePlanet({ name: 'programmatore', label: 'Programmatore', kind: 'tech',   size: 8,  orbit: 70,  speed: 0.10,  phase: 0.4, metalness: 0.35, atmo: 0x3fd0ff, atmoOpacity: 0.18, incl: 0.05, spin: 0.31, bumpScale: 0.35, emissive: 0x5deaff });
  makePlanet({ name: 'social',        label: 'Social',        kind: 'social', size: 10, orbit: 108, speed: 0.072, phase: 2.1, atmo: 0xff68d8, atmoOpacity: 0.2, incl: 0.12, spin: 0.22, rings: 0xff74d8 });
  makePlanet({ name: 'ricercatore',   label: 'Ricercatore',   kind: 'exotic', size: 11, orbit: 150, speed: 0.055, phase: 3.7, atmo: 0x58f0d0, atmoOpacity: 0.17, incl: 0.18, spin: 0.28, bumpScale: 0.45 });
  const lib = makePlanet({ name: 'librarian', label: 'Librarian', kind: 'green', size: 9, orbit: 196, speed: 0.04, phase: 5.2, atmo: 0x4fe06a, atmoOpacity: 0.2, incl: 0.08, spin: 0.2, bumpScale: 0.9 });

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
  const COOL = new THREE.Color(0x9fd6ff), WARM = new THREE.Color(0xff6a2d);
  function matchingPlanets(name) {
    if (!name) return [];
    const q = String(name).toLowerCase();
    return planets.filter(function (p) {
      return q.indexOf(p.name) >= 0 ||
        (p.name === 'programmatore' && /program|cod/.test(q)) ||
        (p.name === 'ricercatore' && /ricerc|research/.test(q)) ||
        (p.name === 'librarian' && /librar|memor/.test(q)) ||
        (p.name === 'social' && /social|client/.test(q));
    });
  }
  function flareAgent(name) {
    matchingPlanets(name).forEach(function (p) { p.flare = 1; });
  }
  function setAgentActive(name, active) {
    matchingPlanets(name).forEach(function (p) {
      p.active = !!active;
    });
  }
  window.cbStar = window.cbCore = {
    setState: function (s) { mode = s || 'idle'; },
    setAmplitude: function (a) { amp = a < 0 ? 0 : (a > 1 ? 1 : a); },
    pulse: function () { manualPulse = 1; spawnRing(1.5); },
    flare: flareAgent,
    setAgentActive: setAgentActive
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
    if (!reduceMotion()) starCore.rotation.y += dt * 0.035;
    const tint = WARM.clone().lerp(COOL, cool * 0.6);
    starCore.material.color.copy(tint);
    starCore.material.emissiveIntensity = 0.58 + bright * 0.12;
    glow.material.color.copy(tint);
    glow.scale.setScalar(R * (6.9 + beat * 0.24 + Math.sin(t * 0.7) * 0.05));
    glow.material.opacity = Math.min(0.48, 0.26 * bright);
    corona.scale.setScalar(R * (10.9 + beat * 0.36 + Math.sin(t * 0.42) * 0.08));
    corona.material.opacity = Math.min(0.32, 0.18 * bright);
    starLight.intensity = 1.55 * bright;
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
      const targetActive = p.active ? 1 : 0;
      const fade = dt / 0.5;
      p.activeLevel += (targetActive - p.activeLevel) * Math.min(1, fade);
      const activePulse = p.activeLevel * (0.62 + 0.38 * (0.5 + 0.5 * Math.sin(t * Math.PI * 2 / 1.2)));
      if (!reduceMotion()) p.mesh.rotation.y += dt * p.spin * (1 + p.activeLevel * 2.6);
      if (p.aura) {
        p.aura.material.opacity = 0.34 * activePulse;
        p.aura.scale.setScalar(1 + p.activeLevel * 0.08 + activePulse * 0.12);
      }
      if (p.flare > 0) {
        p.flare = Math.max(0, p.flare - dt * 0.6);
        p.mesh.material.emissiveIntensity = p.baseEmissive + p.flare * 0.75 + p.activeLevel * 0.22;
        const s = 1 + p.flare * 0.18; p.mesh.scale.setScalar(s);
      } else {
        p.mesh.material.emissiveIntensity = p.baseEmissive + p.activeLevel * 0.22;
        p.mesh.scale.setScalar(1);
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
