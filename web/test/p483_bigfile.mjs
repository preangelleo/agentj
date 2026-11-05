// #483 (merged with #329) — a 32.4 MB video card that sits on 「正在取…」. Real isolated phone client + Noise fakehost; the
// file is generated at run time (never committed). The fakehost answers media_get exactly like serve.py's _media_window:
// ≤ 8 chunks of 45 056 bytes per get, paced 1/22 s, after a configurable per-get delay standing in for relay round trip.
//   A  pace: the whole file arrives; the status text is sampled the whole time (does the card say how far it is?).
//   B  stall: the host stops answering mid-file → the card must leave 「正在取…」 with a reason within STALL_MS + slack.
//   C  verify-throws: the phone's SHA-256 step rejects (iOS memory pressure stand-in) → the card must leave 「正在取…」 and a
//      second tap must start a fresh fetch (not hand back the same never-settling promise).
// Env: P483_OUT (default ~/.cache/p483/web), P483_RTT_MS (default 300), P483_BYTES (default 33 973 862 ≈ 32.4 MB),
//      P483_ONLY=A|B|C.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { createHash, randomFillSync } from 'node:crypto';
import { homedir } from 'node:os';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitState, evaluate, waitFor, sleep } from './browser.mjs';

const out = process.env.P483_OUT || `${homedir()}/.cache/p483/web`; mkdirSync(out, { recursive: true });
const RTT = Number(process.env.P483_RTT_MS || 300), SIZE = Number(process.env.P483_BYTES || 33973862), ONLY = process.env.P483_ONLY || '';
const CHUNK = 45056, WINDOW = 8, FPS = 22;
const bytes = Buffer.alloc(SIZE); randomFillSync(bytes); bytes.write('\0\0\0\x18ftypmp42', 0, 'latin1');
const item = { mid: 'V'.repeat(22), name: '东京一年_测试.mp4', ref: '~/story-animation/jobs/x/final/东京一年_测试.mp4', mime: 'video/mp4', kind: 'video', bytes: SIZE, sha256: createHash('sha256').update(bytes).digest('hex') };
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' }), fake = await startFakeHost(), B = await launch();
const results = {};
const STATUS = `(()=>{const c=document.querySelector('.mcard[data-mid="${item.mid}"]');const s=c&&c.querySelector('.mtap');return {state:c&&c.dataset.state,text:s?s.textContent:null}})()`;

async function scenario(name, { stallAfter = Infinity, breakDigest = false, budgetMs }) {
  fake.reset();
  let gets = 0, served = 0, live = true;
  fake.st.onApp = async (c, m, send) => {
    if (m.t !== 'media_get') return false;
    const n = ++gets;
    (async () => {
      await sleep(RTT);
      for (let i = 0; i < WINDOW; i++) {
        const off = m.o + i * CHUNK;
        if (off >= SIZE || !live || n !== gets) return;   // a newer get replaces this window (serve.py cancels it)
        if (served >= stallAfter) return;                  // B: the host goes quiet
        const d = bytes.subarray(off, Math.min(off + CHUNK, SIZE));
        await send({ t: 'media_chunk', mid: m.mid, o: off, d: d.toString('base64url'), last: off + d.length >= SIZE });
        served++;
        await sleep(1000 / FPS);
      }
    })();
    return true;
  };
  const p = await newPage(B, 390, 900, 'dark', { allow: [web.url, fake.relay + '/'] });
  try {
    await navigate(p, fake.newPairing(web.url + '?lang=zh')); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
    await evaluate(p, `document.getElementById('phone-env-dismiss')?.click();if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click()`);
    if (breakDigest) await evaluate(p, `(()=>{const d=crypto.subtle.digest.bind(crypto.subtle);crypto.subtle.digest=(a,b)=>b&&b.byteLength>8e6?Promise.reject(new DOMException('out of memory','OperationError')):d(a,b)})()`);
    const turn = await fake.addTurn({ k: "phone", text: "东京测试片" }, `做好了：\n\n\`${item.ref}\``, "done"); await fake.updateTurn(turn.id, { media: [item] });
    await waitFor(p, `!!document.querySelector('.mcard[data-mid="${item.mid}"] .mtap')`, 15000);
    const t0 = Date.now();
    await evaluate(p, `document.querySelector('.mcard[data-mid="${item.mid}"] .mtap').click()`);
    const samples = [];
    while (Date.now() - t0 < budgetMs) {
      const s = await evaluate(p, STATUS); samples.push({ ms: Date.now() - t0, ...s, served });
      if (s.state === 'ready' || s.state === 'error') break;
      await sleep(1000);
    }
    const last = samples.at(-1);
    let retry = null;
    if (breakDigest && last.state === 'loading') {        // C: does a second tap start over, or return the stuck promise?
      const g0 = gets;
      await evaluate(p, `document.querySelector('.mcard[data-mid="${item.mid}"] .mtap').disabled=false;document.querySelector('.mcard[data-mid="${item.mid}"] .mtap').click()`);
      await sleep(3000); retry = { newGets: gets - g0, ...(await evaluate(p, STATUS)) };
    }
    const texts = [...new Set(samples.filter((s) => s.state === 'loading').map((s) => s.text))];
    results[name] = { rttMs: RTT, bytes: SIZE, gets, served, totalChunks: Math.ceil(SIZE / CHUNK), finalState: last.state, finalText: last.text, elapsedMs: last.ms, loadingTexts: texts, retry, samples: samples.filter((_, i) => i % 5 === 0 || i === samples.length - 1) };
    console.log(name, JSON.stringify({ ...results[name], samples: undefined }));
  } finally { live = false; await p.dispose?.(); }
}

const run = (k) => !ONLY || ONLY === k;
const failures = [];
try {
  if (run('A')) {
    await scenario('A_pace', { budgetMs: 400000 });
    const r = results.A_pace;
    if (r.finalState !== 'ready') failures.push(`A: did not finish (${r.finalState})`);
    if (r.loadingTexts.length <= 1) failures.push(`A: no progress while loading for ${r.elapsedMs} ms — the only text was ${JSON.stringify(r.loadingTexts)}`);
  }
  if (run('B')) {
    await scenario('B_stall', { stallAfter: 200, budgetMs: 60000 });
    const r = results.B_stall;
    if (r.finalState !== 'error') failures.push(`B: still ${r.finalState} 60 s after the host went quiet`);
  }
  if (run('C')) {
    await scenario('C_verify_throws', { breakDigest: true, budgetMs: 400000 });
    const r = results.C_verify_throws;
    if (r.finalState !== 'error') failures.push(`C: still ${r.finalState} (${r.finalText}) ${r.elapsedMs} ms after the last chunk arrived; retry tap → ${JSON.stringify(r.retry)}`);
  }
} finally {
  writeFileSync(`${out}/p483.json`, JSON.stringify({ results, failures }, null, 2));
  await B.close(); await fake.stop(); await web.stop();
}
if (failures.length) { console.log('FAIL\n' + failures.join('\n')); process.exit(1); }
console.log('PASS');
