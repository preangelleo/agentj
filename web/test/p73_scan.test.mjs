// F29 / ADR-A177: iPhone could not scan the terminal's pairing QR. Covers (1) the compact pairing link — old and new links
// both parse, the new QR is version 8 (49 modules) instead of 13 (69); (2) the scan pipeline (public/js/scan.js) against
// jsQR on simulated screen photos, next to the 0.15.8 pipeline; (3) the real iPhone screenshot from the report, which no
// decoder reads (the photo's problem, not the decoder's: see the test); (4) the page wiring + the 10 s hint.
// Evidence images + the full grid: node web/test/p73_scan_evidence.mjs → reports/qa/p73/f29/.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parsePairing, b64u } from '../../protocol/wire.js';
import { scanPlan, SCAN_CONSTRAINTS, SCAN_FALLBACK, SCAN_SLOW_MS } from '../public/js/scan.js';
import * as L from './p73_scan_lib.mjs';

const FX = JSON.parse(readFileSync(new URL('./fixtures/p73/pair_qr.json', import.meta.url), 'utf8'));
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');

// an ideal render (white background, 4 px per module, quiet zone) → jsQR
function clean(rows, ppm = 4) {
  const n = rows.length, N = n + 8, w = N * ppm, g = new Uint8ClampedArray(w * w).fill(255);
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) if (rows[y][x] === '1')
    for (let j = 0; j < ppm; j++) for (let i = 0; i < ppm; i++) g[((y + 4) * ppm + j) * w + (x + 4) * ppm + i] = 0;
  return L.jsQR(L.rgba(g), w, w)?.data;
}

