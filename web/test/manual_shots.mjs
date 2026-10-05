#!/usr/bin/env node
// F23 (0.15.2): the real screenshots of the user manual (docs page `manual`, skill `agentj-manual`). The real web client
// against the fake host, in an independent headless Chromium (never the shared CDP on :9222), iPhone size 390×844, light.
//   node web/test/manual_shots.mjs [zh|en …]   → agentjarvis/site/content/docs/14-manual/<name>.<lang>.webp
// Every shot is a state the page really reaches; nothing is drawn by hand. Re-run after any visible change of the phone page.
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { webcrypto, createHash } from 'node:crypto';
import { deflateSync, crc32 } from 'node:zlib';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, sleep } from './browser.mjs';
import { makeFiles } from './parity_cases.mjs';

const OUT = fileURLToPath(new URL('../../site/content/docs/14-manual/', import.meta.url));
mkdirSync(OUT, { recursive: true });
const LANGS = process.argv.slice(2).filter((a) => ['zh', 'en'].includes(a));
const UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1';
const hex = (n) => Array.from(webcrypto.getRandomValues(new Uint8Array(n)), (b) => b.toString(16).padStart(2, '0')).join('');
const b64u = (u8) => Buffer.from(u8).toString('base64url');

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
const files = makeFiles();
const made = [];


