// P364 (#74, Leo 10-17): the Android WebAPK splash showed the robot on a Lotion square over Sooty. Chrome picks the splash
// image from the MASKABLE icons first (Chromium ShortcutInfo::UpdateBestSplashIcon), ANY only as a fallback, and draws it on
// background_color — so the maskable is full-bleed background_color, the "any" icons are transparent pixel art, and an
// installed Android app launches with theme-color = background_color (status bar icons light on the Sooty splash).
// Run: node --test web/test/p364-splash.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { inflateSync } from 'node:zlib';
import { createHash } from 'node:crypto';
import { PUBLIC_DIR } from '../build.mjs';

const M = JSON.parse(readFileSync(join(PUBLIC_DIR, 'manifest.webmanifest'), 'utf8'));
const TOK = readFileSync(join(PUBLIC_DIR, 'brand/palette.css'), 'utf8');
const hex = (r, g, b) => '#' + [r, g, b].map((x) => x.toString(16).padStart(2, '0')).join('');

/** 8-bit RGBA, non-interlaced PNG (path or bytes) → { w, h, px(x, y) → [r, g, b, a] } (what make-robot-images.py writes). */
export function readPng(file) {
  const b = Buffer.isBuffer(file) ? file : readFileSync(file);
  let o = 8, w = 0, h = 0; const idat = [];
  while (o < b.length) {
    const n = b.readUInt32BE(o), t = b.toString('latin1', o + 4, o + 8), d = b.subarray(o + 8, o + 8 + n);
    if (t === 'IHDR') { w = d.readUInt32BE(0); h = d.readUInt32BE(4); assert.deepEqual([d[8], d[9], d[12]], [8, 6, 0], `${file}: 8-bit RGBA, not interlaced`); }
    if (t === 'IDAT') idat.push(d);
    o += 12 + n;
  }
  const raw = inflateSync(Buffer.concat(idat)), st = w * 4, out = Buffer.alloc(w * h * 4);
  for (let y = 0; y < h; y++) {
    const f = raw[y * (st + 1)];
    for (let i = 0; i < st; i++) {
      const x = raw[y * (st + 1) + 1 + i], a = i >= 4 ? out[y * st + i - 4] : 0, up = y ? out[(y - 1) * st + i] : 0, c = y && i >= 4 ? out[(y - 1) * st + i - 4] : 0;
      const p = a + up - c, pa = Math.abs(p - a), pb = Math.abs(p - up), pc = Math.abs(p - c);
      out[y * st + i] = (x + [0, a, up, (a + up) >> 1, pa <= pb && pa <= pc ? a : pb <= pc ? up : c][f]) & 255;
    }
  }
  return { w, h, px: (x, y) => [...out.subarray((y * w + x) * 4, (y * w + x) * 4 + 4)] };
}
const icon = (src) => readPng(join(PUBLIC_DIR, src.split('?')[0]));
const all = (im) => { const s = new Map(); for (let y = 0; y < im.h; y++) for (let x = 0; x < im.w; x++) { const p = im.px(x, y); const k = p[3] ? hex(...p) + '/' + p[3] : 'clear'; s.set(k, (s.get(k) || 0) + 1); } return s; };

test('manifest: "any" 192 + 512 and one maskable 512, declared separately, each URL versioned by its own bytes', () => {
  assert.deepEqual(M.icons.map((i) => `${i.sizes}/${i.purpose}`), ['192x192/any', '512x512/any', '512x512/maskable']);
  assert.ok(!M.icons.some((i) => /\s/.test(i.purpose)), 'no "any maskable" double purpose: one image cannot be both');
  const [, any512, mask] = M.icons;
  assert.notEqual(any512.src.split('?')[0], mask.src.split('?')[0]);
  for (const i of M.icons) {
    const [p, q] = i.src.split('?');
    assert.equal(q, 'v=' + createHash('sha256').update(readFileSync(join(PUBLIC_DIR, p))).digest('hex').slice(0, 8), i.src);
  }
  assert.equal(M.background_color, '#141414', 'splash = Sooty');
  assert.equal(M.theme_color, M.background_color, 'status bar on the splash = the splash colour (light icons)');
});

