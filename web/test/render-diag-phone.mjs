// Render-health counters end to end: the real web client + Noise fakehost + ONE headless Chromium (software WebGL).
// Synthetic content only (the two-message fixture of the 2026-11-22 blank-card diagnosis: picture + three tables + file
// link + reply buttons, then a short text reply). Checks what reaches the host in `layout_diag`, never a screenshot of chat.
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync} from 'node:fs';
import {createHash, randomBytes} from 'node:crypto';
import {deflateSync, crc32} from 'node:zlib';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch, newPage, navigate, waitState, waitFor, evaluate, shoot, sleep} from './browser.mjs';

const out = process.env.RENDER_DIAG_OUT || 'reports/qa/render-diag-1122'; mkdirSync(out, {recursive: true});
function png(w, h) {
  const chunk = (t, d) => { const len = Buffer.alloc(4); len.writeUInt32BE(d.length); const td = Buffer.concat([Buffer.from(t), d]); const c = Buffer.alloc(4); c.writeUInt32BE(crc32(td)); return Buffer.concat([len, td, c]); };
  const hd = Buffer.alloc(13); hd.writeUInt32BE(w, 0); hd.writeUInt32BE(h, 4); hd[8] = 8; hd[9] = 2;
  const raw = Buffer.alloc((w * 3 + 1) * h); for (let y = 0; y < h; y++) { const o = y * (w * 3 + 1); raw[o] = 0; randomBytes(w * 3).copy(raw, o + 1); }
  return Buffer.concat([Buffer.from('\x89PNG\r\n\x1a\n', 'latin1'), chunk('IHDR', hd), chunk('IDAT', deflateSync(raw, {level: 1})), chunk('IEND', Buffer.alloc(0))]);
}
const IMG = png(800, 520), DOC = Buffer.from('# synthetic\n\nfixture document\n');   // the picture is > 1 MiB of compressed bytes
const sha = (b) => createHash('sha256').update(b).digest('hex');
const media = [
  {mid: 'A'.repeat(22), name: 'sheet.png', ref: '/srv/fixture/out/sheet.png', mime: 'image/png', kind: 'image', bytes: IMG.length, sha256: sha(IMG)},
  {mid: 'B'.repeat(22), name: 'draft.md', ref: '/srv/fixture/out/draft.md', mime: 'text/markdown', kind: 'file', bytes: DOC.length, sha256: sha(DOC)},
];
const BY = {[media[0].mid]: IMG, [media[1].mid]: DOC};
const row = (i) => `| 角色${i} | 这是第 ${i} 行的一句台词，写得稍微长一点好让它在手机上折成两三行。 | 画面说明第 ${i} 条，同样会折行 |`;
const table = (n, from = 1) => ['| 谁 | 台词 | 画面 |', '|---|---|---|', ...Array.from({length: n}, (_, i) => row(from + i))].join('\n');
const LONG = ['合成夹具：第一段是说明文字，下面是一张图。', '![设定图](/srv/fixture/out/sheet.png)', '## 5A · 第一张表（30 秒）', table(7), '## 5B · 第二张表（27 秒）', table(5, 8), '## 5C · 第三张表', table(4, 13),
  '全稿（含每个镜头的说明）：', '[draft.md](/srv/fixture/out/draft.md)', '[定稿，先出第一段](reply:定稿，先出第一段) [还要改，我说哪里](reply:还要改，我说哪里)'].join('\n\n');
const SHORT = ['这是一条纯文字回复的第一段，**这里有加粗**，后面还有一个 [外部链接](https://example.com/x) 和普通句子。', '第二段再写两行，确认段落之间的间距与换行都正常，没有任何图片或表格。',
  '- 列表第一项，内容稍长，折成两行看看效果如何\n- 列表第二项，**带加粗**\n- 列表第三项', '[定稿，先出第一段](reply:定稿，先出第一段)'].join('\n\n');
const ASK = '合成提问（长）';