test('compact pairing link: QR version 8 / 49 modules (was 13 / 69), decodes, and parses to the same pairing as the old link', () => {
  assert.equal(FX.v2.version, 8); assert.equal(FX.v2.modules, 49); assert.equal(FX.v2.error, 'M');
  assert.equal(FX.v1.version, 13); assert.equal(FX.v1.modules, 69);
  assert.ok(FX.v2.chars < FX.v1.chars, `${FX.v2.chars} < ${FX.v1.chars}`);
  assert.match(FX.v2.link, /^https:\/\/m\.agentj\.app\/#p=[0-9]+$/);
  for (const k of ['v1', 'v2']) assert.equal(clean(FX[k].rows), FX[k].link, `${k} QR decodes back with jsQR`);
  const a = parsePairing(FX.v1.link, 0), b = parsePairing(FX.v2.link, 0);
  assert.equal(b.relay, 'wss://relay.agentj.app');
  assert.equal(b.channel, a.channel); assert.equal(b.expires, a.expires);
  for (const f of ['hostPub', 'pairingId', 'psk']) assert.equal(b64u(b[f]), b64u(a[f]), f);
  assert.throws(() => parsePairing(FX.v2.link), /expired/, 'the fixture expired long ago');
});

// the compact link built here (mirror of host wire.pairing_link) for the edge cases
function v2(fields = {}) {
  const o = { ver: 2, ch: new Uint8Array(16).fill(1), k: new Uint8Array(32).fill(2), i: new Uint8Array(16).fill(3), p: new Uint8Array(32).fill(4), x: 1_800_000_060, relay: '', ...fields };
  const r = new TextEncoder().encode(o.relay), b = new Uint8Array(101 + r.length);
  b[0] = o.ver; b.set(o.ch, 1); b.set(o.k, 17); b.set(o.i, 49); b.set(o.p, 65); new DataView(b.buffer).setUint32(97, o.x); b.set(r, 101);
  return 'https://m.agentj.app/#p=' + BigInt('0x' + Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('')).toString();
}
test('compact pairing link: relay field, version byte, expiry, length, pinning', () => {
  const now = 1_800_000_000;
  assert.equal(parsePairing(v2(), now).relay, 'wss://relay.agentj.app');
  assert.equal(parsePairing(v2({ relay: 'alpha-relay.agentjarvis.net' }), now).relay, 'wss://alpha-relay.agentjarvis.net');
  assert.equal(parsePairing(v2({ relay: 'ws://127.0.0.1:8899' }), now).relay, 'ws://127.0.0.1:8899');
  assert.throws(() => parsePairing(v2({ relay: 'ws://evil.example' }), now), /bad relay/);
  assert.throws(() => parsePairing(v2({ ver: 3 }), now), /unsupported version/);
  assert.throws(() => parsePairing(v2({ x: now - 1 }), now), /expired/);
  assert.throws(() => parsePairing('https://m.agentj.app/#p=' + '9'.repeat(401), now), /bad pairing link/);
  assert.throws(() => parsePairing('https://m.agentj.app/#p=12345', now), /unsupported version/);
  const pin = (u) => u === 'wss://relay.agentj.app';
  assert.equal(parsePairing(v2(), now, pin).channel, b64u(new Uint8Array(16).fill(1)));
  assert.throws(() => parsePairing(v2({ relay: 'relay.evil.example' }), now, pin), /not allowed/);
});

test('scanPlan: centre square at full resolution up to 1080, smoothed and inverted tries, whole frame; never the 800-px squeeze', () => {
  assert.deepEqual(SCAN_CONSTRAINTS.video, { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1080 } });
  assert.deepEqual(SCAN_FALLBACK.video, { facingMode: { ideal: 'environment' } });
  assert.equal(SCAN_SLOW_MS, 10_000);
  assert.deepEqual(scanPlan(1920, 1080, 0), { sx: 420, sy: 0, sw: 1080, sh: 1080, dw: 1080, dh: 1080, invert: false });
  assert.deepEqual(scanPlan(1920, 1080, 1), { sx: 420, sy: 0, sw: 1080, sh: 1080, dw: 720, dh: 720, invert: false });
  assert.deepEqual(scanPlan(1920, 1080, 2), { sx: 420, sy: 0, sw: 1080, sh: 1080, dw: 720, dh: 720, invert: true });
  assert.deepEqual(scanPlan(1920, 1080, 3), { sx: 0, sy: 0, sw: 1920, sh: 1080, dw: 960, dh: 540, invert: false });
  assert.deepEqual(scanPlan(1080, 1920, 4), { sx: 0, sy: 420, sw: 1080, sh: 1080, dw: 1080, dh: 1080, invert: false }, 'portrait');
  assert.deepEqual(scanPlan(640, 480, 0), { sx: 80, sy: 0, sw: 480, sh: 480, dw: 480, dh: 480, invert: false }, 'a 640×480 camera: no shrink');
  assert.equal(scanPlan(3840, 2160, 0).dw, 1080, '4K is shrunk: CPU bounded');
  // pixels per module vs the old pipeline on a 640×480 iPhone stream (no shrink there): the same scene through a 1080p
  // stream is 3× wider (same horizontal field of view); even the smoothed try keeps ≥ 2×
  for (const k of [0, 1, 2]) { const p = scanPlan(1920, 1080, k); assert.ok(3 * p.dw / p.sw >= 2, `try ${k}`); }
});

// One physical scene, the one in the report: a friend at arm's length, the 69-module code ≈ 2.6 camera px per module in iOS
// Safari's default 640×480 stream. The same scene through a 1920×1080 stream (same horizontal field of view) is 3× wider:
// ≈ 7.8 px per module. Eight successive frames each (≈ 2 s of scanning).
test('simulated screen photos: the report\'s scene fails the old pipeline at 640×480; at 1080p the new pipeline reads both codes', () => {
  const at640 = L.screenScene(FX.v1.rows, { w: 640, h: 480, ppm: 2.6, blur: 1 });
  assert.equal(L.scanFrames(at640, 'old'), null, 'reproduces F29: 640×480, 69 modules, whole frame → nothing');
  for (const k of ['v2', 'v1']) {
    const hd = L.screenScene(FX[k].rows, { w: 1920, h: 1080, ppm: 7.8, blur: 2 });
    const got = L.scanFrames(hd, 'new');
    assert.ok(got, `${k}: new pipeline at 1920×1080 decodes`);
    assert.equal(got.data, FX[k].link);
    if (k === 'v1') assert.equal(L.scanFrames(hd, 'old'), null, 'the old 800-px squeeze loses it even from a 1080p stream');
  }
});

