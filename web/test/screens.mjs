#!/usr/bin/env node
import { buttonAuditSource } from '../../brand/test/button-audit.mjs';
// Web client: screenshots + layout audit + full client flow + relay-parity cases against a local fake relay/host, in an
// INDEPENDENT headless Chromium (test/browser.mjs: own profile, random debugging port — never the shared CDP on :9222;
// Chromium's fake camera + microphone). public/ is served by test/serve.mjs (Worker headers, connect-src ws://127.0.0.1:*).
//   0. security regressions of the PROMPT-33 review: P33-X01 (session reset vs queued sends), P33-X02 (unpair, then another computer)
//   1. idle + security panel, every size × scheme (zh)
//   2. full flow (pair → chat → host restart → reload → bad frame → revoke → re-pair → expired link) + an older host
//      without p33 (chat as §8 msg becomes in-memory pages)
//   3. gallery: every screen at 360×800 and 1440×900, light + dark, 中文 + English, audited on every shot: no horizontal
//      overflow, every visible tap target ≥ 44×44 px (relay's 22 px chip × counts its 42 px ::after hit area), no console
//      errors / CSP violations, no request leaving the page's own origin (+ the local relay)
//   4. language: the switch sets <html lang>, survives a reload (localStorage "aj.lang"), ?lang=en wins and is saved
//   5. legacy host (alpha-web.agentjarvis.net through CDP Fetch interception — nothing goes to the network)
//   6. relay parity: one named case per features.json id (test/parity_cases.mjs)
// Results: parity format {"suite":"web/test/screens.mjs","results":{"<id>":…}} at AJ_PARITY_OUT, else reports/qa/parity/web-web_test_screens.mjs.json
// Run: node web/test/screens.mjs      Shots: /tmp/aj-web-shots/*.png (AJ_SHOTS)   AJ_QUICK=1: two gallery
// combinations only · AJ_PARITY_ONLY=id,id: only those parity cases (and no gallery) · AJ_NO_PARITY=1: sections 0–5 only ·
// AJ_SECURITY_ONLY=1: section 0 only
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot as shot0, sleep } from './browser.mjs';
import { runCases, makeFiles } from './parity_cases.mjs';
import { dictionaries } from '../build.mjs';
import { spacingAuditSource, runP119Screens } from './p119.mjs';
import { runP102Screens } from './p102.mjs';
import { parityRecorder } from '../../parity/lib.mjs';

const SHOTS = process.env.AJ_SHOTS || '/tmp/aj-web-shots';
const SIZES = [['mobile', 360, 800], ['phone390',390,844], ['desktop', 1440, 900]];
const SCHEMES = ['light', 'dark'];
const D = dictionaries();
const ZH = D.zh, EN = D.en;
const plain = (s) => s.replace(/`/g, '');
const PAIR_FAIL = plain(ZH['st.pairFail']);
const UA = {
  iphone: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1',
  android: 'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36',
};
const REPO = fileURLToPath(new URL('../../../', import.meta.url));
const ONLY = process.env.AJ_PARITY_ONLY ? process.env.AJ_PARITY_ONLY.split(',') : null;
const SEC_ONLY = process.env.AJ_SECURITY_ONLY === '1';      // section 0 only (nothing written)

mkdirSync(SHOTS, { recursive: true });
const failures = [];
const results = {};
const check = (ok, msg) => { if (!ok) failures.push(msg); console.log(`${ok ? 'PASS' : 'FAIL'}  ${msg}`); return ok; };

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const BASE = web.url;
const fake = await startFakeHost();
const RELAY = fake.relay;
const ALLOW = [BASE, RELAY + '/'];
const B = await launch();

const text = (p, sel) => evaluate(p, `document.querySelector(${JSON.stringify(sel)}).textContent`);
async function shoot(p, name, opts) { const f = join(SHOTS, `${name}.png`); writeFileSync(f, await shot0(p, f, opts)); console.log(`      shot ${f}`); }
async function layoutOk(p, label) {
  const r = await evaluate(p, `(() => {
    const vis = (e) => { if (!e.getClientRects().length) return false; const cs = getComputedStyle(e); return cs.visibility !== 'hidden' && cs.display !== 'none' && cs.opacity !== '0'; };
    const hit = (e) => { const a = getComputedStyle(e, '::after'); return a.content !== 'none' && a.position === 'absolute' ? [parseFloat(a.width) || 0, parseFloat(a.height) || 0] : [0, 0]; };
    const small = [...document.querySelectorAll('button, a[href], summary, input:not([type=file]), select, textarea')].filter(vis)
      .filter((e) => !e.closest('[hidden]'))
      .map((e) => ({ e, r: e.getBoundingClientRect(), h: hit(e) })).filter(({ r, h }) => (r.height < 44 && h[1] < 42) || (r.width < 44 && h[0] < 42))
      .map(({ e, r }) => (e.id || e.className || e.tagName) + ' ' + Math.round(r.width) + 'x' + Math.round(r.height));
  small.push(...${buttonAuditSource});
    const wide = [...document.querySelectorAll('body *')].filter(vis).filter((e) => { const r = e.getBoundingClientRect(); return r.right > innerWidth + 1 && getComputedStyle(e).position !== 'fixed'; })
      .filter((e) => !e.closest('.tablewrap, .codeblock, pre, .hash, .tray, .deck, .rd, .water')).slice(0, 5).map((e) => e.id || e.className || e.tagName);
    return { sw: document.documentElement.scrollWidth, iw: innerWidth, small, wide, waves:[...document.querySelectorAll(".water .wave")].filter(vis).map(e=>({width:e.getBoundingClientRect().width,parent:e.parentElement.getBoundingClientRect().width})) };
  })()`);
  const spacingErrors = await evaluate(p, spacingAuditSource);
  check(!spacingErrors.length, label + ': spacing ≥ shared token ' + JSON.stringify(spacingErrors));
  check(r.sw <= r.iw && r.wide.length === 0, `${label}: no horizontal overflow (scrollWidth ${r.sw} <= ${r.iw}${r.wide.length ? '; sticks out: ' + r.wide.join(', ') : ''})`);
  check(r.waves.every(w=>Math.abs(w.width-2*w.parent)<1), `${label}: decorative waves retain exactly two parent widths`);
  check(r.small.length === 0, `${label}: every visible tap target ≥ 44×44 px${r.small.length ? ' — ' + r.small.join(', ') : ''}`);
}
const noProblems = (p, label) => {
  check(p.problems.length === 0, `${label}: zero console errors / CSP violations${p.problems.length ? ' — ' + p.problems.join(' | ') : ''}`);
  check(p.offsite.length === 0, `${label}: no request leaves the page's origin${p.offsite.length ? ' — ' + p.offsite.join(' | ') : ''}`);
};
async function typeAndEnter(p, s) {
  await evaluate(p, `document.getElementById('input').focus()`);
  await p.send('Input.insertText', { text: s });
  await p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, text: '\r' });
  await p.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 });
}
const reply = (p) => text(p, '#words');
async function waitReply(p, s, ms = 6000) { try { await waitFor(p, `document.getElementById('words').textContent === ${JSON.stringify(s)}`, ms); return true; } catch { return false; } }

