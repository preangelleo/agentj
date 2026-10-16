// P328 (Leo, 0.22.0a1 real phone): (A) the composer's 「× 取消」 shows while the Agent works (blue) for every harness — it
// used to follow only this phone's send (in flight / queued), so a shared Claude Code (say delivered to its TUI within
// milliseconds) lost it at once while a shared Codex (say queued until its app-server takes it) kept it; with nothing of
// ours to take back a tap stops the turn (§8 slash stop). It is centred on the field by layout (`bottom:6px` sat 4 px high
// on a 48 px fine-pointer field). (B) withdrawn (Leo 10-16 11:3x): every reply card — a command page's too — keeps ADR-035's fixed height, a fixed margin above
// the composer, empty space and all. Browser check: p328.mjs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (f) => readFileSync(new URL(f, import.meta.url), 'utf8');
const css = read('../public/app.css');
const relay = read('../public/js/relay.js');
const i18n = read('../public/i18n.js');
const D = Object.fromEntries(['web.zh.src', 'web.zh.override', 'web.zh', 'web.en'].map((n) => [n, JSON.parse(read(`../i18n/${n}.json`))]));
const KEYS = ['p328.stopAria', 'p328.stopSent', 'p328.stopNet'];

test('P328 A: one mode function — send / queued / turn (blue, connected, not stopped)', () => {
  const fn = relay.match(/function sayCancelMode\(\)\{[\s\S]*?\n\}/)?.[0];
  assert.ok(fn, 'sayCancelMode()');
  assert.match(fn, /cur\.status === "working" && document\.body\.dataset\.conn === "on"/, 'blue = status working while connected');
  assert.match(fn, /if \(inflight\) return [^;]*"send"/, 'a send in flight is taken back first');
  assert.match(fn, /if \(queued\.length\) return "queued"/, 'then a queued message');
  assert.match(fn, /if \(blue && !\(C && C\.estopOn\(\)\)\) return "turn"/, 'then the running turn');
  assert.doesNotMatch(fn, /agent|kind|harness|shared/i, 'never gated by harness');
  // every place that used to set it by hand goes through paintSayCancel
  assert.doesNotMatch(relay, /el\("sayCancel"\)\.hidden\s*=/, 'no direct hidden writes left');
  assert.match(relay, /function paintQueued\(\)\{\n  paintSayCancel\(\);/);
  assert.match(relay, /paintCompacting\(s\.compacting === true\);\n  paintSayCancel\(\);/, 'repainted on every status snapshot');
  assert.match(relay, /refreshSend\(\); paintSayCancel\(\);\n\}/, 'repainted on connection changes');
});

test('P328 A: a tap in turn mode is the real /stop (slash stop), at most once per 2.5 s; send / queued keep say_cancel', () => {
  const cancel = relay.match(/async function cancelSay\(\)\{[\s\S]*?\n\}/)[0];
  assert.match(cancel, /^async function cancelSay\(\)\{\n  if \(el\("sayCancel"\)\.dataset\.mode === "turn" && sayCancelMode\(\) === "turn"\) return stopTurn\(\);/);
  assert.match(cancel, /C\.api\.sayCancel\(S\.sid\)/);
  const stop = relay.match(/function stopTurn\(\)\{[\s\S]*?\n\}/)[0];
  assert.match(stop, /C\.api\.slash\("stop"\)/);
  assert.match(stop, /t\('p328\.stopNet'\)/);
  assert.match(stop, /t\('p328\.stopSent'\)/);
  assert.match(relay, /const STOP_AGAIN_MS = 2500;/);
  // the label for assistive tech follows the job (and survives a language switch: data-i18n-attr is rewritten)
  assert.match(relay, /const k = mode === "turn" \? "p328\.stopAria" : "r\.say\.cancelAria";\n    b\.setAttribute\("data-i18n-attr", `aria-label=\$\{k\},title=\$\{k\}`\);/);
});

test('P328 A: centred on the field by layout; the words and the clear-× keep out of its way', () => {
  const rule = css.match(/\n\.saycancel\{[^}]*\}/)[0];
  assert.match(rule, /top:0;bottom:0;margin:auto 0;height:44px/, 'auto margins between top 0 and bottom 0 = the row (= field) centre');
  assert.doesNotMatch(rule, /bottom:6px/, 'no offset that assumes a 56 px field');
  assert.match(css, /\.clr\[hidden\],body\[data-sending="1"\] \.clr,body\[data-saycancel="1"\] \.clr\{display:none\}/);
  assert.match(relay, /function padForCancel\(\)\{[\s\S]*?f\.style\.paddingRight = pad/);
  assert.match(relay, /new ResizeObserver\(padForCancel\)\.observe\(el\("sayCancel"\)\)/);
});

test('P328 B (withdrawn): every reply card, a command page\'s too, keeps the fixed card (ADR-035)', () => {
  assert.doesNotMatch(css, /\.om\[data-src=cmd\][^{]*\.rm/, 'no command-page override of the reply card');
  assert.match(css, /\n\.rm\{flex:1 1 0;min-height:min\(200px,55%\)/, 'ADR-035 fixed card unchanged');
});

test('P328: new strings in every dictionary, both languages, appended', () => {
  for (const n of Object.keys(D)) for (const k of KEYS) assert.equal(typeof D[n][k], 'string', `${k} in ${n}`);
  for (const k of KEYS) assert.equal(i18n.split(`"${k}":`).length - 1, 2, `${k} in both halves of i18n.js`);
  for (const k of KEYS) assert.notEqual(D['web.zh'][k], D['web.en'][k]);
  assert.doesNotMatch(D['web.en']['p328.stopAria'], /[一-鿿]/);
  assert.match(D['web.zh']['p328.stopAria'], /[一-鿿]/);
});
