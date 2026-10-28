#!/usr/bin/env node
// P563 追加 2 (董事长 10-28: 「按钮里写出快捷键，用户能记住」): Settings → 紧急停止所有 Agent shows its key (Alt+Shift+S; ⌥+Shift+S on
// a Mac, nothing on a phone), the 「键盘快捷键」 page lists it among the function keys, and pressing it opens the very confirm
// sheet the button opens — Esc / 取消 sends nothing, 确认 sends one signed estop. A held key (repeat) and an open confirm sheet
// never stack a second sheet; a focused text field does not swallow it (function keys work there, as every Alt+Shift key).
// Real Chromium, isolated fake host + local relay (its own headless browser, never :9222).
//   node web/test/p563-stopkey.mjs                                   → after: asserts + screenshots
//   P563_PHASE=before P563_PUBLIC=<HEAD tree>/web/public node …     → before: screenshots only
// Shots: $P563_SHOTS (default reports/qa/p563/stopkey) / 2-stopall-{settings,shortcuts}-<lang>-<w>-<scheme>-<phase>.png
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { startWebServer, PUBLIC_DIR } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitState, evaluate, waitFor, sleep } from './browser.mjs';

const phase = process.env.P563_PHASE === 'before' ? 'before' : 'after';
const OUT = resolve(process.env.P563_SHOTS || 'reports/qa/p563/stopkey');
mkdirSync(OUT, { recursive: true });
const web = await startWebServer({ port: 0, publicDir: process.env.P563_PUBLIC ? resolve(process.env.P563_PUBLIC) + '/' : PUBLIC_DIR });
const fake = await startFakeHost(), B = await launch();
const IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1';
const MAC = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36';
let failed = 0, checks = 0;
const ok = (name, fn) => fn().then(() => { checks++; console.log('  ✓ ' + name); }, (e) => { failed++; console.log('  ✗ ' + name + ': ' + (e && e.stack || e)); });

const controls = [];
async function paired({ w, scheme, lang, phone, mac }) {
  fake.reset();
  fake.st.prefs = { appearance: { language: lang, theme: scheme }, voice: { speak_replies: false, wake_enabled: false } };
  fake.st.onApp = async (c, m, send) => {
    if (m.t !== 'estop') return false;
    controls.push({ t: m.t, ok: await fake.verifyControl(c, m.t, m, {}) });
    await send({ t: 'ctl_res', r: m.r, ok: true });
    return true;
  };
  const p = await newPage(B, w, phone ? 844 : 900, scheme, { touch: phone, ua: phone ? IPHONE : mac ? MAC : undefined, allow: [web.url, fake.relay + '/'] });
  if (mac) await p.send('Emulation.setUserAgentOverride', { userAgent: MAC, platform: 'MacIntel' });
  await navigate(p, fake.newPairing(web.url + '?lang=' + lang));
  await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await evaluate(p, `(async()=>{for(const id of ['phone-env-dismiss','a2hs-ok'])document.getElementById(id)?.click();document.documentElement.dataset.theme='${scheme}';
    const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s);await document.fonts.ready})()`);
  return p;
}
/** Scroll the first selector to the middle, clip the union of the selectors (viewport-clamped), save. */
async function shot(p, sels, file) {
  const clip = await evaluate(p, `(()=>{const els=${JSON.stringify(sels)}.map(s=>document.querySelector(s)).filter(e=>e&&e.getClientRects().length);if(!els.length)return null;
    els[0].scrollIntoView({block:'center'});const rs=els.map(e=>e.getBoundingClientRect());const pad=12;
    const x=Math.max(0,Math.min(...rs.map(r=>r.left))-pad),y=Math.max(0,Math.min(...rs.map(r=>r.top))-pad);
    const r=Math.min(innerWidth,Math.max(...rs.map(r=>r.right))+pad),b=Math.min(innerHeight,Math.max(...rs.map(r=>r.bottom))+pad);
    return {x,y,width:r-x,height:b-y,scale:1}})()`);
  assert.ok(clip, 'nothing to shoot: ' + sels.join(', '));
  await sleep(120);
  const { data } = await p.send('Page.captureScreenshot', { format: 'png', clip });
  writeFileSync(file, Buffer.from(data, 'base64'));
}
// Alt(⌥)+Shift+S as a Mac sends it: KeyS with the Option symbol as its text (the app reads e.code, never the symbol)
async function altShiftS(p, { repeat = false } = {}) {
  const base = { key: 'Í', code: 'KeyS', modifiers: 9, windowsVirtualKeyCode: 83 };
  await p.send('Input.dispatchKeyEvent', { type: 'keyDown', text: 'Í', ...base });
  if (repeat) await p.send('Input.dispatchKeyEvent', { type: 'keyDown', text: 'Í', autoRepeat: true, ...base });
  await p.send('Input.dispatchKeyEvent', { type: 'keyUp', ...base });
}
const openSettings = async (p) => { await evaluate(p, `document.getElementById('setBtn').click()`); await waitFor(p, `!document.getElementById('settings').hidden`); await sleep(250); };
const openKeys = async (p) => {
  await evaluate(p, `import('./js/settings.js').then(m=>m.close())`); await sleep(150);
  await p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: '?', code: 'Slash', modifiers: 8, text: '?', windowsVirtualKeyCode: 191 });
  await p.send('Input.dispatchKeyEvent', { type: 'keyUp', key: '?', code: 'Slash', modifiers: 8, windowsVirtualKeyCode: 191 });
  await waitFor(p, `!document.getElementById('keys').hidden`); await sleep(250);
};
const CONFIRM_OPEN = `!document.getElementById('confirm').hidden`;
// the shortcut page row: the function-key card when it has it, else the gesture card (before: no key)
const ROW = `section.menucard dt[data-ix="app.stop_all"]`;