test('"any" icons: transparent background, hard pixel edges (alpha 0 or 255 only), closed-palette colours only', () => {
  for (const i of M.icons.filter((x) => x.purpose === 'any')) {
    const im = icon(i.src), c = all(im);
    assert.equal(`${im.w}x${im.h}`, i.sizes, i.src);
    for (const [x, y] of [[0, 0], [im.w - 1, 0], [0, im.h - 1], [im.w - 1, im.h - 1]]) assert.equal(im.px(x, y)[3], 0, `${i.src} corner ${x},${y} transparent`);
    assert.ok(c.get('clear') > im.w * im.h / 2, `${i.src}: mostly transparent`);
    for (const k of c.keys()) {
      if (k === 'clear') continue;
      const [h, a] = k.split('/');
      assert.equal(a, '255', `${i.src}: ${k} — no anti-aliased edge`);
      assert.ok(TOK.includes(`: ${h};`), `${i.src}: ${h} is a palette token`);
    }
  }
});

test('maskable: opaque, full-bleed background_color, the same robot as "any" inside the 80 % safe zone', () => {
  const i = M.icons.find((x) => x.purpose === 'maskable'), im = icon(i.src), any = icon(M.icons[1].src), c = all(im);
  assert.ok(!c.has('clear'), 'no transparent pixel (launchers fill them unpredictably)');
  assert.equal(hex(...im.px(0, 0)), M.background_color, 'background = splash colour: no square on the splash');
  const lo = Math.ceil(im.w * 0.1), hi = Math.floor(im.w * 0.9);
  for (let y = 0; y < im.h; y++) for (let x = 0; x < im.w; x++) {
    const a = any.px(x, y);
    if (a[3]) {
      assert.ok(x >= lo && x < hi && y >= lo && y < hi, `robot pixel ${x},${y} inside the safe zone`);
      assert.deepEqual(im.px(x, y), a, `robot pixel ${x},${y} = the "any" robot`);
    } else assert.equal(hex(...im.px(x, y)), M.background_color, `${x},${y}`);
  }
});

test('iOS apple-touch-icon is untouched: opaque Lotion (iOS turns transparency black)', () => {
  const im = readPng(join(PUBLIC_DIR, 'apple-touch-icon.png'));
  assert.equal(hex(...im.px(0, 0)), '#faf9f5'); assert.equal(im.px(0, 0)[3], 255);
});

test('boot.js: an installed Android app launches with theme-color = background_color (iPhone keeps its saved colour)', () => {
  const B = readFileSync(join(PUBLIC_DIR, 'js/boot.js'), 'utf8');
  assert.match(B, /matchMedia\("\(display-mode: standalone\)"\)\.matches/);
  assert.ok(B.includes(`setAttribute("content", "${M.background_color}")`));
  assert.ok(B.indexOf('display-mode: standalone') < B.indexOf('localStorage.getItem("aj.chrome")'), 'Android branch returns before the iPhone launch colour');
});

// P364 (Leo 10-17: 「logo 在这个圆圈中间，是不是有点向右偏移」): the robot is 9 cells wide on a 16-cell grid, so the plain
// 16 → n scale left it half a cell right of centre (16 px on the 512 icon). Every robot image — PWA any/maskable, apple-touch,
// favicons (+ the .ico payloads), header / status / theme logos, send button, brand docs, Olares and macOS icons — must have
// its robot pixel box centred on the canvas: |box centre − canvas centre| ≤ 0.5 px on both axes.
const AJ = join(dirname(fileURLToPath(import.meta.url)), '../..');
// robot ink: the five state body colours, antenna, eyes, face. Backgrounds (transparent, Sooty, Lotion fill, the disc's Salt,
// the Olares gradient, the macOS green plate) are not robot: a pixel equal to its row's first pixel is background.
const INK = new Set(['#629887', '#6a9bcc', '#d97757', '#827dbd', '#858585', '#ffe292', '#2c2b26', '#faf9f5']);

