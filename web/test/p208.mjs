#!/usr/bin/env node
// P208 · 播报: a tap reads with the phone's own voices (free, unchanged); a long press sends the 「换播报声音」 preset
// (agentj voice status/engines) as the owner and never opens a settings page; with voice.tts.spoken = on the phone's own voice reads the
// computer's spoken script (tts_script, §10.14a), and the reply itself when the computer gives none.
// Real page + local fake host, own headless Chromium (never :9222). Shots (zh/en × 390/1440) → reports/qa/p208/screens/
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, readdirSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, rect, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = ROOT + 'reports/qa/p208/screens/';
const skillRoot = ROOT + 'host/agentj/skills/';
const skillNames = readdirSync(skillRoot).filter(name=>existsSync(skillRoot+name+'/SKILL.md'));
assert.equal(skillNames.length,37,'the approved skill inventory stays at 37');
assert.ok(skillNames.includes('agentj-manual')&&skillNames.includes('agentj-config'));
assert.ok(!skillNames.includes('agentj-voice'),'voice commands do not invent an extra skill');
mkdirSync(OUT, { recursive: true });
const rec = parityRecorder('web/test/p208.mjs');
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
let failed = 0;
const ev = (p, s) => evaluate(p, s);
const $ = (id) => `document.getElementById(${JSON.stringify(id)})`;
const REPLY = '## 结论\n\n| 项 | 值 |\n|---|---|\n| 速度 | 快 |\n\n播报测试的回复，**请读我**。';

async function paired({ w = 390, h = 844, lang = 'zh', prefs = null } = {}) {
  fake.reset();
  const p = await newPage(B, w, h, 'light', { allow: [web.url, fake.relay + '/'], touch: w < 500 });
  await navigate(p, fake.newPairing(web.url + '?lang=' + lang));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await ev(p, `(()=>{${$('phone-env-dismiss')}?.click(); const n=${$('confirm-no')}; if(n&&!${$('confirm')}.hidden)n.click(); const a=${$('a2hs-ok')}; if(a&&!${$('a2hs')}.hidden)a.click();})()`);
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  if (prefs) await fake.send({ t: 'preferences', value: prefs, problem: null });
  await fake.addTurn({ k: 'owner', text: '读一下' }, REPLY, 'done');
  await waitFor(p, `!${$('speakBtn')}.disabled`);
  // the phone's own voices, recorded instead of spoken
  await ev(p, `(() => { const v = { lang: 'zh-CN', localService: true, default: true, name: 'local' };
    window.__spoken = []; const ss = window.speechSynthesis;
    ss.getVoices = () => [v, { lang: 'en-US', localService: true, default: false, name: 'local-en' }];
    ss.speak = (u) => { window.__spoken.push(u.text); setTimeout(() => { u.onstart && u.onstart(); setTimeout(() => u.onend && u.onend(), 20); }, 10); };
    ss.pause = () => {}; ss.resume = () => {}; ss.cancel = () => {}; })()`);
  return p;
}
async function C(name, fn) {
  let ok = true;
  try { await fn(); } catch (e) { ok = false; failed++; console.log(`  ✗ ${name}: ${e && e.stack || e}`); }
  if (ok) console.log(`  ✓ ${name}`);
  rec.record(name, ok);
}
async function hold(p, sel, ms = 750, touch = true) {
  const r = await rect(p, sel);
  if (touch) {
    await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: r.x, y: r.y }] });
    await sleep(ms);
    await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  } else {
    await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: r.x, y: r.y });
    await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
    await sleep(ms);
    await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 });
  }
  await sleep(200);
}
const says = () => fake.st.says.map((m) => m.text || '');
const scriptReqs = () => (fake.st.scriptReqs || []).length;
const noProblems = (p) => { assert.equal(p.problems.length, 0, p.problems.join('\n')); assert.equal(p.offsite.length, 0, p.offsite.join('\n')); };