const ENUM = {reason: ['manual', 'context_lost', 'long_hidden'], agent: ['android-chrome', 'android-webview', 'android-firefox', 'android-other', 'ios-safari', 'ios-other', 'desktop-chrome', 'desktop-safari', 'desktop-firefox', 'desktop-other', 'other'],
  alive: ['lt1m', 'lt10m', 'lt1h', 'lt6h', 'lt1d', 'lt3d', 'ge3d'], hiddenMax: ['none', 'lt1m', 'lt10m', 'lt1h', 'lt6h', 'ge6h'], gl: ['pending', 'ok', 'lost', 'unavailable'],
  cache: ['none', 'lt1mb', 'lt8mb', 'lt32mb', 'lt64mb', 'ge64mb', 'unknown'], cards: ['none', 'lt10', 'lt50', 'lt200', 'lt500', 'lt1000', 'ge1000', 'unknown'], nodes: ['lt500', 'lt1500', 'lt5000', 'lt15000', 'ge15000', 'unknown']};
const TOP = ['v', 'at', 'ios', 'standalone', 'ua', 'theme', 'status', 'conn', 'kb', 'known', 'compacting', 'level', 'bottom', 'innerHeight', 'clientHeight', 'vv', 'layers', 'meter', 'look', 'build', 'render'];
function closed(sample) {
  assert.deepEqual(Object.keys(sample).sort(), [...TOP].sort());
  const r = sample.render;
  assert.deepEqual(Object.keys(r).sort(), [...Object.keys(ENUM), 'hidden', 'glLost', 'glRestored', 'mem'].sort());
  for (const [k, values] of Object.entries(ENUM)) assert.ok(values.includes(r[k]), `${k}=${r[k]}`);
  for (const [k, top] of [['hidden', 9999], ['glLost', 99], ['glRestored', 99]]) assert.ok(Number.isInteger(r[k]) && r[k] >= 0 && r[k] <= top, k);
  assert.ok(r.mem === null || [0.25, 0.5, 1, 2, 4, 8, 16, 32].includes(r.mem));
}
// Nothing of the conversation, the files, the addresses or the browser's own string may be in what was sent.
function contentFree(samples, extra) {
  const text = JSON.stringify(samples);
  assert.ok(/^[\x20-\x7e]*$/.test(text), 'ASCII only: no message text can be inside');
  const words = new Set([LONG, SHORT, ASK].join(' ').match(/[A-Za-z][A-Za-z0-9]{3,}/g));
  for (const w of [...words, 'sheet.png', 'draft.md', '.png', '.md', '/srv', 'http', '://', 'example.com', 'Mozilla', 'Android', 'AppleWebKit', 'Chrome/', ...extra]) assert.ok(!text.includes(w), `"${w}" must not be in a sample`);
  for (const s of samples) for (const v of Object.values(s.render)) assert.ok(v === null || typeof v === 'number' || /^[a-z0-9_-]{1,16}$/.test(v));
}

const UA = 'Mozilla/5.0 (Linux; Android 15; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Mobile Safari/537.36';
// Before any page script: a skewable clock (ten hidden minutes without waiting ten minutes) and a handle on WebGL contexts.
const HOOK = (mode) => `(() => {
  const real = Date.now.bind(Date); window.__skew = 0; Date.now = () => real() + window.__skew;
  const get = HTMLCanvasElement.prototype.getContext; window.__gl = [];
  HTMLCanvasElement.prototype.getContext = function (kind, ...rest) {
    if (/webgl/.test(kind)) { ${mode === 'throw' ? `throw new Error('webgl blocked');` : mode === 'null' ? 'return null;' : ''} }
    const g = get.call(this, kind, ...rest); if (g && /webgl/.test(kind)) window.__gl.push(g); return g;
  };
})()`;
const hide = (p, on) => evaluate(p, `(() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => ${on}}); Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => '${on ? 'hidden' : 'visible'}'}); document.dispatchEvent(new Event('visibilitychange')); })()`);
const manual = (p) => evaluate(p, `import('./js/layout-diag.js').then((m) => m.send()).then(() => document.getElementById('layout-diag-result').textContent)`);

