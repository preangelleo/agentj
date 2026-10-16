// P352 「模型和 API Key」 in Safari's engine (Playwright WebKit, iPhone 390×844 UA + 690×900 + 1440×900, light + dark), on the
// paired page against the fake host. WebKit here has no platform authenticator, so navigator.credentials.get is a stand-in
// that returns a fixed assertion (what is checked is the page's flow, not WebAuthn); navigator.clipboard.write is wrapped to
// record that it was CALLED inside the tap (before any await — Safari's rule) with a ClipboardItem whose text/plain is a
// Promise, and what the item finally held. Not physical iPhone acceptance (that stays with Leo's device).
// Usage: node web/test/p352-webkit.mjs   (needs the owner's Playwright: AJ_PLAYWRIGHT_MODULE / PLAYWRIGHT_BROWSERS_PATH)
import { createRequire } from 'node:module';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { keysHandler, FAKE_VALUE } from './p352-fixture.mjs';
const require = createRequire(import.meta.url);
const pw = require(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1';
const OUT = new URL('../reports/p352/webkit/', import.meta.url);
mkdirSync(OUT, { recursive: true });
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' }), fake = await startFakeHost();
const PAGE = web.url.replace('127.0.0.1', 'localhost');
const B = await pw.webkit.launch();
let failed = 0, passed = 0;
async function C(name, fn) {
  try { await fn(); passed++; console.log('PASS ' + name); } catch (e) { failed++; console.log('FAIL ' + name + ': ' + (e.stack || e)); }
}
const FA = 'cDM1Mi13ZWJraXQtY3JlZA';
// Any real pointer input on this page (even a blank spot; the BASE tree too) crashes WebKit-on-Linux's web process in this
// environment, so the controls are pressed with element.click() — the page's handlers and its synchronous calls inside them
// are what is under test; real-gesture rules stay with Leo's iPhone.
let p;
const press = (sel) => p.evaluate((s) => document.querySelector(s).click(), sel);
try {
  const calls = keysHandler(fake.st, { fa: FA });
  const ctx = await B.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, userAgent: UA, colorScheme: 'light' });
  p = await ctx.newPage();
  const problems = [];
  p.on('crash', () => problems.push('web process crashed'));
  p.on('pageerror', (e) => problems.push(String(e)));
  await p.addInitScript(() => {
    const ab = (s) => new TextEncoder().encode(s).buffer;
    window.__fa = 0;
    // this WebKit build has no navigator.credentials at all: a stand-in object
    if (!navigator.credentials) Object.defineProperty(navigator, 'credentials', { configurable: true, value: {} });
    navigator.credentials.get = (o) => { window.__fa++; window.__faSync = !!o; return Promise.resolve({ rawId: ab('p352-webkit-cred'),
      response: { clientDataJSON: ab('{}'), authenticatorData: ab('ad'), signature: ab('sig'), userHandle: null } }); };
    window.PublicKeyCredential ??= function () {};
    window.__clip = { calls: 0, text: null, promise: false };
    const real = navigator.clipboard?.write?.bind(navigator.clipboard);
    if (navigator.clipboard) navigator.clipboard.write = (items) => {
      window.__clip.calls++;
      const item = items[0];
      const got = item.getType('text/plain').then((b) => b.text()).then((t) => { window.__clip.text = t; });
      window.__clip.promise = true;
      return got.then(() => (real ? real(items).catch(() => {}) : undefined));
    };
  });
  await p.goto(fake.newPairing(PAGE + '?lang=zh'));
  await p.waitForFunction(() => window.__ajState === 'awaiting-approval', null, { timeout: 20000 });
  await fake.approve();
  await p.waitForFunction(() => window.__ajState === 'ready', null, { timeout: 20000 });
  await p.evaluate(() => document.getElementById('setBtn').click());
  await p.waitForTimeout(300);
  await p.evaluate(() => document.getElementById('phone-env-dismiss')?.click());
  await p.evaluate(() => document.getElementById('open-models').click());
  try { await p.waitForFunction(() => document.querySelectorAll('#keys-box .krow').length > 0, null, { timeout: 15000 }); }
  catch (e) { console.log('debug', await p.evaluate(() => [document.body.dataset.view, document.getElementById('keys-status')?.textContent, document.getElementById('settings').hidden]), problems, fake.log.map((m) => m.t).slice(-8)); throw e; }
  await p.waitForTimeout(300);

  await C('webkit: renamed title, two sections, dashed vendor cards', async () => {
    assert.equal(await p.locator('#models-view h1').textContent(), '模型和 API Key');
    assert.deepEqual(await p.$$eval('#keys-box h2', (h) => h.map((x) => x.textContent)), ['基座模型', '第三方 API Key']);
    assert.ok(await p.$$eval('#keys-box .kvendor', (c) => c.every((x) => getComputedStyle(x).borderTopStyle === 'dashed')));
  });
  await C('webkit: icon buttons — aria-label + title, ≥ 44 px, ≥ 8 px apart', async () => {
    const b = await p.$$eval('#keys-box .kbtn', (bs) => bs.map((x) => { const r = x.getBoundingClientRect(); return { a: x.getAttribute('aria-label'), t: x.title, w: r.width, h: r.height }; }));
    assert.ok(b.length > 10 && b.every((x) => x.a && x.t && x.w >= 44 && x.h >= 44), JSON.stringify(b.slice(0, 3)));
    assert.ok(await p.$$eval('#keys-box .kacts', (as) => as.every((a) => { const r = [...a.children].map((x) => x.getBoundingClientRect()); return r.every((x, i) => i === 0 || x.left - r[i - 1].right >= 8); })));
  });
  for (const scheme of ['light', 'dark']) for (const [w, h] of [[390, 844], [690, 900], [1440, 900]]) {
    await p.emulateMedia({ colorScheme: scheme }); await p.setViewportSize({ width: w, height: h });
    // the page's theme = the host preference appearance.theme (as p303 / p314 set it); menu pages stay Noir in both (P299)
    await fake.send({ t: 'preferences', value: { appearance: { language: 'zh', theme: scheme }, voice: { speak_replies: false, wake_enabled: false } }, problem: null });
    await p.waitForFunction((t) => document.documentElement.getAttribute('data-theme') === t, scheme);
    await p.waitForTimeout(250);
    await p.evaluate(() => { document.querySelector('.ajpages').scrollTop = 0; });
    await p.screenshot({ path: new URL(`${scheme}-${w}x${h}.png`, OUT).pathname });
  }
  await p.emulateMedia({ colorScheme: 'light' }); await p.setViewportSize({ width: 390, height: 844 });
  await C('webkit: 拷贝 — Face ID and clipboard.write(ClipboardItem(Promise)) both start in the tap; value never in the page', async () => {
    // both calls happen inside the click handler itself (nothing awaited before them): read back in the same task
    const sync = await p.evaluate(() => { document.querySelector('.krow[data-name="OPENAI_API_KEY"] .kbtn--copy').click(); return [window.__fa, window.__clip.calls]; });
    assert.deepEqual(sync, [1, 1], 'approve() and clipboard.write() both ran synchronously in the click');
    try { await p.waitForFunction(() => window.__clip.text !== null, null, { timeout: 10000 }); }
    catch (e) { console.log('debug', JSON.stringify(await p.evaluate(() => ({ toast: document.getElementById('toast').textContent, fa: window.__fa, clip: window.__clip, cred: !!navigator.credentials, pkc: !!window.PublicKeyCredential, sec: isSecureContext, ci: typeof ClipboardItem }))), JSON.stringify(calls)); throw e; }
    const clip = await p.evaluate(() => window.__clip);
    assert.equal(clip.calls, 1); assert.equal(clip.promise, true);
    assert.equal(clip.text, FAKE_VALUE);
    assert.equal(await p.evaluate(() => window.__fa), 1);
    const c = calls.find((x) => x.op === 'copy');
    assert.ok(c && c.name === 'OPENAI_API_KEY' && c.fa);
    assert.ok(!(await p.evaluate((v) => document.documentElement.outerHTML.includes(v) || JSON.stringify(localStorage).includes(v), FAKE_VALUE)));
    await p.waitForTimeout(400);
    await p.screenshot({ path: new URL('light-390x844-copied.png', OUT).pathname });
  });
  await C('webkit: 删除 on the key in use — strong warning, 删除 sends keys_op delete', async () => {
    await p.waitForTimeout(300);
    await press('.krow[data-name="ANTHROPIC_API_KEY"] .kbtn--delete');
    await p.waitForFunction(() => !document.getElementById('confirm').hidden);
    assert.match(await p.locator('#confirm-text').textContent(), /主 Agent 现在正用这个 Key/);
    await p.screenshot({ path: new URL('light-390x844-delete-inuse.png', OUT).pathname });
    await press('#confirm-yes');
    await p.waitForFunction(() => document.getElementById('toast').textContent.includes('已删除'), null, { timeout: 10000 });
    assert.ok(calls.some((x) => x.op === 'delete' && x.name === 'ANTHROPIC_API_KEY' && x.fa));
  });
  await C('webkit: 修改 / 添加 → keys_set; Esc closes the add form, then leaves the page', async () => {
    await p.waitForFunction(() => document.querySelectorAll('#keys-box .krow').length > 0);
    await press('.krow[data-name="OPENAI_API_KEY"] .kbtn--edit');
    await p.waitForFunction(() => document.getElementById('toast').textContent.includes('密钥卡'));
    assert.ok(calls.some((x) => x.op === 'set' && x.name === 'OPENAI_API_KEY'));
    await press('#keys-add');
    await p.evaluate(() => { const i = document.getElementById('keys-add-name'); i.value = 'MY_SERVICE_TOKEN'; i.dispatchEvent(new Event('input')); });
    await press('.kadd-go');
    await p.waitForTimeout(300);
    assert.ok(calls.some((x) => x.op === 'set' && x.name === 'MY_SERVICE_TOKEN'));
    await press('#keys-add');
    await p.keyboard.press('Escape'); await p.waitForTimeout(200);
    assert.ok(await p.evaluate(() => document.querySelector('.kadd').hidden && document.body.dataset.view === 'models'));
    await p.evaluate(() => document.activeElement?.blur());
    await p.keyboard.press('Escape'); await p.waitForTimeout(300);
    assert.notEqual(await p.evaluate(() => document.body.dataset.view), 'models');
  });
  await C('webkit: no page errors', async () => { assert.deepEqual(problems, []); });
  await ctx.close();
} finally { await B.close(); await fake.stop(); await web.stop(); }
console.log(`p352 webkit: ${passed} passed, ${failed} failed; shots → ${OUT.pathname}`);
process.exit(failed ? 1 : 0);
