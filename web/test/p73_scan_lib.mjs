// F29 / ADR-A177 test helpers (no dependencies): a tiny grayscale PNG reader / writer, a "photo of a terminal QR" simulator
// and the two scan pipelines — the old one reconstructed from 0.15.8 app.js (whole frame scaled to ≤ 800 px wide, one jsQR call
// with its default inversionAttempts "attemptBoth") and the new one, which is public/js/scan.js itself.
import { inflateSync, deflateSync } from 'node:zlib';
import { createRequire } from 'node:module';
import { scanPlan, scanPixels } from '../public/js/scan.js';

export const jsQR = createRequire(import.meta.url)('../public/vendor/jsQR.js');

// ---------------------------------------------------------------- PNG (8-bit grayscale, non-interlaced)
export function readPngGray(buf) {
  if (buf.readUInt32BE(0) !== 0x89504e47) throw new Error('not a PNG');
  let off = 8, w = 0, h = 0;
  const idat = [];
  while (off < buf.length) {
    const len = buf.readUInt32BE(off), type = buf.toString('latin1', off + 4, off + 8), data = buf.subarray(off + 8, off + 8 + len);
    if (type === 'IHDR') {
      w = data.readUInt32BE(0); h = data.readUInt32BE(4);
      if (data[8] !== 8 || data[9] !== 0 || data[12] !== 0) throw new Error('only 8-bit grayscale, non-interlaced');
    } else if (type === 'IDAT') idat.push(data);
    off += 12 + len;
  }
  const raw = inflateSync(Buffer.concat(idat));
  const out = new Uint8ClampedArray(w * h);
  for (let y = 0; y < h; y++) {
    const f = raw[y * (w + 1)], row = raw.subarray(y * (w + 1) + 1, (y + 1) * (w + 1));
    for (let x = 0; x < w; x++) {
      const a = x ? out[y * w + x - 1] : 0, b = y ? out[(y - 1) * w + x] : 0, c = x && y ? out[(y - 1) * w + x - 1] : 0;
      let p = row[x];
      if (f === 1) p += a; else if (f === 2) p += b; else if (f === 3) p += (a + b) >> 1;
      else if (f === 4) { const q = a + b - c, pa = Math.abs(q - a), pb = Math.abs(q - b), pc = Math.abs(q - c); p += pa <= pb && pa <= pc ? a : pb <= pc ? b : c; }
      out[y * w + x] = p & 255;
    }
  }
  return { gray: out, w, h };
}

const CRC = new Int32Array(256).map((_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c; });
const crc32 = (b) => { let c = -1; for (const x of b) c = CRC[(c ^ x) & 255] ^ (c >>> 8); return (c ^ -1) >>> 0; };
function chunk(type, data) {
  const t = Buffer.concat([Buffer.from(type, 'latin1'), data]), o = Buffer.alloc(t.length + 8);
  o.writeUInt32BE(data.length, 0); t.copy(o, 4); o.writeUInt32BE(crc32(t), t.length + 4);
  return o;
}
export function writePngGray(gray, w, h) {
  const raw = Buffer.alloc((w + 1) * h);
  for (let y = 0; y < h; y++) raw.set(gray.subarray(y * w, (y + 1) * w), y * (w + 1) + 1);
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8;
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk('IHDR', ihdr), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}