try {
  if (!ONLY && !SEC_ONLY) { const n=failures.length; await runP119Screens(B, fake, BASE, check, join(SHOTS, 'p119')); results['agentj-spacing-escape']=failures.length===n?'pass':'fail'; }
  // ---------- 0. security regressions (PROMPT-33 review): P33-X01 (a reset never lets an old send meet the new session's
  // keys; hello first; unsent words stay in the composer) and P33-X02 (unpair → nothing of computer A reaches computer B)
  if (!ONLY) {
    const until = async (fn, ms = 8000) => { for (let t = 0; t < ms; t += 50) { if (await fn()) return true; await sleep(50); } return false; };
    {
      fake.reset();
      const p = await newPage(B, 390, 844, 'light', { allow: ALLOW });
      await navigate(p, fake.newPairing(BASE));
      await waitState(p, 'awaiting-approval');
      await fake.approve();
      await waitState(p, 'ready');
      await sleep(300);
      // fill the socket's pacing budget (≤ 22 frames / s) so the next sends wait in the pacer …
      await evaluate(p, `import('./js/session.js').then((s) => { for (let i = 0; i < 60; i++) s.sendApp({ t: 'vis', fg: true }).catch(() => {}); return true; })`);
      const secret = '只给这一次会话的话';
      await typeAndEnter(p, secret);                       // … and the say waits behind them
      await sleep(100);
      fake.setUp(false);                                   // the relay says the host went away, then lets a fresh RESUME through
      fake.setUp(true);
      await waitState(p, 'ready');
      await sleep(4500);                                   // every stale send has woken from the pacer by now
      check(fake.st.errors.length === 0, `X01: no frame the host could not decrypt after the reset${fake.st.errors.length ? ' — ' + fake.st.errors.join(' | ') : ''}`);
      check(fake.st.firsts.length >= 2 && fake.st.firsts.every((t) => t === 'hello'), `X01: hello is the first app message of every handshake (${fake.st.firsts.join(',')})`);
      check((await evaluate(p, 'window.__ajState')) === 'ready', 'X01: the session survived the reset');
      check(!fake.st.says.some((m) => m.text === secret), 'X01: the send queued before the reset never reached the host on its own');
      check((await evaluate(p, `document.getElementById('input').value`)) === secret, 'X01: its words are back in the composer');
      await evaluate(p, `document.getElementById('send').click()`);
      check(await until(() => fake.st.says.some((m) => m.text === secret)), 'X01: one tap on Send delivers it on the new session');
      noProblems(p, 'X01');
      await p.dispose();
    }
    {
      fake.reset(); fake.st.sayMode = 'queued'; fake.st.stallAfter = 2;
      const hostB = await startFakeHost();
      const files = makeFiles();
      const p = await newPage(B, 390, 844, 'light', { allow: [...ALLOW, hostB.relay + '/'] });
      await p.send('DOM.enable');
      await navigate(p, fake.newPairing(BASE));
      await waitState(p, 'awaiting-approval');
      await fake.approve();
      await waitState(p, 'ready');
      await typeAndEnter(p, 'A 的排队消息');                 // accepted, queued behind a running turn
      await until(() => fake.st.says.length === 1);
      const { root } = await p.send('DOM.getDocument', { depth: -1 });
      const { nodeId } = await p.send('DOM.querySelector', { nodeId: root.nodeId, selector: '#fDoc' });
      await p.send('DOM.setFileInputFiles', { nodeId, files: [files.big] });
      check(await until(() => fake.st.chunks >= 2), 'X02: an attachment to A is mid-upload (frozen by the host)');
      await evaluate(p, `document.getElementById('input').focus()`);
      await p.send('Input.insertText', { text: 'A 的草稿' });
      await sleep(900);                                    // the draft is sealed
      await evaluate(p, `document.getElementById('aj-menu').open = true; document.getElementById('unpair').click()`);
      await waitFor(p, `!document.getElementById('confirm').hidden`);
      await evaluate(p, `document.getElementById('confirm-yes').click()`);
      await waitState(p, 'idle');
      const left = await evaluate(p, `new Promise((res) => { const r = indexedDB.open('agentjarvis'); r.onsuccess = () => { const tx = r.result.transaction('kv'); const s = tx.objectStore('kv');
        const a = s.get('draft'), b = s.get('ihist'), c = s.get('local'), d = s.get('host'); tx.oncomplete = () => { r.result.close(); res({ draft: !!a.result, ih: !!b.result, key: !!c.result, host: !!d.result }); }; }; })`);
      check(!left.draft && !left.ih && !left.key && !left.host, `X02: unpair deletes the draft, the input history, their key and the host (${JSON.stringify(left)})`);
      const chunksA = fake.st.chunks;
      await p.send('Page.navigate', { url: hostB.newPairing(BASE) });   // same document: the hashchange pairing path
      await waitState(p, 'awaiting-approval');
      await hostB.approve();
      await waitState(p, 'ready');
      await sleep(2500);                                   // time for any retry / resume / queued send to fire
      const bad = hostB.log.filter((m) => ['say', 'msg', 'say_cancel', 'blob_open', 'blob_chunk', 'blob_end'].includes(m.t)).map((m) => m.t);
      check(bad.length === 0 && hostB.st.chunks === 0, `X02: computer B received nothing from A's session (${bad.join(',') || 'none'})`);
      check(fake.st.chunks === chunksA, 'X02: and A received no more of the upload after the unpair');
      const ui = await evaluate(p, `({ v: document.getElementById('input').value, chips: document.querySelectorAll('#tray .chip').length, cancel: document.getElementById('sayCancel').hidden })`);
      check(ui.v === '' && ui.chips === 0 && ui.cancel, `X02: B starts with an empty field, tray and queue (${JSON.stringify(ui)})`);
      noProblems(p, 'X02');
      await p.dispose();
      await hostB.stop();
      fake.reset();
    }
  }
  if (!ONLY && !SEC_ONLY) {
    // ---------- 1. idle (pairing input) + security panel, every size × scheme
    for (const scheme of SCHEMES) for (const [sz, w, h] of SIZES) {
      const tag = `${sz}-${scheme}`;
      const p = await newPage(B, w, h, scheme, { allow: ALLOW });
      await navigate(p, BASE);
      await waitState(p, 'idle');
      check(!(await evaluate(p, `document.getElementById('pair-view').hidden`)), `${tag}: idle shows the pairing input`);
      check((await text(p, '#badge')) === '网页版', `${tag}: badge visible`);
      check((await evaluate(p, `document.documentElement.lang`)) === 'zh-CN', `${tag}: <html lang=zh-CN> by default`);
      await layoutOk(p, `idle ${tag}`);
      await evaluate(p, `document.getElementById('badge').click()`);
      const want = JSON.parse(readFileSync(new URL('../public/version.json', import.meta.url), 'utf8')).combined;
      check((await text(p, '#version-hash')) === want, `${tag}: badge panel shows the combined hash`);
      await layoutOk(p, `badge ${tag}`);
      await evaluate(p, `document.getElementById('badge-close').click(); document.getElementById('pair-link').value = 'not a link'; document.getElementById('pair-go').click()`);
      check(!(await evaluate(p, `document.getElementById('pair-error').hidden`)), `${tag}: garbage link → inline error`);
      noProblems(p, `idle ${tag}`);
      await p.dispose();
    }

    // ---------- 1b. say refused with why:"too_many" (16 of this phone's sends still wait, P33-C03 / G-A123): its own text,
    // in both languages, and the words stay in the composer
    for (const [lang, L] of [['zh', ZH], ['en', EN]]) {
      fake.reset();
      check(typeof L['r.say.err.too_many'] === 'string' && L['r.say.err.too_many'] !== L['r.say.err.other'], `too_many ${lang}: the dictionary has its own text`);
      const p = await newPage(B, 390, 844, 'light', { allow: ALLOW });
      await navigate(p, fake.newPairing(BASE + (lang === 'en' ? '?lang=en' : '')));
      await waitState(p, 'awaiting-approval');
      await fake.approve();
      await waitState(p, 'ready');
      fake.st.sayWhy = 'too_many';
      await typeAndEnter(p, '第十七条');
      const want = plain(L['r.say.err.too_many']);
      let shown = false;
      try { await waitFor(p, `document.getElementById('toast').classList.contains('on') && document.getElementById('toast').textContent === ${JSON.stringify(want)}`, 4000); shown = true; } catch {}
      check(shown, `too_many ${lang}: the toast reads 「${want}」 (got 「${await text(p, '#toast')}」)`);
      check((await evaluate(p, `document.getElementById('input').value`)) === '第十七条', `too_many ${lang}: the words stay in the composer`);
      if (lang === 'zh') {
        // text that lands in the field while the chat view is hidden (a sub-view open) must not squash it to its padding
        // when the view comes back (was: style.height "0px" from scrollHeight 0 → a 29 px tall field)
        await evaluate(p, `(() => { const v = document.getElementById('chat-view'), i = document.getElementById('input'); v.hidden = true; i.value = '藏起来时放进来的字'; i.dispatchEvent(new Event('input')); v.hidden = false; return true; })()`);
        await sleep(200);
        const h = await evaluate(p, `document.getElementById('input').getBoundingClientRect().height`);
        check(h >= 44, `field typed into while hidden is full height when shown again (${Math.round(h)} px ≥ 44)`);
        await evaluate(p, `(() => { const i = document.getElementById('input'); i.value = ''; i.dispatchEvent(new Event('input')); return true; })()`);
      }
      noProblems(p, `too_many ${lang}`);
      await p.dispose();
    }
    fake.reset();

    // ---------- 2. full flow against the fake relay/host (mobile light + desktop dark)
    for (const [sz, w, h, scheme] of [['mobile', 360, 800, 'light'], ['desktop', 1440, 900, 'dark']]) {
      const tag = `${sz}-${scheme}`;
      fake.reset();
      const p = await newPage(B, w, h, scheme, { allow: ALLOW });
      await navigate(p, fake.newPairing(BASE));
      await waitState(p, 'awaiting-approval');
      check((await evaluate(p, 'location.href')) === BASE, `${tag}: #p= fragment stripped from the URL`);
      const sas = await text(p, '#sas');
      check(/^\d{6}$/.test(sas) && sas === fake.sas, `${tag}: SAS ${sas} matches the host's (${fake.sas})`);
      check(/^网页 · .+ Chrome$/.test(fake.label || ''), `${tag}: device label "${fake.label}"`);
      await layoutOk(p, `sas ${tag}`);
      await fake.approve();
      await waitState(p, 'ready');
      const dev = await evaluate(p, `new Promise((res) => { const r = indexedDB.open('agentjarvis'); r.onsuccess = () => { const tx = r.result.transaction('kv');
        const d = tx.objectStore('kv').get('device'), hst = tx.objectStore('kv').get('host');
        tx.oncomplete = () => res({ ext: d.result.priv.extractable, type: d.result.priv.type, alg: d.result.priv.algorithm.name, host: !!hst.result?.approved, psk: 'psk' in (hst.result || {}) }); }; })`);
      check(dev.ext === false && dev.type === 'private' && dev.alg === 'X25519', `${tag}: device key is a non-extractable X25519 CryptoKey in IndexedDB`);
      check(dev.host && !dev.psk, `${tag}: approved host persisted without the PSK`);
      await typeAndEnter(p, '你好，助理');
      check(await waitReply(p, 'echo: 你好，助理'), `${tag}: Enter sends; the host's reply is the newest page`);
      const xss = '<img src=x onerror=alert(1)><b>bold</b>';
      await typeAndEnter(p, xss);
      check(await waitReply(p, 'echo: ' + xss), `${tag}: markup round-trips as text`);
      check((await evaluate(p, `document.querySelectorAll('#words img, #words b, #words script, #om img').length`)) === 0, `${tag}: no element injection`);
      await layoutOk(p, `chat ${tag}`);

      const brand = () => evaluate(p, `document.getElementById('brand-name').textContent + '|' + document.title`);
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: '市场部 Agent' });
      await waitFor(p, `document.getElementById('brand-name').textContent === '市场部 Agent'`);
      check((await brand()) === '市场部 Agent|市场部 Agent' && (await text(p, '#agent')) === '市场部 Agent', `${tag}: the host's name in the reply header, the menu and the tab title`);
      const evil = '<img src=x onerror=alert(1)>‮\u0000​\n' + '长'.repeat(60);
      await fake.send({ t: 'status', s: 'working', agent: 'claude', name: evil });
      await waitFor(p, `document.body.dataset.status === 'working'`);
      const shown = await evaluate(p, `(() => { const b = document.getElementById('agent'); return { t: b.textContent, kids: b.children.length, title: document.title }; })()`);
      check(shown.kids === 0 && shown.t === shown.title && [...shown.t].length <= 32 && !/[\u0000-\u001f​‮]/.test(shown.t) && shown.t.startsWith('<img src=x onerror=alert(1)> 长'),
        `${tag}: hostile name → plain text, control/format chars gone, ≤ 32 code points ("${shown.t}")`);
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: null });
      await waitFor(p, `document.getElementById('brand-name').textContent === 'Agent J'`);
      check((await brand()) === `Agent J|${ZH['meta.title']}`, `${tag}: name null → back to "Agent J"`);
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Wren' });
      await waitFor(p, `new Promise((res) => { const r = indexedDB.open('agentjarvis'); r.onsuccess = () => { const g = r.result.transaction('kv').objectStore('kv').get('host'); g.onsuccess = () => { r.result.close(); res(g.result && g.result.name === 'Wren'); }; }; })`);
      fake.setUp(false);
      await navigate(p, BASE);
      await waitState(p, 'waiting-host');
      check((await brand()) === 'Wren|Wren', `${tag}: cold start shows the cached name before the connection is up`);
      check(await evaluate(p, `document.body.dataset.conn === 'off' && document.body.dataset.view === 'chat'`), `${tag}: cold start offline = the grey chat screen`);
      fake.setUp(true);
      await waitState(p, 'ready');
      check(await waitReply(p, 'echo: ' + xss), `${tag}: reload → the history pages come back from the host (§10.5)`);

      fake.setUp(false);
      await waitState(p, 'waiting-host');
      check((await text(p, '#status')) === ZH['st.hostDown'], `${tag}: host down → ${ZH['st.hostDown']}`);
      fake.setUp(true);
      await waitState(p, 'ready');
      await typeAndEnter(p, 'after restart');
      check(await waitReply(p, 'echo: after restart'), `${tag}: host restart → RESUME redone on the same socket`);

      fake.sendRaw(Uint8Array.of(4, 1, 2, 3));
      await waitState(p, 'error');
      check(true, `${tag}: undecryptable DATA → socket closed, error state`);
      await evaluate(p, `document.getElementById('retry').click()`);
      await waitState(p, 'ready');

      fake.revokeAll();
      await waitState(p, 'revoked');
      await navigate(p, BASE);
      await waitState(p, 'revoked');
      check(true, `${tag}: RESUME of a removed device → revoked`);
      await evaluate(p, `document.getElementById('repair').click()`);
      await waitState(p, 'idle');
      const after = await evaluate(p, `new Promise((res) => { const r = indexedDB.open('agentjarvis'); r.onsuccess = () => { const tx = r.result.transaction('kv');
        const d = tx.objectStore('kv').get('device'), hst = tx.objectStore('kv').get('host'); tx.oncomplete = () => res({ dev: !!d.result, host: !!hst.result }); }; })`);
      check(after.dev && !after.host, `${tag}: 重新配对 clears the host, keeps the device key`);
      check((await text(p, '#brand-name')) === 'Agent J', `${tag}: re-pairing drops the cached name`);

      await p.send('Page.navigate', { url: fake.newPairing(BASE) });   // same-document: only the fragment changes
      await waitState(p, 'awaiting-approval');
      check((await evaluate(p, 'location.href')) === BASE, `${tag}: hashchange pairing link read and stripped`);
      fake.setUp(false);
      await waitState(p, 'error');
      check((await text(p, '#status')) === PAIR_FAIL, `${tag}: host down mid-pairing → ${PAIR_FAIL}`);
      fake.setUp(true);

      await navigate(p, 'about:blank');
      await navigate(p, fake.newPairing(BASE, -10));
      await waitState(p, 'idle');
      check(!(await evaluate(p, `document.getElementById('pair-error').hidden`)), `${tag}: expired link refused before connecting`);
      noProblems(p, `flow ${tag}`);
      await p.dispose();
    }

    // ---------- 2b. a host without p33 (§10.0): §8 msg / 4 000 limit, pages built in memory
    {
      fake.reset(); fake.st.p33 = false;
      const p = await newPage(B, 390, 844, 'light', { allow: ALLOW });
      await navigate(p, fake.newPairing(BASE));
      await waitState(p, 'awaiting-approval');
      await fake.approve();
      await waitState(p, 'ready');
      check((await evaluate(p, `document.getElementById('input').maxLength`)) === 4000, 'old host: the field caps at 4 000');
      await typeAndEnter(p, '老版本电脑');
      check(await waitReply(p, 'echo: 老版本电脑'), 'old host: msg out, msg back → a page (source + reply)');
      check((await text(p, '#omText')) === '老版本电脑' && fake.st.says.some((m) => m.t === 'msg'), 'old host: the device sent §8 msg, not say');
      noProblems(p, 'old host');
      await p.dispose();
      fake.st.p33 = true;
    }

    // ---------- 3. gallery: every reachable screen, 360 / 1440 × light / dark × zh / en, audited
    const files = makeFiles();
    const now = Date.now();
    fake.answer('mem_list', () => [
      { t: 'mem_sources', harness: 'claude', sources: [
        { id: 'proj', label: 'CLAUDE.md（项目）', path: '~/shop/CLAUDE.md', n: 3 },
        { id: 'user', label: '~/.claude/CLAUDE.md（全局）', path: '~/.claude/CLAUDE.md', problem: 'not_found' }],
        trash: [{ id: 't1', label: 'CLAUDE.md', text: '- 发货前先截图给我看' }] },
      { t: 'mem_items', items: [
        { src: 'proj', file: 'CLAUDE.md', fsha: 'a', iid: '1', kind: 'line', text: '- 回答用中文，先给结论' },
        { src: 'proj', file: 'CLAUDE.md', fsha: 'a', iid: '2', kind: 'line', text: '- 改价格前必须先问我' },
        { src: 'proj', file: 'CLAUDE.md', fsha: 'a', iid: '3', kind: 'line', text: '- Weekly report goes out on Fridays at 17:00' }], more: false },
    ]);
    fake.answer('act_list', () => ({ t: 'act_page', more: false, next: 'p2', items: [
      { k: 'decision', ts: now - 60e3, result: 'deny', by: 'iPhone', tool: 'Bash', text: 'rm -rf build/' },
      { k: 'ask', ts: now - 62e3, tool: 'Bash', cats: ['delete'], text: 'rm -rf build/' },
      { k: 'task_done', ts: now - 3600e3, title: '每日销量日报', verdict: 'ok', line: '昨天 132 单，正常', readonly: true },
    ] }));
    fake.answer('task_list', () => ({ t: 'tasks', more: false, agent: 'claude', paused: false, items: [
      { id: 'daily-report', title: { zh: '每日销量日报', en: 'Daily sales report' }, schedule: '0 9 * * *', tz: 'local', mode: 'research', enabled: true, next: Math.floor(now / 1000) + 3600, tsha: 'x', last: { verdict: 'ok', line: '昨天 132 单，正常' } },
      { id: 'restock', title: { zh: '库存提醒', en: 'Restock check' }, schedule: '30 8 * * 1-5', tz: 'local', mode: 'normal', enabled: false, tsha: 'y' },
    ] }));
    fake.answer('estop', () => [{ t: 'ctl_res', ok: true }, { t: 'estop_state', on: true, by: 'iPhone' }]);
    fake.answer('resume', () => [{ t: 'ctl_res', ok: true }, { t: 'estop_state', on: false }]);
    const hex = (n) => [...crypto.getRandomValues(new Uint8Array(n))].map((b) => b.toString(16).padStart(2, '0')).join('');

    const COMBOS = [];
    for (const lang of ['zh', 'en']) for (const scheme of SCHEMES) for (const [sz, w, h] of SIZES) COMBOS.push({ lang, scheme, sz, w, h });
    const combos = process.env.AJ_QUICK ? COMBOS.filter((c) => (c.sz === 'mobile' && c.scheme === 'light' && c.lang === 'zh') || (c.sz === 'desktop' && c.scheme === 'dark' && c.lang === 'en')) : COMBOS;
    for (const { lang, scheme, sz, w, h } of combos) {
      const tag = `${w}-${scheme}-${lang}`;
      const L = lang === 'en' ? EN : ZH;
      const ua = sz === 'mobile' ? (scheme === 'light' ? UA.iphone : UA.android) : undefined;
      fake.reset();
      const p = await newPage(B, w, h, scheme, { ua, touch: sz === 'mobile', allow: ALLOW });
      const shot = async (name, opts) => { await layoutOk(p, `${name} ${tag}`); await shoot(p, `${name}-${tag}`, opts); };
      await navigate(p, BASE + (lang === 'en' ? '?lang=en' : ''));
      await waitState(p, 'idle');
      check((await text(p, '#status')) === plain(L['st.idle']), `${tag}: status in ${lang}`);
      if (sz === 'mobile') check(!(await evaluate(p, `document.getElementById('a2hs').hidden`)), `${tag}: Add-to-Home-Screen hint in a phone browser`);
      else check(await evaluate(p, `document.getElementById('a2hs').hidden`), `${tag}: no Add-to-Home-Screen hint on a desktop browser`);
      await shot('01-login', { full: true });
      await evaluate(p, `document.getElementById('aj-menu').open = true`);
      await shot('02-menu');
      await evaluate(p, `document.getElementById('menu-about').click()`);
      await shot('03-security-panel');
      await evaluate(p, `document.getElementById('badge-close').click()`);
      await evaluate(p, `document.getElementById('pair-link').value = 'not a link'; document.getElementById('pair-go').click()`);
      check((await text(p, '#pair-error')) === plain(L['pair.badLink']), `${tag}: bad link error in ${lang}`);

      await navigate(p, 'about:blank');
      await navigate(p, fake.newPairing(BASE + (lang === 'en' ? '?lang=en' : '')));
      await waitState(p, 'awaiting-approval');
      await shot('04-pair-code');
      await fake.approve();
      await waitState(p, 'ready');
      if (sz === 'mobile') {
        await shot('05-chat-a2hs-hint');
        await evaluate(p, `document.getElementById('a2hs-ok').click()`);
      }
      const NAME = lang === 'en' ? 'Sales Agent' : '市场部 Agent';
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: NAME });
      await fake.send({ t: 'meter', model: 'opus', model_name: 'Opus 5.5', effort: 'medium', ctx: { used: 62, max: 100 }, h5: { pct: 34, reset: 0 }, week: { pct: 58, reset: 0 }, at: 0 });
      await fake.addTurn({ k: 'phone', dev: 'x', name: 'iPad', text: lang === 'en' ? 'Check today’s orders and draft a restock list' : '看一下今天的订单，拟一份补货清单' },
        lang === 'en' ? '## Today\n\n**132 orders**; 3 items are low.\n\n| item | left |\n|---|---|\n| mugs | 4 |\n| caps | 2 |\n\n```sh\nnpm run restock\n```' : '## 今天\n\n**132 单**，有 3 个商品库存偏低。\n\n| 商品 | 剩余 |\n|---|---|\n| 杯子 | 4 |\n| 帽子 | 2 |\n\n```sh\nnpm run restock\n```');
      await waitFor(p, `document.getElementById('pg').textContent === '1 / 1'`);
      await shot('06-chat-idle');
      await fake.send({ t: 'status', s: 'working', agent: 'claude', name: NAME });
      await waitFor(p, `document.body.dataset.status === 'working'`);
      await shot('07-chat-working');
      await fake.send({ t: 'status', s: 'waiting', agent: 'claude', name: NAME });
      await fake.ask({ id: hex(16), tool: 'Bash', summary: 'rm -rf exports/old/', ttl: 120, cat: ['delete'], why: 'rm -rf' });
      await fake.ask({ id: hex(16), tool: 'Bash', summary: 'cat orders/2026-10-03.csv | wc -l', ttl: 120, batch: 'Bash：cat', batch_max: 20, batch_secs: 600 });
      await waitFor(p, `document.body.dataset.sheet === '1'`);
      await shot('08-approval-sheet');
      await fake.send({ t: 'question', id: hex(16), ttl: 180, qs: [{ q: lang === 'en' ? 'Which supplier?' : '找哪家供应商？', h: '', m: false, o: [{ l: 'A', d: lang === 'en' ? 'cheaper' : '便宜' }, { l: 'B', d: lang === 'en' ? 'faster' : '快' }] }] });
      await evaluate(p, `document.getElementById('sheetClose').click()`);
      await shot('09-pending-tag');
      await evaluate(p, `document.getElementById('pendTag').click()`);
      fake.reset();
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: NAME });
      await navigate(p, BASE + (lang === 'en' ? '?lang=en' : ''));
      await waitState(p, 'ready');
      await fake.send({ t: 'status', s: 'waiting', kind: 'question', agent: 'claude', name: NAME });
      await fake.send({ t: 'question', id: hex(16), ttl: 180, qs: [{ q: lang === 'en' ? 'Which supplier?' : '找哪家供应商？', h: lang === 'en' ? 'Supplier' : '供应商', m: true, o: [{ l: 'A', d: lang === 'en' ? 'cheaper' : '便宜' }, { l: 'B', d: lang === 'en' ? 'faster' : '快' }, { l: 'C' }] }] });
      await waitFor(p, `document.body.dataset.kind === 'question' && document.body.dataset.sheet === '1'`);
      await shot('10-question-sheet');
      await fake.send({ t: 'question_done', id: 'x'.repeat(32), result: 'gone' });
      fake.reset();
      await navigate(p, BASE + (lang === 'en' ? '?lang=en' : ''));
      await waitState(p, 'ready');
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: NAME });
      await fake.send({ t: 'meter', model: 'opus', model_name: 'Opus 5.5', effort: 'medium', ctx: { used: 30, max: 100 }, h5: { pct: 34, reset: 0 }, week: { pct: 58, reset: 0 }, at: 0 });
      await fake.addTurn({ k: 'agent' }, lang === 'en' ? 'Done. The list is in restock.md.' : '好了，清单在 restock.md 里。');
      await evaluate(p, `document.getElementById('keysBtn').click()`);
      await shot('11-keys-panel');
      await evaluate(p, `document.getElementById('keysClose').click()`);
      await evaluate(p, `document.getElementById('readBtn').click()`);
      await waitFor(p, `!document.getElementById('rd').hidden`);
      await shot('12-reader');
      await sleep(400);                                   // the reader swallows clicks for 350 ms after it opens (relay ADR-049)
      await evaluate(p, `document.getElementById('rdClose').click()`);
      await waitFor(p, `document.getElementById('rd').hidden`);
      await sleep(400);
      await evaluate(p, `document.getElementById('slashBtn').click()`);
      await waitFor(p, `!document.getElementById('menu').hidden`);
      await shot('13-command-menu');
      await evaluate(p, `document.body.click()`);
      const { root } = await p.send('DOM.getDocument', { depth: -1 });
      const { nodeId } = await p.send('DOM.querySelector', { nodeId: root.nodeId, selector: '#fPhoto' });
      await p.send('DOM.setFileInputFiles', { nodeId, files: [files.png1, files.txt] });
      await waitFor(p, `document.querySelectorAll('#tray .chip[data-st=ready]').length === 2`, 10000);
      await evaluate(p, `document.getElementById('replyBtn').click()`);
      await p.send('Input.insertText', { text: lang === 'en' ? 'Use supplier A' : '用 A 家' });
      await shot('14-tray-quote');
      await evaluate(p, `[...document.querySelectorAll('#tray .chip .x')].forEach((b) => b.click()); document.getElementById('qbX').click(); document.activeElement.blur()`);
      if (sz === 'mobile') {
        const r = await evaluate(p, `(() => { const b = document.getElementById('mic').getBoundingClientRect(); return { x: b.x + b.width / 2, y: b.y + b.height / 2 }; })()`);
        await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: r.x, y: r.y }] });
        await waitFor(p, `document.getElementById('mic').classList.contains('rec')`, 4000);
        await sleep(1200);
        await shot('15-recording');
        await p.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: r.x, y: r.y - 120 }] });
        await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
      }
      await evaluate(p, `document.getElementById('aj-menu').open = true; document.getElementById('open-mem').click()`);
      await waitFor(p, `document.querySelectorAll('#mem-list .mem-item').length === 3`);
      await shot('16-memory', { full: true });
      await evaluate(p, `document.querySelector('#mem-list .mem-del').click()`);
      await waitFor(p, `!document.getElementById('confirm').hidden`);
      await shot('17-memory-delete-confirm');
      await evaluate(p, `document.getElementById('confirm-no').click(); document.querySelector('#mem-view [data-back]').click()`);
      await evaluate(p, `document.getElementById('aj-menu').open = true; document.getElementById('open-act').click()`);
      await waitFor(p, `document.querySelectorAll('#act-list li').length === 3`);
      await shot('18-activity');
      await evaluate(p, `document.querySelector('#act-view [data-back]').click(); document.getElementById('aj-menu').open = true; document.getElementById('open-tasks').click()`);
      await waitFor(p, `document.querySelectorAll('#tasks-list li').length === 2`);
      await shot('19-scheduled-tasks');
      await evaluate(p, `document.querySelector('#tasks-view [data-back]').click()`);
      await evaluate(p, `document.getElementById('slashBtn').click()`);
      await evaluate(p, `document.querySelector('#menu [data-estop="stop"]').click()`);
      await waitFor(p, `!document.getElementById('confirm').hidden`);
      check((await text(p, '#confirm-yes')) === L['estop.confirmYes'], `${tag}: stop-everything confirm in ${lang}`);
      await shot('20-stop-everything-confirm');
      await evaluate(p, `document.getElementById('confirm-yes').click()`);
      await waitFor(p, `!document.getElementById('estop-banner').hidden`);
      await fake.send({ t: 'status', s: 'stopped', agent: 'claude', name: NAME });
      await waitFor(p, `document.body.dataset.agent === 'stopped'`);
      check((await evaluate(p, `document.getElementById('input').placeholder`)) === L['chat.placeholderStopped'], `${tag}: the field says everything is stopped`);
      await shot('21-stopped');
      await evaluate(p, `document.getElementById('resume').click()`);
      await waitFor(p, `!document.getElementById('confirm').hidden`);
      await evaluate(p, `document.getElementById('confirm-yes').click()`);
      await waitFor(p, `document.getElementById('estop-banner').hidden`);
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: NAME });
      fake.setUp(false);
      await waitState(p, 'waiting-host');
      check((await text(p, '#status')) === L['st.hostDown'], `${tag}: offline status in ${lang}`);
      await shot('22-computer-offline');
      fake.setUp(true);
      await waitState(p, 'ready');
      fake.sendRaw(Uint8Array.of(4, 1, 2, 3));
      await waitState(p, 'error');
      check((await text(p, '#error-text')) === plain(L['error.dataBad']), `${tag}: disconnect reason in ${lang}`);
      await shot('23-disconnected');
      await evaluate(p, `document.getElementById('retry').click()`);
      await waitState(p, 'ready');
      fake.revokeAll();
      await waitState(p, 'revoked');
      await shot('24-removed');
      noProblems(p, `gallery ${tag}`);
      await p.dispose();
    }

    // ---------- 4. language switch: html[lang], persistence across reloads, ?lang= wins and is saved
    {
      const p = await newPage(B, 360, 800, 'light', { allow: ALLOW });
      await navigate(p, BASE);
      await waitState(p, 'idle');
      check((await text(p, '#pair-view h1')) === ZH['pair.title'], 'lang: 中文 by default');
      await evaluate(p, `document.querySelector('#pair-view [data-aj-lang="en"]').click()`);
      check((await evaluate(p, `document.documentElement.lang + '|' + document.title`)) === `en|${EN['meta.title']}`, 'lang: the switch on the pairing screen sets <html lang=en> and the title');
      check((await text(p, '#pair-view h1')) === EN['pair.title'] && (await text(p, '#status')) === EN['st.idle'], 'lang: visible text switches at once (static + status)');
      check((await evaluate(p, `document.getElementById('pair-link').placeholder`)) === EN['pair.placeholder'], 'lang: attributes switch too (placeholder)');
      await navigate(p, BASE);
      await waitState(p, 'idle');
      check((await evaluate(p, `document.documentElement.lang + '|' + localStorage.getItem('aj.lang')`)) === 'en|en', 'lang: English survives a reload (localStorage aj.lang)');
      await evaluate(p, `document.getElementById('aj-menu').open = true; document.querySelector('#aj-menu [data-aj-lang="zh"]').click()`);
      check((await evaluate(p, `document.documentElement.lang + '|' + localStorage.getItem('aj.lang')`)) === 'zh-CN|zh', 'lang: the switch in the menu goes back to 中文 and saves it');
      await navigate(p, BASE + '?lang=en');
      await waitState(p, 'idle');
      check((await evaluate(p, `document.documentElement.lang + '|' + localStorage.getItem('aj.lang')`)) === 'en|en', 'lang: ?lang=en wins and is saved');
      await navigate(p, 'about:blank');
      await navigate(p, fake.newPairing(BASE + '?lang=en'));
      await waitState(p, 'awaiting-approval');
      check((await evaluate(p, 'location.href')) === BASE + '?lang=en' && (await text(p, '#sas-view h1')) === EN['sas.title'], 'lang: a pairing link keeps ?lang= while its #p= fragment is stripped');
      noProblems(p, 'lang');
      await p.dispose();
    }

    // ---------- 5. legacy web host: served via CDP Fetch interception. Nothing leaves this machine.
    {
      const LEG = 'https://alpha-web.agentjarvis.net', NEW = 'https://m.agentj.app';
      const p = await newPage(B, 360, 800, 'light', { allow: [...ALLOW, LEG, NEW] });
      await p.send('Fetch.enable', { patterns: [{ urlPattern: LEG + '/*' }, { urlPattern: NEW + '/*' }] });
      const STUB = Buffer.from('<!doctype html><title>stub</title><p>m.agentj.app</p>').toString('base64');
      const SKIP = new Set(['content-length', 'connection', 'keep-alive', 'transfer-encoding', 'date']);
      p.on(async (m) => {
        if (m.method !== 'Fetch.requestPaused') return;
        const { requestId, request } = m.params;
        const u = new URL(request.url);
        try {
          if (u.origin === NEW || u.pathname === '/__seed') {
            await p.send('Fetch.fulfillRequest', { requestId, responseCode: 200, responseHeaders: [{ name: 'content-type', value: 'text/html' }], body: STUB });
            return;
          }
          const r = await fetch(new URL(u.pathname.slice(1) + u.search, BASE));
          const headers = [...r.headers].filter(([k]) => !SKIP.has(k)).map(([name, value]) => ({ name, value }));
          await p.send('Fetch.fulfillRequest', { requestId, responseCode: r.status, responseHeaders: headers, body: Buffer.from(await r.arrayBuffer()).toString('base64') });
        } catch { /* page gone */ }
      });
      const href = async () => { try { return await evaluate(p, 'location.href'); } catch { return ''; } };
      async function landsOn(want, ms = 8000) { let h = ''; for (let t = 0; t < ms; t += 100) { h = await href(); if (h === want) return true; await sleep(100); } return h; }
      const link = fake.newPairing(LEG + '/?lang=en');
      await p.send('Page.navigate', { url: link });
      const r1 = await landsOn(link.replace(LEG, NEW));
      const a = check(r1 === true, `legacy: no pairing + pairing link → ${NEW} with path, query and #p= intact${r1 === true ? '' : ' (got ' + r1 + ')'}`);
      await p.send('Page.navigate', { url: LEG + '/' });
      const r2 = await landsOn(NEW + '/');
      const b = check(r2 === true, `legacy: no pairing → ${NEW}/${r2 === true ? '' : ' (got ' + r2 + ')'}`);
      await navigate(p, LEG + '/__seed');
      await evaluate(p, `new Promise((res, rej) => { const r = indexedDB.open('agentjarvis', 1); r.onupgradeneeded = () => r.result.createObjectStore('kv');
        r.onsuccess = () => { const tx = r.result.transaction('kv', 'readwrite'); tx.objectStore('kv').put({ relay: ${JSON.stringify(RELAY)}, channel: ${JSON.stringify(fake.channel)},
          hostPub: crypto.getRandomValues(new Uint8Array(32)), approved: true, name: 'Wren' }, 'host'); tx.oncomplete = () => { r.result.close(); res(true); }; tx.onerror = () => rej(tx.error); }; })`);
      await navigate(p, LEG + '/');
      await sleep(1500);
      const c = check((await href()) === LEG + '/', 'legacy: a paired phone stays on the legacy host (no redirect)');
      const d = check((await evaluate(p, `document.getElementById('pair-view').hidden && document.getElementById('brand-name').textContent === 'Wren' && document.title === 'Wren'`)),
        'legacy: …and runs normally (resume, cached Agent name shown)');
      results['aj-legacy-redirect'] = a && b && c && d ? 'pass' : 'fail';
      await p.dispose();
    }
  }

  // ---------- P127: a Telegram page has its own fill (both schemes) and shows the owner's words without the Agent's source
  // line (also an old page stored with it); a compaction in flight says so on the open page and the water sinks at once
  if (!ONLY && !SEC_ONLY) {
    for (const [lang, L] of [['zh', ZH], ['en', EN]]) for (const scheme of SCHEMES) {
      const tag = `P127 ${lang}-${scheme}`;
      fake.reset();
      const p = await newPage(B, 390, 844, scheme, { allow: ALLOW });
      await navigate(p, fake.newPairing(BASE + (lang === 'en' ? '?lang=en' : '')));
      await waitState(p, 'awaiting-approval');
      await fake.approve();
      await waitState(p, 'ready');
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Wren' });
      await fake.addTurn({ k: 'telegram', dev: 'telegram:1:1', name: 'Telegram owner', text: 'Telegram owner private chat.\n帮我看看明天的会' }, '', 'open');
      try { await waitFor(p, `document.getElementById('om').dataset.src === 'tg'`, 6000); } catch {}
      const om = await evaluate(p, `(() => { const o = document.getElementById('om'), s = getComputedStyle(o); return { src: o.dataset.src, bg: s.backgroundColor,
        text: document.getElementById('omText').textContent, label: document.getElementById('omLabel').textContent, icon: !!document.querySelector('#omIcon svg') }; })()`);
      check(om.src === 'tg' && om.label === 'Telegram' && om.icon, `${tag}: Telegram source card with its label and icon (${JSON.stringify(om)})`);
      check(!['rgba(0, 0, 0, 0)', 'transparent', 'rgb(255, 226, 146)', 'rgb(193, 225, 240)'].includes(om.bg), `${tag}: its own opaque fill, not the phone's yellow / another device's blue (${om.bg})`);
      check(om.text === '帮我看看明天的会', `${tag}: the words without 「Telegram owner private chat.」 (got 「${om.text}」)`);
      check((await text(p, '#words')) === plain(L['r.replyPending']), `${tag}: before the compaction the open page waits for the computer`);
      await fake.send({ t: 'status', s: 'compacting', agent: 'claude', name: 'Wren' });
      let sank = false;
      try { await waitFor(p, `document.getElementById('water').dataset.compacting === '1'`, 4000); sank = true; } catch {}
      check(sank, `${tag}: the water starts sinking as soon as the compaction starts`);
      check((await text(p, '#words')) === plain(L['r.replyCompacting']), `${tag}: the open page reads 「${plain(L['r.replyCompacting'])}」 (got 「${await text(p, '#words')}」)`);
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Wren' });
      let ended = false;
      try { await waitFor(p, `document.getElementById('water').dataset.compacting === '0'`, 4000); ended = true; } catch {}
      check(ended && (await text(p, '#words')) === plain(L['r.replyPending']), `${tag}: the end of the compaction ends the effect and the words`);
      if (lang === 'zh') await shoot(p, `p127-telegram-${scheme}`);
      noProblems(p, tag);
      await p.dispose();
    }
  }

  if (!ONLY && !SEC_ONLY) await runP102Screens({B,web,fake,out:join(SHOTS,'p102')});

  // ---------- 6. relay parity: one named case per feature id
  if (!process.env.AJ_NO_PARITY && !SEC_ONLY) {
    const r = await runCases({ B, web, fake, only: ONLY });
    Object.assign(results, r.results);
    for (const f of r.fails) failures.push('parity ' + f);
  }
} catch (e) {
  console.error(e); failures.push(String(e && e.stack || e));
} finally {
  await B.close();
  await web.stop();
  await fake.stop();
}
// ---------- report in the parity gate's format (parity/lib.mjs parityRecorder): {"suite","results":{"<id>":"pass"|"fail"}}
// at AJ_PARITY_OUT (tests/e2e_parity.mjs sets it) or reports/qa/parity/web-web_test_screens.mjs.json
const rec = parityRecorder('web/test/screens.mjs');
for (const [id, v] of Object.entries(results).sort()) rec.record(id, v === 'pass');
const outFile = !process.env.AJ_NO_PARITY && !SEC_ONLY ? rec.write() : '(not written: AJ_NO_PARITY / AJ_SECURITY_ONLY)';
const nPass = Object.values(results).filter((x) => x === 'pass').length;
console.log(`\nparity cases: ${nPass} pass, ${Object.keys(results).length - nPass} fail → ${outFile}`);
console.log(`${failures.length ? 'FAILED' : 'ALL PASS'} — ${failures.length} failure(s); screenshots in ${SHOTS}`);
for (const f of failures) console.log('  - ' + f);
process.exit(failures.length ? 1 : 0);
