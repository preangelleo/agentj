// P182 — long recordings: a locked take is cut into parts (here 3 s instead of 20 min, via the test-only
// localStorage["aj.test.segMs"]) that join the tray in order and upload while the recording goes on; every chunk is sealed into
// IndexedDB first, so a reload (= crash / closed tab) offers the parts back with their old upload ids; an interruption by the
// system keeps what was recorded; Screen Wake Lock is held during the take; /会议总结 · /meeting-notes in the `/` menu.
// Real Chromium (fake microphone) + Noise fake host. Not covered: iOS / Android screen-lock behaviour (see REPORT-182 §锁屏).
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch, newPage, navigate, waitFor, waitState, evaluate, shoot, rect, tap, sleep} from './browser.mjs';

const out = new URL('../../../reports/qa/p182/browser/', import.meta.url); mkdirSync(out, {recursive: true});
const web = await startWebServer(), fake = await startFakeHost(), B = await launch();
const results = {};
const T = {
  zh: {part: '录音第', seg: '第 3/10 段', back: '有没发出去的录音', backYes: '放回附件栏', interrupted: '录音被系统打断', cmd: '/会议总结', awake: '请别按电源键锁屏'},
  en: {part: 'Recording part', seg: 'Part 3/10', back: 'Unsent recordings', backYes: 'Put them back', interrupted: 'The system interrupted', cmd: '/meeting-notes', awake: "Please don't press the power button"},
};
const idbCount = (store) => `new Promise(res => { const r = indexedDB.open('agentj-rec', 1); r.onupgradeneeded = () => { r.result.createObjectStore('takes', {keyPath: 'id'}); r.result.createObjectStore('chunks', {keyPath: ['take','seg','i']}); };
  r.onsuccess = () => { const d = r.result, q = d.transaction(${JSON.stringify(store)}).objectStore(${JSON.stringify(store)}).count(); q.onsuccess = () => { d.close(); res(q.result); }; }; r.onerror = () => res(-1); })`;
const chips = `[...document.querySelectorAll('#tray .chip')].map(c => ({st: c.dataset.st, nm: c.querySelector('.nm').textContent}))`;
// a fake Screen Wake Lock: headless Chromium has none; the page must request it at lock and release it at the end
const WAKE = `(() => { window.__wl = {req: 0, rel: 0}; Object.defineProperty(navigator, 'wakeLock', {configurable: true, value: {request: async (k) => { window.__wl.req++;
  const s = {released: false, type: k, release: async () => { if (!s.released){ s.released = true; window.__wl.rel++; } }}; return s; }}});
  const g = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices); navigator.mediaDevices.getUserMedia = async (c) => (window.__stream = await g(c)); })()`;
async function micTap(p){ const r = await rect(p, '#mic'); await tap(p, r.x, r.y); }
async function lock(p){ const r = await rect(p, '#mic'); await tap(p, r.x, r.y); await sleep(80); await tap(p, r.x, r.y);
  await waitFor(p, `document.getElementById('mic').classList.contains('lock') && /^\\d+:\\d\\d$/.test(document.getElementById('pttTime').textContent)`, 5000); }