test('simulated screen photos: a camera that stays at 640×480 reads the new code when it fills about half the frame height', () => {
  // the code (with its quiet zone) 55 % of the frame height: 49 modules → 4.6 px each; the old 69-module code → 3.4 px
  const ppm = (k) => 0.55 * 480 / (FX[k].modules + 8);
  const nw = L.scanFrames(L.screenScene(FX.v2.rows, { w: 640, h: 480, ppm: ppm('v2'), blur: 1 }), 'new');
  assert.ok(nw, 'new code, new pipeline, 640×480'); assert.equal(nw.data, FX.v2.link);
  assert.equal(L.scanFrames(L.screenScene(FX.v1.rows, { w: 640, h: 480, ppm: ppm('v1'), blur: 1 }), 'old'), null,
    'the old code at the same size on screen was not readable');
});

test('the real iPhone screenshot from the report: unreadable by any pipeline — the photo, not the decoder', () => {
  // fixtures/p73/iphone-scan-terminal.crop50.png: the laptop screen cut out of the friend's screenshot (status bar and the
  // people in the background removed), grey, half size (QR modules ≈ 6 px here). The screenshot shows the page's camera
  // preview: iOS's ~640×480 stream blown up ×4.35, so each module was ≈ 2.8 stream pixels, and it is visibly smeared across
  // module edges. jsQR fails at every scale and crop below; zbarimg and ZXingReader (tried by hand, P73) fail on the original
  // full-resolution screenshot and on the crop too. So the picture itself never carried a readable code: the stream
  // resolution (fixed by asking for 1080p) was the cause, not a jsQR weakness a better decoder would have hidden.
  const img = L.readPngGray(readFileSync(new URL('./fixtures/p73/iphone-scan-terminal.crop50.png', import.meta.url)));
  assert.deepEqual([img.w, img.h], [570, 555]);
  assert.equal(L.oldPipeline(img), null);
  for (let k = 0; k < 4; k++) assert.equal(L.newPipeline(img, k), null, `try ${k}`);
  for (const s of [0.5, 0.75, 1]) {
    const q = L.resample(img, 20, 30, 460, 490, Math.round(460 * s), Math.round(490 * s));   // the code alone
    assert.equal(L.jsQR(L.rgba(q.gray), q.w, q.h, { inversionAttempts: 'attemptBoth' }), null, `cropped ×${s}`);
  }
});

test('page wiring: 1080p request with fallback, scanFrame instead of the 800-px squeeze, the 10 s hint in both languages', () => {
  const app = read('../public/app.js');
  assert.match(app, /from '\.\/js\/scan\.js'/);
  assert.match(app, /getUserMedia\(SCAN_CONSTRAINTS\)/);
  assert.match(app, /getUserMedia\(SCAN_FALLBACK\)/);
  assert.match(app, /scanFrame\(ctx, video, video\.videoWidth, video\.videoHeight, attempt\+\+, window\.jsQR\)/);
  assert.doesNotMatch(app, /800 \/ video\.videoWidth/);
  assert.match(app, /SCAN_SLOW_MS/);
  assert.match(app, /t\(push\.standalone\(\) \? 'camera\.slowApp' : 'camera\.slow'\)/);
  for (const f of ['../i18n/web.zh.json', '../i18n/web.en.json']) {
    const c = JSON.parse(read(f)).camera;
    for (const k of ['slow', 'slowApp']) assert.match(c[k], /`agentj pair --link`/, `${f} camera.${k}`);
  }
  assert.match(JSON.parse(read('../i18n/web.zh.json')).camera.slow, /相机/);
  assert.match(JSON.parse(read('../i18n/web.en.json')).camera.slow, /Camera app/);
});
