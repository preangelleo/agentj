// P340 A group (#27 screenshot → clipboard, #22 one Noir notice in Settings, #23 Android navigation bar colour).
// Pure / static checks; the real page is test/p340_a.mjs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { clipSupport, copyImage, canShareFile, shareFile, shotNoticePlan } from '../public/js/shotcopy.js';

const PUB = fileURLToPath(new URL('../public/', import.meta.url));
const read = (f) => readFileSync(PUB + f, 'utf8');
const ZH = JSON.parse(readFileSync(new URL('../i18n/web.zh.src.json', import.meta.url), 'utf8'));
const ZHO = JSON.parse(readFileSync(new URL('../i18n/web.zh.override.json', import.meta.url), 'utf8'));
const EN = JSON.parse(readFileSync(new URL('../i18n/web.en.json', import.meta.url), 'utf8'));

// a ClipboardItem stand-in that keeps what it was given (and can refuse a Promise like an old Chromium)
const mkCI = (promiseOk = true) => class { constructor(d) { if (!promiseOk && Object.values(d).some((v) => v && typeof v.then === 'function')) throw new TypeError('blob only'); this.d = d; } };
const mkNav = (writeImpl) => { const calls = []; return { calls, clipboard: { write: (items) => { calls.push(items); return writeImpl(items); } } }; };

test('#27 copyImage writes synchronously with the Promise form (Safari rule), before the picture exists', async () => {
  let resolve; const pic = new Promise((r) => { resolve = r; });
  const nav = mkNav(async (items) => { assert.equal(await items[0].d['image/png'], 'PNG'); });
  const r = copyImage(pic, nav, mkCI(true));
  assert.equal(nav.calls.length, 1, 'clipboard.write is called inside the gesture, not after the capture');
  assert.ok(typeof nav.calls[0][0].d['image/png'].then === 'function', 'the item holds a promise');
  resolve('PNG');
  assert.equal(await r, 'ok');
});

test('#27 copyImage: Blob-only ClipboardItem → waits for the Blob; refusal → failed; no API → unsupported', async () => {
  const nav = mkNav(async () => {});
  assert.equal(await copyImage(Promise.resolve('PNG'), nav, mkCI(false)), 'ok');
  assert.equal(nav.calls[0][0].d['image/png'], 'PNG');
  assert.equal(await copyImage('PNG', mkNav(() => Promise.reject(new DOMException('no', 'NotAllowedError'))), mkCI()), 'failed');
  assert.equal(await copyImage('PNG', mkNav(() => { throw new Error('sync'); }), mkCI()), 'failed');
  assert.equal(await copyImage(Promise.reject(new Error('render')), mkNav(async () => {}), mkCI(false)), 'failed');
  assert.equal(await copyImage('PNG', {}, mkCI()), 'unsupported');
  assert.equal(await copyImage('PNG', mkNav(async () => {}), undefined), 'unsupported');
  assert.equal(clipSupport({ clipboard: { write() {} } }, class {}), true);
  assert.equal(clipSupport({ clipboard: {} }, class {}), false);
});

test('#27 share: canShare({files}) decides; a closed sheet is a cancel, not a failure', async () => {
  const f = { name: 'agentj-page.png' };
  assert.equal(canShareFile(f, {}), false);
  assert.equal(canShareFile(f, { share() {}, canShare: () => false }), false);
  assert.equal(canShareFile(f, { share() {}, canShare: () => { throw new Error('x'); } }), false);
  const shared = [];
  const nav = { canShare: (d) => d.files.length === 1, share: async (d) => { shared.push(d); } };
  assert.equal(await shareFile(f, nav), 'ok'); assert.deepEqual(shared, [{ files: [f] }]);
  assert.equal(await shareFile(f, { canShare: () => true, share: async () => { throw new DOMException('x', 'AbortError'); } }), 'cancel');
  assert.equal(await shareFile(f, { canShare: () => true, share: async () => { throw new DOMException('x', 'NotAllowedError'); } }), 'failed');
  assert.equal(await shareFile(f, {}), 'failed');
});

