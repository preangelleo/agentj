// P205 item 5 (Leo 10-13: a take on a locked Android phone came back with 23.5 s of -112 dBFS after one sentence): the
// microphone opens without noise suppression / echo cancellation, digital silence is found and told to the owner, and every
// way of recording shows the same centred dark circle (hold, double tap / M lock, long takes) with end / dim / cancel icons.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {silentRuns, GAP_MIN_MS, GAP_SKIP_MS} from '../public/js/wav.js';
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const src = read('../public/js/relay.js'), html = read('../public/index.html'), css = read('../public/app.css');
const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));
const zhSrc = JSON.parse(read('../i18n/web.zh.src.json')), ov = JSON.parse(read('../i18n/web.zh.override.json'));

const RATE = 16000;
// a room with the suppression off: a noise floor around -60 dBFS, speech bursts, and a stretch of true zeros
function take(spec) {
  const x = new Float32Array(Math.round(spec.reduce((n, s) => n + s[1], 0) * RATE));
  let i = 0, seed = 7;
  const rnd = () => ((seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff) * 2 - 1;
  for (const [kind, secs] of spec) for (let k = 0; k < secs * RATE; k++, i++)
    x[i] = kind === 'zero' ? 0 : kind === 'gate' ? rnd() * 1.5 / 32768 : kind === 'talk' ? Math.sin(i / 9) * 0.2 + rnd() * 0.001 : rnd() * 0.001;
  return x;
}

test('silentRuns: digital zeros are a gap, a room-tone pause is not', () => {
  const x = take([['room', 1], ['talk', 3], ['room', 4], ['talk', 2], ['zero', 23.5]]);
  assert.deepEqual(silentRuns(x, RATE), [{a: 10000, b: 33500}]);
});
test('silentRuns: the warm-up and short dropouts are ignored', () => {
  const x = take([['zero', 1.2], ['talk', 2], ['zero', 2], ['talk', 2]]);
  assert.deepEqual(silentRuns(x, RATE), [], 'starts inside GAP_SKIP_MS / shorter than GAP_MIN_MS');
  assert.ok(GAP_MIN_MS >= 2000 && GAP_SKIP_MS >= 1000);
});
test('silentRuns: why it is only trusted with suppression off — a gated pause reads exactly like a gap', () => {
  // Leo's 105727 take (suppression on): a 14 s pause between sentences sits at the same ±1 LSB as 105918's missing 23.5 s
  const x = take([['talk', 3], ['gate', 14], ['talk', 3]]);
  assert.equal(silentRuns(x, RATE).length, 1);
  assert.match(src, /p\.nsOff = tr\.getSettings\(\)\.noiseSuppression === false/);
  assert.match(src, /if \(!p\.nsOff\) return;/, 'the live meter does not report pauses when suppression stayed on');
  assert.match(src, /p\.nsOff && decoded \? decoded : \[\]/, 'decoded gaps only count with suppression off');
});

test('the microphone opens raw: no suppression, no echo cancellation, auto gain kept', () => {
  assert.match(src, /const MIC_AUDIO = \{echoCancellation: false, noiseSuppression: false, autoGainControl: true\};/);
  assert.match(src, /getUserMedia\(\{audio: MIC_AUDIO\}\)/);
  assert.doesNotMatch(src.slice(src.indexOf('function pttStart'), src.indexOf('function recOptions')), /getUserMedia\(\{audio: true\}\)/);
});

test('gap ranges merge and read as m:ss', () => {
  const lang = {v: 'zh'};
  const body = src.slice(src.indexOf('export function gapMerge'), src.indexOf('// Told once per take'));
  const ctx = {lang: () => lang.v, mmss: (s) => { s = Math.round(s); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); }};
  vm.runInNewContext(body.replace(/export function/g, 'function') + ';this.gapMerge=gapMerge;this.gapText=gapText;', ctx);
  const m = ctx.gapMerge([{a: 30000, b: 40000}, {a: 6850, b: 30300}, {a: 70000, b: 69000}]);
  assert.deepEqual(JSON.parse(JSON.stringify(m)), [{a: 6850, b: 40000}]);
  assert.equal(ctx.gapText([{a: 6850, b: 30300}]), '0:07–0:30');
  assert.equal(ctx.gapText([{a: 0, b: 3000}, {a: 5000, b: 8000}, {a: 9000, b: 12000}, {a: 20000, b: 23000}]), '0:00–0:03、0:05–0:08、0:09–0:12、…');
  lang.v = 'en'; assert.equal(ctx.gapText([{a: 0, b: 3000}, {a: 5000, b: 8000}]), '0:00–0:03, 0:05–0:08');
});

