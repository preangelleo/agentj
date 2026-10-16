#!/usr/bin/env node
// F23 (0.15.2): the real screenshots of the user manual (docs page `manual`, skill `agentj-manual`). The real web client
// against the fake host, in an independent headless Chromium (never the shared CDP on :9222), light.
// P354: docs shots are the phone page opened in a landscape desktop browser, 4:3 (1024×768 CSS px × 1.5625 = 1600×1200);
// the agentj.app home gets four 9:16 phone shots (540×960 × 2 = 1080×1920), one per state colour, per language.
//   node web/test/manual_shots.mjs [zh|en …] [--home-only | --docs-only]
//     → agentjarvis/site/content/docs/14-manual/<name>.<lang>.webp + agentjarvis/site/static/home-<state>.<lang>.webp
// Every shot is a state the page really reaches; nothing is drawn by hand. Re-run after any visible change of the phone
// page (logo, palette), then `node agentjarvis/site/build.mjs` (and `git checkout -- agentjarvis/site/public/dl/`).
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
const HOME_OUT = fileURLToPath(new URL('../../site/static/', import.meta.url));
mkdirSync(OUT, { recursive: true });
const LANGS = process.argv.slice(2).filter((a) => ['zh', 'en'].includes(a));
const DO_HOME = !process.argv.includes('--docs-only'), DO_DOCS = !process.argv.includes('--home-only');
const ONLY = (process.env.AJ_MANUAL_ONLY || '').split(',').filter(Boolean);
const UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1';
const DESK_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36';
// docs/phone shows the four state colours too (docs images must sit next to their page): the same files, copied
const PHONE_DOC = fileURLToPath(new URL('../../site/content/docs/04-phone/', import.meta.url));
const ALSO = { idle: PHONE_DOC, working: PHONE_DOC, approval: PHONE_DOC, question: PHONE_DOC, settings: PHONE_DOC };
// [css width, css height, device scale, mobile]: docs = landscape desktop 4:3, home = 9:16 phone
const DESK = [1024, 768, 1600 / 1024, false], HOME_PHONE = [540, 960, 2, true];
const fit = (p, [width, height, deviceScaleFactor, mobile]) => p.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor, mobile });
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

async function shot(p, name, lang, { dir = OUT, quality = 72 } = {}) {
  await sleep(450);                                   // the state colour fades in (relay's 0.35 s transition)
  const { cssLayoutViewport: v } = await p.send('Page.getLayoutMetrics');
  const { data } = await p.send('Page.captureScreenshot', { format: 'webp', quality, clip: { x: 0, y: v.pageY, width: v.clientWidth, height: v.clientHeight, scale: 1 } });
  const f = join(dir, `${name}.${lang}.webp`);
  if (ONLY.length && !ONLY.includes(name)) return;          // AJ_MANUAL_ONLY=settings,remotes: re-shoot just these
  writeFileSync(f, Buffer.from(data, 'base64'));
  if (dir === OUT && ALSO[name]) writeFileSync(join(ALSO[name], `${name}.${lang}.webp`), Buffer.from(data, 'base64'));
  made.push(f);
  console.log('shot', f);
}
// every rendering mark (code, maths, diagram) that is actually visible — inside the viewport and not clipped by a
// scrolling ancestor — has finished upgrading (data-rx set)
const SHOWN_DONE = `[...document.querySelectorAll('#words pre[data-lang], #words .math, #words .mermaid-src')].every((el) => {
  const r = el.getBoundingClientRect();
  for (let a = el.parentElement; a; a = a.parentElement) { if (/auto|scroll|hidden/.test(getComputedStyle(a).overflowY)) { const q = a.getBoundingClientRect(); if (r.top >= q.bottom || r.bottom <= q.top) return true; } }
  return r.top >= innerHeight || r.bottom <= 0 || !!el.dataset.rx; })`;
const click = (p, sel) => evaluate(p, `document.querySelector(${JSON.stringify(sel)}).click()`);