test('#27 the notice plan: copied → success (share only where it exists); not copied → warn + 「复制图片」 / 「分享」', () => {
  assert.deepEqual(shotNoticePlan('ok', { canShare: false }), { key: 'p340a.shotBoth', kind: 'success', actions: [] });
  assert.deepEqual(shotNoticePlan('ok', { canShare: true }).actions, ['share']);
  assert.deepEqual(shotNoticePlan('failed', { canShare: true }), { key: 'p340a.shotNoClip', kind: 'warn', actions: ['copy', 'share'] });
  assert.deepEqual(shotNoticePlan('unsupported', { canClip: false, canShare: true }), { key: 'p340a.shotNoClipApi', kind: 'warn', actions: ['share'] });
  assert.equal(shotNoticePlan('ok', { added: false }).key, 'p340a.shotClipOnly');
  assert.equal(shotNoticePlan('failed', { added: false }).key, 'p340a.shotNothing');
  for (const k of ['shotBoth', 'shotClipOnly', 'shotNoClip', 'shotNoClipApi', 'shotNothing', 'copyImg', 'share', 'copied', 'copyFail', 'shareFail']) {
    const key = 'p340a.' + k;
    for (const [name, d] of [['zh', ZH], ['zh override', ZHO], ['en', EN]]) assert.ok(typeof d[key] === 'string' && d[key], `${name} ${key}`);
    assert.deepEqual((ZH[key].match(/\{\w+\}/g) || []).sort(), (EN[key].match(/\{\w+\}/g) || []).sort(), key + ' placeholders');
    assert.ok(!/[㐀-鿿]/.test(EN[key]), key + ' en has no Chinese');
  }
});

test('#27 wiring: the gear fires on release, the screenshot writes the clipboard before any await, shotclip skips our copy', () => {
  const s = read('js/settings.js'), a = read('app.js'), sc = read('js/shotclip.js'), ui = read('js/ui.js');
  assert.match(s, /longPress\(el\('setBtn'\), \(\) => H\.screenshot\?\.\(\), \{ at: 'release' \}\)/);
  const body = a.slice(a.indexOf('const screenshot = () => {'), a.indexOf('const shotNotice'));
  assert.ok(body.indexOf('shotcopy.copyImage(shot)') > 0 && !/await/.test(body.slice(0, body.indexOf('shotcopy.copyImage(shot)'))), 'no await before copyImage');
  assert.match(sc, /if \(ownCopy\) \{ ownCopy = false; lastHash = h; return; \}/);
  assert.match(ui, /spec\.media/);
});

test('#22 Settings has no notice bar of its own: say() is the Noir Notification', async () => {
  const html = read('index.html'), s = read('js/settings.js');
  assert.ok(!html.includes('set-status-bar') && !html.includes('id="set-status"'), 'old bar markup gone');
  assert.ok(!/set-status/.test(s), 'settings.js no longer touches the bar');
  assert.ok(!/say\(t\('set\.saved'\)\)/.test(s), 'saved = only the Noir ✓ circle');
  assert.match(s, /notify\(\{ kind: 'success', shape: 'circle', text: t\('set\.saved'\) \}\)/);
  assert.ok(!/setstatusbar/.test(read('menu.css')), 'its CSS went with it');
});

test('#23 → P349 manifest colours = Sooty, light and dark alike (the WebAPK navigation bar takes theme_color; black sits under every page colour)', () => {
  const m = JSON.parse(read('manifest.webmanifest'));
  const sooty = read('brand/palette.css').match(/--c-sooty: (#[0-9a-f]{6});/)[1];
  assert.equal(m.theme_color, sooty); assert.equal(m.background_color, sooty);
  assert.equal(m.user_preferences, undefined, 'no separate dark colour: one fixed colour for both schemes');
  assert.match(read('index.html'), /viewport-fit=cover/);
  assert.match(read('index.html'), /interactive-widget=resizes-content/);
});
