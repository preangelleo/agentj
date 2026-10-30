#!/usr/bin/env node
// P205 item 6 (Leo 10-13, Mac Chrome 0.20.1a1: 「这个音频你发过来我下载不了」) — the file card's 下载 icon in real Chromium against
// the fake host: a reply that links 思念.mp3 (1.06 MB) and 文案.md inline (Markdown links, as Jarvis sent them), plus a
// strip of other names (spaces, emoji) and kinds (video, PDF, a big one that waits for a tap). Each icon is clicked with a
// real mouse / touch event; Chromium's own download events must report the file, its name and the exact bytes.
//   node web/test/p205_dl.mjs
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync, rmSync, existsSync } from 'node:fs';
import { createHash, randomBytes } from 'node:crypto';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, sleep, shoot } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = join(ROOT, 'reports/qa/p205'); mkdirSync(OUT, { recursive: true });
const sha = (b) => createHash('sha256').update(b).digest('hex');
const CHUNK = 45056, WINDOW = 8;
const id = (c) => c.repeat(22);
const BYTES = new Map();
const item = (mid, name, mime, kind, bytes) => { BYTES.set(mid, bytes); return { mid, name, mime, kind, bytes: bytes.length, sha256: sha(bytes), ref: '~/work/audio/memorial/' + name }; };
// an MP3-looking body (ID3 header + frames of noise): the page never decodes it for a download
const mp3 = (n) => Buffer.concat([Buffer.from('ID3\x04\x00\x00\x00\x00\x00\x00', 'latin1'), randomBytes(n - 10)]);
const ITEMS = [
  item(id('A'), '思念.mp3', 'audio/mpeg', 'audio', mp3(1_060_000)),
  item(id('M'), '文案.md', 'text/markdown', 'file', Buffer.from('# 文案\n\n思念如歌。\n')),
  item(id('E'), 'my notes 🎉 v2.txt', 'text/plain', 'file', Buffer.from('spaces and an emoji in the name\n')),
  item(id('V'), '会议 录像.mp4', 'video/mp4', 'video', Buffer.concat([Buffer.from([0, 0, 0, 0x18]), Buffer.from('ftypmp42'), randomBytes(40_000)])),
  item(id('P'), '报告 final.pdf', 'application/pdf', 'pdf', Buffer.concat([Buffer.from('%PDF-1.4\n'), randomBytes(20_000)])),
  item(id('B'), '长音频 big.mp3', 'audio/mpeg', 'audio', mp3(3_200_000)),
];
const BIGB = BYTES.get(id('B'));
const REPLY = ['董事长，音频和文案都在这里：', '', '- [思念.mp3](~/work/audio/memorial/思念.mp3)',
  '- [文案.md](~/work/audio/memorial/文案.md)', '', '其余文件见下方卡片。'].join('\n');

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
const rec = parityRecorder('web/test/p205_dl.mjs');
const results = {};
let failed = 0;
const ev = (p, s) => evaluate(p, s);