const fresh = (ids) => [...fake.st.blobs.entries()].filter(([id]) => !ids.has(id));
try {
  for (const language of ['zh', 'en']){
    const L = T[language];
    const p = await newPage(B, 390, 844, 'light', {touch: true, allow: [web.url, fake.relay + '/']});
    await navigate(p, fake.newPairing(web.url + '?lang=' + language)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
    await evaluate(p, `localStorage.setItem('aj.test.segMs', '3000')`);
    await evaluate(p, WAKE);

    // 1. a locked take cut into parts: each part joins the tray (in order) and uploads while recording goes on
    let seen = new Set(fake.st.blobs.keys());
    await lock(p);
    await waitFor(p, `document.querySelectorAll('#tray .chip').length >= 2`, 12000);
    assert.ok(await evaluate(p, `!document.getElementById('ptt').hidden && document.getElementById('mic').classList.contains('lock')`), 'still recording');
    const segLine = await evaluate(p, `document.getElementById('pttSeg').textContent`);
    const note = await evaluate(p, `document.getElementById('pttNote').textContent`);   // P205: the screen advice sits under the circle
    assert.ok(segLine.includes(L.seg) && note.includes(L.awake), segLine + ' / ' + note);
    assert.equal(await evaluate(p, 'window.__wl.req'), 1, 'Screen Wake Lock requested at lock');
    assert.ok(await evaluate(p, idbCount('chunks')) >= 2, 'chunks sealed into IndexedDB while recording');
    writeFileSync(new URL(`recording-${language}.png`, out), await shoot(p, ''));
    await micTap(p);                                                        // end
    await waitFor(p, `document.getElementById('ptt').hidden && document.querySelectorAll('#tray .chip[data-st="ready"]').length === 3`, 15000);
    assert.equal(await evaluate(p, 'window.__wl.rel'), 1, 'Screen Wake Lock released at the end');
    const c1 = await evaluate(p, chips);
    assert.deepEqual(c1.map(c => c.nm.startsWith(L.part + ' 1') || c.nm.startsWith(L.part + ' 2') || c.nm.startsWith(L.part + ' 3')), [true, true, true], JSON.stringify(c1));
    assert.ok(c1[0].nm.includes(' 1') && c1[1].nm.includes(' 2') && c1[2].nm.includes(' 3'), 'in recording order');
    const ups = fresh(seen).map(([, b]) => b);
    assert.equal(ups.length, 3);
    ups.forEach((b, i) => { assert.equal(b.origin, 'recording'); assert.match(b.mime, /^audio\/(webm|ogg|mp4)$/); assert.ok(i < 2 ? b.secs > 2 && b.secs < 5 : b.secs > 0, String(b.secs)); assert.ok(b.bytes.length > 200); });
    assert.deepEqual(ups.map(b => b.name.match(/-(\d)\.\w+$/)[1]), ['1', '2', '3']);
    assert.equal(ups[0].bytes.subarray(0, 4).toString('hex'), '1a45dfa3', 'each part is a complete WebM file');
    assert.equal(ups[1].bytes.subarray(0, 4).toString('hex'), '1a45dfa3');
    results['segments-in-order-' + language] = 'PASS';
    const bids = fresh(seen).map(([id]) => id);

    // 2. a reload before Send (= a crash / closed tab): the parts are offered back, with their upload ids (the host resumes)
    await navigate(p, web.url + '?lang=' + language); await waitState(p, 'ready');
    await waitFor(p, `!document.getElementById('confirm').hidden`, 8000);
    assert.equal(await evaluate(p, `document.getElementById('confirm-title').textContent`), L.back);
    assert.match(await evaluate(p, `document.getElementById('confirm-text').textContent`), /3/);
    writeFileSync(new URL(`recover-${language}.png`, out), await shoot(p, ''));
    assert.equal(await evaluate(p, `document.getElementById('confirm-yes').textContent`), L.backYes);
    await evaluate(p, `document.getElementById('confirm-yes').click()`);
    await waitFor(p, `document.querySelectorAll('#tray .chip[data-st="ready"]').length === 3`, 15000);
    const opens = fake.st.opens.filter(Boolean).slice(-3).map(o => o.bid);
    assert.deepEqual(opens, bids, 'the same upload ids, same order');
    results['reload-recovers-parts-' + language] = 'PASS';

    // 3. /会议总结 with the parts: one message, three files in order; afterwards nothing is left on the phone
    await evaluate(p, `document.getElementById('input').focus()`);
    await p.send('Input.insertText', {text: '/'});
    await waitFor(p, `!document.getElementById('sug').hidden && [...document.querySelectorAll('#sug b')].some(b => b.textContent === ${JSON.stringify(L.cmd)})`, 5000);
    writeFileSync(new URL(`slash-${language}.png`, out), await shoot(p, ''));
    await evaluate(p, `(() => { const i = document.getElementById('input'); i.value = ''; i.dispatchEvent(new Event('input')); })()`);
    await p.send('Input.insertText', {text: L.cmd + ' 发给 li@corp.cn'});
    const before = fake.st.says.length;
    const s = await rect(p, '#send'); await tap(p, s.x, s.y);
    await waitFor(p, `document.querySelectorAll('#tray .chip').length === 0`, 10000);
    assert.equal(fake.st.says.length, before + 1);
    const say = fake.st.says.at(-1);
    assert.equal(say.text, L.cmd + ' 发给 li@corp.cn'); assert.deepEqual(say.att, bids);
    await sleep(400);
    assert.equal(await evaluate(p, idbCount('chunks')), 0, 'sent → nothing kept on the phone');
    assert.equal(await evaluate(p, idbCount('takes')), 0);
    results['meeting-notes-sends-parts-' + language] = 'PASS';

    // 4. a reload in the middle of a take: the part being recorded is kept too; 「丢弃」 deletes everything
    await evaluate(p, WAKE);
    await lock(p);
    await sleep(4500);
    await navigate(p, web.url + '?lang=' + language); await waitState(p, 'ready');
    await waitFor(p, `!document.getElementById('confirm').hidden`, 8000);
    await evaluate(p, `document.getElementById('confirm-no').click()`);
    await sleep(500);
    assert.equal(await evaluate(p, idbCount('chunks')), 0, 'discarded');
    assert.equal(await evaluate(p, `document.querySelectorAll('#tray .chip').length`), 0);
    results['mid-take-reload-and-discard-' + language] = 'PASS';

    // 5. the system takes the microphone away (screen lock / a call): the take ends, what was recorded goes to the tray
    await evaluate(p, `localStorage.setItem('aj.test.segMs', '3000')`);
    await evaluate(p, WAKE);
    await lock(p);
    await sleep(1800);
    await evaluate(p, `window.__stream.getAudioTracks()[0].dispatchEvent(new Event('ended'))`);
    await waitFor(p, `document.getElementById('ptt').hidden && document.getElementById('toast').textContent.includes(${JSON.stringify(L.interrupted)})`, 5000);
    await waitFor(p, `document.querySelectorAll('#tray .chip').length === 1 || document.getElementById('input').value.length > 0`, 10000);
    assert.equal(await evaluate(p, 'window.__wl.rel'), 1);
    results['interruption-keeps-recording-' + language] = 'PASS';
    await evaluate(p, `document.querySelectorAll('#tray .chip .x').forEach(x => x.click())`);
    await evaluate(p, `(() => { const i = document.getElementById('input'); i.value = ''; i.dispatchEvent(new Event('input')); })()`);
    assert.deepEqual(p.problems, []); assert.deepEqual(p.offsite, []);
  }
} finally { await B.close(); await fake.stop(); await web.stop(); }
writeFileSync(new URL('results.json', out), JSON.stringify({results, scope: 'Real Chromium (fake microphone, 3-second parts) + Noise fake host; phone screen-lock behaviour is in REPORT-182'}, null, 2) + '\n');
console.log(JSON.stringify(results));
if (process.env.AJ_PARITY_OUT) writeFileSync(process.env.AJ_PARITY_OUT, JSON.stringify({suite: 'web/test/p182.mjs', results: Object.fromEntries(Object.entries(results).map(([k, v]) => [k, v.toLowerCase()]))}));
