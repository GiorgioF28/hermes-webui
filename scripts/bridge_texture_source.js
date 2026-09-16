// Original procedural textures, used offline by bake_bridge_textures.py only.
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
      putPixel(img.data, i * 4, mix(0.84, 1.0, heat), mix(0.56, 0.94, heat), mix(0.22, 0.68, heat));
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