function serve(st) {
  st.onApp = async (c, m, send) => {
    if (m.t !== 'media_get') return false;
    const b = BYTES.get(m.mid);
    if (!b) { await send({ t: 'media_err', mid: m.mid, why: 'gone' }); return true; }
    for (let i = 0; i < WINDOW; i++) {
      const o = m.o + i * CHUNK;
      if (o >= b.length) break;
      const d = b.subarray(o, Math.min(b.length, o + CHUNK));
      await send({ t: 'media_chunk', mid: m.mid, o, d: Buffer.from(d).toString('base64url'), last: o + d.length >= b.length });
    }
    return true;
  };
}
// Chromium's download events for one browser context (Browser domain, browser-level session)
const dls = [];
B.browser.on((m) => {
  if (m.method === 'Browser.downloadWillBegin') dls.push({ guid: m.params.guid, name: m.params.suggestedFilename, url: m.params.url, state: 'begun' });
  if (m.method === 'Browser.downloadProgress') { const d = dls.find((x) => x.guid === m.params.guid); if (d && m.params.state !== 'inProgress') d.state = m.params.state; }
});
async function paired(w, h, touch, { bypassCsp = false } = {}) {
  fake.reset(); serve(fake.st);
  const p = await newPage(B, w, h, 'light', { allow: [web.url, fake.relay + '/'], touch });
  p.dir = mkdtempSync(join(tmpdir(), 'aj-p205-dl-'));
  await B.browser.send('Browser.setDownloadBehavior', { behavior: 'allowAndName', browserContextId: p.ctx, downloadPath: p.dir, eventsEnabled: true });
  if (bypassCsp) await p.send('Page.setBypassCSP', { enabled: true });   // only so the test can fetch() a blob: URL to see it is live
  await navigate(p, fake.newPairing(web.url));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  const turn = await fake.addTurn({ k: 'phone', dev: null, text: '把音频发我' }, REPLY, 'done');
  await fake.updateTurn(turn.id, { media: ITEMS, media_skip: [] });
  await waitFor(p, `document.querySelectorAll('.mcard').length === ${ITEMS.length}`, 15000);
  p.turn = turn;
  return p;
}
const card = (mid) => `document.querySelector('#words .mcard[data-mid="${mid}"], #mstrip .mcard[data-mid="${mid}"]')`;
async function press(p, sel, touch) {
  // the page settles its own scroll after a reply lands (newest page pinned); scroll the icon into view until it is the hit target
  let r;
  for (let i = 0; i < 6; i++) {
    await ev(p, `${sel}.scrollIntoView({ block: 'center' })`); await sleep(400);
    r = await ev(p, `(() => { const b = ${sel}.getBoundingClientRect(), x = b.left + b.width / 2, y = b.top + b.height / 2, e = document.elementFromPoint(x, y);
      return { x, y, hit: !!(e && e.closest('button') === ${sel}) }; })()`);
    if (r.hit) break;
  }
  assert.ok(r.hit, `${sel}: not reachable on screen`);
  if (touch) {
    await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: r.x, y: r.y }] });
    await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  } else {
    for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) await p.send('Input.dispatchMouseEvent', { type, x: r.x, y: r.y, button: 'left', clickCount: 1 });
  }
}
async function downloadOne(p, m, touch) {
  const before = dls.length;
  await press(p, `${card(m.mid)}.querySelector('.mact.dl')`, touch);
  const t0 = Date.now();
  while (Date.now() - t0 < 20000 && !(dls.length > before && dls.slice(before).some((d) => d.state !== 'begun'))) await sleep(100);
  const d = dls.slice(before)[0];
  assert.ok(d, `${m.name}: Chromium saw no download`);
  assert.equal(d.state, 'completed', `${m.name}: download ${d.state}`);
  assert.equal(d.name, m.name, `${m.name}: the saved name`);
  const f = join(p.dir, d.guid);
  assert.ok(existsSync(f), `${m.name}: file on disk`);
  assert.equal(sha(readFileSync(f)), m.sha256, `${m.name}: the exact bytes`);
  return d;
}
async function C(name, fn) {
  let ok = true;
  try { await fn(); } catch (e) { ok = false; failed++; console.log(`  ✗ ${name}: ${e && e.stack || e}`); }
  if (ok) console.log(`  ✓ ${name}`);
  results[name] = ok ? 'PASS' : 'FAIL';
  rec.record(name.split(':')[0].replace(/^p205-/, ''), ok);
}