const web = await startWebServer({port: 0, relayCsp: 'ws://127.0.0.1:*'}), fake = await startFakeHost();
const B = await launch({args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist']});
const results = {}; let got = [], oldHost = false;
async function open(width, theme, mode, ua) {
  fake.reset(); got = [];
  fake.st.prefs = {appearance: {theme, language: 'zh'}, voice: {speak_replies: false}};
  fake.st.onApp = async (conn, m, send) => {
    if (m.t === 'layout_diag') {
      got.push(JSON.parse(JSON.stringify(m.sample)));
      await send(oldHost && m.sample.render ? {t: 'layout_diag_res', r: m.r, ok: false, why: 'save_failed'} : {t: 'layout_diag_res', r: m.r, ok: true});
      return true;
    }
    if (m.t !== 'media_get') return false;
    const b = BY[m.mid]; if (!b) return false;
    for (let i = 0; i < 8; i++) { const o = m.o + i * 45056; if (o >= b.length) break; const d = b.subarray(o, o + 45056); await send({t: 'media_chunk', mid: m.mid, o, d: d.toString('base64url'), last: o + d.length >= b.length}); }
    return true;
  };
  const p = await newPage(B, width, 873, theme, {ua, touch: !!ua, allow: [web.url, fake.relay + '/']});
  await p.send('Page.addScriptToEvaluateOnNewDocument', {source: HOOK(mode)});
  await navigate(p, fake.newPairing(web.url + '?lang=zh')); await waitState(p, 'awaiting-approval', 30000); await fake.approve(); await waitState(p, 'ready', 30000);
  await evaluate(p, `document.getElementById('phone-env-dismiss')?.click();if(document.getElementById('a2hs')&&!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click();`);
  await fake.send({t: 'status', s: 'idle', agent: 'claude', name: 'Agent J'});
  await fake.addTurn({k: 'phone', text: '合成提问'}, SHORT, 'done');
  const turn = await fake.addTurn({k: 'phone', text: ASK}, LONG, 'done'); await fake.updateTurn(turn.id, {media});
  await waitFor(p, `document.getElementById('words').textContent.includes('第三张表')`, 20000);
  await waitFor(p, `(document.querySelector('#words .mcard img')?.naturalWidth || 0) > 0`, 30000);
  await waitFor(p, `import('./js/render-diag.js').then((m) => m.snapshot('manual').gl !== 'pending')`, 15000);
  return p;
}
const forbidden = () => [new URL(web.url).host, new URL(fake.relay).host, '127.0.0.1', 'localhost'];
try {
  // ── 1. Android phone, dark: normal sample → long hidden → lost GPU context → hourly cap ─────────────────────────────
  let p = await open(393, 'dark', 'ok', UA);
  try {
    assert.equal(await evaluate(p, 'window.__gl.length'), 1, 'one canary context, created once');
    assert.equal(await evaluate(p, `[...document.querySelectorAll('canvas')].filter((c) => c.width === 1 && c.height === 1 || c === window.__gl[0].canvas).length`), 0, 'the canary is not in the page');
    assert.equal(got.length, 0, 'a healthy page sends nothing by itself');
    // a) the owner's own 「发送画面诊断」
    assert.match(await manual(p), /已保存/); assert.equal(got.length, 1); closed(got[0]);
    assert.deepEqual({...got[0].render, alive: 0, nodes: 0}, {reason: 'manual', agent: 'android-chrome', alive: 0, hidden: 0, hiddenMax: 'none', gl: 'ok', glLost: 0, glRestored: 0,
      mem: got[0].render.mem, cache: 'lt8mb', cards: 'lt10', nodes: 0});
    assert.match(got[0].build.version, /^\d+\.\d+\.\d+/, 'the page version rides in build.version');
    // a short trip to the background is counted and asks for nothing
    await hide(p, true); await evaluate(p, 'window.__skew += 9 * 60000'); await hide(p, false); await sleep(1200);
    assert.equal(got.length, 1);
    // ten minutes hidden → exactly one sample when the page is back in front
    await hide(p, true); await sleep(300); assert.equal(got.length, 1, 'nothing is sent from the background');
    await evaluate(p, 'window.__skew += 11 * 60000'); await hide(p, false);
    await waitFor(p, 'true', 100); for (let i = 0; i < 60 && got.length < 2; i++) await sleep(100);
    await sleep(1500); assert.equal(got.length, 2, 'exactly one sample for one long absence'); closed(got[1]);
    assert.equal(got[1].render.reason, 'long_hidden'); assert.equal(got[1].render.hidden, 2); assert.equal(got[1].render.hiddenMax, 'lt1h'); assert.equal(got[1].render.alive, 'lt1h');
    // b) the GPU context is lost → exactly one sample, with the reason
    await evaluate(p, `(window.__ext ||= window.__gl[0].getExtension('WEBGL_lose_context')).loseContext()`);
    for (let i = 0; i < 60 && got.length < 3; i++) await sleep(100);
    await sleep(1500); assert.equal(got.length, 3, 'exactly one sample for one lost context'); closed(got[2]);
    assert.equal(got[2].render.reason, 'context_lost'); assert.equal(got[2].render.gl, 'lost'); assert.equal(got[2].render.glLost, 1); assert.equal(got[2].render.glRestored, 0);
    // c) five more losses within the hour: one more sample (3 per page per hour), counters keep counting
    for (let i = 0; i < 5; i++) {
      await evaluate(p, `(window.__ext ||= window.__gl[0].getExtension('WEBGL_lose_context')).restoreContext()`); await waitFor(p, '!window.__gl[0].isContextLost()', 5000);
      await evaluate(p, `(window.__ext ||= window.__gl[0].getExtension('WEBGL_lose_context')).loseContext()`); await sleep(900);
    }
    await sleep(1500); assert.equal(got.length, 4, 'the hourly cap holds'); closed(got[3]); assert.equal(got[3].render.reason, 'context_lost'); assert.equal(got[3].render.glLost, 2);
    assert.match(await manual(p), /已保存/); assert.equal(got.length, 5, 'the owner can still send one by hand'); closed(got[4]);
    assert.equal(got[4].render.reason, 'manual'); assert.equal(got[4].render.glLost, 6); assert.equal(got[4].render.glRestored, 5);
    // after the hour there is room again
    await evaluate(p, 'window.__skew += 61 * 60000');
    await evaluate(p, `(window.__ext ||= window.__gl[0].getExtension('WEBGL_lose_context')).restoreContext()`); await waitFor(p, '!window.__gl[0].isContextLost()', 5000);
    await evaluate(p, `(window.__ext ||= window.__gl[0].getExtension('WEBGL_lose_context')).loseContext()`);
    for (let i = 0; i < 60 && got.length < 6; i++) await sleep(100);
    assert.equal(got.length, 6); assert.equal(got[5].render.reason, 'context_lost'); assert.equal(got[5].render.alive, 'lt6h');
    // d) nothing of the conversation in any of them; the reply is still on screen and nothing visible was added
    contentFree(got, forbidden());
    assert.ok(await evaluate(p, `document.getElementById('words').textContent.includes('第三张表') && !window.__gl[0].canvas.isConnected`));
    assert.deepEqual(p.problems, []);
    await evaluate(p, `document.getElementById('settings')?.close?.()`);
    writeFileSync(`${out}/phone-393-dark.png`, await shoot(p));
    results.phone = {samples: got.map((s) => s.render), problems: p.problems.length};
  } finally { await p.dispose(); }

  // ── 2. Desktop 1440, light, a browser without WebGL (getContext throws), and a host from before the counters ─────────
  for (const mode of ['throw', 'null']) {
    p = await open(1440, 'light', mode, undefined);
    try {
      assert.equal(await evaluate(p, 'window.__gl.length'), 0);
      oldHost = mode === 'null';
      const text = await manual(p); assert.match(text, /已保存/);
      if (oldHost) {
        assert.equal(got.length, 2, 'refused with the counters, accepted without'); closed(got[0]); assert.equal(got[0].render.gl, 'unavailable');
        assert.ok(!('render' in got[1])); assert.deepEqual(Object.keys(got[1]).sort(), TOP.filter((k) => k !== 'render').sort());
        contentFree([got[0]], forbidden());
      } else {
        assert.equal(got.length, 1); closed(got[0]);
        assert.equal(got[0].render.gl, 'unavailable'); assert.equal(got[0].render.glLost, 0); assert.equal(got[0].render.agent, 'desktop-chrome'); assert.equal(got[0].render.reason, 'manual');
        contentFree(got, forbidden());
      }
      await hide(p, true); await evaluate(p, 'window.__skew += 11 * 60000'); await hide(p, false);
      const n = got.length; for (let i = 0; i < 60 && got.length === n; i++) await sleep(100);
      assert.equal(got.at(-1).render.reason, 'long_hidden', 'the other counters work without WebGL');
      assert.deepEqual(p.problems, [], 'no WebGL is not an error');
      if (mode === 'throw') writeFileSync(`${out}/desktop-1440-light.png`, await shoot(p));
      results['desktop-' + mode] = {samples: got.map((s) => s.render || null), problems: p.problems.length};
    } finally { oldHost = false; await p.dispose(); }
  }
  writeFileSync(`${out}/results.json`, JSON.stringify(results, null, 1));
  console.log('render-diag-phone: PASS', JSON.stringify(Object.fromEntries(Object.entries(results).map(([k, v]) => [k, v.samples.length]))));
} finally { await B.close(); await fake.stop(); await web.stop(); }