// ---------------------------------------------------------------- images
export function rgba(gray) {
  const o = new Uint8ClampedArray(gray.length * 4);
  for (let i = 0; i < gray.length; i++) { o[4 * i] = o[4 * i + 1] = o[4 * i + 2] = gray[i]; o[4 * i + 3] = 255; }
  return o;
}
/** What canvas drawImage(src, sx, sy, sw, sh, 0, 0, dw, dh) roughly does: area average when shrinking, bilinear when growing. */
export function resample(img, sx, sy, sw, sh, dw, dh) {
  const { gray, w } = img, out = new Uint8ClampedArray(dw * dh), fx = sw / dw, fy = sh / dh;
  for (let y = 0; y < dh; y++) for (let x = 0; x < dw; x++) {
    if (fx > 1 || fy > 1) {
      const x0 = sx + x * fx, x1 = x0 + fx, y0 = sy + y * fy, y1 = y0 + fy;
      let s = 0, n = 0;
      for (let yy = Math.floor(y0); yy < Math.ceil(y1); yy++) for (let xx = Math.floor(x0); xx < Math.ceil(x1); xx++) {
        const wt = (Math.min(xx + 1, x1) - Math.max(xx, x0)) * (Math.min(yy + 1, y1) - Math.max(yy, y0));
        s += gray[yy * w + xx] * wt; n += wt;
      }
      out[y * dw + x] = s / n;
    } else {
      const u = sx + (x + 0.5) * fx - 0.5, v = sy + (y + 0.5) * fy - 0.5, i = Math.max(0, Math.floor(u)), j = Math.max(0, Math.floor(v));
      const a = Math.min(Math.max(u - i, 0), 1), b = Math.min(Math.max(v - j, 0), 1), i1 = Math.min(i + 1, sx + sw - 1), j1 = Math.min(j + 1, sy + sh - 1);
      out[y * dw + x] = (gray[j * w + i] * (1 - a) + gray[j * w + i1] * a) * (1 - b) + (gray[j1 * w + i] * (1 - a) + gray[j1 * w + i1] * a) * b;
    }
  }
  return { gray: out, w: dw, h: dh };
}

function rng(seed) {   // mulberry32: the same "photo" on every run
  return () => { seed |= 0; seed = (seed + 0x6d2b79f5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}
// 3×3 homography mapping the unit square (0,0)(1,0)(1,1)(0,1) onto quad q = [[x,y]×4] (Heckbert), and its inverse
function squareToQuad(q) {
  const [[x0, y0], [x1, y1], [x2, y2], [x3, y3]] = q;
  const dx1 = x1 - x2, dx2 = x3 - x2, dy1 = y1 - y2, dy2 = y3 - y2, sx = x0 - x1 + x2 - x3, sy = y0 - y1 + y2 - y3;
  const den = dx1 * dy2 - dx2 * dy1, g = (sx * dy2 - dx2 * sy) / den, h = (dx1 * sy - sx * dy1) / den;
  return [x1 - x0 + g * x1, x3 - x0 + h * x3, x0, y1 - y0 + g * y1, y3 - y0 + h * y3, y0, g, h, 1];
}
function invert3([a, b, c, d, e, f, g, h, i]) {
  const A = e * i - f * h, B = -(d * i - f * g), C = d * h - e * g, det = a * A + b * B + c * C;
  return [A / det, -(b * i - c * h) / det, (b * f - c * e) / det, B / det, (a * i - c * g) / det, -(a * f - c * d) / det, C / det, -(a * h - b * g) / det, (a * e - b * d) / det];
}
function boxBlur(src, w, h, r) {
  if (r <= 0) return src;
  const tmp = new Float32Array(w * h), out = new Float32Array(w * h), n = 2 * r + 1;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) { let s = 0; for (let k = -r; k <= r; k++) s += src[y * w + Math.min(w - 1, Math.max(0, x + k))]; tmp[y * w + x] = s / n; }
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) { let s = 0; for (let k = -r; k <= r; k++) s += tmp[Math.min(h - 1, Math.max(0, y + k)) * w + x]; out[y * w + x] = s / n; }
  return out;
}

/** A laptop screen showing a QR the way `agentj pair` draws it in a terminal (light modules lit, dark ones = the dark terminal,
 *  a 4-module light quiet zone), as the camera's optics see it: w×h, ppm = camera pixels per module at the centre, tilt =
 *  perspective (the far edge this much narrower), rot in degrees, the screen's own pixel grid (cell screen px per module)
 *  integrated by the sensor (3×3 samples per pixel, so it beats into moiré), lens blur (box radius, twice) and uneven light.
 *  shoot() turns it into one video frame: moiré bands that drift and sensor noise, different per frame (seed). */