try {
  for (const [label, w, h, touch] of [['desktop', 1440, 900, false], ['phone', 390, 844, true]]) {
    await C(`p205-dl-${label}: every file card downloads with its own name and exact bytes (中文 / spaces / emoji; audio, video, PDF, text; a big one loads first)`, async () => {
      const p = await paired(w, h, touch);
      try {
        for (const m of ITEMS) await downloadOne(p, m, touch);
        // the card's player and preview still work after a download (one blob URL, not revoked by the download)
        await ev(p, `${card(id('A'))}.scrollIntoView({ block: 'center' })`);
        assert.equal(await ev(p, `!!${card(id('A'))}.querySelector('audio')?.src.startsWith('blob:')`), true);
        // the same file twice: Chromium names the second copy itself; the page fires a second download
        await downloadOne(p, ITEMS[1], touch);
        // the owner sees that it went to the browser
        assert.ok((await ev(p, `document.getElementById('toast').textContent`)).includes(ITEMS[1].name), 'a toast names the file');
        // the card's own player URL revoked (what a page re-render does to it): the download does not use it
        await ev(p, `URL.revokeObjectURL(${card(id('A'))}.querySelector('audio').src)`);
        await downloadOne(p, ITEMS[0], touch);
        assert.deepEqual(p.offsite, []); assert.deepEqual(p.problems, []);
      } finally { await p.dispose(); rmSync(p.dir, { recursive: true, force: true }); }
    });
  }
  await C('p205-dl-stale: the page re-renders without the file list (key → null: the scope revokes its URLs, the inline cards stay) — 下载 still saves', async () => {
    const p = await paired(1440, 900, false, { bypassCsp: true });
    try {
      await ev(p, `${card(id('A'))}.scrollIntoView({ block: 'center' })`);
      await waitFor(p, `${card(id('A'))}.dataset.state === 'ready'`, 15000);
      await fake.updateTurn(p.turn.id, { media: [], media_skip: [] });
      await waitFor(p, `!document.querySelector('#mstrip .mcard')`, 10000);
      assert.ok(await ev(p, `!!document.querySelector('#words .mcard[data-mid="${id('A')}"]')`), 'the inline card is still on the page');
      assert.equal(await ev(p, `fetch(${card(id('A'))}.querySelector('audio').src).then((r) => r.ok, () => false)`), true, 'its player URL is still live');
      await downloadOne(p, ITEMS[0], false);
    } finally { await p.dispose(); rmSync(p.dir, { recursive: true, force: true }); }
  });
  await C('p205-dl-fail: a file the Agent J host no longer has — the icon says so and the owner is told (no silent nothing)', async () => {
    const p = await paired(390, 844, true);
    try {
      const m = ITEMS[5];                                       // the big one: not loaded until a tap
      BYTES.delete(m.mid);
      const before = dls.length;
      await press(p, `${card(m.mid)}.querySelector('.mact.dl')`, true);
      await waitFor(p, `${card(m.mid)}.querySelector('.mact.dl').dataset.state === 'failed'`, 10000);
      assert.equal(await ev(p, `document.getElementById('toast').textContent`), '已过期');
      assert.equal(dls.length, before, 'no download');
    } finally { BYTES.set(ITEMS[5].mid, BIGB); await p.dispose(); rmSync(p.dir, { recursive: true, force: true }); }
  });
  await C('p205-dl-viewer: the full-screen preview\'s download icon saves the file too', async () => {
    const p = await paired(1440, 900, false);
    try {
      const m = ITEMS[1];
      await ev(p, `${card(m.mid)}.querySelector('.mact.pv').click()`);
      await waitFor(p, `document.querySelector('.mview')?.hidden === false`, 15000);
      const before = dls.length;
      await press(p, `document.querySelector('.mview .mvdl')`, false);
      const t0 = Date.now();
      while (Date.now() - t0 < 10000 && !(dls.length > before && dls.at(-1).state !== 'begun')) await sleep(100);
      assert.equal(dls.at(-1)?.state, 'completed'); assert.equal(dls.at(-1)?.name, m.name);
    } finally { await p.dispose(); rmSync(p.dir, { recursive: true, force: true }); }
  });
  await C('p205-dl-unsent: a finished page whose [label](~/…/.worktrees/…) never got a file list shows the label and says so — no raw Markdown; a list that arrives later still makes it a card', async () => {
    const p = await paired(390, 844, true);
    try {
      const md = item(id('W'), 'plan.md', 'text/markdown', 'file', Buffer.from('# 邮件策略\n'));
      md.ref = '~/work/proj/.worktrees/w1/reports/plan.md';
      const reply = '完整设计稿：[plan.md](~/work/proj/.worktrees/w1/reports/plan.md)\n\n另外几张单的进度：做完了。';
      const old = await fake.addTurn({ k: 'agent', text: '', via: 'task' }, reply, 'done');
      const slot = `[...document.querySelectorAll('#words .mslot[data-link="1"]')].find((s) => s.dataset.ref.includes('.worktrees'))`;
      await waitFor(p, `!!${slot}`, 10000);
      // inside the grace (the host's file list follows a page's end): still the source, no note yet
      assert.equal(await ev(p, `${slot}.dataset.unsent`), 'wait');
      writeFileSync(join(OUT, 'unsent-before.png'), await shoot(p));
      await waitFor(p, `${slot}?.dataset.unsent === '1'`, 12000);
      assert.equal(await ev(p, `${slot}.querySelector('.mlabel').textContent`), 'plan.md');
      assert.equal(await ev(p, `${slot}.querySelector('.mskip').textContent`), '这个文件没有随回复发过来，这里打不开；可以让 Agent J 再发一次');
      assert.ok(!(await ev(p, `document.getElementById('words').textContent`)).includes(']('), 'no raw Markdown on the page');
      writeFileSync(join(OUT, 'unsent-note.png'), await shoot(p));
      // the file list arrives after all (a slow scan): the slot becomes the card
      await fake.updateTurn(old.id, { media: [md], media_skip: [] });
      await waitFor(p, `!!${slot}?.querySelector('.mcard[data-mid="${md.mid}"]')`, 10000);
      assert.equal(await ev(p, `!!${slot}.querySelector('.mskip')`), false);
      writeFileSync(join(OUT, 'unsent-card.png'), await shoot(p));
      assert.deepEqual(p.offsite, []); assert.deepEqual(p.problems, []);
    } finally { await p.dispose(); rmSync(p.dir, { recursive: true, force: true }); }
  });
} finally {
  await B.close(); await fake.stop(); await web.stop();
}
writeFileSync(join(OUT, 'dl-results.json'), JSON.stringify(results, null, 1) + '\n');
console.log(JSON.stringify(results));
console.log('parity results → ' + rec.write());
console.log(failed ? `${failed} FAILED` : 'ALL PASS');
process.exit(failed ? 1 : 0);