test('gaps come from mute → unmute, live digital silence and decoded take; hidden pages end capture', () => {
  assert.match(src, /listen\("unmute", \(\) => \{ p\.mutedAt = 0; gapAdd\(p, p\.muteT0, performance\.now\(\)\)/);
  assert.match(src, /if \(p\.muteT0\)\{ gapAdd\(p, p\.muteT0, tEnd\)/, 'still muted when the take ends');
  assert.match(src, /gapNote\(p, takeGaps\(p, r\.gaps\)\)/, 'words path');
  assert.match(src, /a\.label \+= t\('r\.tray\.gap'/, 'a segment chip says so');
  assert.match(src, /document\.visibilityState !== "visible"\) microphoneAway\(\)/, 'hidden pages end capture immediately');
  assert.match(read('../public/js/wav.js'), /gaps: silentRuns\(pcm\)/);
});

test('one recording card: a centred dark circle with the microphone, at body level (no transformed ancestor)', () => {
  const at = html.indexOf('id="ptt"');
  assert.ok(at > html.indexOf('</footer>') && at < html.indexOf('id="toast"'), 'moved out of the composer');
  for (const id of ['pttTime', 'pttHint', 'pttSeg', 'pttAct', 'pttEnd', 'pttSend', 'pttCancel', 'pttNote']) assert.ok(html.includes(`id="${id}"`), id);
  assert.match(html, /class="pttcopy" role="status" aria-live="assertive"/);
  assert.match(css, /\.ptt\{position:fixed;inset:0;/);
  assert.match(css, /\.pttc\{[^}]*aspect-ratio:1;border-radius:50%/);
  assert.match(css, /\.pttc\{[^}]*background:var\(--dlg-bg\)/, 'P218: P207\'s one dialog token (Noir by default)');
  assert.match(html, /class="ptt gdlg--circle" id="ptt"/, 'P218: marked as the global circle dialog shape');
  assert.match(css, /\.pttring\{[^}]*var\(--lv\)/, 'the ring follows the voice');
  assert.doesNotMatch(html, /id="pttDim"/, 'P346: the dim icon is gone');
  assert.match(css, /\.ptt\{[^}]*color-mix\(in srgb,var\(--pg\) \d+%,transparent\)[^}]*backdrop-filter:blur\(/, 'P346: every take blurs the page in its own colour');
  assert.match(css, /\.pttact\{[^}]*pointer-events:auto/);
  assert.doesNotMatch(css, /\.ptt\{position:absolute/);
  assert.match(src, /pttWatch\(p\); if \(!p\.over\) pttUI\(p\);/, 'P346: an interrupted take is not painted back as a stuck card');
});

test('dictionaries: icons, gaps (zh = src = override, en)', () => {
  const keys = ['end', 'cancelBtn', 'send', 'actions', 'gap', 'gapLive'];
  for (const k of ['dim', 'undim', 'dimNote']) assert.ok(!zh['r.ptt.' + k] && !en['r.ptt.' + k], 'P346: ' + k + ' removed');
  for (const k of keys) {
    assert.ok(zh['r.ptt.' + k] && en['r.ptt.' + k], k);
    assert.equal(zhSrc['r.ptt.' + k], zh['r.ptt.' + k]); assert.equal(ov['r.ptt.' + k], zh['r.ptt.' + k]);
  }
  assert.match(zh['r.ptt.gap'], /\{r\}/); assert.match(en['r.ptt.gap'], /\{r\}/);
  assert.match(zh['r.tray.gap'], /\{r\}/); assert.match(en['r.tray.gap'], /\{r\}/);
  assert.match(zh.r.ptt.awake, /电源键/); assert.doesNotMatch(zh.r.ptt.awake, /暗屏|月亮/);
  assert.match(en.r.ptt.awake, /power button/); assert.doesNotMatch(en.r.ptt.awake, /moon|dim/);
});
