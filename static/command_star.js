import { scheduleVisibleAnimation } from './command_animation.js';
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
const SUN_BASE_COLOR = new THREE.Color(0xfff0c2);
const SUN_EMISSIVE_COLOR = new THREE.Color(0xfff6d8);
const SUN_LISTENING_COLOR = new THREE.Color(0xf7fbff);
const SUN_LIGHT_COLOR = new THREE.Color(0xfffbf0);

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

// These pixel-identical maps are baked offline; never run procedural noise at boot.
const textureLoader = new THREE.TextureLoader();
function bakedMap(kind, name) {
  const url = new URL('./textures/bridge/' + kind + '-' + name + '.png', import.meta.url);
  const t = textureLoader.load(url.href);
  t.wrapS = THREE.RepeatWrapping;
  t.wrapT = THREE.ClampToEdgeWrapping;
  if (name === 'map' || name === 'emissiveMap') t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 4;
  return t;
}
function solarTexture() {
  const map = bakedMap('solar', 'map');
  return { map, emissiveMap: map, bumpMap: bakedMap('solar', 'bumpMap') };
}
function planetMaps(kind) {
  return Object.fromEntries(['map', 'emissiveMap', 'bumpMap', 'roughnessMap']
    .map(name => [name, bakedMap(kind, name)]));
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

  const starLight = new THREE.PointLight(SUN_LIGHT_COLOR, 1.22, 0, 0);
  scene.add(starLight);
  scene.add(new THREE.AmbientLight(0x303238, 0.64));

  // ── Star = Hermes Prime ───────────────────────────────────────────────
  const R = 26;
  const solar = solarTexture();
  const starCore = new THREE.Mesh(new THREE.SphereGeometry(R, 72, 48), new THREE.MeshStandardMaterial({
    map: solar.map,
    emissiveMap: solar.emissiveMap,
    emissive: SUN_EMISSIVE_COLOR,
    emissiveIntensity: 0.56,
    bumpMap: solar.bumpMap,
    bumpScale: 1.25,
    color: SUN_BASE_COLOR,
    roughness: 1,
    metalness: 0
  }));
  scene.add(starCore);
  const glow = new THREE.Sprite(new THREE.SpriteMaterial({
    map: radialTexture([[0, 'rgba(255,246,216,.34)'], [0.2, 'rgba(255,238,186,.22)'], [0.48, 'rgba(255,232,170,.09)'], [0.78, 'rgba(255,220,150,.025)'], [1, 'rgba(255,220,150,0)']]),
    transparent: true, blending: THREE.AdditiveBlending, depthWrite: false
  }));
  glow.scale.setScalar(R * 7.2); scene.add(glow);
  const corona = new THREE.Sprite(new THREE.SpriteMaterial({
    map: radialTexture([[0, 'rgba(255,246,216,.18)'], [0.46, 'rgba(255,232,180,.06)'], [1, 'rgba(255,232,180,0)']]),
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
    const tint = SUN_BASE_COLOR.clone().lerp(SUN_LISTENING_COLOR, cool * 0.35);
    starCore.material.color.copy(tint);
    starCore.material.emissiveIntensity = 0.48 + bright * 0.1;
    glow.material.color.copy(tint);
    glow.scale.setScalar(R * (6.9 + beat * 0.24 + Math.sin(t * 0.7) * 0.05));
    glow.material.opacity = Math.min(0.34, 0.18 * bright);
    corona.scale.setScalar(R * (10.9 + beat * 0.36 + Math.sin(t * 0.42) * 0.08));
    corona.material.opacity = Math.min(0.2, 0.1 * bright);
    starLight.intensity = 1.16 * bright;
    starLight.color.copy(SUN_LIGHT_COLOR);

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
  scheduleVisibleAnimation(container, animate);
};