/** Robot pixel box of a decoded image → { dx, dy } = box centre − canvas centre, in px. */
export function offset(im, plate = []) {
  let x0 = Infinity, x1 = -1, y0 = Infinity, y1 = -1;
  for (let y = 0; y < im.h; y++) {
    const bg = im.px(0, y);
    for (let x = 0; x < im.w; x++) {
      const p = im.px(x, y), h = hex(...p);
      if (p[3] < 128 || !INK.has(h) || plate.includes(h) || (bg[3] >= 128 && hex(...bg) === h)) continue;
      x0 = Math.min(x0, x); x1 = Math.max(x1, x + 1); y0 = Math.min(y0, y); y1 = Math.max(y1, y + 1);
    }
  }
  assert.ok(x1 > 0, 'robot found');
  return { dx: (x0 + x1) / 2 - im.w / 2, dy: (y0 + y1) / 2 - im.h / 2, box: [x0, y0, x1, y1] };
}

const STATES = ['idle', 'working', 'waiting', 'question', 'offline'];
const FILES = [
  ...['icon-192', 'icon-512', 'icon-512-maskable', 'favicon-192-round', 'apple-touch-icon', 'favicon-16', 'favicon-32', 'favicon-48',
    'logo-mark', 'shield-64', 'shield-128', 'shield-256', 'shield-source'].map((n) => `brand/img/${n}.png`),
  ...STATES.map((s) => `brand/img/robot-${s}-128.png`),
  ...STATES.slice(1).map((s) => `brand/img/status/logo-${s}.png`),
  ...['robots', 'dots'].flatMap((t) => [...STATES.map((s) => `host/agentj/themes/${t}/logo-${s}.png`), `host/agentj/themes/${t}/send-robot.png`]),
  ...['icon-192', 'icon-512', 'icon-512-maskable', 'favicon-192-round', 'logo-mark', 'shield-64', 'shield-128', 'shield-256'].map((n) => `web/public/brand/img/${n}.png`),
  'web/public/apple-touch-icon.png', 'web/public/assets/five-robot.png', 'web/public/assets/send-robot-128.png', 'web/public/assets/send-robot-256.png',
  'android/app/src/main/res/drawable-nodpi/ic_launcher.png',
  ...[...STATES.map((s) => `logo-${s}`), 'logo-disc'].map((n) => `site/content/docs/32-robots/${n}.png`),
  'site/static/olares/agentj-icon-256.png',
  ...[16, 32, 64, 128, 256, 512, 1024].map((n) => `release/macos/icon/icon-${n}.png`),
];
const PLATE = { 'release/macos': ['#629887'] };   // the macOS icon is a Lotion robot on a green plate

test('every robot image: robot box centred on the canvas within 0.5 px (both axes)', () => {
  let n = 0;
  for (const f of FILES) {
    const p = join(AJ, f);
    if (!existsSync(p)) { assert.ok(!f.startsWith('web/'), `${f} missing`); continue; }   // brand/, site/, release/ are private
    const { dx, dy, box } = offset(readPng(p), PLATE[Object.keys(PLATE).find((k) => f.startsWith(k))] || []);
    assert.ok(Math.abs(dx) <= 0.5 && Math.abs(dy) <= 0.5, `${f}: box ${box} off centre by ${dx}, ${dy} px`);
    n++;
  }
  assert.ok(n >= 12, `${n} images measured`);
});

test('favicon.ico: every embedded PNG has its robot centred within 0.5 px', () => {
  for (const f of ['web/public/favicon.ico', 'brand/img/favicon.ico']) {
    const p = join(AJ, f);
    if (!existsSync(p)) continue;
    const b = readFileSync(p), count = b.readUInt16LE(4);
    assert.ok(count >= 3, f);
    for (let i = 0; i < count; i++) {
      const e = 6 + 16 * i, size = b.readUInt32LE(e + 8), at = b.readUInt32LE(e + 12);
      const im = readPng(b.subarray(at, at + size)), { dx, dy } = offset(im);
      assert.ok(Math.abs(dx) <= 0.5 && Math.abs(dy) <= 0.5, `${f} #${i} (${im.w}px) off by ${dx}, ${dy}`);
    }
  }
});
