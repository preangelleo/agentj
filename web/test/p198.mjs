// P198 (K) — a recording the system interrupts: the notice says where the audio really went (the message field while it is
// being transcribed, the tray, or nowhere when not a sound was captured), and the time the page spent hidden with the
// microphone gone (an iPhone screen lock) is not counted into the take's length. P496: every hidden page stops capture immediately, including locked takes on desktop and Android.
// Real Chromium (fake microphone) + Noise fake host. "Hidden" is document.visibilityState overridden + visibilitychange;
// "iOS took the microphone" = a muted track, or the recorder paused and the track stopped while hidden (no audio arrives).
// Not covered: a real iPhone (REVIEW §2). Shots → /tmp/aj-p198/rec/. Usage: node web/test/p198.mjs
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch, newPage, navigate, waitFor, waitState, evaluate, shoot, rect, tap, sleep} from './browser.mjs';

const out = '/tmp/aj-p198/rec/'; mkdirSync(out, {recursive: true});
const web = await startWebServer(), fake = await startFakeHost(), B = await launch();
const results = {};
const T = {
  zh: {text: '录音被系统打断（锁屏或切到别的 App），已录的', toText: '正在转成文字，会放进输入框', tray: '已放进附件栏', none: '这次没有录到声音'},
  en: {text: 'The system interrupted the recording', toText: 'is being turned into text for the message field', tray: 'is in the tray', none: 'no sound was recorded'},
};
// the stream and every recorder the page makes; a switchable page visibility
const HOOKS = `(() => {
  const g = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices); navigator.mediaDevices.getUserMedia = async (c) => (window.__stream = await g(c));
  window.__recs = []; const st = MediaRecorder.prototype.start; MediaRecorder.prototype.start = function (...a) { window.__recs.push(this); return st.apply(this, a); };
  window.__vis = 'visible';
  Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => window.__vis});
  Object.defineProperty(document, 'hidden', {configurable: true, get: () => window.__vis !== 'visible'});
})()`;
const setVis = (v) => `(() => { window.__vis = ${JSON.stringify(v)}; document.dispatchEvent(new Event('visibilitychange')); })()`;
// a recorder that never produces a byte (iOS taking the microphone before the first chunk; REVIEW a6)
const DEAD_REC = `(() => { window.__realRec = window.__realRec || window.MediaRecorder;
  window.MediaRecorder = class extends EventTarget { constructor(s){ super(); this.stream = s; this.state = 'inactive'; this.mimeType = 'audio/webm'; }
    static isTypeSupported(){ return true; } start(){ this.state = 'recording'; } requestData(){} pause(){ this.state = 'paused'; }
    stop(){ this.state = 'inactive'; setTimeout(() => this.onstop && this.onstop(), 0); } }; })()`;
const REAL_REC = `(() => { if (window.__realRec) window.MediaRecorder = window.__realRec; })()`;
const toastText = `document.getElementById('toast').textContent`;
const secsOf = (s) => { const m = /(\d+):(\d\d)/.exec(s); return m ? +m[1] * 60 + +m[2] : -1; };
async function lock(p){ const r = await rect(p, '#mic'); await tap(p, r.x, r.y); await sleep(80); await tap(p, r.x, r.y);
  await waitFor(p, `document.getElementById('mic').classList.contains('lock') && /^\\d+:\\d\\d$/.test(document.getElementById('pttTime').textContent)`, 5000)
    .catch(async (e) => { console.log('lock?', await evaluate(p, `JSON.stringify({mic: document.getElementById('mic').className, t: document.getElementById('pttTime').textContent, ptt: document.getElementById('ptt').hidden, toast: document.getElementById('toast').textContent, label: document.getElementById('micLabel').textContent})`)); throw e; }); }