async function paired(lang, form = DESK, shots = true) {
  fake.reset();
  const p = await newPage(B, form[0], form[1], 'light', { ua: form[3] ? UA : DESK_UA, touch: form[3], allow: [web.url, fake.relay + '/'] });
  await fit(p, form);
  // a device with a built-in unlock (Face ID / Touch ID), like the owner's: without it the page rightly warns that this
  // browser can't keep or unlock the pairing, which is the headless browser, not what an owner sees
  await p.send('WebAuthn.enable', { enableUI: false });
  await p.send('WebAuthn.addVirtualAuthenticator', { options: { protocol: 'ctap2', transport: 'internal', hasResidentKey: true, hasUserVerification: true, isUserVerified: true } });
  // …and a regular (not throw-away) window: the headless profile answers storage.persisted() with false
  await p.send('Page.addScriptToEvaluateOnNewDocument', { source: `navigator.storage && (navigator.storage.persisted = async () => true, navigator.storage.persist = async () => true);` });
  const base = web.url + (lang === 'en' ? '?lang=en' : '');
  await navigate(p, base);
  await waitState(p, 'idle');
  if (shots) await shot(p, 'pair', lang);
  await navigate(p, 'about:blank');
  await navigate(p, fake.newPairing(base));
  await waitState(p, 'awaiting-approval');
  if (shots) await shot(p, 'pair-code', lang);
  await fake.approve();
  await waitState(p, 'ready');
  // the one-time Face ID offer (F20) and the Add-to-Home-Screen hint are not part of these shots
  await evaluate(p, `(() => { const n = document.getElementById('confirm-no'); if (n && !document.getElementById('confirm').hidden) n.click();
    const a = document.getElementById('a2hs-ok'); if (a && !document.getElementById('a2hs').hidden) a.click(); })()`);
  return p;
}

