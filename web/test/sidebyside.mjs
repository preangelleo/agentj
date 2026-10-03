#!/usr/bin/env node
// Side by side: relay's phone page (pwa/index.html, served here from this repo with a stub of its bridge endpoints — never a
// real bridge, no admin key) and the Agent J web client, driven through the same states at 390×844 in our own headless
// Chromium. The Agent J side runs against the REAL host by default: `agentj serve` (test launcher, throw-away state) with
// the stand-in Claude Code (host/tests/fakeclaude.py, FAKE_CLAUDE_SCRIPT gives it relay's sample answer) over a local
// workerd relay — every card, meter and page on it came from the host (`--fake`: test/fakehost.mjs instead). Writes
// reports/qa/parity/shots/<state>-relay.png, <state>-agentj.png, <state>-pair.png (the two next to each other), pairs.json
// and montage.png (all ten pairs).
//   node web/test/sidebyside.mjs [--fake]
import { createServer } from 'node:http';
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { join, extname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep, key } from './browser.mjs';
import { makeFiles } from './parity_cases.mjs';
import { mkdtempSync, chmodSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';

const REAL = !process.argv.includes('--fake');

const REPO = fileURLToPath(new URL('../../../', import.meta.url));
const PWA = join(REPO, 'pwa');
const OUT = join(REPO, 'reports', 'qa', 'parity', 'shots');
mkdirSync(OUT, { recursive: true });
const W = 390, H = 844;

// ---------------------------------------------------------------- relay stub (pwa/ + the endpoints its page calls)
const R = { snap: null, turns: [], sse: new Set(), down: false };
const TYPES = { '.png': 'image/png', '.js': 'text/javascript', '.mp3': 'audio/mpeg', '.ico': 'image/x-icon', '.json': 'application/json' };
const relaySrv = createServer((req, res) => {
  const u = new URL(req.url, 'http://x');
  const p = u.pathname.replace(/^\/r/, '') || '/';
  const json = (o, s = 200) => { res.writeHead(s, { 'content-type': 'application/json' }); res.end(JSON.stringify(o)); };
  if (p === '/' || p === '/index.html') {
    const html = readFileSync(join(PWA, 'index.html'), 'utf8').replaceAll('__RELAY_BASE__', '/r').replaceAll('__RELAY_MAX_TEXT__', '20000');
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' }); res.end(html); return;
  }
  if (p === '/events') {
    if (R.down) { res.writeHead(503); res.end(); return; }
    res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-store' });
    res.write(`data: ${JSON.stringify(R.snap)}\n\n`);
    R.sse.add(res); req.on('close', () => R.sse.delete(res)); return;
  }
  if (p === '/state') { if (R.down) { res.writeHead(503); res.end(); return; } return json(R.snap); }
  if (p === '/history') {
    let ts = R.turns.slice();
    const before = Number(u.searchParams.get('before')), after = Number(u.searchParams.get('after'));
    if (before) ts = ts.filter((x) => x.id < before);
    if (after) ts = ts.filter((x) => x.id > after);
    return json({ turns: ts.slice(-50), count: R.turns.length, first_id: R.turns.length ? R.turns[0].id : 0, epoch: 1 });
  }
  if (p === '/menu') return json({ ok: false });
  if (p === '/attach') { req.resume(); req.on('end', () => json({ ok: true, attachment_id: 'a' + Math.random().toString(16).slice(2) })); return; }
  const f = join(PWA, p);
  if (f.startsWith(PWA) && existsSync(f)) { res.writeHead(200, { 'content-type': TYPES[extname(f)] || 'application/octet-stream' }); res.end(readFileSync(f)); return; }
  res.writeHead(404); res.end();
});
await new Promise((r) => relaySrv.listen(0, '127.0.0.1', r));
const RELAY_URL = `http://127.0.0.1:${relaySrv.address().port}/r/`;
const push = (patch) => { R.snap = { ...R.snap, ...patch, published_at: Date.now() / 1000 }; for (const s of R.sse) s.write(`data: ${JSON.stringify(R.snap)}\n\n`); };

const REPLY = '## 今天\n\n**132 单**，有 3 个商品库存偏低。\n\n| 商品 | 剩余 |\n|---|---|\n| 杯子 | 4 |\n| 帽子 | 2 |\n\n```sh\nnpm run restock\n```\n\n要我现在就下补货单吗？';
const ASKQ = '找哪家供应商？';
R.turns = [{ id: 1, ts: Date.now() / 1000 - 300, source: { kind: 'leo', label: 'Leo', text: '看一下今天的订单，拟一份补货清单' }, reply: REPLY }];
R.snap = { status: 'idle', agent: 'shop', last_message: REPLY, history: { count: 1, last_id: 1, epoch: 1 },
  usage: { model: 'Opus 5.5', effort: 'medium', week_pct: 58, five_hour_pct: 34, context_pct: 62, source: 'statusline', age_s: 3 } };
if (REAL) R.snap.usage = { model: 'Default', effort: null, week_pct: 47, five_hour_pct: 45, context_pct: 10, source: 'statusline', age_s: 3 };

const web = await startWebServer({ port: 0 });
const B = await launch();
const pairs = [];
let fake = null, real = null;
// ---------------------------------------------------------------- the Agent J side: the real host (default) or the fake one
async function startReal() {
  const { startLocalRelay } = await import('../../relay/test/local_relay.mjs');
  const { HostProc } = await import('../../tests/lib/host.mjs');
  const tmp = mkdtempSync(join(tmpdir(), 'aj-sbs-'));
  const work = join(tmp, 'work'); mkdirSync(work, { recursive: true });
  mkdirSync(join(tmp, 'state'), { mode: 0o700 });
  // inside the work folder: the fence shows the stand-in only its own folder
  writeFileSync(join(work, '.script.json'), JSON.stringify({ '看一下今天的订单，拟一份补货清单': { reply: REPLY }, '再看一下库存': { sleep: 6, reply: '库存已更新。' } }));
  const wrapper = join(tmp, 'claude');
  writeFileSync(wrapper, `#!/bin/sh\nexec python3 ${join(REPO, 'host/tests/fakeclaude.py')} "$@"\n`);
  chmodSync(wrapper, 0o700);
  const relay = await startLocalRelay({ tap: false });
  const host = new HostProc({ relay: relay.url, web: web.url, stateDir: join(tmp, 'state'),
    env: { AGENTJ_CLAUDE_BIN: wrapper, FAKE_CLAUDE_SCRIPT: join(work, '.script.json'), AGENTJ_TEST_Q_TTL: '300', AGENTJ_TEST_ASK_TTL: '300' } }).init();
  host.run(['agent', 'claude', '--dir', work]);
  host.run(['name', 'shop']);
  await host.serve();
  return { tmp, relay, host, stop: async () => { await host.stop(); await relay.stop(); rmSync(tmp, { recursive: true, force: true }); } };
}
try {
  const rp = await newPage(B, W, H, 'light', { touch: true, allow: [RELAY_URL] });
  await navigate(rp, RELAY_URL);
  await waitFor(rp, `document.body.dataset.conn === 'on' && document.getElementById('pg').textContent === '1 / 1'`);
  let ap;
  if (REAL) {
    real = await startReal();
    ap = await newPage(B, W, H, 'light', { touch: true, allow: [web.url, real.relay.url] });
    const pr = await real.host.pair();
    await navigate(ap, web.url + pr.link.slice(pr.link.indexOf('#')));
    await waitState(ap, 'awaiting-approval', 20000);
    const sas = (await evaluate(ap, `document.getElementById('sas').textContent`)).replace(/\D/g, '');
    await pr.waitPrompt(); pr.typeCode(sas);
    await waitState(ap, 'ready', 20000);
    await pr.exited;
    await waitFor(ap, `document.body.dataset.agent === 'idle'`, 30000);
  } else {
    fake = await startFakeHost();
    ap = await newPage(B, W, H, 'light', { touch: true, allow: [web.url, fake.relay + '/'] });
    await navigate(ap, fake.newPairing(web.url));
    await waitState(ap, 'awaiting-approval');
    await fake.approve();
    await waitState(ap, 'ready');
    await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'shop' });
    await fake.send({ t: 'meter', model: 'opus', model_name: 'Opus 5.5', effort: 'medium', ctx: { used: 62, max: 100 }, h5: { pct: 34, reset: 0 }, week: { pct: 58, reset: 0 }, at: 0 });
    fake.st.replyFor = () => REPLY;
  }
  await evaluate(ap, `document.getElementById('a2hs-ok') && !document.getElementById('a2hs').hidden && document.getElementById('a2hs-ok').click()`).catch(() => {});
  const sayAj = async (t) => { await evaluate(ap, `document.getElementById('input').focus()`); await ap.send('Input.insertText', { text: t }); await evaluate(ap, `document.getElementById('send').click()`); };
  // the source card is this phone's own message ("你"), like relay's owner card: send it from the page
  await sayAj('看一下今天的订单，拟一份补货清单');
  await waitFor(ap, `document.getElementById('pg').textContent === '1 / 1' && document.getElementById('words').textContent.includes('132')`, 20000);
  if (REAL) await waitFor(ap, `document.body.dataset.agent === 'idle' && document.getElementById('water').dataset.known === '1'`, 20000);
  const toFirst = async () => { await evaluate(ap, `document.activeElement && document.activeElement.blur()`); await key(ap, 'G', { code: 'KeyG', shift: true, text: 'G' }); await waitFor(ap, `document.getElementById('pg').textContent.startsWith('1 / ')`); };
  const AJ = REAL ? {
    working: async () => { await sayAj('再看一下库存'); await waitFor(ap, `document.body.dataset.agent === 'working'`, 10000); },
    approval: async () => {
      await waitFor(ap, `document.body.dataset.agent === 'idle'`, 20000);
      await sayAj('RUN: rm -rf exports/old/');
      await waitFor(ap, `document.body.dataset.sheet === '1'`, 20000);
    },
    question: async () => {
      await evaluate(ap, `document.getElementById('apprDeny').click()`);
      await waitFor(ap, `document.body.dataset.agent === 'idle'`, 20000);
      await sleep(6500);
      await sayAj('ASK: ' + JSON.stringify([{ question: ASKQ, header: '', options: [{ label: 'A', description: '便宜' }, { label: 'B', description: '快' }], multiSelect: false }]));
      await waitFor(ap, `document.body.dataset.sheet === '1' && document.body.dataset.kind === 'question'`, 20000);
    },
    afterQuestion: async () => {
      await evaluate(ap, `document.getElementById('askCancel').click(); document.getElementById('askCancel').click()`);
      await waitFor(ap, `document.body.dataset.agent === 'idle'`, 20000);
      await sleep(6500);
      await toFirst();
    },
    offline: async () => { await real.relay.stop(); await waitFor(ap, `document.body.dataset.conn === 'off'`, 20000); },
  } : {
    working: async () => fake.send({ t: 'status', s: 'working', agent: 'claude', name: 'shop' }),
    approval: async () => { await fake.send({ t: 'status', s: 'waiting', agent: 'claude', name: 'shop' }); await fake.ask({ id: 'c'.repeat(32), tool: 'Bash', summary: 'rm -rf exports/old/', ttl: 120, cat: ['delete'], why: 'rm -rf' }); },
    question: async () => { await fake.askDone('c'.repeat(32), 'deny'); await sleep(6500); await fake.send({ t: 'question', id: 'd'.repeat(32), ttl: 180, qs: [{ q: ASKQ, h: '', m: false, o: [{ l: 'A', d: '便宜' }, { l: 'B', d: '快' }] }] }); await fake.send({ t: 'status', s: 'waiting', kind: 'question', agent: 'claude', name: 'shop' }); },
    afterQuestion: async () => { await fake.send({ t: 'question_done', id: 'd'.repeat(32), result: 'gone' }); await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'shop' }); await sleep(6500); },
    offline: async () => fake.setUp(false),
  };

  async function both(name, relayStep, ajStep) {
    await relayStep(); await ajStep();
    await sleep(900);
    const a = await shoot(rp), b = await shoot(ap);
    writeFileSync(join(OUT, `${name}-relay.png`), a); writeFileSync(join(OUT, `${name}-agentj.png`), b);
    const m = await newPage(B, W * 2 + 60, H + 70, 'light', { allow: [] });
    const html = `<!doctype html><meta charset="utf-8"><body style="margin:0;background:#2c2b26;font:600 15px system-ui;color:#faf9f5;display:flex;gap:20px;padding:10px 20px">
      <figure style="margin:0"><figcaption style="height:36px">relay · ${name}</figcaption><img src="data:image/png;base64,${a.toString('base64')}" width="${W}" height="${H}"></figure>
      <figure style="margin:0"><figcaption style="height:36px">Agent J · ${name}</figcaption><img src="data:image/png;base64,${b.toString('base64')}" width="${W}" height="${H}"></figure></body>`;
    await navigate(m, 'data:text/html;base64,' + Buffer.from(html).toString('base64'));
    writeFileSync(join(OUT, `${name}-pair.png`), await shoot(m));
    await m.dispose();
    pairs.push(name);
    console.log('shot', name);
  }
  const ev = evaluate;
  await both('01-idle', async () => {}, async () => {});
  await both('02-working', async () => push({ status: 'working' }), AJ.working);
  await both('03-approval', async () => push({ status: 'waiting', kind: 'permission', card: { kind: 'permission', tool_name: 'Bash', summary: { command: 'rm -rf exports/old/', description: '清理旧导出' }, received_at: Date.now() / 1000 },
    approval: { id: 'p1', state: 'open', tool: 'Bash', summary: { command: 'rm -rf exports/old/', description: '清理旧导出' } } }),
  AJ.approval);
  await both('04-question', async () => push({ status: 'waiting', kind: 'question', card: { kind: 'question', received_at: Date.now() / 1000 }, approval: null,
    ask: { id: 'q1', state: 'open', questions: [{ question: ASKQ, multi: false, options: [{ label: 'A', description: '便宜' }, { label: 'B', description: '快' }] }] } }),
  AJ.question);
  push({ status: 'idle', kind: null, card: null, ask: null, approval: null });
  await AJ.afterQuestion();
  await both('05-reader', async () => ev(rp, `document.getElementById('readBtn').click()`), async () => ev(ap, `document.getElementById('readBtn').click()`));
  await sleep(400);
  await ev(rp, `document.getElementById('rdClose').click()`); await ev(ap, `document.getElementById('rdClose').click()`);
  await sleep(400);
  await both('06-keys', async () => ev(rp, `document.getElementById('keysBtn').click()`), async () => ev(ap, `document.getElementById('keysBtn').click()`));
  await ev(rp, `document.getElementById('keysClose').click()`); await ev(ap, `document.getElementById('keysClose').click()`);
  await both('07-menu', async () => ev(rp, `document.getElementById('slashBtn').click()`), async () => ev(ap, `document.getElementById('slashBtn').click()`));
  await ev(rp, `document.body.click()`); await ev(ap, `document.body.click()`);
  const files = makeFiles();
  const setFiles = async (p, paths) => { await p.send('DOM.enable'); const { root } = await p.send('DOM.getDocument', { depth: -1 }); const { nodeId } = await p.send('DOM.querySelector', { nodeId: root.nodeId, selector: '#fPhoto' }); await p.send('DOM.setFileInputFiles', { nodeId, files: paths }); };
  await both('08-tray', async () => { await setFiles(rp, [files.png1, files.txt]); await ev(rp, `document.getElementById('replyBtn').click()`); await rp.send('Input.insertText', { text: '用 A 家' }); },
    async () => { await setFiles(ap, [files.png1, files.txt]); await ev(ap, `document.getElementById('replyBtn').click()`); await ap.send('Input.insertText', { text: '用 A 家' }); });
  await ev(rp, `[...document.querySelectorAll('#tray .chip .x')].forEach((b) => b.click()); document.getElementById('qbX').click(); document.activeElement.blur()`);
  await ev(ap, `[...document.querySelectorAll('#tray .chip .x')].forEach((b) => b.click()); document.getElementById('qbX').click(); document.activeElement.blur()`);
  const hold = async (p) => { const r = await ev(p, `(() => { const b = document.getElementById('mic').getBoundingClientRect(); return { x: b.x + b.width / 2, y: b.y + b.height / 2 }; })()`); await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: r.x, y: r.y }] }); p.__r = r; };
  await both('09-recording', async () => hold(rp), async () => { await hold(ap); await sleep(1300); });
  for (const p of [rp, ap]) { await p.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: p.__r.x, y: p.__r.y - 120 }] }); await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); }
  await sleep(500);
  await both('10-offline', async () => { R.down = true; for (const s of R.sse) s.destroy(); R.sse.clear(); }, AJ.offline);
  writeFileSync(join(OUT, 'pairs.json'), JSON.stringify({ size: `${W}x${H}`, pairs: pairs.map((n) => ({ state: n, relay: `${n}-relay.png`, agentj: `${n}-agentj.png`, pair: `${n}-pair.png` })) }, null, 1) + '\n');
  // montage: all pairs, half size, two per row
  const imgs = pairs.map((n) => `<figure style="margin:0"><figcaption style="height:22px">${n}</figcaption><img src="data:image/png;base64,${readFileSync(join(OUT, `${n}-pair.png`)).toString('base64')}" style="width:420px"></figure>`).join('');
  const mw = 2 * 420 + 30, mh = Math.ceil(pairs.length / 2) * 490 + 40;
  const mp = await newPage(B, mw, mh, 'light', { allow: [] });
  await navigate(mp, 'data:text/html;base64,' + Buffer.from(`<!doctype html><meta charset="utf-8"><body style="margin:0;padding:10px;background:#2c2b26;font:600 13px system-ui;color:#faf9f5;display:grid;grid-template-columns:420px 420px;gap:10px">${imgs}</body>`).toString('base64'));
  await sleep(400);
  writeFileSync(join(OUT, 'montage.png'), await shoot(mp, null, { full: true }));
  await mp.dispose();
  await rp.dispose(); await ap.dispose();
} catch (e) { console.error(e); process.exitCode = 1; }
finally { await B.close(); await web.stop(); if (fake) await fake.stop(); if (real) await real.stop().catch(() => {}); relaySrv.closeAllConnections?.(); relaySrv.close(); }
console.log(`${pairs.length} pairs → ${OUT}`);