// F21 media: a chart the "Agent" made, served by the fake host the way serve answers media_get (PROTOCOL §13)
function png(w, h, f) {
  const raw = Buffer.alloc((w * 3 + 1) * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) raw.set(f(x, y), y * (w * 3 + 1) + 1 + x * 3);
  const chunk = (t, d) => { const len = Buffer.alloc(4); len.writeUInt32BE(d.length); const td = Buffer.concat([Buffer.from(t), d]); const c = Buffer.alloc(4); c.writeUInt32BE(crc32(td)); return Buffer.concat([len, td, c]); };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2;
  return Buffer.concat([Buffer.from('\x89PNG\r\n\x1a\n', 'latin1'), chunk('IHDR', ihdr), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}
const BARS = [0.35, 0.55, 0.48, 0.8];
const CHART = png(480, 270, (x, y) => { const i = Math.floor(x / 120), top = 250 - BARS[i] * 220; return x % 120 > 20 && x % 120 < 100 && y > top && y < 250 ? [64, 112, 196] : [246, 245, 240]; });
const CSV = Buffer.from('城市,数量\n上海,12\n北京,9\n');
const MEDIA = new Map([['A'.repeat(22), CHART], ['F'.repeat(22), CSV]]);
const sha = (b) => createHash('sha256').update(b).digest('hex');
function serveMedia(st) {
  st.onApp = async (c, m, send) => {
    if (m.t !== 'media_get') return false;
    const b = MEDIA.get(m.mid);
    if (!b) { await send({ t: 'media_err', mid: m.mid, why: 'gone' }); return true; }
    for (let o = m.o; o < b.length; o += 45056) await send({ t: 'media_chunk', mid: m.mid, o, d: Buffer.from(b.subarray(o, o + 45056)).toString('base64url'), last: o + 45056 >= b.length });
    return true;
  };
}

async function shot(p, name, lang) {
  await sleep(450);                                   // the state colour fades in (relay's 0.35 s transition)
  const { cssLayoutViewport: v } = await p.send('Page.getLayoutMetrics');
  const { data } = await p.send('Page.captureScreenshot', { format: 'webp', quality: 78, clip: { x: 0, y: v.pageY, width: v.clientWidth, height: v.clientHeight, scale: 1 } });
  const f = join(OUT, `${name}.${lang}.webp`);
  writeFileSync(f, Buffer.from(data, 'base64'));
  made.push(f);
  console.log('shot', f);
}
const click = (p, sel) => evaluate(p, `document.querySelector(${JSON.stringify(sel)}).click()`);

async function paired(lang) {
  fake.reset();
  const p = await newPage(B, 390, 844, 'light', { ua: UA, touch: true, allow: [web.url, fake.relay + '/'] });
  const base = web.url + (lang === 'en' ? '?lang=en' : '');
  await navigate(p, base);
  await waitState(p, 'idle');
  await shot(p, 'pair', lang);
  await navigate(p, 'about:blank');
  await navigate(p, fake.newPairing(base));
  await waitState(p, 'awaiting-approval');
  await shot(p, 'pair-code', lang);
  await fake.approve();
  await waitState(p, 'ready');
  // the one-time Face ID offer (F20) and the Add-to-Home-Screen hint are not part of these shots
  await evaluate(p, `(() => { const n = document.getElementById('confirm-no'); if (n && !document.getElementById('confirm').hidden) n.click();
    const a = document.getElementById('a2hs-ok'); if (a && !document.getElementById('a2hs').hidden) a.click(); })()`);
  return p;
}

async function run(lang) {
  const en = lang === 'en';
  const NAME = 'Agent J';
  const p = await paired(lang);
  const meter = (ctx) => fake.send({ t: 'meter', model: 'opus', model_name: 'Opus 5.5', effort: 'medium', ctx: { used: ctx, max: 100 }, h5: { pct: 34, reset: 0 }, week: { pct: 58, reset: 0 }, at: 0 });
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: NAME });
  await meter(28);
  await fake.addTurn({ k: 'phone', dev: 'x', name: 'iPhone', text: en ? 'Check today’s orders and draft a restock list' : '看一下今天的订单，拟一份补货清单' },
    en ? '## Today\n\n**132 orders**; 3 items are running low.\n\n| item | left |\n|---|---|\n| mugs | 4 |\n| caps | 2 |\n\nThe restock list is in `restock.md`. Shall I place the order with supplier B?'
      : '## 今天\n\n**132 单**，有 3 个商品库存偏低。\n\n| 商品 | 剩余 |\n|---|---|\n| 杯子 | 4 |\n| 帽子 | 2 |\n\n补货清单在 `restock.md` 里。要我向 B 家下单吗？');
  await waitFor(p, `document.getElementById('pg').textContent === '1 / 1'`);
  await shot(p, 'idle', lang);                                                    // green
  await fake.send({ t: 'status', s: 'working', agent: 'claude', name: NAME });
  await waitFor(p, `document.body.dataset.status === 'working'`);
  await shot(p, 'working', lang);                                                 // blue
  await fake.send({ t: 'status', s: 'waiting', agent: 'claude', name: NAME });
  await fake.ask({ id: hex(16), tool: 'Bash', summary: 'rm -rf exports/old/', ttl: 120, cat: ['delete'], why: 'rm -rf' });
  await waitFor(p, `document.body.dataset.sheet === '1'`);
  await shot(p, 'approval', lang);                                                // orange
  await click(p, '#sheetClose');
  await shot(p, 'approval-later', lang);
  fake.reset();
  await navigate(p, web.url + (en ? '?lang=en' : ''));
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'waiting', kind: 'question', agent: 'claude', name: NAME });
  await fake.send({ t: 'question', id: hex(16), ttl: 180, qs: [{ q: en ? 'Which supplier?' : '找哪家供应商？', h: en ? 'Supplier' : '供应商', m: false, o: [{ l: 'A', d: en ? 'cheaper' : '便宜' }, { l: 'B', d: en ? 'faster' : '快' }] }] });
  await waitFor(p, `document.body.dataset.kind === 'question' && document.body.dataset.sheet === '1'`);
  await shot(p, 'question', lang);                                                // purple
  await fake.send({ t: 'question_done', id: 'x'.repeat(32), result: 'gone' });
  fake.reset();
  await navigate(p, web.url + (en ? '?lang=en' : ''));
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: NAME });
  await meter(64);
  await fake.addTurn({ k: 'agent' }, en ? 'Done. The list is in restock.md.' : '好了，清单在 restock.md 里。');
  await waitFor(p, `document.getElementById('water').dataset.lvl === '64'`);
  await shot(p, 'water-high', lang);                                              // water past half
  // rendering (code, maths, a diagram) and a picture + a file the Agent made — they appear inside the reply
  serveMedia(fake.st);
  const rich = await fake.addTurn({ k: 'phone', dev: 'x', name: 'iPhone', text: en ? 'Plot the weekly orders and give me the formula' : '把每周订单画成图，再给我公式' },
    (en ? 'Done — the chart is below.\n\n![Weekly orders](out/orders.png)\n\nGrowth rate: $r = \\frac{x_4 - x_1}{x_1}$\n\n```python\nimport pandas as pd\ndf = pd.read_csv("orders.csv")\nprint(df.groupby("week").sum())\n```\n\n```mermaid\nflowchart LR\n  A[Orders] --> B[Weekly sum] --> C[Chart]\n```'
      : '好了，图在下面。\n\n![每周订单](out/orders.png)\n\n增长率：$r = \\frac{x_4 - x_1}{x_1}$\n\n```python\nimport pandas as pd\ndf = pd.read_csv("orders.csv")\nprint(df.groupby("week").sum())\n```\n\n```mermaid\nflowchart LR\n  A[订单] --> B[按周汇总] --> C[画图]\n```'), 'done');
  await fake.updateTurn(rich.id, { media: [
    { mid: 'A'.repeat(22), name: 'orders.png', mime: 'image/png', kind: 'image', bytes: CHART.length, sha256: sha(CHART), ref: 'out/orders.png' },
    { mid: 'F'.repeat(22), name: 'orders.csv', mime: 'text/csv', kind: 'file', bytes: CSV.length, sha256: sha(CSV) }],
    media_skip: [{ name: '.env', why: 'secret' }] });
  await waitFor(p, `!!document.querySelector('#words .mslot img') && !!document.querySelector('#words pre[data-lang="python"] code.hljs') && document.querySelectorAll('#words .math[data-rx="ok"] .katex').length >= 1`, 20000);
  await shot(p, 'rich', lang);
  await evaluate(p, `document.querySelector('#words .mermaid-src').scrollIntoView({ block: 'center' })`);   // diagrams upgrade when in view
  await waitFor(p, `[...document.querySelectorAll('#words .mermaid-src')].every((w) => w.dataset.rx === 'ok' || w.dataset.rx === 'x')`, 30000);
  await evaluate(p, `(document.getElementById('mstrip') || document.querySelector('#words .mermaid-src')).scrollIntoView({ block: 'end' })`);
  await shot(p, 'rich-more', lang);
  await click(p, '#slashBtn');
  await waitFor(p, `!document.getElementById('menu').hidden`);
  await shot(p, 'commands', lang);
  await evaluate(p, `document.body.click()`);
  await click(p, '#keysBtn');
  await shot(p, 'keys', lang);
  await click(p, '#keysClose');
  await click(p, '#setBtn');
  await waitFor(p, `!document.getElementById('settings').hidden`);
  await shot(p, 'settings', lang);
  await evaluate(p, `document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))`);
  await waitFor(p, `document.getElementById('settings').hidden`);
  await evaluate(p, `document.getElementById('aj-menu').open = true`);
  await shot(p, 'menu', lang);
  await evaluate(p, `document.getElementById('aj-menu').open = false`);
  const { root } = await p.send('DOM.getDocument', { depth: -1 });
  const { nodeId } = await p.send('DOM.querySelector', { nodeId: root.nodeId, selector: '#fPhoto' });
  await p.send('DOM.setFileInputFiles', { nodeId, files: [files.png1, files.txt] });
  await waitFor(p, `document.querySelectorAll('#tray .chip[data-st=ready]').length === 2`, 10000);
  await click(p, '#replyBtn');
  await click(p, '#input');
  await p.send('Input.insertText', { text: en ? 'Use supplier A' : '用 A 家' });
  await shot(p, 'attach', lang);
  await evaluate(p, `[...document.querySelectorAll('#tray .chip .x')].forEach((b) => b.click()); document.getElementById('qbX').click(); document.getElementById('input').value = ''; document.activeElement.blur()`);
  const r = await evaluate(p, `(() => { const b = document.getElementById('mic').getBoundingClientRect(); return { x: b.x + b.width / 2, y: b.y + b.height / 2 }; })()`);
  await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: r.x, y: r.y }] });
  await waitFor(p, `document.getElementById('mic').classList.contains('rec')`, 4000);
  await sleep(1200);
  await shot(p, 'voice', lang);
  await p.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: r.x, y: r.y - 120 }] });
  await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  // F17 cards: the computer asks for the admin password / a key; the phone seals it to a one-time key
  const card = async (kind, extra) => {
    const kp = await webcrypto.subtle.generateKey({ name: 'X25519' }, true, ['deriveBits']);
    const epk = new Uint8Array(await webcrypto.subtle.exportKey('raw', kp.publicKey));
    const m = { t: 'elev', id: hex(16), kind, n: hex(16), epk: b64u(epk), ttl: 120, tries: 3, ...extra };
    await fake.send(m);
    await waitFor(p, `!!document.getElementById('elev') && !document.getElementById('elev').hidden`);
    return m;
  };
  await fake.send({ t: 'status', s: 'working', agent: 'claude', name: NAME });
  let m = await card('sudo', en ? { cmd: 'apt-get install -y ffmpeg', why: 'ffmpeg is needed to turn the recording into an mp3', effect: 'installs one system package' }
    : { cmd: 'apt-get install -y ffmpeg', why: '把录音转成 mp3 要用 ffmpeg', effect: '安装一个系统软件包' });
  await shot(p, 'sudo', lang);
  await fake.send({ t: 'elev_done', id: m.id, result: 'denied' });
  m = await card('secret', { name: 'ELEVENLABS_API_KEY', purpose: en ? 'Voice-over for the video' : '给视频配音',
    dest: '/home/me/proj/.env (ELEVENLABS_API_KEY=…)', verify: 'GET https://api.elevenlabs.io/v1/user  (xi-api-key: {value})' });
  await shot(p, 'secret', lang);
  await fake.send({ t: 'elev_done', id: m.id, result: 'denied' });
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: NAME });
  fake.answer('estop', () => [{ t: 'ctl_res', ok: true }, { t: 'estop_state', on: true, by: 'iPhone' }]);
  fake.answer('resume', () => [{ t: 'ctl_res', ok: true }, { t: 'estop_state', on: false }]);
  await click(p, '#slashBtn');
  await click(p, '#menu [data-estop="stop"]');
  await waitFor(p, `!document.getElementById('confirm').hidden`);
  await click(p, '#confirm-yes');
  await waitFor(p, `!document.getElementById('estop-banner').hidden`);
  await fake.send({ t: 'status', s: 'stopped', agent: 'claude', name: NAME });
  await waitFor(p, `document.body.dataset.agent === 'stopped'`);
  await shot(p, 'stopped', lang);                                                 // grey + red banner
  await click(p, '#resume');
  await waitFor(p, `!document.getElementById('confirm').hidden`);
  await click(p, '#confirm-yes');
  await waitFor(p, `document.getElementById('estop-banner').hidden`);
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: NAME });
  // offline: sends wait in the encrypted queue with one line (0.15.2)
  await sleep(3000);                                  // let the card's toast fade
  await p.send('Network.emulateNetworkConditions', { offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  fake.kick();
  await waitFor(p, `window.__ajState !== 'ready' && navigator.onLine === false`);
  await waitFor(p, `!document.getElementById('outbox').hidden`);
  for (const m of en ? ['Also check the returns', 'And email the supplier'] : ['顺便看看退货', '再给供应商发封邮件']) {
    await evaluate(p, `(() => { const i = document.getElementById('input'); i.value = ${JSON.stringify(m)}; i.dispatchEvent(new Event('input')); document.getElementById('send').click(); })()`);
    await sleep(200);
  }
  await waitFor(p, `document.querySelectorAll('#outbox .obi').length === 2`);
  await evaluate(p, `document.activeElement.blur()`);
  await shot(p, 'offline-queue', lang);
  fake.setUp(false);
  await p.send('Network.emulateNetworkConditions', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  await waitState(p, 'waiting-host');
  await shot(p, 'offline', lang);                                                 // grey: computer offline
  fake.setUp(true);
  await waitState(p, 'ready');
  await p.dispose();
}

try {
  for (const lang of LANGS.length ? LANGS : ['zh', 'en']) await run(lang);
  console.log(JSON.stringify({ ok: true, shots: made.length }));
} finally {
  await B.close(); await fake.stop(); await web.stop();
}