// ---------------------------------------------------------------- a) tap = the phone's own voice, nothing sent anywhere
await C('speak-tap-phone-voice', async () => {
  fake.answer('tts_script', (m) => { (fake.st.scriptReqs = fake.st.scriptReqs || []).push(m); return { t: 'tts_script_res', ok: true, text: 'X', spoken: true }; });
  const p = await paired();
  const n0 = says().length;
  await ev(p, `${$('speakBtn')}.click()`);
  await waitFor(p, 'window.__spoken.length > 0');
  const text = await ev(p, 'window.__spoken.join("")');
  assert.match(text, /请读我/); assert.doesNotMatch(text, /\*\*|\|---|##/, 'Markdown is reduced to words');
  assert.equal(scriptReqs(), 0, 'the free path asks the computer nothing (spoken auto + phone voices = off)');
  assert.equal(says().length, n0, 'a tap sends no message');
  noProblems(p);
  await p.dispose();
});

// ---------------------------------------------------------------- b) hold = the approved agentj voice commands, no settings page
await C('speak-hold-voice-preset', async () => {
  for (const lang of ['zh', 'en']) for (const [w, h] of [[390, 844], [1440, 900]]) {
    const p = await paired({ w, h, lang });
    const title = await ev(p, `${$('speakBtn')}.title`);
    assert.match(title, lang === 'zh' ? /长按：换播报的声音/ : /hold: change the voice/);
    const n0 = says().length;
    await hold(p, '#speakBtn', 750, w < 500);
    await sleep(300);
    assert.equal(says().length, n0 + 1, 'one preset message');
    const t1 = says().at(-1);
    assert.doesNotMatch(t1, /agentj-voice/); assert.match(t1, /agentj voice status/); assert.match(t1, /agentj voice engines/);
    assert.match(t1, lang === 'zh' ? /密钥卡/ : /secret card/);
    assert.equal(await ev(p, 'window.__spoken.length'), 0, 'a hold never also reads');
    assert.equal(await ev(p, `!!document.querySelector('.menu-page:not([hidden]), #settings:not([hidden])')`), false, 'no settings page opens');
    writeFileSync(`${OUT}${lang}-${w}-hold-sent.png`, await shoot(p, ''));
    noProblems(p);
    await p.dispose();
  }
});

// ---------------------------------------------------------------- c) spoken on + phone voices → the computer's script
await C('speak-spoken-script', async () => {
  fake.st.scriptReqs = [];
  fake.answer('tts_script', (m) => { fake.st.scriptReqs.push(m); return { t: 'tts_script_res', ok: true, text: '结论是速度很快。', spoken: true }; });
  let p = await paired({ prefs: { voice: { tts: { mode: 'phone', spoken: 'on' } } } });
  await ev(p, `${$('speakBtn')}.click()`);
  await waitFor(p, 'window.__spoken.length > 0', 8000);
  assert.equal(await ev(p, 'window.__spoken.join("")'), '结论是速度很快。');
  assert.equal(fake.st.scriptReqs.length, 1);
  assert.equal(typeof fake.st.scriptReqs[0].id, 'number');
  noProblems(p);
  await p.dispose();
  // the computer gives no script (an older host / a failure) → the reply itself, never silence
  fake.answer('tts_script', (m) => { fake.st.scriptReqs.push(m); return { t: 'tts_script_res', ok: false, why: 'engine' }; });
  p = await paired({ prefs: { voice: { tts: { mode: 'phone', spoken: 'on' } } } });
  await ev(p, `${$('speakBtn')}.click()`);
  await waitFor(p, 'window.__spoken.length > 0', 8000);
  assert.match(await ev(p, 'window.__spoken.join("")'), /请读我/);
  noProblems(p);
  await p.dispose();
});

rec.write();
await B.close(); await fake.stop(); await web.stop();
if (failed) { console.log(`p208: ${failed} failed`); process.exit(1); }
console.log('p208: all passed');