export function screenScene(rows, { w, h, ppm, rot = 4, tilt = 0.08, cell = 9, grid = 0.22, blur = 1 } = {}) {
  const n = rows.length, N = n + 8, side = N * ppm, cx = w / 2 + w * 0.01, cy = h / 2 - h * 0.015, rad = rot * Math.PI / 180;
  const corner = (u, v) => {   // u, v ∈ [-0.5, 0.5]; the top edge (v = -0.5) is narrower by `tilt`
    const k = 1 - tilt * (0.5 - v);
    const x = u * side * k, y = v * side * (1 - tilt / 2);
    return [cx + x * Math.cos(rad) - y * Math.sin(rad), cy + x * Math.sin(rad) + y * Math.cos(rad)];
  };
  const H = invert3(squareToQuad([corner(-0.5, -0.5), corner(0.5, -0.5), corner(0.5, 0.5), corner(-0.5, 0.5)]));
  const img = new Float32Array(w * h), S = 3;
  const lum = (u, v) => {    // u, v: position on the symbol incl. quiet zone, 0..1
    if (u < -0.15 || u > 1.25 || v < -0.2 || v > 1.3) return 110 + 40 * v;                           // the room around the laptop
    if (u < 0 || u > 1 || v < 0 || v > 1) return 24;                                                   // dark terminal
    const mx = Math.floor(u * N) - 4, my = Math.floor(v * N) - 4;
    const dark = mx >= 0 && my >= 0 && mx < n && my < n && rows[my][mx] === '1';
    const px = (u * N * cell) % 1, py = (v * N * cell) % 1;                                            // the screen's pixel grid
    return (dark ? 22 : 212) * (px < grid || py < grid ? 0.82 : 1);
  };
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    let s = 0;
    for (let j = 0; j < S; j++) for (let i = 0; i < S; i++) {
      const X = x + (i + 0.5) / S, Y = y + (j + 0.5) / S, d = H[6] * X + H[7] * Y + H[8];
      s += lum((H[0] * X + H[1] * Y + H[2]) / d, (H[3] * X + H[4] * Y + H[5]) / d);
    }
    img[y * w + x] = s / (S * S);
  }
  const b = boxBlur(boxBlur(img, w, h, blur), w, h, blur);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) b[y * w + x] *= 0.78 + 0.32 * (x / w) - 0.12 * Math.hypot(x / w - 0.5, y / h - 0.5);
  return { base: b, w, h, ppm, modules: n };
}
export function shoot(scene, seed = 1, { moire = 0.12, noise = 4 } = {}) {
  const { base, w, h } = scene, r = rng(seed), out = new Uint8ClampedArray(w * h), ph = r() * 6.28;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const band = 1 + moire * Math.sin((x * 0.9 + y * 0.35) / (w / 26) * 6.28 + ph);                   // moiré bands
    out[y * w + x] = base[y * w + x] * band + (r() + r() + r() - 1.5) * 2 * noise;                    // σ ≈ noise
  }
  return { gray: out, w, h };
}

// ---------------------------------------------------------------- the two pipelines
/** 0.15.8: the whole frame drawn at ≤ 800 px wide, jsQR with its defaults ("attemptBoth" — passed explicitly because the pinned
 *  jsQR writes every option it is given into its shared defaults). → decoded text or null */
export function oldPipeline(img) {
  const s = Math.min(1, 800 / img.w), f = resample(img, 0, 0, img.w, img.h, Math.round(img.w * s), Math.round(img.h * s));
  return jsQR(rgba(f.gray), f.w, f.h, { inversionAttempts: 'attemptBoth' })?.data ?? null;
}
/** ADR-A177: scanPlan's attempt k on one frame. → decoded text or null */
export function newPipeline(img, k) {
  const p = scanPlan(img.w, img.h, k), f = resample(img, p.sx, p.sy, p.sw, p.sh, p.dw, p.dh);
  return scanPixels(rgba(f.gray), f.w, f.h, p.invert, jsQR) ?? null;
}
/** Scan `frames` successive frames of a scene (≈ 4 per second in the page) → {frame, data} of the first frame that decoded, or null. */
export function scanFrames(scene, pipeline, frames = 8, seed = 1) {
  for (let k = 0; k < frames; k++) {
    const d = pipeline === 'old' ? oldPipeline(shoot(scene, seed + k)) : newPipeline(shoot(scene, seed + k), k);
    if (d) return { frame: k, data: d };
  }
  return null;
}