// P354: the four agentj.app home shots — green idle, blue working, orange approval, purple question — on a 9:16 phone.
// A large phone's CSS width at 2× (540×960 × 2 = 1080×1920), not 360 × 3: type and icons stay at their real size relative
// to the screen, so a shot holds as much of a reply as an owner's own screen does. Each shot sits on a real conversation.
const HOME_CSV = Buffer.from('item,left,daily,order\nmug,4,6,60\ncap,2,3,40\ntote,6,5,50\nbottle,9,4,30\n');
async function home(lang) {
  const en = lang === 'en', NAME = 'Agent J', out = { dir: HOME_OUT, quality: 72 };
  const p = await paired(lang, HOME_PHONE, false);
  serveMedia(fake.st);
  MEDIA.set('R'.repeat(22), HOME_CSV);
  const meter = (ctx, h5) => fake.send({ t: 'meter', model: 'opus', model_name: 'Opus 5.5', effort: 'medium', ctx: { used: ctx, max: 100 }, h5: { pct: h5, reset: 0 }, week: { pct: 41, reset: 0 }, at: 0 });
  const phone = (text) => ({ k: 'phone', dev: 'x', name: 'iPhone', text });
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: NAME });
  await meter(31, 18);
  // earlier turns of the same morning (the pager reads n / n; the latest one is on screen)
  await fake.addTurn(phone(en ? 'Summarise last week’s returns' : '把上周的退货原因汇总一下'),
    en ? '**11 returns** last week: 6 wrong size, 3 damaged in transit, 2 changed mind.' : '上周 **11 单**退货：尺码不对 6，运输破损 3，不想要了 2。');
  await fake.addTurn(phone(en ? 'Add a size chart to the cap page' : '给帽子详情页加一张尺码表'),
    en ? 'Added — `pages/cap.md` now has a size chart (S / M / L, head 54–60 cm).' : '加好了，`pages/cap.md` 里有尺码表（S / M / L，头围 54–60 cm）。');
  const t = await fake.addTurn(phone(en ? 'Check today’s orders and draft a restock list' : '看一下今天的订单，拟一份补货清单'),
    en ? '## Today\n\n**132 orders** (+18% on yesterday), 2 returns. Four items will run out this week:\n\n| item | left | per day | order |\n|---|---|---|---|\n| mug | 4 | 6 | 60 |\n| cap | 2 | 3 | 40 |\n| tote | 6 | 5 | 50 |\n| bottle | 9 | 4 | 30 |\n\n### Suppliers\n\n- **A** — 8% cheaper, arrives in 5 days\n- **B** — arrives in 2 days, in time for the weekend sale\n\nThe list: [restock.csv](restock.csv)\n\nShall I order from B?'
      : '## 今天\n\n**132 单**（比昨天多 18%），退货 2 单。有 4 个商品本周会断货：\n\n| 商品 | 剩余 | 日销 | 建议补 |\n|---|---|---|---|\n| 马克杯 | 4 | 6 | 60 |\n| 棒球帽 | 2 | 3 | 40 |\n| 帆布袋 | 6 | 5 | 50 |\n| 保温杯 | 9 | 4 | 30 |\n\n### 供应商\n\n- **A 家**：便宜 8%，5 天到\n- **B 家**：2 天到，赶得上周末活动\n\n清单：[restock.csv](restock.csv)\n\n要我向 B 家下单吗？');
  await fake.updateTurn(t.id, { media: [{ mid: 'R'.repeat(22), name: 'restock.csv', mime: 'text/csv', kind: 'file', bytes: HOME_CSV.length, sha256: sha(HOME_CSV), ref: 'restock.csv' }] });
  await waitFor(p, `document.getElementById('pg').textContent === '3 / 3' && !!document.querySelector('#words .mslot[data-mid]')`);
  await shot(p, 'home-idle', lang, out);                                          // green
  // blue: the next request, half done — the reply streams in as the Agent works
  const w = await fake.addTurn(phone(en ? 'Order from B, then write this week’s report' : '向 B 家下单，然后把这周的周报写了'),
    en ? 'On it.\n\n1. ✅ Checked stock and B’s price list\n2. ✅ Purchase order `po-1016.csv` — 180 items, ¥4,260\n3. ⏳ Weekly report — 3 of 5 sections\n   - sales ✅ · returns ✅ · stock ✅\n   - customers · next week\n4. Email the order to B\n\n> Sales are up 12% week on week; the mug is the best seller for the third week running.\n\n| this week | sales | vs last |\n|---|---|---|\n| orders | 812 | +12% |\n| returns | 9 | −2 |\n| new customers | 64 | +21% |\n| average order | ¥96 | +¥4 |'
      : '好的，在做。\n\n1. ✅ 核对库存和 B 家报价\n2. ✅ 采购单 `po-1016.csv`：180 件，¥4,260\n3. ⏳ 周报：5 节写完 3 节\n   - 销售 ✅ · 退货 ✅ · 库存 ✅\n   - 客户反馈 · 下周计划\n4. 把采购单发邮件给 B 家\n\n> 本周销售额环比 +12%，马克杯连续三周卖得最好。\n\n| 本周 | 数值 | 环比 |\n|---|---|---|\n| 订单 | 812 | +12% |\n| 退货 | 9 | −2 |\n| 新客 | 64 | +21% |\n| 客单价 | ¥96 | +¥4 |', 'open');
  await meter(46, 24);
  await fake.send({ t: 'status', s: 'working', agent: 'claude', name: NAME });
  await waitFor(p, `document.body.dataset.status === 'working' && document.getElementById('pg').textContent === '4 / 4'`);
  await shot(p, 'home-busy', lang, out);                                          // blue
  // orange: step 4 wants to send mail on the owner's behalf
  await fake.send({ t: 'status', s: 'waiting', agent: 'claude', name: NAME });
  const ask = hex(16);
  await fake.ask({ id: ask, tool: 'Bash', summary: 'mail -s "PO 1016" -A po-1016.csv supplier-b', ttl: 120, cat: ['send'], why: en ? 'sends the purchase order to supplier B' : '把采购单发给 B 家' });
  await waitFor(p, `document.body.dataset.sheet === '1'`);
  await shot(p, 'home-waiting', lang, out);                                       // orange
  await fake.askDone(ask, 'allow');
  await fake.updateTurn(w.id, { end: 'done' });
  await waitFor(p, `document.body.dataset.sheet !== '1'`);
  // purple: the report needs one choice from the owner
  await fake.send({ t: 'status', s: 'waiting', kind: 'question', agent: 'claude', name: NAME });
  await fake.send({ t: 'question', id: hex(16), ttl: 180, qs: [{ q: en ? 'Who should get the weekly report?' : '周报发给谁？', h: en ? 'Send to' : '发送对象', m: false,
    o: [{ l: en ? 'Just me' : '只发我', d: en ? 'to your inbox, as a PDF' : '发到你的邮箱，PDF' },
      { l: en ? 'The whole team' : '全组', d: en ? 'the shared channel, with the charts' : '发到团队群，带图表' },
      { l: en ? 'Hold it' : '先不发', d: en ? 'keep the draft for Monday' : '留草稿，周一再说' }] }] });
  await waitFor(p, `document.body.dataset.kind === 'question' && document.body.dataset.sheet === '1'`);
  await shot(p, 'home-question', lang, out);                                      // purple
  await fake.send({ t: 'question_done', id: 'x'.repeat(32), result: 'gone' });
  await p.dispose();
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
  // code blocks upgrade only once in view (render.js IntersectionObserver); in the 4:3 frame the code may sit below the fold
  await waitFor(p, `!!document.querySelector('#words .mslot img') && ${SHOWN_DONE}`, 20000);
  await shot(p, 'rich', lang);
  await evaluate(p, `document.querySelector('#words .mermaid-src').scrollIntoView({ block: 'center' })`);   // diagrams upgrade when in view
  await waitFor(p, `[...document.querySelectorAll('#words .mermaid-src')].every((w) => w.dataset.rx === 'ok' || w.dataset.rx === 'x')`, 30000);
  await evaluate(p, `(document.getElementById('mstrip') || document.querySelector('#words .mermaid-src')).scrollIntoView({ block: 'end' })`);
  await shot(p, 'rich-more', lang);
  await click(p, '#slashBtn');
  await waitFor(p, `!document.getElementById('menu').hidden`);
  await sleep(250);                                   // P187: menu pages fade in (160 ms)
  await shot(p, 'commands', lang);
  await evaluate(p, `document.body.click()`);
  await click(p, '#keysBtn');
  await sleep(250);
  await shot(p, 'keys', lang);
  await click(p, '#keysClose');
  await click(p, '#setBtn');
  await waitFor(p, `!document.getElementById('settings').hidden`);
  await shot(p, 'settings', lang);
  await evaluate(p, `document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))`);
  await waitFor(p, `document.getElementById('settings').hidden`);
  // P171: the ≡ menu merged into Settings; its own second-level page 遥控器管理 is the second shot
  const now = Math.floor(Date.now() / 1000);
  fake.remotes({ limit: 10, link: '', qr: '', code: '', items: [
    { id: 'ME', me: true, name: 'iPhone', paired_at: now - 86400 * 12, seen: now, online: true, source: 'local' },
    { id: 'Kx3pQ9wLm2Vb7Tz1', name: en ? 'MacBook · Chrome' : 'MacBook · Chrome', paired_at: now - 86400 * 30, seen: now - 3600, online: true, source: 'account' },
    { id: 'Rf8sN2cHq5Yd4Jk6', name: en ? 'Pixel 9' : '华为 Pura', paired_at: now - 86400 * 5, seen: now - 86400 * 2, online: false, source: 'phone' }] });
  await evaluate(p, `(document.getElementById('settings').hidden && document.getElementById('setBtn').click())`);
  await click(p, '#set-remotes-open');
  await waitFor(p, `document.querySelectorAll('#rm-list .rmitem').length === 3`);
  await shot(p, 'remotes', lang);
  fake.st.remotes = null;
  await evaluate(p, `(!document.getElementById('settings').hidden && document.getElementById('setBtn').click())`);
  const { root } = await p.send('DOM.getDocument', { depth: -1 });
  const { nodeId } = await p.send('DOM.querySelector', { nodeId: root.nodeId, selector: '#fPhoto' });
  await p.send('DOM.setFileInputFiles', { nodeId, files: [files.png1, files.txt] });
  await waitFor(p, `document.querySelectorAll('#tray .chip[data-st=ready]').length === 2`, 10000);
  await click(p, '#replyBtn');
  await click(p, '#input');
  await p.send('Input.insertText', { text: en ? 'Use supplier A' : '用 A 家' });
  await shot(p, 'attach', lang);
  await evaluate(p, `[...document.querySelectorAll('#tray .chip .x')].forEach((b) => b.click()); document.getElementById('qbX').click(); document.getElementById('input').value = ''; document.activeElement.blur()`);
  await p.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 });   // hold-to-talk is a touch gesture
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
  await sleep(3000);                                  // let the "Declined" toast fade before the stopped shot
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
  for (const lang of LANGS.length ? LANGS : ['zh', 'en']) { if (DO_HOME) await home(lang); if (DO_DOCS) await run(lang); }
  console.log(JSON.stringify({ ok: true, shots: made.length }));
} finally {
  await B.close(); await fake.stop(); await web.stop();
}
