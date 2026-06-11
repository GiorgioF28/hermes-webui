/* ──────────────────────────────────────────────────────────────────────────
 * Vault Planet — three.js render of the Obsidian vault as a transparent globe
 * with an inside-the-sphere radial tree. Exposes window.cbInitPlanet(el, graph).
 * Phase 4: shell + radial-tree layout + instanced nodes + edges + orbit controls
 * + incandescent core. (Hover/click detail + LOD labels: follow-up.)
 * ────────────────────────────────────────────────────────────────────────── */
import * as THREE from './vendor/three/three.module.js';
import { OrbitControls } from './vendor/three/OrbitControls.js';

const mounted = new WeakSet();
const GOLDEN = Math.PI * (3 - Math.sqrt(5));
const C_WHITE = new THREE.Color(0xffffff);
const C_ORANGE = new THREE.Color(0xff6a00);
const C_YELLOW = new THREE.Color(0xffc814);

function reduceMotion() {
  try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { return false; }
}

function glowTexture() {
  const s = 128, c = document.createElement('canvas'); c.width = c.height = s;
  const g = c.getContext('2d');
  const grd = g.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
  grd.addColorStop(0, 'rgba(255,255,255,1)');
  grd.addColorStop(0.18, 'rgba(255,247,205,0.95)');
  grd.addColorStop(0.42, 'rgba(255,205,30,0.55)');
  grd.addColorStop(0.7, 'rgba(255,150,20,0.12)');
  grd.addColorStop(1, 'rgba(255,150,20,0)');
  g.fillStyle = grd; g.fillRect(0, 0, s, s);
  const t = new THREE.CanvasTexture(c); t.needsUpdate = true; return t;
}

/* radial-tree layout: positions inside a sphere of radius R */
function layout(graph, R) {
  const byId = new Map();
  graph.nodes.forEach(n => byId.set(n.id, Object.assign({}, n,
    { children: [], pos: new THREE.Vector3(), dir: new THREE.Vector3(0, 1, 0) })));
  graph.edges.forEach(e => {
    if (e.kind !== 'hierarchy') return;
    const p = byId.get(e.source), c = byId.get(e.target);
    if (p && c) p.children.push(c);
  });
  const root = byId.get('core') || byId.values().next().value;
  let maxDepth = 1;
  byId.forEach(n => { if ((n.depth || 0) > maxDepth) maxDepth = n.depth; });
  const rOf = d => R * Math.pow(Math.min(1, d / maxDepth), 0.85);

  function basis(dir) {
    const a = Math.abs(dir.y) > 0.9 ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 1, 0);
    const u = new THREE.Vector3().crossVectors(dir, a).normalize();
    const v = new THREE.Vector3().crossVectors(dir, u).normalize();
    return [u, v];
  }
  function place(node, coneHalf) {
    const kids = node.children;
    const N = kids.length;
    if (!N) return;
    kids.sort((a, b) => (b.childCount || 0) - (a.childCount || 0));
    if (node === root) {
      for (let i = 0; i < N; i++) {
        const y = 1 - (i + 0.5) / N * 2, r = Math.sqrt(Math.max(0, 1 - y * y)), phi = i * GOLDEN;
        kids[i].dir.set(Math.cos(phi) * r, y, Math.sin(phi) * r).normalize();
      }
    } else {
      const [u, v] = basis(node.dir);
      for (let i = 0; i < N; i++) {
        const theta = coneHalf * Math.sqrt((i + 0.5) / N), phi = i * GOLDEN;
        kids[i].dir.copy(node.dir).multiplyScalar(Math.cos(theta))
          .addScaledVector(u, Math.sin(theta) * Math.cos(phi))
          .addScaledVector(v, Math.sin(theta) * Math.sin(phi)).normalize();
      }
    }
    for (const k of kids) {
      k.pos.copy(k.dir).multiplyScalar(rOf(k.depth || 1));
      place(k, coneHalf * 0.58);
    }
  }
  root.pos.set(0, 0, 0);
  place(root, Math.PI * 0.42);
  return { byId, root, maxDepth, rOf };
}

function nodeColor(n, maxDepth) {
  const t = Math.min(1, (n.depth || 0) / maxDepth);
  const col = C_WHITE.clone().lerp(C_ORANGE, t * 0.92);
  if (n.type === 'agent') col.lerp(C_YELLOW, 0.55);
  return col;
}
function nodeRadius(n, R, maxDepth) {
  if (n.type === 'core') return R * 0.055;
  const t = (n.depth || 1) / maxDepth;
  let s = R * (0.03 - 0.016 * t);
  if (n.type === 'project' || n.type === 'agent') s *= 1.6;
  return Math.max(R * 0.0075, s);
}