try {
  for (const lang of ['zh', 'en']) for (const scheme of ['light', 'dark']) for (const [w, phone] of [[390, true], [1440, false]]) {
    const tag = `${lang}-${w}-${scheme}`;
    await ok('settings + shortcut page ' + tag, async () => {
      const p = await paired({ w, scheme, lang, phone });
      try {
        await openSettings(p);
        await shot(p, ['.setrow:has(> #set-estop)', '#set-s-safety .setg'], `${OUT}/2-stopall-settings-${tag}-${phase}.png`);
        const kbd = await evaluate(p, `(()=>{const k=document.querySelector('#set-estop .setkbd');return {shown:!k.hidden&&k.getClientRects().length>0,text:k.textContent,aria:document.getElementById('set-estop').getAttribute('aria-keyshortcuts')}})()`);
        await openKeys(p);
        await evaluate(p, `(()=>{const d=document.querySelector('${ROW}');d.scrollIntoView({block:'center'})})()`);
        await shot(p, [`section[data-group="functional"] dt[data-ix="app.stop_all"]`, `section[data-group="functional"] dt[data-ix="app.stop_all"] + dd`, ROW, `${ROW} + dd`],
          `${OUT}/2-stopall-shortcuts-${tag}-${phase}.png`);
        if (phase === 'after') {
          if (phone) assert.deepEqual([kbd.shown, kbd.aria], [false, null], 'a phone shows no key');
          else assert.deepEqual(kbd, { shown: true, text: 'Alt+Shift+S', aria: 'Alt+Shift+S' }, 'the button shows the table key');
          const row = await evaluate(p, `(()=>{const dt=document.querySelector('section[data-group="functional"] dt[data-ix="app.stop_all"]');
            return dt?{keys:[...dt.querySelectorAll('kbd')].map(k=>k.textContent).join('+'),what:dt.nextElementSibling.textContent}:null})()`);
          assert.ok(row, 'the function-key card lists 紧急停止');
          assert.equal(row.keys, phone ? '⌥+Shift+S' : 'Alt+Shift+S', 'the page draws the platform glyph (the phone here is an iPhone)');
          assert.match(row.what, lang === 'zh' ? /停止所有 Agent（先确认）/ : /Stop all Agents \(confirm first\)/);
          assert.doesNotMatch(row.what, lang === 'zh' ? /Stop all/ : /停止/, 'one language only');
        }
      } finally { await p.dispose(); }
    });
  }

  if (phase === 'after') {
    await ok('Mac: ⌥ glyph on the button and the shortcut page', async () => {
      const p = await paired({ w: 1440, scheme: 'light', lang: 'zh', phone: false, mac: true });
      try {
        await openSettings(p);
        assert.equal(await evaluate(p, `document.querySelector('#set-estop .setkbd').textContent`), '⌥+Shift+S');
        await shot(p, ['.setrow:has(> #set-estop)', '#set-s-safety .setg'], `${OUT}/2-stopall-settings-zh-1440-light-mac-after.png`);
        await openKeys(p);
        assert.equal(await evaluate(p, `[...document.querySelectorAll('section[data-group="functional"] dt[data-ix="app.stop_all"] kbd')].map(k=>k.textContent).join('+')`), '⌥+Shift+S');
      } finally { await p.dispose(); }
    });

    for (const lang of ['zh', 'en']) await ok('the key = the button: confirm first, Esc / 取消 send nothing, 确认 sends one estop (' + lang + ')', async () => {
      const p = await paired({ w: 1440, scheme: 'light', lang, phone: false });
      try {
        controls.length = 0;
        // the button's sheet, for comparison
        await openSettings(p);
        await evaluate(p, `document.getElementById('set-estop').click()`); await waitFor(p, CONFIRM_OPEN, 3000);
        const fromButton = await evaluate(p, `[document.getElementById('confirm-title')?.textContent||'',document.getElementById('confirm-text').textContent,document.getElementById('confirm-yes').textContent]`);
        await evaluate(p, `document.getElementById('confirm-no').click()`); await waitFor(p, `document.getElementById('confirm').hidden`, 3000);
        await evaluate(p, `import('./js/settings.js').then(m=>m.close())`); await sleep(150);
        // 1) on the chat screen: the same sheet; Esc cancels; nothing sent
        await altShiftS(p); await waitFor(p, CONFIRM_OPEN, 3000);
        const fromKey = await evaluate(p, `[document.getElementById('confirm-title')?.textContent||'',document.getElementById('confirm-text').textContent,document.getElementById('confirm-yes').textContent]`);
        assert.deepEqual(fromKey, fromButton, 'the key opens the button\'s confirm sheet');
        // a second press while the sheet is up is blocked (no stacked sheet, no estop)
        await altShiftS(p); await sleep(150);
        await evaluate(p, `document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))`);
        await waitFor(p, `document.getElementById('confirm').hidden`, 3000); await sleep(200);
        assert.equal(controls.length, 0, 'Esc cancels: no estop');
        // 2) with the message box focused (function keys work in a field): 取消 → nothing; no Option symbol typed
        await evaluate(p, `(()=>{const i=document.getElementById('input');i.value='draft';i.focus()})()`);
        await altShiftS(p, { repeat: true }); await waitFor(p, CONFIRM_OPEN, 3000);
        assert.equal(await evaluate(p, `document.getElementById('input').value`), 'draft', 'nothing typed into the field');
        await evaluate(p, `document.getElementById('confirm-no').click()`); await waitFor(p, `document.getElementById('confirm').hidden`, 3000); await sleep(200);
        assert.equal(controls.length, 0, '取消: no estop (and a held key did not stack a second sheet)');
        // 3) from Settings itself: 确认 → exactly one signed estop
        await openSettings(p);
        await altShiftS(p); await waitFor(p, CONFIRM_OPEN, 3000);
        await shot(p, ['#confirm .box'], `${OUT}/2-stopall-confirm-${lang}-1440-light-after.png`);
        await evaluate(p, `document.getElementById('confirm-yes').click()`); await waitFor(p, `document.getElementById('confirm').hidden`, 3000); await sleep(300);
        assert.deepEqual(controls, [{ t: 'estop', ok: true }], 'one signed estop after 确认');
        assert.equal(p.problems.length, 0, 'page errors: ' + p.problems.join(' | '));
      } finally { await p.dispose(); }
    });
  }
} finally {
  await B.close(); await fake.stop(); await web.stop();
}
console.log(failed ? `p563-stopkey (${phase}): ${failed} FAILED` : `p563-stopkey (${phase}): ${checks} passed → ${OUT}`);
process.exit(failed ? 1 : 0);
