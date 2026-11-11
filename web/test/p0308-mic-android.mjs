// 0.30.8 (chairman 11-11 09:2x, Oppo, Android home-screen app): one tap on the microphone used to open the recording card;
// now the card opens and closes at once, three or four taps until it stays. The real paired page in Chromium with Android
// emulation (Pixel UA, touch, display-mode standalone) and a controlled getUserMedia: the native permission prompt is played
// as the event orders Android Chrome can deliver around it — the press taken away (pointercancel / lost capture) before the
// window blur, the page hidden and shown again by a system dialog, then focus back — before the grant / denial settles.
// The prompt itself, ColorOS and a real Oppo are not emulated (GAP, see the report).
//   node web/test/p0308-mic-android.mjs              → the working tree
//   node web/test/p0308-mic-android.mjs --baseline   → ffd7ca9b9d (0.30.7 cand) public/, expected red
// Screens (390 / 1440 × dark / light × zh / en): idle · permission pending · recording · denied → reports/qa/p0308-mic/<after|before>/
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {mkdirSync, mkdtempSync, rmSync, writeFileSync} from 'node:fs';
import {homedir} from 'node:os';
import {join} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';

const pw = createRequire(import.meta.url)(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const baseline = process.argv.includes('--baseline'), BASE = process.env.P0308_BASE || 'ffd7ca9b9d';
const out = join(process.env.OLARES_RUN_OUT || 'reports/qa/p0308-mic', baseline ? 'before' : 'after');
mkdirSync(out, {recursive: true});
let temp = null;
if (baseline) {
  mkdirSync(join(homedir(), '.cache'), {recursive: true});
  temp = mkdtempSync(join(homedir(), '.cache', 'p0308-base-'));
  execFileSync('tar', ['-x', '-C', temp], {input: execFileSync('git', ['archive', BASE, 'web/public'], {maxBuffer: 200e6})});
}
const web = await startWebServer(baseline ? {publicDir: temp + '/web/public'} : {}), fake = await startFakeHost();
const browser = await pw.chromium.launch(process.env.CHROMIUM ? {executablePath: process.env.CHROMIUM} : {});
const ANDROID_UA = 'Mozilla/5.0 (Linux; Android 14; PHB110 Build/UP1A.231005.007) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36';
const rows = [], shots = [];

const stubs = () => {
  window.Audio = undefined;
  window.AudioContext = window.webkitAudioContext = class { resume() { return Promise.resolve(); } close() { return Promise.resolve(); } createMediaStreamSource() { throw Error('meter unavailable'); } };
  window.__vis = 'visible';
  Object.defineProperty(document, 'visibilityState', {get: () => window.__vis, configurable: true});
  Object.defineProperty(document, 'hidden', {get: () => window.__vis !== 'visible', configurable: true});
  const mm = window.matchMedia.bind(window);   // the Android home-screen app (WebAPK): display-mode standalone
  window.matchMedia = (q) => /display-mode:\s*standalone/.test(q) ? mm('(min-width:0px)') : mm(q);
  window.__requests = []; window.__tracks = []; window.__recs = [];
  Object.defineProperty(navigator, 'mediaDevices', {configurable: true, value: {getUserMedia: (c) => new Promise((resolve, reject) => window.__requests.push({c, resolve, reject}))}});
  window.__source = () => {
    const tracks = [Object.assign(new EventTarget(), {enabled: true, readyState: 'live', stop() { this.enabled = false; this.readyState = 'ended'; }, getSettings: () => ({noiseSuppression: false})})];
    window.__tracks.push(tracks);
    return {getTracks: () => tracks, getAudioTracks: () => tracks, getVideoTracks: () => []};
  };
  window.MediaRecorder = class { static isTypeSupported() { return true; } constructor(s) { this.stream = s; this.state = 'inactive'; this.mimeType = 'audio/webm'; window.__recs.push(this); } start() { this.state = 'recording'; } requestData() {} stop() { this.state = 'inactive'; setTimeout(() => this.onstop?.(), 20); } };
};

try {
  for (const width of [390, 1440]) for (const scheme of ['dark', 'light']) for (const lang of ['zh', 'en']) {
    const ctx = await browser.newContext({viewport: {width, height: 844}, colorScheme: scheme, isMobile: width === 390, hasTouch: true, userAgent: ANDROID_UA, deviceScaleFactor: width === 390 ? 2 : 1, serviceWorkers: 'block', locale: lang === 'zh' ? 'zh-CN' : 'en-US'});
    await ctx.addInitScript(stubs);
    const p = await ctx.newPage(), errors = [];
    p.on('pageerror', (e) => errors.push(e.message));
    fake.reset(); fake.setUp(true);
    await p.goto(fake.newPairing(web.url + '?lang=' + lang));
    await p.waitForFunction(() => window.__ajState === 'awaiting-approval').catch(async (e) => { console.error(JSON.stringify({phase: 'pairing', state: await p.evaluate(() => window.__ajState), view: await p.evaluate(() => document.body.dataset.view), errors})); throw e; });
    await fake.approve();
    await p.waitForFunction(() => window.__ajState === 'ready');
    await p.evaluate(() => { document.getElementById('phone-env-dismiss')?.click(); document.getElementById('a2hs-ok')?.click(); });
    await p.waitForTimeout(300);
    const tag = `${width}-${scheme}-${lang}`;
    const shot = async (state) => { const f = `${out}/${tag}-${state}.png`; await p.screenshot({path: f}); shots.push(f); };
    const ev = (type, extra = {}) => p.locator('#mic').dispatchEvent(type, {pointerId: 1, pointerType: 'touch', button: 0, isPrimary: true, clientX: 100, clientY: 760, ...extra});
    const begin = async () => { const n = await p.evaluate(() => window.__requests.length); await ev('pointerdown'); await p.waitForFunction((n) => window.__requests.length === n + 1, n); };
    const up = () => ev('pointerup');
    const blur = () => p.evaluate(() => dispatchEvent(new Event('blur')));
    const focus = () => p.evaluate(() => dispatchEvent(new Event('focus')));
    const vis = (v) => p.evaluate((v) => { window.__vis = v; document.dispatchEvent(new Event('visibilitychange')); }, v);
    const grant = () => p.evaluate(() => window.__requests.at(-1).resolve(window.__source()));
    const deny = () => p.evaluate(() => window.__requests.at(-1).reject(new DOMException('Permission denied', 'NotAllowedError')));
    const card = () => p.evaluate(() => !document.getElementById('ptt').hidden);
    const recording = () => p.evaluate(() => !document.getElementById('ptt').hidden && window.__recs.at(-1)?.state === 'recording' && window.__recs.at(-1).stream.getTracks().every((t) => t.enabled && t.readyState === 'live'));
    const stopped = () => p.evaluate(() => window.__tracks.every((ts) => ts.every((t) => !t.enabled && t.readyState === 'ended')));
    const settle = async () => {   // no take left over for the next case
      await p.evaluate(() => { const c = document.getElementById('pttCancel'); if (!document.getElementById('ptt').hidden && c && c.getClientRects().length) c.click(); });
      await p.evaluate(() => dispatchEvent(new Event('pagehide')));
      await p.waitForTimeout(460);
    };
    const check = async (name, fn) => {
      try { await fn(); rows.push({tag, name, pass: true}); }
      catch (e) { rows.push({tag, name, pass: false, error: e.message.split('\n')[0]}); }
      await settle();
    };
    const first = tag === '390-dark-zh';   // the event-order matrix once per run; every combination gets the four screens

    await shot('idle');
    // ① Android Chrome permission prompt: the press is taken away (pointercancel + lostpointercapture) before the blur
    await check('android prompt: pointercancel before blur, granted → the card stays and records', async () => {
      await begin(); await ev('pointercancel'); await ev('lostpointercapture'); await blur();
      await p.waitForTimeout(150);
      assert.ok(await card(), 'the recording card closed while the permission prompt was up (闪退)');
      await shot('pending');
      await grant(); await focus();
      await p.waitForFunction(() => window.__recs.at(-1)?.state === 'recording', null, {timeout: 2000}).catch(() => {});
      assert.ok(await recording(), 'the granted microphone did not start recording on the first tap');
      await shot('recording');
      assert.ok(await p.locator('#pttCancel').isVisible(), 'cancel reachable');
      await p.locator('#pttCancel').click(); assert.ok(await stopped(), 'cancel stops every track');
    });
    if (first) {
      await check('android prompt: lost capture only (no pointercancel), then blur, granted → records', async () => {
        await begin(); await ev('lostpointercapture'); await p.waitForTimeout(120); await blur(); await grant(); await focus();
        await p.waitForFunction(() => window.__recs.at(-1)?.state === 'recording', null, {timeout: 2000}).catch(() => {});
        assert.ok(await recording(), 'closed on a lost capture while the microphone was opening');
      });
      // ② a system dialog hides the page for a moment (blur → hidden → visible → focus), the grant lands after the return
      await check('system dialog: blur → hidden → visible → focus, granted after return → records', async () => {
        await begin(); await ev('pointercancel'); await blur(); await vis('hidden'); await vis('visible'); await focus();
        await p.waitForTimeout(100);
        assert.ok(await card(), 'the card closed during the system permission dialog');
        await grant();
        await p.waitForFunction(() => window.__recs.at(-1)?.state === 'recording', null, {timeout: 2000}).catch(() => {});
        assert.ok(await recording(), 'the grant after the dialog did not record');
      });
      await check('system dialog after a normal pointerup: hidden → visible, granted → records', async () => {
        await begin(); await up(); await blur(); await vis('hidden'); await vis('visible'); await focus(); await grant();
        await p.waitForFunction(() => window.__recs.at(-1)?.state === 'recording', null, {timeout: 2000}).catch(() => {});
        assert.ok(await recording(), 'a locked first tap was ended by the dialog');
      });
      // privacy boundary (P496) unchanged: nothing is captured while hidden, return never asks again
      await check('P496: a grant landing while hidden is stopped; return asks nothing', async () => {
        await begin(); await ev('pointercancel'); await blur(); await vis('hidden'); await grant(); await p.waitForTimeout(80);
        assert.ok(await stopped(), 'a hidden page kept a live microphone');
        const n = await p.evaluate(() => window.__requests.length);
        await vis('visible'); await focus(); await p.waitForTimeout(80);
        assert.equal(await p.evaluate(() => window.__requests.length), n, 'coming back asked for the microphone');
        assert.equal(await card(), false, 'the aborted take left a card');
        assert.equal(await p.evaluate(() => !document.getElementById('toast').hidden && /麦克风|microphone/i.test(document.getElementById('toast').textContent)), false, 'an abort (not a refusal) said 「no microphone」');
      });
      // security r1 L3: the wait is bounded (PROMPT_HIDE_MAX_MS = 3 s) — a long hide, then the grant answered on return,
      // must not start a locked recording without another tap; a short hide (the Android prompt switch) still records
      const noMicToast = () => p.evaluate(() => !document.getElementById('toast').hidden && /麦克风|microphone/i.test(document.getElementById('toast').textContent));
      await check('L3: hidden 5 s while opening → visible → grant → closed quietly, no MediaRecorder, nothing live', async () => {
        await begin(); await ev('pointercancel'); await blur(); await vis('hidden');
        await p.waitForTimeout(5000);
        await vis('visible'); await focus(); await p.waitForTimeout(80);
        assert.equal(await card(), false, 'the take survived a 5 s hide');
        const recs = await p.evaluate(() => window.__recs.length);
        await grant(); await p.waitForTimeout(300);
        assert.equal(await p.evaluate(() => window.__recs.length), recs, 'a MediaRecorder was created after the return');
        assert.ok(await stopped(), 'the late grant was kept live');
        assert.equal(await card(), false, 'the card came back');
        assert.equal(await noMicToast(), false, 'closing after a long hide said 「no microphone」');
      });
      await check('L3: hidden 0.5 s while opening → visible → grant → the take continues locked and records', async () => {
        await begin(); await ev('pointercancel'); await blur(); await vis('hidden');
        await p.waitForTimeout(500);
        await vis('visible'); await focus(); await p.waitForTimeout(80);
        assert.ok(await card(), 'a short prompt hide closed the take (the Android fix regressed)');
        await grant();
        await p.waitForFunction(() => window.__recs.at(-1)?.state === 'recording', null, {timeout: 2000}).catch(() => {});
        assert.ok(await recording(), 'the grant after a short hide did not record');
        assert.ok(await p.locator('#pttCancel').isVisible(), 'locked: cancel on screen');
      });
      await check('P496: pagehide while the prompt is up ends the take and stops a late grant', async () => {
        await begin(); await ev('pointercancel'); await blur(); await p.evaluate(() => dispatchEvent(new Event('pagehide'))); await grant(); await p.waitForTimeout(80);
        assert.ok(await stopped()); assert.equal(await card(), false);
      });
      await check('recording hidden mid-take still ends as an interruption (P182/P496)', async () => {
        await begin(); await up(); await grant();
        await p.waitForFunction(() => window.__recs.at(-1)?.state === 'recording');
        await vis('hidden'); await p.waitForTimeout(120);
        assert.ok(await stopped(), 'tracks live while hidden');
        await vis('visible');
      });
      // unchanged contracts
      await check('ordinary first tap (no prompt) locks and records', async () => {
        await begin(); await up(); await grant();
        await p.waitForFunction(() => window.__recs.at(-1)?.state === 'recording');
        assert.ok(await recording());
      });
      await check('slide out to cancel while opening still cancels on pointercancel', async () => {
        await begin(); await ev('pointermove', {clientX: 100, clientY: 400}); await ev('pointercancel'); await grant(); await p.waitForTimeout(80);
        assert.equal(await card(), false, 'a cancelled press was locked'); assert.ok(await stopped());
      });
      await check('a plain touch cancel while opening (no prompt, no blur) still cancels — never locks (P272)', async () => {
        await begin(); await ev('pointercancel'); await p.waitForTimeout(300);
        assert.equal(await card(), false, 'a cancelled press was locked'); await grant(); await p.waitForTimeout(80); assert.ok(await stopped());
      });
      await check('a hold already recording still cancels on pointercancel (P181)', async () => {
        await begin(); await grant(); await p.waitForFunction(() => window.__recs.at(-1)?.state === 'recording');
        await ev('pointercancel'); await p.waitForTimeout(80);
        assert.equal(await card(), false); assert.ok(await stopped());
      });
      await check('explicit cancel while the prompt is up stops the late grant', async () => {
        await begin(); await ev('pointercancel'); await blur(); await p.locator('#pttCancel').click(); await grant(); await p.waitForTimeout(80);
        assert.ok(await stopped()); assert.equal(await card(), false);
      });
      await check('keyboard take: blur while opening still cancels', async () => {
        await p.locator('#mic').dispatchEvent('keydown', {key: 'Enter'}); await blur(); await grant(); await p.waitForTimeout(80);
        assert.ok(await stopped()); assert.equal(await card(), false);
      });
      // 举一反三: the QR scanner is the same class (camera prompt behind a system dialog that hides the page)
      const scanGrant = () => p.evaluate(() => { const c = document.createElement('canvas'); c.width = 64; c.height = 64; c.getContext('2d').fillRect(0, 0, 64, 64); const s = c.captureStream(5); window.__scanStream = s; window.__requests.at(-1).resolve(s); });
      const scanOpen = async () => { const n = await p.evaluate(() => window.__requests.length); await p.evaluate(() => document.getElementById('scanBtn').click()); await p.waitForFunction((n) => window.__requests.length === n + 1 && !!window.__requests.at(-1).c?.video, n); };
      const scanLeave = () => p.evaluate(() => { if (document.body.dataset.view === 'scan') history.back(); });
      await check('QR scan: a system camera dialog (hidden → visible) keeps the scanner, the grant shows the camera', async () => {
        await scanOpen(); await blur(); await vis('hidden'); await vis('visible'); await focus();
        assert.equal(await p.evaluate(() => document.body.dataset.view), 'scan', 'the scanner closed while the camera was being asked for');
        await scanGrant();
        await p.waitForFunction(() => !document.getElementById('scan-box').hidden, null, {timeout: 2000}).catch(() => {});
        assert.ok(await p.evaluate(() => !document.getElementById('scan-box').hidden && window.__scanStream.getTracks().every((t) => t.readyState === 'live')), 'granted camera not shown');
        await vis('hidden'); await p.waitForTimeout(50);
        assert.ok(await p.evaluate(() => window.__scanStream.getTracks().every((t) => t.readyState === 'ended')), 'a live scan kept the camera while hidden');
        await vis('visible'); await scanLeave(); await p.waitForTimeout(150);
      });
      await check('QR scan: a camera grant landing while hidden is stopped', async () => {
        await scanOpen(); await vis('hidden'); await scanGrant(); await p.waitForTimeout(80);
        assert.ok(await p.evaluate(() => window.__scanStream.getTracks().every((t) => t.readyState === 'ended')), 'hidden page kept the camera');
        await vis('visible'); await scanLeave(); await p.waitForTimeout(150);
      });
      await check('L3 QR scan: hidden 5 s while asking → visible → the scanner closes; the late grant is stopped', async () => {
        await scanOpen(); await blur(); await vis('hidden');
        await p.waitForTimeout(5000);
        await vis('visible'); await focus(); await p.waitForTimeout(80);
        assert.notEqual(await p.evaluate(() => document.body.dataset.view), 'scan', 'the scanner survived a 5 s hide');
        await scanGrant(); await p.waitForTimeout(150);
        assert.ok(await p.evaluate(() => window.__scanStream.getTracks().every((t) => t.readyState === 'ended')), 'the late camera grant was kept');
        assert.ok(await p.evaluate(() => document.getElementById('scan-box').hidden), 'the camera view came back');
      });
      await check('L3 QR scan: hidden 0.5 s while asking → visible → grant shows the camera', async () => {
        await scanOpen(); await blur(); await vis('hidden');
        await p.waitForTimeout(500);
        await vis('visible'); await focus();
        assert.equal(await p.evaluate(() => document.body.dataset.view), 'scan', 'a short hide closed the scanner');
        await scanGrant();
        await p.waitForFunction(() => !document.getElementById('scan-box').hidden, null, {timeout: 2000}).catch(() => {});
        assert.ok(await p.evaluate(() => !document.getElementById('scan-box').hidden && window.__scanStream.getTracks().every((t) => t.readyState === 'live')), 'granted camera not shown');
        await scanLeave(); await p.waitForTimeout(150);
      });
    }
    // ③ denied in the prompt: the card closes and says so (the error state)
    await check('android prompt: denied → the card closes with the no-microphone notice', async () => {
      await begin(); await ev('pointercancel'); await blur(); await deny(); await focus();
      await p.waitForTimeout(200);
      assert.equal(await card(), false);
      assert.ok(await p.evaluate(() => !document.getElementById('toast').hidden && document.getElementById('toast').textContent.trim().length > 0), 'no notice after a denial');
      await shot('denied');
      assert.ok(await stopped());
    });
    if (errors.length) rows.push({tag, name: 'no page errors', pass: false, error: errors.join(' | ').slice(0, 300)});
    await ctx.close();
  }
  // security r1 L3, camera sheet (desktop only: no touch, fine pointer — the tool row's camera opens the in-page sheet)
  {
    const ctx = await browser.newContext({viewport: {width: 1440, height: 900}, colorScheme: 'dark', hasTouch: false, isMobile: false, serviceWorkers: 'block', locale: 'zh-CN'});
    await ctx.addInitScript(stubs);
    const p = await ctx.newPage(), errors = [], tag = '1440-dark-zh-desktop';
    p.on('pageerror', (e) => errors.push(e.message));
    fake.reset(); fake.setUp(true);
    await p.goto(fake.newPairing(web.url + '?lang=zh'));
    await p.waitForFunction(() => window.__ajState === 'awaiting-approval');
    await fake.approve();
    await p.waitForFunction(() => window.__ajState === 'ready');
    await p.evaluate(() => { document.getElementById('phone-env-dismiss')?.click(); document.getElementById('a2hs-ok')?.click(); });
    await p.waitForTimeout(300);
    const vis = (v) => p.evaluate((v) => { window.__vis = v; document.dispatchEvent(new Event('visibilitychange')); }, v);
    const camOpen = async () => { const n = await p.evaluate(() => window.__requests.length); await p.evaluate(() => document.getElementById('tCamera').click()); await p.waitForFunction((n) => window.__requests.length === n + 1 && !!window.__requests.at(-1).c?.video, n); };
    const camGrant = () => p.evaluate(() => { const c = document.createElement('canvas'); c.width = 64; c.height = 64; c.getContext('2d').fillRect(0, 0, 64, 64); const s = c.captureStream(5); window.__camStream = s; window.__requests.at(-1).resolve(s); });
    const sheet = () => p.evaluate(() => !document.getElementById('cam').hidden);
    const check = async (name, fn) => {
      try { await fn(); rows.push({tag, name, pass: true}); }
      catch (e) { rows.push({tag, name, pass: false, error: e.message.split('\n')[0]}); }
      await p.evaluate(() => { window.__vis = 'visible'; dispatchEvent(new Event('pagehide')); }); await p.waitForTimeout(200);
    };
    await check('L3 camera sheet: hidden 5 s while asking → visible → the sheet closes; the late grant is stopped', async () => {
      await camOpen(); await vis('hidden'); await p.waitForTimeout(5000); await vis('visible'); await p.waitForTimeout(80);
      assert.equal(await sheet(), false, 'the camera sheet survived a 5 s hide');
      await camGrant(); await p.waitForTimeout(200);
      assert.ok(await p.evaluate(() => window.__camStream.getTracks().every((t) => t.readyState === 'ended')), 'the late camera grant was kept');
      assert.equal(await sheet(), false, 'the sheet came back');
    });
    await check('L3 camera sheet: hidden 0.5 s while asking → visible → grant goes live', async () => {
      await camOpen(); await vis('hidden'); await p.waitForTimeout(500); await vis('visible');
      assert.ok(await sheet(), 'a short hide closed the camera sheet');
      await camGrant();
      await p.waitForFunction(() => document.getElementById('cam').dataset.mode === 'live', null, {timeout: 2000}).catch(() => {});
      assert.ok(await p.evaluate(() => document.getElementById('cam').dataset.mode === 'live' && window.__camStream.getTracks().every((t) => t.readyState === 'live')), 'granted camera not live');
    });
    await check('camera sheet: a live camera still closes when the page hides', async () => {
      await camOpen(); await camGrant();
      await p.waitForFunction(() => document.getElementById('cam').dataset.mode === 'live', null, {timeout: 2000});
      await vis('hidden'); await p.waitForTimeout(80);
      assert.equal(await sheet(), false); assert.ok(await p.evaluate(() => window.__camStream.getTracks().every((t) => t.readyState === 'ended')));
    });
    if (errors.length) rows.push({tag, name: 'no page errors', pass: false, error: errors.join(' | ').slice(0, 300)});
    await ctx.close();
  }
} finally {
  await browser.close(); await fake.stop(); await web.stop();
  if (temp) rmSync(temp, {recursive: true, force: true});
  writeFileSync(`${out}/results.json`, JSON.stringify({baseline, base: baseline ? BASE : 'worktree', rows, shots}, null, 2) + '\n');
}
for (const r of rows) console.log(`${r.pass ? 'PASS' : 'FAIL'} ${r.tag} ${r.name}${r.error ? ' — ' + r.error : ''}`);
console.log(`${rows.filter((r) => r.pass).length}/${rows.length} pass · ${shots.length} screens → ${out}`);
assert.ok(rows.every((r) => r.pass), '0.30.8 Android microphone first-tap assertions failed');
