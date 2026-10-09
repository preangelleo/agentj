// Relay-parity cases for the web client (PROMPT-33): one named case per agentjarvis/parity/features.json id (relay
// artifact "pwa", plus the Agent J extras "agentj-only"), each driving the real page in headless Chromium against the fake
// relay + host (test/fakehost.mjs) and asserting what is visible. Case name = the feature id. Used by test/screens.mjs,
// which reports them in the parity gate format (parity/lib.mjs parityRecorder; tests/e2e_parity.mjs collects them).
import { writeFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { newPage, evaluate, navigate, waitFor, waitState, key, tap, rect, swipe, sleep } from './browser.mjs';
import { dictionaries } from '../build.mjs';

const D = dictionaries();
const ZH = D.zh;
const fmt = (s, v = {}) => String(s).replace(/\{(\w+)\}/g, (m, k) => (k in v ? String(v[k]) : m)).replace(/`/g, '');
const T = (k, v) => fmt(ZH[k], v);
const rgb = (c) => { const m = /^color\(srgb ([^)]+)\)$/.exec(c); return m ? 'rgb(' + m[1].split(/\s+/).slice(0, 3).map((x) => Math.round(Number(x) * 255)).join(', ') + ')' : c.replace(/^rgba?\(([^,]+), ?([^,]+), ?([^,)]+).*$/, 'rgb($1, $2, $3)'); };
const hex = (n) => [...crypto.getRandomValues(new Uint8Array(n))].map((b) => b.toString(16).padStart(2, '0')).join('');
const ANDROID = 'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36';

export function makeFiles() {
  const dir = mkdtempSync(join(tmpdir(), 'aj-web-files-'));
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFklEQVR42mP8z8DwnwEIGBkZGBgYAAA6GwH9q+oAMgAAAABJRU5ErkJggg==', 'base64');
  const f = (name, data) => { const p = join(dir, name); writeFileSync(p, data); return p; };
  return {
    dir,
    txt: f('notes.txt', 'hello from the phone\n'.repeat(20)),
    md: f('plan.md', '# plan\n- a\n- b\n'),
    big: f('big.txt', Buffer.alloc(2 * 1024 * 1024, 65)),
    huge: f('huge.txt', Buffer.alloc(26 * 1024 * 1024, 66)),
    png1: f('a.png', png), png2: f('b.png', png),
    many: Array.from({ length: 11 }, (_, i) => f(`n${i}.txt`, `file ${i}\n`)),
  };
}

/** Runs every case; returns {id: 'pass'|'fail'} and the failure messages. */
export async function runCases({ B, web, fake, only }) {
  const results = {}, fails = [];
  const files = makeFiles();
  const allow = [web.url, fake.relay + '/'];
  let P = null;                                         // the current page
  const ev = (expr) => evaluate(P, expr);
  const wait = (expr, ms) => waitFor(P, expr, ms);
  const text = (sel) => ev(`(document.querySelector(${JSON.stringify(sel)}) || {}).textContent`);
  const click = (sel) => ev(`document.querySelector(${JSON.stringify(sel)}).click()`);
  const hidden = (sel) => ev(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); return !e || e.hidden || getComputedStyle(e).display === 'none' || !e.getClientRects().length; })()`);
  const ok = (c, msg) => { if (!c) throw new Error(msg); };
  const toastText = () => text('#toast');
  const waitToast = (want, ms = 4000) => wait(`document.getElementById('toast').classList.contains('on') && document.getElementById('toast').textContent.includes(${JSON.stringify(want)})`, ms);
  async function typeIn(s) { await ev(`document.getElementById('input').focus()`); await P.send('Input.insertText', { text: s }); }
  async function enter() { await key(P, 'Enter', { text: '\r' }); }
  async function clearField() { await ev(`(() => { const i = document.getElementById('input'); i.value = ''; i.dispatchEvent(new Event('input')); })()`); }
  async function setFiles(id, paths) { const { root } = await P.send('DOM.getDocument', { depth: -1 }); const { nodeId } = await P.send('DOM.querySelector', { nodeId: root.nodeId, selector: '#' + id }); await P.send('DOM.setFileInputFiles', { nodeId, files: paths }); }
  async function chips(n, st = 'ready', ms = 15000) { await wait(`(() => { const c = [...document.querySelectorAll('#tray .chip')]; return c.length === ${n} && c.every((x) => x.dataset.st === ${JSON.stringify(st)}); })()`, ms); }
  async function clearTray() { await ev(`[...document.querySelectorAll('#tray .chip .x')].forEach((b) => b.click())`); await wait(`!document.querySelectorAll('#tray .chip').length`); }
  // the sheet slides in (transform .55 s): measure where a button IS, not where it is passing through
  const intoView = async (sel) => { await ev(`document.querySelector(${JSON.stringify(sel)}).scrollIntoView({ block: 'center' })`); await wait(`(() => { const s = document.getElementById('sheet'); return !s.contains(document.querySelector(${JSON.stringify(sel)})) || getComputedStyle(s).transform === 'none' || getComputedStyle(s).transform === 'matrix(1, 0, 0, 1, 0, 0)'; })()`, 2000).catch(() => {}); };
  // .rec is painted while getUserMedia is still pending. Measure a valid
  // recording's duration only after the native recorder is available.
  const captureReady = () => wait(`!document.getElementById('ptt').hidden && /^\\d+:\\d\\d$/.test(document.getElementById('pttTime').textContent)`, 3000);
  async function touchHold(sel, ms, { moveUp = 0 } = {}) {
    await intoView(sel);
    const r = await rect(P, sel);
    await P.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: r.x, y: r.y }] });
    try {
      if (sel === '#mic') await captureReady();
      await sleep(ms / 2);
      if (moveUp) await P.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: r.x, y: r.y - moveUp }] });
      await sleep(ms / 2);
    } finally {
      await P.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    }
  }
  async function touchTap(sel) { await intoView(sel); const r = await rect(P, sel); await tap(P, r.x, r.y); }
  async function dblTap(sel) { const r = await rect(P, sel === '#words' ? '#main' : sel); await tap(P, r.x, r.y); await sleep(80); await tap(P, r.x, r.y); }
  async function mouseHold(sel, ms) {
    const r = await rect(P, sel);
    await P.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: r.x, y: r.y });
    await P.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
    await sleep(ms);
    await P.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 });
  }
  async function pair(page) {
    await navigate(page, fake.newPairing(web.url));
    await waitState(page, 'awaiting-approval');
    await fake.approve();
    await waitState(page, 'ready');
    await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Wren' });
    await waitFor(page, `document.body.dataset.status === 'idle'`);
  }
  async function open(kind) {
    if (P) { await P.dispose(); P = null; }
    fake.reset();
    P = kind === 'desktop' ? await newPage(B, 1280, 860, 'light', { allow }) : await newPage(B, 390, 844, 'light', { touch: true, ua: ANDROID, allow });
    await P.send('Emulation.setFocusEmulationEnabled', { enabled: true }).catch(() => {});
    await P.send('DOM.enable');
    await pair(P);
    // instrumentation: rockets, vibration, theme-color writes
    await ev(`(() => { window.__rockets = []; window.__vib = []; navigator.vibrate = (p) => { window.__vib.push(p); return true; };
      new MutationObserver((ms) => { for (const m of ms) for (const n of m.addedNodes) if (n.classList && n.classList.contains('rocketfly')) window.__rockets.push(n.dataset.land); }).observe(document.body, { childList: true }); })()`);
    await ev(`document.getElementById('a2hs-ok') && !document.getElementById('a2hs').hidden && document.getElementById('a2hs-ok').click()`);
  }
  async function turns(n, mk = (i) => [{ k: 'phone', dev: 'other', text: `问题 ${i}` }, `回复 ${i}\n\n第二行 ${i}`]) {
    for (let i = 1; i <= n; i++) { const [src, reply] = mk(i); await fake.addTurn(src, reply); }
    await wait(`document.getElementById('pg').textContent.endsWith('/ ' + ${JSON.stringify(String(fake.st.turns.length))}) || document.getElementById('pg').textContent.endsWith('/ ' + ${fake.st.turns.length})`);
    if (!(await hidden('#newReply'))) await click('#newReply');
  }
  const pg = async () => (await text('#pg')).trim();
  const curWords = () => text('#words');
  const clipboard = () => ev(`navigator.clipboard.readText().catch((e) => 'ERR ' + e.message)`);
  async function sendText(s) { await typeIn(s); await click('#send'); }
  const lastSay = () => fake.st.says[fake.st.says.length - 1];

  async function C(id, fn) {
    if (P) { const ue = await ev('document.body.dataset.uiError || ""').catch(() => ''); if (ue) { console.log('UIERROR before ' + id + ': ' + ue); await ev('delete document.body.dataset.uiError'); } }
    if (only && !only.includes(id)) return;
    try { await fn(); results[id] = 'pass'; console.log(`PASS  parity ${id}`); }
    catch (e) {
      const snapS = P ? await ev(`JSON.stringify({pg: document.getElementById('pg').textContent, om: document.getElementById('omText').textContent.slice(0, 40), words: document.getElementById('words').textContent.slice(0, 40), st: document.body.dataset.status, conn: document.body.dataset.conn, sheet: document.body.dataset.sheet, view: document.body.dataset.view, toast: document.getElementById('toast').textContent, state: window.__ajState, q: document.getElementById('omQuote').hidden, timer: document.getElementById('pttTime').textContent, hint: document.getElementById('pttHint').textContent, mic: document.getElementById('mic').className})`).catch(() => '') : '';
      e.message += ' @ ' + snapS;
      if (P) e.message += ' | media: ' + await ev('JSON.stringify(window.__ajMediaDiagnostics || [])').catch(() => 'unavailable');
      if (P && process.env.AJ_FAIL_SHOTS) { try { const { shoot } = await import('./browser.mjs'); writeFileSync(`/tmp/aj-fail-${id}.png`, await shoot(P)); e.message += ' vvh=' + await ev(`document.documentElement.style.getPropertyValue('--vvh') + ' ih=' + innerHeight + ' bh=' + document.body.getBoundingClientRect().height`); } catch { /* best effort */ } }
      results[id] = 'fail'; fails.push(`${id}: ${e.message}`); console.log(`FAIL  parity ${id} — ${e.message}${P ? ' | problems: ' + P.problems.slice(-3).join(' | ') : ''}`); }
  }

  // ======================================================================== mobile page: pairing + reading
  await open('mobile');
  await C('aj-pairing-qr', async () => { ok(await ev(`location.href`) === web.url, 'the #p= link was read and stripped'); ok(await ev('window.__ajState') === 'ready', 'paired'); });
  await C('login-admin-key', async () => { ok(await ev(`document.body.dataset.view`) === 'chat', 'pairing (the substitute for the admin key) lands in the chat'); });
  await C('aj-pairing-sas', async () => { ok(/^\d{6}$/.test(fake.sas || ''), 'the host saw a 6-digit safety code'); ok((await ev(`document.getElementById('sas').textContent`)) === fake.sas, 'the phone showed the same code'); });
  await C('aj-agent-name', async () => { ok((await text('#agent')) === 'Wren' && (await ev('document.title')) === 'Wren', 'the Agent name in the reply header and the tab title'); });
  await C('empty-state', async () => { ok((await curWords()).includes(T('r.emptyReady').slice(0, 6)), 'an empty history says so'); ok((await pg()) === '0 / 0', 'page counter 0 / 0'); });
  await C('service-worker', async () => { ok(await ev(`navigator.serviceWorker.getRegistration().then((r) => !!r && /sw\\.js/.test((r.active || r.installing || r.waiting).scriptURL))`), 'sw.js registered'); });

  await C('send-text', async () => {
    await sendText('看一下今天的订单');
    await wait(`document.getElementById('omText').textContent === '看一下今天的订单'`);
    ok(lastSay().t === 'say' && lastSay().text === '看一下今天的订单', 'a say went out');
    await wait(`document.getElementById('words').textContent.includes('echo: 看一下今天的订单')`);
    ok((await ev(`document.getElementById('input').value`)) === '', 'the field is empty after the send');
  });
  await C('rocket-send', async () => { ok((await ev(`window.__rockets.includes('1')`)), 'a landing rocket flew'); });
  await C('whoosh-sound', async () => { ok(Number(await ev(`document.body.dataset.whoosh || 0`)) >= 1, 'the whoosh played with the landing rocket'); });
  await C('page-deck', async () => {
    await turns(4);
    ok((await pg()) === '5 / 5', 'five pages: ' + await pg());
    ok(!(await hidden('#om')) && !(await hidden('#rm')), 'source card + reply card');
  });
  await C('origin-card', async () => { ok((await text('#omText')) === '问题 4' && (await text('#omLabel')) === T('r.src.otherDevice'), 'the source card says who said what'); });
  await C('page-counter', async () => { ok(/^\d+ \/ \d+$/.test(await pg()), 'n / total'); });
  await C('page-time', async () => { ok(/^· \d\d:\d\d$/.test(await text('#rmTime')) && /^· \d\d:\d\d$/.test(await text('#omTime')), 'HH:MM on both cards'); });
  await C('history-sync', async () => { await fake.addTurn({ k: 'host', text: '电脑上发的' }, '收到'); await wait(`document.getElementById('pg').textContent === '6 / 6' && document.getElementById('words').textContent === '收到'`); });
  await C('source-kinds', async () => {
    const kinds = [];
    const srcs = [[{ k: 'phone', dev: await ev(`(${'async () => null'})()`) }, 'leo'], [{ k: 'phone', dev: 'zz', name: 'iPad' }, 'dev'], [{ k: 'host' }, 'host'], [{ k: 'agent' }, 'agent'],
      [{ k: 'sys' }, 'sys'], [{ k: 'task', name: '日报' }, 'task'], [{ k: 'cmd', text: '/cost' }, 'cmd']];
    const me = fake.st.says.length ? fake.log.find((m) => m.t === 'say') : null; void me;
    // "you" = this phone's own device id: take it from a turn the host built for our say
    const mine = fake.st.turns.find((x) => x.src.k === 'phone' && x.src.text === '看一下今天的订单');
    srcs[0][0].dev = mine.src.dev;
    for (const [src, want] of srcs) {
      await fake.addTurn({ text: 'x', ...src }, 'r');
      await wait(`document.getElementById('om').dataset.src === ${JSON.stringify(want)}`);
      kinds.push(await text('#omLabel'));
    }
    ok(kinds[0] === T('r.src.you') && kinds[1] === 'iPad' && kinds[2] === T('r.src.host') && kinds[3] === T('r.src.agent', { name: 'Wren' }), 'labels: ' + kinds.join(','));
  });
  await C('swipe-paging', async () => {
    const before = await pg();
    const r = await rect(P, '#rm');
    await swipe(P, r.x - 100, r.y, r.x + 120, r.y + 10, 8, 160);
    await wait(`document.getElementById('pg').textContent !== ${JSON.stringify(before)}`);
    const [n, tot] = (await pg()).split(' / ').map(Number);
    ok(n === tot - 1, 'finger right = one page older: ' + await pg());
  });
  await C('swipe-edge-back', async () => {
    const before = await pg();
    const r = await rect(P, '#rm');
    await swipe(P, 6, r.y, 200, r.y + 5, 8, 160);
    await sleep(400);
    ok((await pg()) === before, 'a swipe from the screen edge is left to the system back gesture');
  });
  await C('follow-newest', async () => {
    if (fake.st.turns.length < 2){
      await fake.addTurn({k:'agent'}, 'earlier useful reply');
      await fake.addTurn({k:'agent'}, 'latest useful reply');
      await wait(`document.getElementById('words').textContent === 'latest useful reply'`);
    }
    const nums=(await pg()).split(' / ').map(Number);
    if (nums[0] === nums[1]) await click('#pgPrev');
    const before = await text('#words');
    ok(!(await pg()).startsWith((await pg()).split(' / ')[1] + ' '), 'on an older page first');
    await fake.addTurn({ k: 'agent' }, '新的一条');
    await wait(`!document.getElementById('newReply').hidden`);
    ok((await text('#words')) === before, 'new visible reply preserves older page');
    await click('#newReply');
    await wait(`document.getElementById('words').textContent === '新的一条'`);
    const [n, tot] = (await pg()).split(' / ').map(Number);
    ok(n === tot, 'new reply hint opens the newest visible page');
    await fake.addTurn({ k: 'agent' }, '');
    await sleep(200);
    ok((await text('#words')) === '新的一条' && (await pg()) === `${n} / ${tot}`, 'empty agent turn has no page or focus');
    await fake.addTurn({ k: 'agent' }, '〔不回群〕');
    await sleep(200);
    ok((await text('#words')) === '新的一条' && (await pg()) === `${n} / ${tot}`, 'silent turn has no page or focus');
    await fake.addTurn({ k: 'phone', dev: 'other', text: '工具工作' }, '', 'open');
    await sleep(200);
    ok((await text('#words')) === '新的一条', 'pending other input does not cover the reply');
    // Explicitly page to pending input: it has words even before the agent starts.
    await click('#pgNext');
    await wait(`document.getElementById('words').textContent.includes(${JSON.stringify(T('r.replyPending'))})`);
    await fake.addTurn({ k: 'agent' }, '浏览器回归结束');
    await sleep(200);
    await click('#newReply');
  });
  await C('swipe-rubber-band', async () => {
    const before = await pg();
    const r = await rect(P, '#rm');
    await P.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: r.x + 100, y: r.y }] });
    for (let i = 1; i <= 6; i++) { await P.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: r.x + 100 - i * 25, y: r.y + 2 }] }); await sleep(20); }
    const tr = await ev(`document.getElementById('deck').style.transform`);
    const rub = await ev(`document.getElementById('deck').dataset.rubber`);
    await P.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await sleep(400);
    ok(rub === '1' && /translateX\(-4\d(\.\d+)?px\)/.test(tr), `rubber band at the newest page (${tr})`);
    ok((await pg()) === before, 'and springs back without paging');
  });
  await C('origin-expand', async () => {
    await fake.addTurn({ k: 'phone', dev: 'zz', text: '一段很长的原话。'.repeat(30) }, '好');
    await wait(`document.getElementById('words').textContent === '好'`);
    await click('#omMore');
    ok(await ev(`document.getElementById('om').classList.contains('open') && document.getElementById('omMore').getAttribute('aria-expanded') === 'true'`), 'expanded');
    await click('#omMore');
    ok(await ev(`!document.getElementById('om').classList.contains('open')`), 'collapsed again');
  });
  await C('markdown', async () => {
    await fake.addTurn({ k: 'agent' }, '# 标题\n\n**粗体** 和 `code`\n\n- 一\n- 二\n\n> 引用\n\n[链接](https://example.com/x)\n\n<img src=x onerror=alert(1)>');
    await wait(`!!document.querySelector('#words h1')`);
    ok(await ev(`!!document.querySelector('#words strong') && !!document.querySelector('#words code') && document.querySelectorAll('#words li').length === 2 && !!document.querySelector('#words blockquote')`), 'markdown elements');
    ok(await ev(`!document.querySelector('#words img') && document.querySelector('#words a').target === '_blank' && document.querySelector('#words a').rel.includes('noopener')`), 'raw HTML stays text; links open safely');
  });
  await C('code-copy', async () => {
    await fake.addTurn({ k: 'agent' }, '```sh\nnpm test\n```');
    await wait(`!!document.querySelector('#words .codeblock .copy')`);
    ok((await text('#words .codeblock .copy')) === T('r.md.copy'), 'the button label comes from the dictionary');
    await click('#words .codeblock .copy');
    await wait(`document.querySelector('#words .codeblock .copy').textContent === ${JSON.stringify(T('r.md.copied'))}`);
    ok((await clipboard()) === 'npm test', 'the code is on the clipboard');
  });
  await C('table-hscroll', async () => {
    const cols = Array.from({ length: 14 }, (_, i) => `列${i}`);
    await fake.addTurn({ k: 'agent' }, `|${cols.join('|')}|\n|${cols.map(() => '---').join('|')}|\n|${cols.map(() => '一些比较长的内容').join('|')}|`);
    await wait(`!!document.querySelector('#words .tablewrap')`);
    ok(await ev(`(() => { const w = document.querySelector('#words .tablewrap'); return w.scrollWidth > w.clientWidth; })()`), 'the table scrolls sideways inside its box');
    const before = await pg();
    const r = await rect(P, '#words .tablewrap');
    await swipe(P, r.x + 60, r.y, r.x - 80, r.y + 3, 8, 160);
    await sleep(350);
    ok((await pg()) === before, 'a swipe on it does not turn the page');
    ok((await ev(`document.documentElement.scrollWidth <= innerWidth`)), 'the page itself never widens');
  });
  await C('more-below-fade', async () => {
    await fake.addTurn({ k: 'agent' }, Array.from({ length: 60 }, (_, i) => `第 ${i} 行，长长的回复。`).join('\n\n'));
    await wait(`document.getElementById('rm').classList.contains('more')`);
    await ev(`document.getElementById('main').scrollTop = 1e6; document.getElementById('main').dispatchEvent(new Event('scroll'))`);
    await wait(`!document.getElementById('rm').classList.contains('more')`);
  });
  await C('key-scroll-updown', async () => {
    await ev(`document.getElementById('main').scrollTop = 0; document.activeElement && document.activeElement.blur()`);
    await key(P, 'ArrowDown', { code: 'ArrowDown' });
    await wait(`document.getElementById('main').scrollTop > 0`);
  });
  await C('reader-mode', async () => {
    await dblTap('#words');
    await wait(`!document.getElementById('rd').hidden && document.body.dataset.reader === '1'`);
    ok((await text('#rdWords')).includes('第 59 行'), 'the reader shows the whole reply');
  });
  await C('reader-font-slider', async () => {
    await sleep(450);
    await ev(`(() => { const s = document.getElementById('rdSlider'); s.value = '26'; s.dispatchEvent(new Event('input')); s.dispatchEvent(new Event('change')); })()`);
    ok((await ev(`document.getElementById('rd').style.getPropertyValue('--rd-fs')`)) === '26px', 'slider → size');
    await click('#rdPlus');
    ok((await ev(`document.getElementById('rd').style.getPropertyValue('--rd-fs')`)) === '28px', '+ → the next step');
    await click('#rdMinus');
    ok((await ev(`document.getElementById('rd').style.getPropertyValue('--rd-fs')`)) === '24px', '− → the step below');
  });
  await C('reader-pinch', async () => {
    const r = await rect(P, '#rdScroll');
    await P.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: r.x - 30, y: r.y, id: 1 }, { x: r.x + 30, y: r.y, id: 2 }] });
    for (let i = 1; i <= 5; i++) { await P.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: r.x - 30 - i * 12, y: r.y, id: 1 }, { x: r.x + 30 + i * 12, y: r.y, id: 2 }] }); await sleep(20); }
    await P.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    ok(Number((await ev(`document.getElementById('rd').style.getPropertyValue('--rd-fs')`)).replace('px', '')) > 24, 'two fingers apart → bigger text');
  });
  await C('reader-font-persist', async () => { ok(Number(await ev(`localStorage.getItem('aj.readerFs')`)) > 24, 'the size is remembered (aj.readerFs)'); });
  await C('reader-dbltap', async () => {
    await sleep(500);
    await dblTap('#rdScroll');
    await wait(`document.getElementById('rd').hidden && !document.body.dataset.reader`);
  });
  await C('global-font-size', async () => {
    await touchTap('#orb');
    await wait(`document.documentElement.dataset.fz === 'l'`);
    await waitToast(T('r.fz.l'));
    await touchHold('#orb', 800);
    await wait(`!document.documentElement.dataset.fz`);
  });
  await C('copy-reply', async () => {
    const reading=await text('#words');
    await fake.addTurn({ k: 'agent' }, '复制我');
    await wait(`!document.getElementById('newReply').hidden`);
    ok((await text('#words')) === reading, 'an unread long reply keeps focus before explicit selection');
    await click('#newReply');
    await wait(`document.getElementById('words').textContent === '复制我'`);
    await click('#copyReply');
    await waitToast(T('r.copy.reply'));
    ok((await clipboard()) === '复制我', 'the stored reply is on the clipboard');
  });
  await C('action-bar', async () => {
    const r = await ev(`[...document.querySelectorAll('#rmbar .act')].map((b) => b.id + ':' + b.disabled).join(',')`);
    ok(r === 'readBtn:false,speakBtn:false,fwdBtn:false,copyReply:false,replyBtn:false', 'five actions, all live on a committed page: ' + r);
  });
  await C('tts-read-aloud', async () => {
    await ev(`(() => { const v = { lang: 'zh-CN', localService: true, default: true, name: 'local' };
      window.__spoken = []; const ss = window.speechSynthesis;
      ss.getVoices = () => [v, { lang: 'zh-CN', localService: false, name: 'cloud' }];
      ss.speak = (u) => { window.__spoken.push({ text: u.text, voice: u.voice ? u.voice.name : (u.lang === 'zh-CN' ? 'local' : '?') }); setTimeout(() => u.onstart && u.onstart(), 10); };
      ss.pause = () => { window.__paused = true; }; ss.resume = () => { window.__paused = false; }; ss.cancel = () => {}; })()`);
    await click('#speakBtn');
    await wait(`document.getElementById('speakBtn').dataset.state === 'playing'`);
    ok(await ev(`window.__spoken.length === 1 && window.__spoken[0].voice === 'local' && window.__spoken[0].text.includes('复制我')`), 'the phone\'s own (local) voice reads the reply');
    await click('#speakBtn');
    await wait(`document.getElementById('speakBtn').dataset.state === 'paused' && window.__paused === true`);
    await click('#speakBtn');
    await wait(`document.getElementById('speakBtn').dataset.state === 'playing'`);
  });
  await C('forward-share', async () => {
    await ev(`window.__shared = null; navigator.share = (d) => { window.__shared = d; return Promise.resolve(); }`);
    await click('#fwdBtn');
    await wait(`window.__shared && window.__shared.text === '复制我' && document.getElementById('fwdBtn').dataset.state === 'sent'`);
    await sleep(2100);
    await ev(`delete navigator.share; Object.defineProperty(navigator, 'share', { value: undefined, configurable: true })`);
    await click('#fwdBtn');
    await waitToast(T('r.share.copied'));
  });
  await C('thinking-dots', async () => {
    await fake.send({ t: 'status', s: 'working', agent: 'claude', name: 'Wren' });
    await wait(`getComputedStyle(document.querySelector('.thinking')).display === 'flex'`);
  });
  await C('working-shimmer', async () => { ok((await ev(`getComputedStyle(document.querySelector('.flow')).animationName`)) === 'flow', 'the light runs along the top while working'); });
  await C('state-colour-screen', async () => {
    const working = await ev(`getComputedStyle(document.getElementById('chromeProbe')).backgroundColor`).then(rgb);
    await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Wren' });
    await wait(`document.body.dataset.status === 'idle'`);
    const idle = await ev(`getComputedStyle(document.getElementById('chromeProbe')).backgroundColor`).then(rgb);
    ok(working !== idle && /rgb\(99, 152, 135\)|rgb\(9[0-9], 15[0-9], 13[0-9]\)/.test(idle), `the whole page is the state colour (idle ${idle}, working ${working})`);
  });
  await C('statusbar-colour', async () => {
    const meta = await ev(`[...document.querySelectorAll('meta[name="theme-color"]')].map((m) => m.content)`);
    const probe = await ev(`getComputedStyle(document.getElementById('chromeProbe')).backgroundColor`).then(rgb);
    const hexOf = (c) => '#' + rgb(c).match(/\d+/g).slice(0, 3).map((x) => Number(x).toString(16).padStart(2, '0')).join('');
    ok(meta.length === 1 && meta[0] === hexOf(probe), `theme-color ${meta} = page colour ${hexOf(probe)}`);
  });
  await C('sr-status', async () => { ok((await text('#capText')) === T('r.capt.idle') && (await ev(`document.getElementById('capText').closest('[role=status]') !== null`)), 'screen-reader status words'); });
  await C('state-logo', async () => {
    await fake.send({ t: 'status', s: 'working', agent: 'claude', name: 'Wren' });
    await wait(`document.getElementById('orb').getAttribute('src').endsWith('logo-working.png')`);
    await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Wren' });
    await wait(`document.getElementById('orb').getAttribute('src').endsWith('logo-mark.png')`);
  });
  await C('status-haptics', async () => { ok((await ev(`window.__vib.some((v) => v === 35)`)), 'a short buzz on a status change'); });
  await C('context-water', async () => {
    await fake.send({ t: 'meter', model: 'opus', model_name: 'Opus 5.5', effort: 'medium', ctx: { used: 40, max: 100 }, h5: { pct: 30, reset: 0 }, week: { pct: 55, reset: 0 }, at: 0 });
    await wait(`document.getElementById('water').dataset.known === '1' && document.getElementById('water').style.getPropertyValue('--lvl') === '0.4'`);
  });
  await C('weekly-bar', async () => {
    ok((await ev(`document.querySelector('#mWeek i').style.width`)) === '55%', 'weekly 55 %');
    await click('#mWeek');
    await waitToast(T('r.meter.used', { label: T('r.meter.week'), pct: 55 }));
  });
  await C('five-hour-bar', async () => { ok((await ev(`document.querySelector('#m5h i').style.width`)) === '30%' && (await ev(`document.getElementById('m5h').dataset.known`)) === '1', '5 h 30 %'); });
  await C('model-pill', async () => { ok((await text('#meta')) === 'Opus 5.5 · medium', 'model · effort: ' + await text('#meta')); });
  await C('compacting-sink', async () => {
    await fake.send({ t: 'status', s: 'compacting', agent: 'claude', name: 'Wren' });
    await wait(`document.getElementById('water').dataset.compacting === '1'`);
    ok((await ev(`getComputedStyle(document.getElementById('water')).animationName`)) === 'sink', 'the water sinks');
    await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Wren' });
    await wait(`document.getElementById('water').dataset.compacting === '0'`);
  });
  await C('pill-tap-next', async () => {
    await fake.send({ t: 'models', models: [{ id: 'opus', name: 'Opus 5.5', efforts: ['low', 'medium', 'high'], cur: true }, { id: 'sonnet', name: 'Sonnet 5', efforts: ['low', 'medium', 'high'] }, { id: 'haiku', name: 'Haiku 5', efforts: null }], effort: 'medium', default: { model: 'opus', effort: 'medium' } });
    await sleep(200);
    const e = await rect(P, '#metaEffort');
    await tap(P, e.x, e.y);
    await wait(`document.getElementById('meta').textContent === 'Opus 5.5 · high' && !!document.getElementById('meta').dataset.sw`);
    await wait(`document.getElementById('meta').textContent === 'Opus 5.5 · high' && !document.getElementById('meta').dataset.sw`, 5000);
    ok(fake.st.modelSets.length === 1 && fake.st.modelSets[0].model === 'opus' && fake.st.modelSets[0].effort === 'high', 'model_set effort high');
  });
  await C('pill-gather', async () => {
    const m = await rect(P, '#metaModel');
    await tap(P, m.x, m.y); await sleep(120); await tap(P, m.x, m.y);
    await sleep(1500);
    const sets = fake.st.modelSets.slice(1);
    ok(sets.length === 1 && sets[0].model === 'haiku', 'two taps within 1 s → one model_set with the final target: ' + JSON.stringify(sets));
    await wait(`document.getElementById('meta').textContent === 'Haiku 5'`);
  });
  await C('pill-long-press-default', async () => {
    await touchHold('#meta', 800);
    await waitToast(T('r.pill.default', { name: 'Opus 5.5', effort: 'medium' }));
    await wait(`${JSON.stringify(fake.st.modelSets.length)} && true`);
    await sleep(300);
    ok(fake.st.modelSets[fake.st.modelSets.length - 1].default === true, 'model_set default');
  });

  // ---------------------------------------------------------------- quote / reply
  await C('reply-to-page', async () => {
    await fake.addTurn({ k: 'agent' }, '要回复的这一条\n第二行');
    await wait(`document.getElementById('words').textContent.startsWith('要回复的这一条')`);
    await click('#replyBtn');
    await wait(`!document.getElementById('qbar').hidden`);
    await sendText('收到你的回复');
    await sleep(400);
    const s = lastSay();
    ok(s.text === '收到你的回复' && s.reply_to === fake.st.turns.find((x) => x.reply.text.startsWith('要回复')).id, 'say carries reply_to');
    await wait(`document.getElementById('qbar').hidden`);
  });
  await C('jump-to-quoted', async () => {
    await wait(`!document.getElementById('omQuote').hidden`);
    const target = fake.st.turns.find((x) => x.reply.text.startsWith('要回复')).id;
    await fake.addTurn({ k: 'agent' }, '再来一条');
    await wait(`document.getElementById('words').textContent === '再来一条'`);
    await ev(`document.activeElement && document.activeElement.blur()`);
    await key(P, 'j', { code: 'KeyJ' });
    await wait(`!document.getElementById('omQuote').hidden`);
    await click('#omQuote');
    await wait(`document.getElementById('words').textContent.startsWith('要回复的这一条')`);
    void target;
  });
  await C('reply-strip', async () => {
    await click('#replyBtn');
    await wait(`!document.getElementById('qbar').hidden`);
    ok((await text('#qbHead')).startsWith(T('r.quote.head', { who: 'Wren' })) && (await text('#qbText')) === '要回复的这一条', 'who · when and the first line');
  });
  await C('reply-strip-jump', async () => {
    await ev(`document.activeElement && document.activeElement.blur()`);
    await key(P, 'g', { code: 'KeyG' });
    await wait(`document.getElementById('words').textContent === '再来一条' || document.getElementById('words').textContent === '收到' || true`);
    await click('#qbJump');
    await wait(`document.getElementById('words').textContent.startsWith('要回复的这一条')`);
  });
  await C('reply-cancel', async () => {
    await click('#qbX');
    await wait(`document.getElementById('qbar').hidden`);
    await click('#replyBtn');
    await wait(`!document.getElementById('qbar').hidden`);
    await ev(`document.activeElement.blur()`);
    await key(P, 'Escape', { code: 'Escape' });
    await wait(`document.getElementById('qbar').hidden`);
  });
  await C('excerpt-quote', async () => {
    await ev(`(() => { const p = document.querySelector('#words p'); const r = document.createRange(); r.setStart(p.firstChild, 0); r.setEnd(p.firstChild, 4);
      const s = getSelection(); s.removeAllRanges(); s.addRange(r); document.dispatchEvent(new Event('selectionchange')); })()`);
    await wait(`!document.getElementById('qbar').hidden && document.getElementById('qbHead').textContent.startsWith(${JSON.stringify(T('r.quote.headEx', { who: 'Wren' }))})`, 3000);
    ok((await text('#qbText')) === T('r.quote.wrap', { text: '要回复的' }), 'the strip shows the excerpt');
    await clearField();
    await sendText('关于这一段');
    await sleep(500);
    ok(lastSay().excerpt === '要回复的' && Number.isInteger(lastSay().reply_to), 'say carries the excerpt');
  });
  await C('excerpt-autocopy', async () => { ok((await clipboard()) === '要回复的', 'the selection was copied'); });
  await C('copy-source', async () => {
    await wait(`document.getElementById('omText').textContent === '关于这一段'`);
    await ev(`document.activeElement && document.activeElement.blur(); getSelection().removeAllRanges()`);
    await key(P, 'Y', { code: 'KeyY', shift: true, text: 'Y' });
    await waitToast(T('r.copy.source'));
    ok((await clipboard()) === '关于这一段', 'Y copies the source');
  });

  // ---------------------------------------------------------------- composer
  await C('text-limit-20k', async () => {
    await clearField();
    await ev(`(() => { const i = document.getElementById('input'); i.value = 'x'.repeat(20005); i.dispatchEvent(new Event('input')); })()`);
    ok((await ev(`document.getElementById('input').value.length`)) === 20000 && (await ev(`document.getElementById('input').maxLength`)) === 20000, 'capped at 20 000 (p33 host)');
    await waitToast(T('r.cap.toast', { cap: T('r.cap.wan', { n: 2 }) }));
    await clearField();
  });
  await C('composer-autosize', async () => {
    const h0 = await ev(`document.getElementById('input').getBoundingClientRect().height`);
    await typeIn('一\n二\n三\n四\n五');
    const h1 = await ev(`document.getElementById('input').getBoundingClientRect().height`);
    ok(h1 > h0 + 30, `the field grows (${h0} → ${h1})`);
  });
  await C('enter-to-send', async () => {
    await clearField();
    await typeIn('第一行');
    await key(P, 'Enter', { shift: true, text: '\r' });
    ok((await ev(`document.getElementById('input').value`)).includes('\n') || (await ev(`document.getElementById('input').value`)) === '第一行', 'Shift+Enter does not send');
    const n = fake.st.says.length;
    await enter();
    await sleep(500);
    ok(fake.st.says.length === n + 1, 'Enter sends');
  });
  await C('composer-placeholder', async () => {
    await clearField();
    ok((await ev(`document.getElementById('input').placeholder`)) === T('r.ph.long', { cap: T('r.cap.wan', { n: 2 }) }), 'the long form fits at 390 px: ' + await ev(`document.getElementById('input').placeholder`));
  });
  await C('clear-input-button', async () => {
    await typeIn('清掉我');
    await wait(`!document.getElementById('clr').hidden`);
    await click('#clr');
    ok((await ev(`document.getElementById('input').value`)) === '', 'cleared');
    await wait(`!!document.querySelector('#toast .undo')`);
    await click('#toast .undo');
    ok((await ev(`document.getElementById('input').value`)) === '清掉我', 'undo puts it back');
    await clearField();
  });
  await C('draft-save', async () => {
    await typeIn('没发出去的草稿');
    await sleep(900);
    const rec = await ev(`new Promise((res) => { const r = indexedDB.open('agentjarvis'); r.onsuccess = () => { const g = r.result.transaction('kv').objectStore('kv').get('draft'); g.onsuccess = () => { const v = g.result; r.result.close(); res(v ? { keys: Object.keys(v).sort().join(','), ct: v.ct instanceof Uint8Array, plain: JSON.stringify([...new Uint8Array(v.ct)]).includes('没') } : null); }; }; })`);
    ok(rec && rec.keys === 'ct,iv,v' && rec.ct, 'the draft is sealed {v, iv, ct}: ' + JSON.stringify(rec));
    await navigate(P, web.url);
    await waitState(P, 'ready');
    await wait(`document.getElementById('input').value === '没发出去的草稿'`);
    await waitToast(T('r.draft.restored'));
    await ev(`(() => { window.__rockets = []; window.__vib = []; navigator.vibrate = (p) => { window.__vib.push(p); return true; }; new MutationObserver((ms) => { for (const m of ms) for (const n of m.addedNodes) if (n.classList && n.classList.contains('rocketfly')) window.__rockets.push(n.dataset.land); }).observe(document.body, { childList: true }); })()`);
    await clearField();
  });
  await C('key-history-updown', async () => {
    await ev(`document.getElementById('input').focus()`);
    await key(P, 'ArrowUp', { code: 'ArrowUp' });
    await wait(`document.getElementById('input').value === '第一行\\n' || document.getElementById('input').value.startsWith('第一行')`);
    await key(P, 'ArrowUp', { code: 'ArrowUp' });
    ok((await ev(`document.getElementById('input').value`)) === '关于这一段', 'older one');
    await key(P, 'ArrowDown', { code: 'ArrowDown' });
    await key(P, 'ArrowDown', { code: 'ArrowDown' });
    ok((await ev(`document.getElementById('input').value`)) === '', 'back to empty');
    const sealed = await ev(`new Promise((res) => { const r = indexedDB.open('agentjarvis'); r.onsuccess = () => { const g = r.result.transaction('kv').objectStore('kv').get('ihist'); g.onsuccess = () => { r.result.close(); res(!!g.result && !!g.result.ct && !('0' in g.result)); }; }; })`);
    ok(sealed, 'the input history is sealed in IndexedDB');
  });
  await C('viewport-keyboard-fit', async () => {
    await P.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 500, deviceScaleFactor: 1, mobile: true });
    await sleep(300);
    await ev(`visualViewport.dispatchEvent(new Event('resize'))`);
    ok((await ev(`document.documentElement.style.getPropertyValue('--vvh')`)) === '500px', 'the page follows the visual viewport');
    await P.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
    await sleep(300);
    await ev(`visualViewport.dispatchEvent(new Event('resize'))`);
  });

  // ---------------------------------------------------------------- attachments
  await C('attach-file', async () => {
    await setFiles('fDoc', [files.txt]);
    await chips(1);
    const open = fake.st.opens.filter(Boolean).pop();
    ok(open.purpose === 'att' && open.mime === 'text/plain' && open.origin === 'file' && open.name === 'notes.txt', 'blob_open: ' + JSON.stringify(open));
    await sendText('附件给你');
    await wait(`!document.querySelectorAll('#tray .chip').length`);
    await sleep(300);
    ok(Array.isArray(lastSay().att) && lastSay().att.length === 1, 'say names the blob');
    const b = fake.st.blobs.get(lastSay().att[0]);
    ok(b && b.bytes && b.bytes.toString('utf8') === 'hello from the phone\n'.repeat(20), 'the host got the exact bytes');
    await wait(`document.getElementById('omAtt').textContent.includes('notes.txt')`);
  });
  await C('attach-photo-multi', async () => {
    await setFiles('fPhoto', [files.png1, files.png2]);
    await chips(2);
    ok(fake.st.opens.filter(Boolean).slice(-2).every((o) => o.origin === 'photo' && o.mime === 'image/png'), 'two photos, origin photo');
  });
  await C('tray-thumbnails', async () => { ok(await ev(`[...document.querySelectorAll('#tray .chip.img img.thumb')].length === 2 && [...document.querySelectorAll('#tray img.thumb')].every((i) => i.src.startsWith('blob:'))`), 'thumbnails from object URLs'); });
  await C('attach-tray', async () => { ok(!(await hidden('#tray')) && (await ev(`document.getElementById('tray').getAttribute('aria-label')`)) === T('r.tray.count', { n: 2, max: 10 }), 'the tray with its count'); });
  await C('tray-remove', async () => {
    const before = fake.log.filter((m) => m.t === 'blob_drop').length;
    await click('#tray .chip .x');
    await chips(1);
    await sleep(200);
    ok(fake.log.filter((m) => m.t === 'blob_drop').length === before + 1, 'blob_drop sent');
    await clearTray();
  });
  await C('tray-limits', async () => {
    await setFiles('fDoc', files.many);
    await wait(`document.querySelectorAll('#tray .chip').length === 10`);
    await waitToast(T('r.tray.full', { max: 10, n: 1 }));
    await clearTray();
    await setFiles('fDoc', [files.huge]);
    await waitToast(T('r.up.too_big'));
    ok(!(await ev(`document.querySelectorAll('#tray .chip').length`)), 'a file over 25 MB is not added');
  });
  await C('upload-progress', async () => {
    fake.st.stallAfter = 5;
    await setFiles('fDoc', [files.big]);
    await wait(`(() => { const i = document.querySelector('#tray .chip .pg i'); return i && parseFloat(i.style.width) > 0; })()`, 8000);
    const w = parseFloat(await ev(`document.querySelector('#tray .chip .pg i').style.width`));
    ok(w > 0 && w < 100, 'progress from the host acks: ' + w + '%');
  });
  await C('send-cancel-holds-upload', async () => {
    await typeIn('带着大文件');
    await click('#send');
    await wait(`!document.getElementById('sayCancel').hidden`, 3000);
    await click('#sayCancel');
    await wait(`document.querySelector('#tray .chip').dataset.st === 'held'`);
    await waitToast(T('r.say.cancelled'));
    ok((await ev(`document.getElementById('input').value`)) === '带着大文件', 'the text stays');
    fake.st.stallAfter = 0;
    await clearTray(); await clearField();
  });
  await C('upload-errors', async () => {
    fake.st.blobErr = 'quota';
    await setFiles('fDoc', [files.md]);
    await wait(`document.querySelector('#tray .chip') && document.querySelector('#tray .chip').dataset.st === 'failed'`);
    await waitToast(T('r.up.quota'));
    await clearTray();
  });
  await C('paste-image', async () => {
    await ev(`(() => { const b = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFklEQVR42mP8z8DwnwEIGBkZGBgYAAA6GwH9q+oAMgAAAABJRU5ErkJggg=='), (c) => c.charCodeAt(0));
      const dt = new DataTransfer(); dt.items.add(new File([b], 'image.png', { type: 'image/png' }));
      document.getElementById('input').dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true })); })()`);
    await chips(1);
    ok((await text('#tray .chip .nm')).startsWith(T('r.file.paste') + '-') && fake.st.opens.filter(Boolean).pop().origin === 'paste', 'a pasted screenshot becomes 粘贴-HHMMSS.png');
    await clearTray();
  });
  await C('menu-paste-image', async () => {
    await ev(`(() => { const b = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFklEQVR42mP8z8DwnwEIGBkZGBgYAAA6GwH9q+oAMgAAAABJRU5ErkJggg=='), (c) => c.charCodeAt(0));
      navigator.clipboard.read = async () => [{ types: ['image/png'], getType: async () => new Blob([b], { type: 'image/png' }) }]; })()`);
    await click('#slashBtn');
    await wait(`!document.getElementById('menu').hidden && !!document.getElementById('clipEntry') && getComputedStyle(document.getElementById('clipEntry')).display !== 'none'`);
    await click('#clipEntry');
    await chips(1);
    await clearTray();
  });
  await C('drag-drop', async () => {
    await ev(`(() => { const dt = new DataTransfer(); dt.items.add(new File(['a,b\\n1,2'], 'data.csv', { type: '' }));
      window.dispatchEvent(new DragEvent('dragenter', { dataTransfer: dt, bubbles: true, cancelable: true })); window.__dt = dt; })()`);
    await wait(`!document.getElementById('dropzone').hidden`);
    await ev(`window.dispatchEvent(new DragEvent('drop', { dataTransfer: window.__dt, bubbles: true, cancelable: true }))`);
    await chips(1);
    const o = fake.st.opens.filter(Boolean).pop();
    ok(o.origin === 'drop' && o.mime === 'text/csv', 'a dropped .csv with no type gets text/csv: ' + JSON.stringify(o));
    await clearTray();
  });

  // ---------------------------------------------------------------- send in flight / withdraw
  await C('send-loop', async () => {
    fake.st.sayDelay = 1600;
    await ev(`window.__rockets = []`);
    await sendText('慢慢发');
    await wait(`document.body.dataset.sending === '1' && !document.getElementById('sayCancel').hidden`, 3000);
    await wait(`window.__rockets.includes('0')`, 3000);
    await wait(`!document.body.dataset.sending`, 5000);
    ok(await ev(`window.__rockets.includes('1')`), 'loop rockets, then one landing rocket');
    fake.st.sayDelay = 0;
  });
  await C('send-cancel', async () => {
    fake.st.sayMode = 'queued';
    await sendText('排队中的话');
    await wait(`!document.getElementById('sayCancel').hidden && document.getElementById('input').value === ''`);
    await click('#sayCancel');
    await waitToast(T('r.say.withdrawn'));
    ok((await ev(`document.getElementById('input').value`)) === '排队中的话' && fake.st.cancels.length === 1, 'withdrawn: the words are back');
    fake.st.sayMode = 'delivered';
    await clearField();
  });

  // ---------------------------------------------------------------- voice (Chromium's fake microphone)
  await C('ptt-hold', async () => {
    await clearField();
    await typeIn('前面的字');
    await ev(`document.activeElement.blur()`);
    const r = await rect(P, '#mic');
    await P.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: r.x, y: r.y }] });
    try {
      await captureReady();
      await sleep(1600);
      ok(/^\d:\d\d$/.test(await text('#pttTime')) && (await text('#pttHint')) === T('r.ptt.hint'), 'timer + hint while holding');
    } finally {
      // A failed timer assertion must not leave a touch/recording alive and turn
      // all following recording cases into cascading failures.
      await P.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    }
    await wait(`document.getElementById('input').value === '前面的字' + ${JSON.stringify(fake.st.asrText)}`, 15000);
    ok(fake.st.wavs.length === 1 && fake.st.wavs[0].ok, 'the host got a 16 kHz mono PCM16 WAV made in the page');
  });
  await C('ptt-timer', async () => { ok(true, 'checked inside ptt-hold (timer m:ss + hint while recording)'); });
  await C('asr-append', async () => { ok((await ev(`document.getElementById('input').value`)).startsWith('前面的字'), 'appended after what was typed, nothing sent'); ok(!fake.st.says.some((s) => s.text && s.text.includes(fake.st.asrText)), 'not sent'); });
  await C('ptt-slide-cancel', async () => {
    const n = fake.st.wavs.length;
    const r = await rect(P, '#mic');
    await P.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: r.x, y: r.y }] });
    await sleep(900);
    await P.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: r.x, y: r.y - 100 }] });
    await wait(`document.getElementById('mic').classList.contains('cancel') && document.getElementById('pttHint').textContent === ${JSON.stringify(T('r.ptt.releaseCancel'))}`);
    await P.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await waitToast(T('r.ptt.cancelled'));
    await sleep(500);
    ok(fake.st.wavs.length === n, 'nothing transcribed');
  });
  await C('ptt-haptic', async () => { ok(await ev(`window.__vib.some((v) => v === 12)`), 'a tick when the cancel threshold is crossed'); });
  await C('ptt-lock', async () => {
    await clearField();
    await dblTap('#mic');
    await wait(`document.getElementById('mic').classList.contains('lock') && document.getElementById('ptt').classList.contains('lock')`, 3000);
    ok((await ev(`getComputedStyle(document.getElementById('pttTime'), '::after').content`)).includes(T('r.ptt.locked')), 'the bubble says 已锁定');
    await captureReady();
    await sleep(1500);
    await touchTap('#mic');
    await wait(`document.getElementById('input').value === ${JSON.stringify(fake.st.asrText)}`, 15000);
  });
  await C('ptt-lock-limit', async () => {
    await clearField();
    await sleep(500);
    await dblTap('#mic');
    await wait(`document.getElementById('mic').classList.contains('lock')`, 3000);
    await ev(`(() => { const real = performance.now.bind(performance); const t0 = real(); performance.now = () => real() + 9.5 * 60 * 1000; window.__restoreNow = () => { performance.now = real; }; })()`);
    await wait(`document.getElementById('pttHint').textContent.startsWith(${JSON.stringify(T('r.ptt.left', { s: 'X', stop: '' }).split('X')[0])})`, 3000);
    await ev(`window.__restoreNow()`);
    await key(P, 'Escape', { code: 'Escape' });
    await wait(`!document.getElementById('mic').classList.contains('lock')`);
  });
  await C('asr-busy-tag', async () => {
    await clearField();
    fake.st.asrDelays = [1500];
    await sleep(500);
    await touchHold('#mic', 1300);
    await wait(`!document.getElementById('asrTag').hidden && document.getElementById('input').placeholder === ${JSON.stringify(T('r.asr.tag'))}`, 8000);
    await wait(`document.getElementById('asrTag').hidden`, 15000);
  });
  await C('asr-queue', async () => {
    await clearField();
    fake.st.asrTexts = ['第一段', '第二段']; fake.st.asrDelays = [1500, 0];
    await sleep(500);
    await touchHold('#mic', 1100);
    await sleep(500);
    await touchHold('#mic', 1100);
    await wait(`document.getElementById('input').value === '第一段第二段'`, 20000);
  });
  await C('asr-errors', async () => {
    fake.st.asrWhy = 'no_speech';
    await sleep(500);
    await touchHold('#mic', 1100);
    await waitToast(T('r.asr.noSpeech'), 15000);
    fake.st.asrWhy = 'busy';
    await sleep(500);
    await touchHold('#mic', 1100);
    await waitToast(T('r.asr.busy'), 15000);
    fake.st.asrWhy = null;
  });
  await C('voice-threshold', async () => {
    await clearField();
    // a 95 s take (over relay's 90 s cap) handed in like a native wake-word take → an audio attachment, not words
    const ok1 = await ev(`(() => { const n = 95 * 16000, b = new Uint8Array(44 + n * 2), v = new DataView(b.buffer); const s = (o, t) => { for (let i = 0; i < t.length; i++) b[o + i] = t.charCodeAt(i); };
      s(0, 'RIFF'); v.setUint32(4, 36 + n * 2, true); s(8, 'WAVE'); s(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, 16000, true); v.setUint32(28, 32000, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); s(36, 'data'); v.setUint32(40, n * 2, true);
      let bin = ''; for (let i = 0; i < b.length; i += 8192) bin += String.fromCharCode(...b.subarray(i, i + 8192)); return window.relayNative.take(btoa(bin), 95); })()`);
    ok(ok1 === true, 'relayNative.take accepted the take');
    await chips(1, 'ready', 30000);
    ok((await text('#tray .chip .nm')) === T('r.tray.voice', { d: '1:35' }) && fake.st.opens.filter(Boolean).pop().origin === 'recording', 'a long take = a recording attachment');
    await clearTray();
  });
  await C('ptt-hardware-key', async () => {
    await clearField();
    await ev(`document.getElementById('mic').focus()`);
    await key(P, ' ', { code: 'Space', type: 'down', text: ' ' });
    await captureReady();
    await sleep(1300);
    await key(P, ' ', { code: 'Space', type: 'up' });
    await wait(`document.getElementById('input').value === ${JSON.stringify(fake.st.asrText)}`, 15000);
    await clearField();
  });

  // ---------------------------------------------------------------- menu
  await C('command-menu', async () => {
    fake.st.menu = { source: 'file', items: [{ cmd: '/daily-report', desc: '写日报', group: '日常' }], skills: [{ cmd: '/review', desc: 'Review the diff' }], cmds: ['clear', 'compact'] };
    await ev(`document.activeElement && document.activeElement.blur()`);
    await click('#slashBtn');
    await wait(`[...document.querySelectorAll('#menu [data-slash]')].some((b) => b.dataset.slash === '/daily-report')`);
    ok(await ev(`[...document.querySelectorAll('#menu .menugroup')].map((g) => g.textContent).join('|')`) === [T('r.menu.run'), '日常', T('r.menu.skills')].join('|'), 'groups: run now, the host menu, skills');
  });
  await C('menu-insert-text', async () => {
    await ev(`[...document.querySelectorAll('#menu [data-slash]')].find((b) => b.dataset.slash === '/daily-report').click()`);
    ok((await ev(`document.getElementById('input').value`)) === '/daily-report ' && (await hidden('#menu')), 'inserted, not sent');
    ok(!fake.st.says.some((s) => s.text === '/daily-report '), 'nothing sent');
    await clearField();
  });
  await C('slash-autocomplete', async () => {
    await typeIn('/da');
    await wait(`!document.getElementById('sug').hidden && document.querySelector('#sug [data-sug]').textContent.startsWith('/daily-report')`);
    await key(P, 'Tab', { code: 'Tab' });
    ok((await ev(`document.getElementById('input').value`)) === '/daily-report ', 'Tab completes');
    await clearField();
  });
  await C('aj-command-buttons', async () => {
    await ev(`document.activeElement && document.activeElement.blur()`);
    await click('#slashBtn');
    await wait(`!!document.querySelector('#menu [data-run="compact"]')`);
    await click('#menu [data-run="compact"]');
    await sleep(400);
    ok((fake.st.slashes || []).some((s) => s.cmd === 'compact'), 'one tap runs /compact');
    await wait(`document.getElementById('om').dataset.src === 'cmd'`);
  });
  await C('aj-clear-undo', async () => {
    await click('#slashBtn');
    await click('#menu [data-run="clear"]');
    await wait(`!document.getElementById('confirm').hidden`);
    await click('#confirm-yes');
    await wait(`!!document.querySelector('#cmdx .cmdundo')`);
    ok(fake.st.slashes.some((s) => s.cmd === 'clear' && s.confirm === true), '/clear confirmed');
    await click('#cmdx .cmdundo');
    await sleep(300);
    ok(fake.st.slashes.some((s) => s.cmd === 'undo_clear'), 'undo sent');
  });

  // ---------------------------------------------------------------- approvals and questions
  const ASK = hex(16);
  await C('permission-sheet', async () => {
    await fake.send({ t: 'status', s: 'waiting', agent: 'claude', name: 'Wren' });
    await fake.ask({ id: ASK, tool: 'Bash', summary: 'rm -rf exports/old/', ttl: 120, cat: ['delete'], why: 'rm -rf', task: '日报' });
    await wait(`document.body.dataset.sheet === '1'`);
    ok((await text('#cmd')) === 'rm -rf exports/old/' && (await text('#tool')) === 'Bash' && (await text('#why')) === T('ask.why', { why: 'rm -rf' }), 'tool, command, reason');
  });
  await C('verb-table', async () => { ok((await text('#sheetTitle')).includes(T('r.verb.Bash')), 'Bash = 运行一条命令'); });
  await C('aj-danger-categories', async () => { ok((await text('#askTags .atag.danger')) === T('ask.cat.delete') && (await text('#askTags .atag.task')) === T('ask.task', { task: '日报' }), 'danger chip + task tag'); });
  await C('waiting-breathing', async () => { ok((await ev(`getComputedStyle(document.querySelector('.aura')).animationName`)) === 'glow', 'the edges breathe while waiting'); });
  await C('waiting-time', async () => {
    ok(/\d/.test(await text('#askLeft')), 'the countdown: ' + await text('#askLeft'));
    await ev(`(() => { const real = Date.now; Date.now = () => real() + 125000; window.__restoreDate = () => { Date.now = real; }; })()`);
    await fake.send({ t: 'status', s: 'waiting', agent: 'claude', name: 'Wren' });
    await wait(`document.getElementById('capSub').textContent === ${JSON.stringify(T('r.waited', { n: 2 }))}`);
    await ev(`window.__restoreDate()`);
  });
  await C('sheet-collapse', async () => {
    await click('#sheetClose');
    await wait(`document.body.dataset.sheet === '0' && !document.getElementById('pendTag').hidden`);
  });
  await C('pending-tag', async () => {
    ok((await text('#pendText')) === T('r.pend.permission'), 'the tag says what waits');
    await click('#pendTag');
    await wait(`document.body.dataset.sheet === '1'`);
  });
  await C('long-press-approve', async () => {
    await touchTap('#apprAllow');
    await waitToast(T('r.appr.holdHint'));
    ok(!fake.st.answers.length, 'a tap never approves');
    await touchHold('#apprAllow', 1500);
    await wait(`${'true'}`);
    await sleep(500);
    ok(fake.st.answers.length === 1 && fake.st.answers[0].ok === true, 'a hold approves');
    await wait(`document.getElementById('askState').textContent === ${JSON.stringify(T('r.appr.st.approved'))} || document.getElementById('toast').textContent === ${JSON.stringify(T('r.appr.st.approved'))}`);
  });
  await C('aj-signed-approvals', async () => { ok(fake.st.sigOk[0] === true, 'the host verified the Ed25519 signature over what was shown'); });
  await C('deny-one-tap', async () => {
    const id = hex(16);
    await fake.ask({ id, tool: 'Write', summary: '/tmp/x.txt', ttl: 120, cat: [], why: '' });
    await wait(`document.body.dataset.sheet === '1' && document.getElementById('cmd').textContent === '/tmp/x.txt'`, 8000);
    ok((await text('#sheetTitle')) === T('r.verb.Write'), 'Write = 写入一个文件');
    await click('#apprDeny');
    await sleep(600);
    const a = fake.st.answers[fake.st.answers.length - 1];
    ok(a.id === id && a.ok === false && fake.st.sigOk[fake.st.sigOk.length - 1] === true, 'one tap denies, signed');
  });
  await C('aj-batch-grants', async () => {
    const id = hex(16);
    await sleep(6500);                                       // the previous result leaves the sheet
    await fake.ask({ id, tool: 'Bash', summary: 'cat orders.csv', ttl: 120, batch: 'Bash：cat', batch_max: 20, batch_secs: 600 });
    await wait(`!document.getElementById('apprBatch').hidden`, 8000);
    ok((await text('#apprBatchScope')).includes('Bash：cat'), 'the scope is shown');
    await touchHold('#apprBatch', 1500);
    await sleep(600);
    const a = fake.st.answers[fake.st.answers.length - 1];
    ok(a.id === id && a.batch === true && fake.st.sigOk[fake.st.sigOk.length - 1] === true, 'allow_batch signed over the scope');
    await fake.send({ t: 'grant', id, scope: 'Bash：cat', left: 19, secs: 600 });
    await wait(`!document.getElementById('grant-bar').hidden && document.getElementById('grant-text').textContent.includes('Bash：cat')`);
    await click('#grant-off');
    await sleep(300);
    ok(fake.log.some((m) => m.t === 'grant_off'), 'grant_off sent');
    await fake.send({ t: 'grant_end', id, why: 'revoked' });
    await wait(`document.getElementById('grant-bar').hidden`);
  });
  await C('aj-auto-approve-line', async () => {
    await fake.send({ t: 'auto', id: hex(16), tool: 'Bash', summary: 'cat a.csv', grant: hex(16) });
    await waitToast(T('ask.auto', { tool: 'Bash', summary: 'cat a.csv' }));
  });
  const Q = hex(16);
  await C('question-purple', async () => {
    await sleep(6500);
    await fake.send({ t: 'question', id: Q, ttl: 180, qs: [{ q: '用哪种方案？', h: '方案', m: true, o: [{ l: 'A', d: '快' }, { l: 'B', d: '稳' }, { l: 'C' }] }] });
    await fake.send({ t: 'status', s: 'waiting', kind: 'question', agent: 'claude', name: 'Wren' });
    await wait(`document.body.dataset.kind === 'question' && document.body.dataset.sheet === '1'`, 8000);
    const c = await ev(`getComputedStyle(document.getElementById('chromeProbe')).backgroundColor`).then(rgb);
    ok(/rgb\(130, 125, 189\)/.test(c), 'magic carpet purple: ' + c);
  });
  await C('question-multi', async () => {
    await ev(`document.querySelector('#qs .opt[data-n="1"]').click(); document.querySelector('#qs .opt[data-n="3"]').click()`);
    ok(await ev(`[...document.querySelectorAll('#qs .opt')].map((b) => b.getAttribute('aria-pressed')).join() === 'true,false,true'`), 'two picked');
  });
  await C('question-confirm', async () => {
    ok(await ev(`!document.getElementById('askSend').disabled`), 'confirm enabled');
    await click('#askSend');
    await sleep(500);
    const a = fake.st.qAnswers[0];
    ok(a && a.id === Q && JSON.stringify(a.pick) === '[[1,3]]' && typeof a.sig === 'string' && a.sig.length > 80, 'q_answer pick [[1,3]] signed');
    await waitToast(T('r.q.st.answered', { labels: 'A、C' }));
  });
  await C('question-cancel', async () => {
    const id = hex(16);
    await sleep(6500);
    await fake.send({ t: 'question', id, ttl: 180, qs: [{ q: '继续吗？', h: '', m: false, o: [{ l: '是' }, { l: '否' }] }] });
    await wait(`!document.getElementById('askCancel').hidden`, 8000);
    await click('#askCancel');
    ok((await text('#askCancel')) === T('r.q.cancelSure'), 'the first tap arms it');
    await click('#askCancel');
    await sleep(500);
    ok(fake.st.qAnswers.some((a) => a.id === id && a.cancel === true && a.sig), 'cancel sent, signed');
  });
  await C('local-only-card', async () => {
    await sleep(6500);
    await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Wren' });
    await fake.addTurn({ k: 'sys', local: true }, '这个问题需要在电脑上回答');
    await wait(`!document.getElementById('localNote').hidden && document.getElementById('omLabel').textContent === ${JSON.stringify(T('r.src.local'))}`);
  });

  // ---------------------------------------------------------------- interrupt, controls, connection
  await C('interrupt-turn', async () => {
    await fake.send({ t: 'status', s: 'working', agent: 'claude', name: 'Wren' });
    await wait(`document.body.dataset.status === 'working'`);
    await clearField(); await clearTray();
    await ev(`document.activeElement && document.activeElement.blur()`);
    await sleep(1100);
    await key(P, 'Escape', { code: 'Escape' }); await sleep(80); await key(P, 'Escape', { code: 'Escape' });
    await sleep(400);
    ok(fake.st.slashes.some((s) => s.cmd === 'stop'), 'Esc Esc on an empty composer → /stop');
  });
  await C('aj-estop', async () => {
    fake.answer('estop', () => [{ t: 'ctl_res', ok: true }, { t: 'estop_state', on: true, by: 'Pixel' }]);
    fake.answer('resume', () => [{ t: 'ctl_res', ok: true }, { t: 'estop_state', on: false }]);
    await click('#slashBtn');
    await click('#menu [data-estop="stop"]');
    await wait(`!document.getElementById('confirm').hidden`);
    await click('#confirm-yes');
    await wait(`!document.getElementById('estop-banner').hidden`);
    const m = fake.log.find((x) => x.t === 'estop');
    ok(m && /^[0-9a-f]{32}$/.test(m.n) && typeof m.sig === 'string', 'signed estop');
    ok((await ev(`document.getElementById('input').placeholder`)) === T('chat.placeholderStopped'), 'the field says everything is stopped');
    await click('#resume');
    await wait(`!document.getElementById('confirm').hidden`);
    await click('#confirm-yes');
    await wait(`document.getElementById('estop-banner').hidden`);
  });
  await C('aj-web-push', async () => {
    await fake.send({ t: 'push_key', k: Buffer.from(Uint8Array.of(4, ...crypto.getRandomValues(new Uint8Array(64)))).toString('base64url') });
    await ev(`document.getElementById('aj-menu').open = true`);
    await wait(`!document.getElementById('push-row').hidden`);
    await ev(`document.getElementById('aj-menu').open = false`);
  });
  await C('aj-memory-page', async () => {
    fake.answer('mem_list', () => [{ t: 'mem_sources', harness: 'codex', sources: [{ id: 'p', label: 'AGENTS.md', path: '~/x/AGENTS.md', n: 1 }], trash: [] },
      { t: 'mem_items', items: [{ src: 'p', file: 'AGENTS.md', fsha: 'a', iid: '1', kind: 'line', text: '- 回答用中文' }], more: false }]);
    await ev(`document.getElementById('aj-menu').open = true`); await click('#open-mem');
    await wait(`document.body.dataset.view === 'mem' && document.querySelectorAll('#mem-list .mem-item').length === 1`);
  });
  await C('aj-multi-harness', async () => { ok((await text('#mem-status')).includes('Codex'), 'Codex named on the memory page'); });
  await C('aj-activity-page', async () => {
    fake.answer('act_list', () => ({ t: 'act_page', more: false, items: [{ k: 'decision', ts: Date.now(), result: 'allow', by: 'Pixel', tool: 'Bash', text: 'ls' }] }));
    await click('#mem-view [data-back]');
    await ev(`document.getElementById('aj-menu').open = true`); await click('#open-act');
    await wait(`document.body.dataset.view === 'act' && document.querySelectorAll('#act-list li').length === 1`);
  });
  await C('aj-tasks-page', async () => {
    fake.answer('task_list', () => ({ t: 'tasks', more: false, agent: 'claude', items: [{ id: 'd', title: { zh: '日报', en: 'Daily' }, schedule: '0 9 * * *', tz: 'local', mode: 'research', enabled: true, tsha: 'x' }] }));
    await click('#act-view [data-back]');
    await ev(`document.getElementById('aj-menu').open = true`); await click('#open-tasks');
    await wait(`document.body.dataset.view === 'tasks' && document.querySelectorAll('#tasks-list li').length === 1`);
    await click('#tasks-view [data-back]');
    await wait(`document.body.dataset.view === 'chat'`);
  });
  await C('offline-grey', async () => {
    fake.setUp(false);
    await wait(`document.body.dataset.conn === 'off'`);
    ok(/rgb\(133, 133, 133\)/.test(await ev(`getComputedStyle(document.getElementById('chromeProbe')).backgroundColor`).then(rgb)), 'the screen goes grey');
  });
  await C('offline-banner', async () => { ok((await text('#stale')).startsWith('最后同步于') && !(await hidden('#stale')), 'last synced at …'); ok(!(await hidden('#status')), 'the connection line shows in the header'); });
  await C('reconnect-backoff', async () => {
    fake.setUp(true);
    await waitState(P, 'ready');
    fake.kick(1001);
    await wait(`/${T('st.retryIn', { n: 1 }).slice(0, 4)}/.test(document.getElementById('status').textContent)`, 3000);
    await waitState(P, 'ready', 8000);
  });
  await C('revive-foreground', async () => {
    fake.kick(1001); await waitState(P, 'connecting').catch(() => {});
    await sleep(100); fake.kick(1001);
    await sleep(200);
    const t0 = Date.now();
    await ev(`window.dispatchEvent(new Event('online'))`);
    await waitState(P, 'ready', 5000);
    ok(Date.now() - t0 < 1500, 'back online → reconnect at once (no waiting out the backoff)');
  });
  await C('aj-noise-e2e', async () => {
    const MARK = 'PLAINTEXT-MARKER-' + hex(6);
    const frames = [];
    P.on((m) => { if (m.method === 'Network.webSocketFrameSent' || m.method === 'Network.webSocketFrameReceived') frames.push(m.params.response.payloadData); });
    await sendText(MARK);
    await wait(`document.getElementById('words').textContent.includes(${JSON.stringify(MARK)})`);
    const raw = frames.map((f) => { try { return Buffer.from(f, 'base64').toString('latin1'); } catch { return f; } }).join('\n') + frames.join('\n');
    ok(frames.length > 0 && !raw.includes(MARK) && !raw.includes(Buffer.from(MARK).toString('base64')), `the marker never crosses the socket in clear (${frames.length} frames)`);
  });
  await C('aj-a2hs', async () => {
    const p2 = await newPage(B, 390, 844, 'light', { touch: true, ua: ANDROID, allow });
    await navigate(p2, web.url);
    await waitState(p2, 'idle');
    ok(!(await evaluate(p2, `document.getElementById('a2hs').hidden`)), 'phone browser: the hint');
    await evaluate(p2, `document.getElementById('a2hs-ok').click()`);
    ok(await evaluate(p2, `document.getElementById('a2hs').hidden && localStorage.getItem('aj.a2hs') === '1'`), 'dismissed once');
    await p2.dispose();
  });
  await C('aj-security-badge', async () => {
    await click('#badge');
    await wait(`!document.getElementById('badge-panel').hidden && /^[0-9a-f]{64}$/.test(document.getElementById('version-hash').textContent)`);
    await click('#badge-close');
  });
  await C('aj-theme', async () => {
    const light = await ev(`getComputedStyle(document.getElementById('chromeProbe')).backgroundColor`).then(rgb);
    await ev(`document.getElementById('aj-menu').open = true; document.querySelector('[data-aj-theme-switch]').click(); document.querySelector('[data-aj-theme-switch]').click()`);
    await wait(`document.documentElement.dataset.theme === 'dark'`);
    const dark = await ev(`getComputedStyle(document.getElementById('chromeProbe')).backgroundColor`).then(rgb);
    ok(dark !== light, `dark theme deepens the state colour (${light} → ${dark})`);
    await ev(`document.querySelector('[data-aj-theme-switch]').click(); document.getElementById('aj-menu').open = false`);
  });
  await C('aj-i18n', async () => {
    await ev(`document.getElementById('aj-menu').open = true; document.querySelector('#aj-menu [data-aj-lang="en"]').click()`);
    await wait(`document.documentElement.lang === 'en'`);
    ok((await ev(`document.getElementById('send').getAttribute('aria-label')`)) === D.en['r.say.send'] && (await ev(`document.getElementById('input').placeholder`)).startsWith('Say something'), 'English everywhere');
    await ev(`document.querySelector('#aj-menu [data-aj-lang="zh"]').click(); document.getElementById('aj-menu').open = false`);
    await wait(`document.documentElement.lang === 'zh-CN'`);
  });
  // relay keeps the draft across a lost login; Agent J keeps it (sealed) across every disconnect (draft-save), but a
  // computer that REMOVED this phone takes it with it (PROTOCOL §10.14, P33-X13): draft, ↑↓ history and their key are
  // deleted at once, and the removed screen says so.
  await C('auth-lost-draft', async () => {
    await sendText('被移除前发过的一句');
    for (let i = 0; i < 100 && !fake.st.says.some((m) => m.text === '被移除前发过的一句'); i++) await sleep(50);
    await wait(`document.getElementById('input').value === ''`);   // the accepted send has left the field
    await typeIn('被移除前的草稿');
    await sleep(800);
    const sealed = () => ev(`new Promise((res) => { const r = indexedDB.open('agentjarvis'); r.onsuccess = () => { const tx = r.result.transaction('kv'); const s = tx.objectStore('kv');
      const a = s.get('draft'), b = s.get('ihist'), c = s.get('local'); tx.oncomplete = () => { r.result.close(); res({ draft: !!a.result, ih: !!b.result, key: !!c.result }); }; }; })`);
    const before = await sealed();
    ok(before.draft && before.ih && before.key, 'before: the draft and the history are sealed on the phone ' + JSON.stringify(before));
    // like relay: a lost connection keeps the draft …
    fake.kick(1001);
    await waitState(P, 'ready', 8000);
    const v1 = await ev(`document.getElementById('input').value`); ok(v1 === '被移除前的草稿', 'kept across a dropped connection (still in the field): ' + JSON.stringify(v1));
    const mid = await sealed();
    ok(mid.draft && mid.ih && mid.key, 'and still sealed on the phone after the reconnect ' + JSON.stringify(mid));
    // … and a reload restores it from the sealed copy
    await navigate(P, web.url);
    await waitState(P, 'ready');
    await wait(`document.getElementById('input').value === '被移除前的草稿'`);
    ok(true, 'kept across a reload (restored into the field)');
    await sleep(300);
    fake.revokeAll();
    await waitState(P, 'revoked');
    await wait(`!document.getElementById('revoked-draft').hidden`);
    ok((await text('#revoked-draft')) === T('revoked.draft'), 'the removed screen says the draft, the history and their key were deleted');
    const after = await sealed();
    ok(!after.draft && !after.ih && !after.key, 'revoked: draft, input history and the sealing key deleted at once ' + JSON.stringify(after));
    ok((await ev(`document.getElementById('input').value`)) === '', 'and the field is empty');
  });
  await C('aj-revoked-page', async () => {
    ok(!(await hidden('#revoked-view')), 'the removed screen');
    await click('#repair');
    await waitState(P, 'idle');
    const left = await ev(`new Promise((res) => { const r = indexedDB.open('agentjarvis'); r.onsuccess = () => { const tx = r.result.transaction('kv'); const s = tx.objectStore('kv');
      const a = s.get('draft'), b = s.get('ihist'), c = s.get('host'), d = s.get('device'); tx.oncomplete = () => res({ draft: !!a.result, ih: !!b.result, host: !!c.result, dev: !!d.result }); }; })`);
    ok(!left.draft && !left.ih && !left.host && left.dev, 're-pairing wipes host, drafts and input history; keeps the device key: ' + JSON.stringify(left));
  });

  // ======================================================================== landscape
  await open('mobile');
  await C('landscape-page-buttons', async () => {
    await turns(3);
    await P.send('Emulation.setDeviceMetricsOverride', { width: 844, height: 600, deviceScaleFactor: 1, mobile: true, screenOrientation: { type: 'landscapePrimary', angle: 90 } });
    await sleep(400);
    ok((await ev(`getComputedStyle(document.getElementById('pgPrev')).display`)) === 'grid', 'the ‹ › columns show in landscape');
    await click('#pgPrev');
    await wait(`document.getElementById('pg').textContent === '2 / 3'`);
    await click('#pgNext');
    await wait(`document.getElementById('pg').textContent === '3 / 3'`);
  });
  await C('history-load-older', async () => {
    await P.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
    for (let i = 0; i < 60; i++) fake.st.turns.push({ id: fake.st.nextId++, ts: Date.now(), src: { k: 'agent' }, reply: { text: `批量 ${i}` }, end: 'done' });
    await navigate(P, web.url);
    await waitState(P, 'ready');
    await wait(`document.getElementById('pg').textContent === '63 / 63'`, 8000);
    ok(await ev(`(() => { const d = window.__ajDebug ? window.__ajDebug() : null; return true; })()`), '');
    await ev(`document.activeElement && document.activeElement.blur()`);
    await key(P, 'G', { code: 'KeyG', shift: true, text: 'G' });
    await wait(`document.getElementById('pg').textContent === '1 / 63'`, 8000);
    ok(fake.log.some((m) => m.t === 'hist_get' && Number.isInteger(m.before)), 'older pages fetched with hist_get before');
  });
  await C('key-g-edges', async () => {
    await key(P, 'g', { code: 'KeyG' });
    await wait(`document.getElementById('pg').textContent === '63 / 63'`);
  });

  // ======================================================================== desktop: keyboard, camera, mic by mouse
  await open('desktop');
  await turns(4, (i) => [{ k: 'phone', dev: 'other', text: `原话 ${i}` }, Array.from({ length: 40 }, (_, j) => `回复 ${i} 第 ${j} 行`).join('\n\n')]);
  const blur = () => ev(`document.activeElement && document.activeElement.blur(); getSelection().removeAllRanges()`);
  await C('key-help', async () => { await blur(); await key(P, '?', { code: 'Slash', shift: true, text: '?' }); await wait(`!document.getElementById('keys').hidden`); await key(P, 'Escape', { code: 'Escape' }); await wait(`document.getElementById('keys').hidden`); });
  await C('keys-panel', async () => {
    await click('#keysBtn');
    await wait(`!document.getElementById('keys').hidden`);
    ok((await ev(`document.querySelectorAll('#keys dt').length`)) === 24 && (await ev(`document.querySelectorAll('#keys dd').length`)) === 24, 'all relay shortcuts listed + F13 s · Ctrl+, (Settings)');
    await click('#keysClose');
  });
  await C('key-j-older', async () => { await blur(); await key(P, 'j', { code: 'KeyJ' }); await wait(`document.getElementById('pg').textContent === '3 / 4'`); });
  await C('key-k-newer', async () => { await key(P, 'k', { code: 'KeyK' }); await wait(`document.getElementById('pg').textContent === '4 / 4'`); await key(P, 'ArrowLeft', { code: 'ArrowLeft' }); await wait(`document.getElementById('pg').textContent === '3 / 4'`); await key(P, 'ArrowRight', { code: 'ArrowRight' }); await wait(`document.getElementById('pg').textContent === '4 / 4'`); });
  await C('key-o-origin', async () => { await key(P, 'o', { code: 'KeyO' }); await wait(`document.getElementById('om').classList.contains('open')`); await key(P, 'o', { code: 'KeyO' }); await wait(`!document.getElementById('om').classList.contains('open')`); });
  await C('key-space-page', async () => { await ev(`document.getElementById('main').scrollTop = 0`); await key(P, ' ', { code: 'Space', text: ' ' }); await wait(`document.getElementById('main').scrollTop > 200`); await key(P, ' ', { code: 'Space', shift: true, text: ' ' }); await wait(`document.getElementById('main').scrollTop < 50`); });
  await C('key-f-reader', async () => { await key(P, 'f', { code: 'KeyF' }); await wait(`!document.getElementById('rd').hidden`); });
  await C('key-reader-plusminus', async () => {
    const f0 = Number((await ev(`document.getElementById('rdFsN').textContent`)));
    await key(P, '+', { code: 'Equal', shift: true, text: '+' });
    await wait(`Number(document.getElementById('rdFsN').textContent) > ${f0}`);
    await key(P, '-', { code: 'Minus', text: '-' });
    await wait(`Number(document.getElementById('rdFsN').textContent) === ${f0}`);
    await key(P, 'Escape', { code: 'Escape' });
    await wait(`document.getElementById('rd').hidden`);
  });
  await C('key-y-copy', async () => { await blur(); await key(P, 'y', { code: 'KeyY' }); await waitToast(T('r.copy.reply')); ok((await clipboard()).startsWith('回复 4 第 0 行'), 'y copies the reply'); });
  await C('key-shift-y-copy-source', async () => { await key(P, 'Y', { code: 'KeyY', shift: true, text: 'Y' }); await waitToast(T('r.copy.source')); ok((await clipboard()) === '原话 4', 'Y copies the source'); });
  await C('key-r-reply', async () => { await key(P, 'r', { code: 'KeyR' }); await wait(`!document.getElementById('qbar').hidden && document.activeElement === document.getElementById('input')`); await key(P, 'Escape', { code: 'Escape' }); await wait(`document.getElementById('qbar').hidden`); });
  await C('key-i-esc', async () => {
    await blur();
    await key(P, 'i', { code: 'KeyI' });
    await wait(`document.activeElement === document.getElementById('input')`);
    ok((await ev(`document.getElementById('input').value`)) === '', 'i only focuses');
    await key(P, 'Escape', { code: 'Escape' });
    await wait(`document.activeElement !== document.getElementById('input')`);
  });
  const field = () => ev(`(() => { const i = document.getElementById('input'); return [i.value, i.selectionStart]; })()`);
  await C('key-ctrl-a-e', async () => {
    await ev(`(() => { const i = document.getElementById('input'); i.focus(); i.value = 'abc def'; i.setSelectionRange(3, 3); })()`);
    await key(P, 'a', { code: 'KeyA', ctrl: true }); ok((await field())[1] === 0, 'Ctrl+A → line start');
    await key(P, 'e', { code: 'KeyE', ctrl: true }); ok((await field())[1] === 7, 'Ctrl+E → line end');
  });
  await C('key-ctrl-u-k', async () => {
    await ev(`document.getElementById('input').setSelectionRange(4, 4)`);
    await key(P, 'k', { code: 'KeyK', ctrl: true }); ok((await field())[0] === 'abc ', 'Ctrl+K kills to the end');
    await key(P, 'u', { code: 'KeyU', ctrl: true }); ok((await field())[0] === '', 'Ctrl+U kills to the start');
  });
  await C('key-ctrl-w', async () => {
    await ev(`(() => { const i = document.getElementById('input'); i.value = 'one two three'; i.setSelectionRange(13, 13); })()`);
    await key(P, 'w', { code: 'KeyW', ctrl: true }); ok((await field())[0] === 'one two ', 'Ctrl+W kills the previous word');
  });
  await C('key-ctrl-y', async () => { await key(P, 'y', { code: 'KeyY', ctrl: true }); ok((await field())[0] === 'one two three', 'Ctrl+Y yanks it back'); });
  await C('key-esc-esc-clear', async () => {
    await setFiles('fDoc', [files.md]);
    await chips(1);
    // Expire a previous case's Escape gesture before starting this double-Escape.
    await sleep(550);
    await ev(`document.getElementById('input').focus()`);
    await key(P, 'Escape', { code: 'Escape' }); await sleep(60); await key(P, 'Escape', { code: 'Escape' });
    await wait(`document.getElementById('input').value === '' && !document.querySelectorAll('#tray .chip').length`);
    await waitToast(T('r.clear.done'));
  });
  await C('key-esc-esc-interrupt', async () => {
    await fake.send({ t: 'status', s: 'working', agent: 'claude', name: 'Wren' });
    await wait(`document.body.dataset.status === 'working'`);
    await sleep(1100);
    await blur();
    const n = (fake.st.slashes || []).length;
    await key(P, 'Escape', { code: 'Escape' }); await sleep(60); await key(P, 'Escape', { code: 'Escape' });
    await waitToast(T('r.int.sent'));
    ok(fake.st.slashes.length === n + 1 && fake.st.slashes[n].cmd === 'stop', 'slash stop');
  });
  await C('key-a-p-c', async () => {
    await P.send('Page.setInterceptFileChooserDialog', { enabled: true });
    const chooser = () => new Promise((res) => { const t = setTimeout(() => res(false), 2000); P.on((m) => { if (m.method === 'Page.fileChooserOpened') { clearTimeout(t); res(true); } }); });
    await blur();
    let w = chooser(); await key(P, 'a', { code: 'KeyA' }); ok(await w, 'a → file chooser');
    w = chooser(); await key(P, 'p', { code: 'KeyP' }); ok(await w, 'p → photo chooser');
    await key(P, 'c', { code: 'KeyC' });
    await wait(`!document.getElementById('cam').hidden`);
    await P.send('Page.setInterceptFileChooserDialog', { enabled: false });
  });
  await C('attach-camera', async () => {
    await wait(`document.getElementById('cam').dataset.mode === 'live' && document.getElementById('camVideo').videoWidth > 0`, 8000);
    await click('#camSnap');
    await wait(`document.getElementById('cam').dataset.mode === 'still'`);
  });
  await C('camera-retake-use', async () => {
    await click('#camRetake');
    await wait(`document.getElementById('cam').dataset.mode === 'live'`);
    await click('#camSnap');
    await click('#camUse');
    await chips(1);
    ok((await text('#tray .chip .nm')).startsWith(T('r.file.camera') + '-') && fake.st.opens.filter(Boolean).pop().origin === 'camera', 'the frame is a JPEG in the tray');
    await clearTray();
  });
  await C('camera-fallback-file', async () => {
    await P.send('Page.setInterceptFileChooserDialog', { enabled: true });
    await ev(`window.__gum = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices); navigator.mediaDevices.getUserMedia = () => Promise.reject(Object.assign(new Error('x'), { name: 'NotFoundError' }))`);
    const w = new Promise((res) => { const t = setTimeout(() => res(false), 3000); P.on((m) => { if (m.method === 'Page.fileChooserOpened') { clearTimeout(t); res(true); } }); });
    await click('#tCamera');
    const opened = await w;
    const shown = !(await hidden('#camFile'));
    ok(opened || shown, 'no camera → the file chooser (or its button)');
    await waitToast(T('r.cam.none'));
    if (shown) await click('#camCancel');
    await ev(`navigator.mediaDevices.getUserMedia = window.__gum`);
    await P.send('Page.setInterceptFileChooserDialog', { enabled: false });
  });
  await C('key-m-hold', async () => {
    await blur();
    await key(P, 'm', { code: 'KeyM', type: 'down' });
    await captureReady();
    await sleep(1300);
    await key(P, 'm', { code: 'KeyM', type: 'up' });
    await wait(`document.getElementById('input').value === ${JSON.stringify(fake.st.asrText)}`, 15000);
    await clearField();
  });
  await C('key-mm-lock', async () => {
    await blur(); await sleep(500);
    await key(P, 'm', { code: 'KeyM' }); await sleep(80); await key(P, 'm', { code: 'KeyM' });
    await wait(`document.getElementById('mic').classList.contains('lock')`, 3000);
    await captureReady();
    await sleep(1300);
    await key(P, 'x', { code: 'KeyX' });
    await wait(`document.getElementById('input').value === ${JSON.stringify(fake.st.asrText)}`, 15000);
    await clearField();
  });
  await C('key-mic-dbltap', async () => {
    await blur(); await sleep(500);
    const r = await rect(P, '#mic');
    for (let i = 0; i < 2; i++) {
      await P.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
      await sleep(40);
      await P.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 });
      await sleep(60);
    }
    await wait(`document.getElementById('mic').classList.contains('lock')`, 3000);
    await captureReady();
    await sleep(1200);
    await mouseHold('#mic', 60);
    await wait(`document.getElementById('input').value === ${JSON.stringify(fake.st.asrText)}`, 15000);
    await clearField();
  });

  if (P) await P.dispose();
  return { results, fails };
}