window.cbInitPlanet = function (container, graph) {
  if (!container || mounted.has(container)) return;
  if (!graph || !graph.nodes || !graph.nodes.length) return;
  mounted.add(container);

  const R = 100;
  const { byId, maxDepth } = layout(graph, R * 0.9);
  const nodes = Array.from(byId.values());

  const w = container.clientWidth || 800, h = container.clientHeight || 600;
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  renderer.setSize(w, h);
  container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, w / h, 1, 4000);
  camera.position.set(0, R * 0.35, R * 3.1);

  // sphere shell (faint lat/long wireframe)
  const shell = new THREE.Mesh(
    new THREE.SphereGeometry(R, 40, 28),
    new THREE.MeshBasicMaterial({ color: 0xffffff, wireframe: true, transparent: true, opacity: 0.045 })
  );
  scene.add(shell);
  const shell2 = new THREE.Mesh(
    new THREE.SphereGeometry(R * 1.002, 64, 1),
    new THREE.MeshBasicMaterial({ color: 0xffb066, wireframe: true, transparent: true, opacity: 0.05 })
  );
  scene.add(shell2);

  // edges (vertex-coloured lines parent -> child)
  const epos = [], ecol = [];
  graph.edges.forEach(e => {
    const p = byId.get(e.source), c = byId.get(e.target);
    if (!p || !c) return;
    const cp = nodeColor(p, maxDepth), cc = nodeColor(c, maxDepth);
    const fade = e.kind === 'wikilink' ? 0.35 : 1;
    epos.push(p.pos.x, p.pos.y, p.pos.z, c.pos.x, c.pos.y, c.pos.z);
    ecol.push(cp.r * 0.5, cp.g * 0.5, cp.b * 0.5, cc.r * fade, cc.g * fade, cc.b * fade);
  });
  const egeo = new THREE.BufferGeometry();
  egeo.setAttribute('position', new THREE.Float32BufferAttribute(epos, 3));
  egeo.setAttribute('color', new THREE.Float32BufferAttribute(ecol, 3));
  const edges = new THREE.LineSegments(egeo,
    new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.5, blending: THREE.AdditiveBlending, depthWrite: false }));
  scene.add(edges);

  // nodes (instanced)
  const drawn = nodes.filter(n => n.type !== 'core');
  const geo = new THREE.IcosahedronGeometry(1, 1);
  const mat = new THREE.MeshBasicMaterial({ vertexColors: false });
  const inst = new THREE.InstancedMesh(geo, mat, drawn.length);
  const dummy = new THREE.Object3D();
  drawn.forEach((n, i) => {
    dummy.position.copy(n.pos);
    const r = nodeRadius(n, R * 0.9, maxDepth);
    dummy.scale.setScalar(r);
    dummy.updateMatrix();
    inst.setMatrixAt(i, dummy.matrix);
    inst.setColorAt(i, nodeColor(n, maxDepth));
  });
  inst.instanceMatrix.needsUpdate = true;
  if (inst.instanceColor) inst.instanceColor.needsUpdate = true;
  scene.add(inst);

  // incandescent core (the Heart)
  const core = new THREE.Sprite(new THREE.SpriteMaterial({
    map: glowTexture(), transparent: true, blending: THREE.AdditiveBlending, depthWrite: false
  }));
  core.scale.setScalar(R * 1.1);
  scene.add(core);
  const coreDot = new THREE.Mesh(new THREE.IcosahedronGeometry(R * 0.05, 2),
    new THREE.MeshBasicMaterial({ color: 0xffffff }));
  scene.add(coreDot);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.enablePan = false;
  controls.minDistance = R * 1.3;
  controls.maxDistance = R * 6;
  controls.autoRotate = !reduceMotion();
  controls.autoRotateSpeed = 0.45;
  let interacting = false;
  controls.addEventListener('start', () => { interacting = true; controls.autoRotate = false; });
  controls.addEventListener('end', () => { interacting = false; if (!reduceMotion()) setTimeout(() => { if (!interacting) controls.autoRotate = true; }, 2500); });

  function resize() {
    const ww = container.clientWidth, hh = container.clientHeight;
    if (!ww || !hh) return;
    camera.aspect = ww / hh; camera.updateProjectionMatrix();
    renderer.setSize(ww, hh);
  }
  try { new ResizeObserver(resize).observe(container); } catch (e) { window.addEventListener('resize', resize); }

  // ── orb control (Voice Orb, Phase 5): the core IS the orb ──────────────
  const orb = { amp: 0, mode: 'idle' };
  const CW = new THREE.Color(0xffffff), CCOOL = new THREE.Color(0x7fd0ff);
  window.cbPlanet = {
    setAmplitude: function (a) { orb.amp = Math.max(0, Math.min(1, a || 0)); },
    setState: function (s) { orb.mode = s || 'idle'; }
  };

  let running = true;
  const clock = new THREE.Clock();
  function animate() {
    if (!running) return;
    requestAnimationFrame(animate);
    const t = clock.getElapsedTime();
    const a = orb.amp;
    let scale = 1.06 + Math.sin(t * 1.2) * 0.04, bright = 1, cool = false;
    if (orb.mode === 'speaking') { scale = 1.05 + a * 0.7; bright = 1 + a * 0.85; }
    else if (orb.mode === 'listening') { scale = 1.04 + a * 0.35 + Math.sin(t * 4) * 0.02; bright = 1.05; cool = true; }
    else if (orb.mode === 'thinking') { scale = 1.02 + Math.sin(t * 2.2) * 0.02; bright = 0.62; }
    core.scale.setScalar(R * scale);
    core.material.opacity = Math.min(1, 0.8 * bright + a * 0.3);
    coreDot.scale.setScalar(1 + a * 1.3);
    const target = cool ? CCOOL : CW;
    coreDot.material.color.lerp(target, 0.08);
    core.material.color.lerp(target, 0.05);
    controls.update();
    renderer.render(scene, camera);
  }
  animate();
};