async function reset(p){
  await evaluate(p, `document.querySelectorAll('#tray .chip .x').forEach(x => x.click())`);
  await evaluate(p, `(() => { const i = document.getElementById('input'); i.value = ''; i.dispatchEvent(new Event('input')); })()`);
  await evaluate(p, `(() => { const n = document.getElementById('toast'); clearTimeout(n._t); n.classList.remove('on', 'act'); n.textContent = ''; })()`);
  await sleep(600);
}
/** wait for the take to end with a notice containing `want`; → the notice */
async function ended(p, want){
  await waitFor(p, `document.getElementById('ptt').hidden && document.getElementById('toast').classList.contains('on') && ${toastText}.includes(${JSON.stringify(want)})`, 8000);
  return evaluate(p, toastText);
}
try {
  for (const language of ['zh', 'en']){
    const L = T[language];
    const p = await newPage(B, 390, 844, 'light', {touch: true, allow: [web.url, fake.relay + '/']});
    await navigate(p, fake.newPairing(web.url + '?lang=' + language)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
    await evaluate(p, HOOKS);

    // 1. iPhone screen lock, the track muted: 2 s spoken, 6 s locked → the notice says 0:02 and "into the field"
    await lock(p);
    await sleep(2000);
    await evaluate(p, setVis('hidden'));
    await evaluate(p, `window.__stream.getAudioTracks()[0].dispatchEvent(new Event('mute'))`);
    await sleep(6000);
    await evaluate(p, setVis('visible'));
    let n = await ended(p, L.text);
    assert.ok(n.includes(L.toText), n);
    assert.ok(secsOf(n) >= 1 && secsOf(n) <= 3, `locked time not counted: ${n}`);
    await waitFor(p, `document.getElementById('input').value.includes('这是转写出来的文字')`, 10000);
    assert.equal(await evaluate(p, `document.querySelectorAll('#tray .chip').length`), 0, 'not in the tray');
    writeFileSync(out + `muted-${language}.png`, await shoot(p, ''));
    results['mute-locked-time-not-counted-' + language] = 'PASS';
    await reset(p);

    // 2. iOS freezes the page and ends the track while it is hidden (no audio arrives): found on return → 0:02, into the field
    await lock(p);
    await sleep(2000);
    await evaluate(p, setVis('hidden'));
    await evaluate(p, `(() => { const r = window.__recs.at(-1); try { r.pause(); } catch {} window.__stream.getAudioTracks()[0].stop(); })()`);
    await sleep(6000);
    await evaluate(p, setVis('visible'));
    n = await ended(p, L.text);
    assert.ok(n.includes(L.toText), n);
    assert.ok(secsOf(n) >= 1 && secsOf(n) <= 3, `frozen time not counted: ${n}`);
    await waitFor(p, `document.getElementById('input').value.length > 0`, 10000);
    results['frozen-locked-time-not-counted-' + language] = 'PASS';
    await reset(p);

    // 3. P496: even if the browser could keep recording, hidden capture ends immediately.
    await lock(p);
    await sleep(1500);
    await evaluate(p, setVis('hidden'));
    assert.equal(await evaluate(p, `window.__stream.getAudioTracks()[0].readyState`), 'ended');
    assert.ok(await evaluate(p, `window.__recs.every(r => r.state === 'inactive')`));
    n = await ended(p, L.text);
    assert.ok(n.includes(L.toText) && secsOf(n) <= 3, `background time excluded: ${n}`);
    await evaluate(p, setVis('visible'));
    assert.ok(await evaluate(p, `document.getElementById('ptt').hidden`), 'return does not restart capture');
    await waitFor(p, `document.getElementById('input').value.length > 0`, 10000);
    results['background-capture-stopped-' + language] = 'PASS';
    await reset(p);

    // 4. a long take in parts: part 1 is in the tray, the interruption puts the rest there too → the notice says the tray
    await evaluate(p, `localStorage.setItem('aj.test.segMs', '3000')`);
    await lock(p);
    await waitFor(p, `document.querySelectorAll('#tray .chip').length >= 1`, 8000);
    await sleep(800);
    await evaluate(p, `window.__stream.getAudioTracks()[0].dispatchEvent(new Event('ended'))`);
    n = await ended(p, L.text);
    assert.ok(n.includes(L.tray), n);
    await waitFor(p, `document.querySelectorAll('#tray .chip').length === 2`, 8000);
    writeFileSync(out + `tray-${language}.png`, await shoot(p, ''));
    await evaluate(p, `localStorage.removeItem('aj.test.segMs')`);
    results['parts-notice-says-tray-' + language] = 'PASS';
    await reset(p);

    // 5. not a byte captured: the notice says so — nothing in the tray, nothing in the field (it used to say "in the tray")
    await evaluate(p, DEAD_REC);
    await lock(p);
    await sleep(1500);
    await evaluate(p, `window.__stream.getAudioTracks()[0].dispatchEvent(new Event('ended'))`);
    n = await ended(p, L.none);
    assert.ok(!n.includes(L.tray), n);
    await sleep(600);
    assert.equal(await evaluate(p, `document.querySelectorAll('#tray .chip').length`), 0);
    assert.equal(await evaluate(p, `document.getElementById('input').value`), '');
    writeFileSync(out + `none-${language}.png`, await shoot(p, ''));
    await evaluate(p, REAL_REC);
    results['nothing-captured-says-so-' + language] = 'PASS';

    assert.deepEqual(p.problems, []); assert.deepEqual(p.offsite, []);
  }
} finally { await B.close(); await fake.stop(); await web.stop(); }
console.log(JSON.stringify(results));
if (Object.keys(results).length !== 10) { console.log('FAILED'); process.exit(1); }
console.log('ALL PASS');
