// P340 E (#45): Settings → 「输入框按钮顺序」 — real page + local fake host (real Noise), own headless Chromium (never :9222).
//   tools-default      out of the box the four buttons are 文件 · 相册 · 拍照 · 麦克风 (message box and Settings row alike);
//                      「恢复默认」 is disabled; every chip ≥ 44 px; no sideways overflow
//   tools-drag         desktop: a mouse drags 麦克风 to the far left; phone: a finger holds it, then drags → the message box
//                      shows the new order at once, ONE pref_set ui.composer_buttons with that order
//   tools-keyboard     focus a chip, → / ← / Home / End move it; a burst of presses is one pref_set with the last order
//   tools-tap          tap 拍照 (picked up, aria-pressed), tap 文件's place → 拍照 goes there; Esc puts a picked one down
//   tools-reset        「恢复默认」 → the default order, pref_set with the default, the button disabled again
//   tools-sync         a second paired remote (its own browser profile) repaints its message box from the preferences
//                      broadcast; an invalid order from the computer reads as the default
//   tools-old-host     a computer that does not list the key: no pref_set, the 「只保存在这台设备上」 note, the order survives a
//                      reload on this device
// Shots: zh/en × light/dark × 350 (folded outer screen) / 390 / 690 (unfolded inner) / 1440 — the Settings row and the
// message box, before / after → reports/qa/p340/e/.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep, key } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = join(ROOT, 'reports/qa/p340/e');
const KEY = 'ui.composer_buttons';
const DEF = ['file', 'image', 'camera', 'mic'];
const MIC_LEFT = ['mic', 'file', 'image', 'camera'];
const S = { zh: { label: '输入框按钮顺序', mic: '麦克风', chip0: '文件，第 1 个，共 4 个' }, en: { label: 'Message box buttons', mic: 'Mic', chip0: 'File, position 1 of 4' } };
export const CASES = { a: 'tools-default', b: 'tools-drag', c: 'tools-keyboard', d: 'tools-tap', e: 'tools-reset', f: 'tools-sync', g: 'tools-old-host' };
const $ = (id) => `document.getElementById('${id}')`;
const box = (p, sel) => evaluate(p, `(()=>{const r=document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect();return {l:r.left,r:r.right,t:r.top,b:r.bottom,w:r.width,h:r.height,x:r.left+r.width/2,y:r.top+r.height/2}})()`);
const composer = (p) => evaluate(p, `[...${$('tools')}.children].map(n=>({tDoc:'file',tPhoto:'image',tCamera:'camera',mic:'mic'})[n.id]).filter(Boolean)`);
const chipsOf = (p) => evaluate(p, `[...document.querySelectorAll('#set-tools button[data-tool]')].map(b=>b.dataset.tool)`);
const sets = (fake) => fake.st.prefSets.filter((m) => m.key === KEY).map((m) => m.value);
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

export async function runP340E() {
  mkdirSync(OUT, { recursive: true });
  const rec = parityRecorder('web/test/p340_e.mjs');
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
  const fake = await startFakeHost();
  const B = await launch();
  let checks = 0;
  const check = (ok, label) => { assert.ok(ok, label); checks++; };
  const done = new Set();
  const step = async (c, f) => { try { await f(); if (!done.has(c)) { rec.record(CASES[c], true); done.add(c); } } catch (e) { rec.record(CASES[c], false); throw e; } };

  const pair = async (p, lang) => {
    await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
    await evaluate(p, `${$('phone-env-dismiss')}?.click()`);
  };
  const openSet = async (p) => {
    await evaluate(p, `${$('settings')}.hidden && ${$('setBtn')}.click()`);
    await waitFor(p, `!${$('settings')}.hidden && document.querySelectorAll('#set-tools button[data-tool]').length===4`);
    await evaluate(p, `${$('set-tools-row')}.scrollIntoView({block:'center'})`);
    await sleep(250);
  };
  const closeSet = async (p) => { await evaluate(p, `${$('settings')}.hidden || ${$('setBtn')}.click()`); await waitFor(p, `${$('settings')}.hidden`); };
  const chipSel = (id) => `#set-tools button[data-tool="${id}"]`;
  const settle = () => sleep(800);    // the save waits SAVE_MS (450) for a burst of moves, then the round trip

  // [lang, theme, w, h, touch, full]: every case on a zh phone and the zh desktop; the rest are shots + the core checks
  const runs = [['zh', 'light', 390, 844, true, true], ['zh', 'dark', 1440, 900, false, true],
    ['zh', 'dark', 390, 844, true, false], ['zh', 'light', 1440, 900, false, false],
    ['en', 'light', 390, 844, true, false], ['en', 'dark', 390, 844, true, false], ['en', 'light', 1440, 900, false, false], ['en', 'dark', 1440, 900, false, false],
    ['zh', 'light', 350, 780, true, false], ['zh', 'dark', 350, 780, true, false], ['en', 'light', 350, 780, true, false], ['en', 'dark', 350, 780, true, false],
    ['zh', 'light', 690, 840, true, false], ['zh', 'dark', 690, 840, true, false], ['en', 'light', 690, 840, true, false], ['en', 'dark', 690, 840, true, false]];
  try {
    for (const [lang, theme, w, h, touch, full] of runs) {
      const s = S[lang];
      fake.reset();
      fake.st.prefSet = true; fake.st.version = '0.23.0a1';
      fake.st.prefs = { appearance: { language: lang, theme: 'system' }, ui: { card_hot_action: 'web-page-report', composer_buttons: [...DEF] } };
      const p = await newPage(B, w, h, theme, { touch, allow: [web.url, fake.relay + '/'] });
      const tag = `${lang}-${theme}-${w}`;
      const save = async (name) => {   // the saved ✓ fades first, so the shot shows the row itself
        await waitFor(p, `!${$('toast')}?.classList.contains('on')`, 6000).catch(() => {}); await sleep(350);
        writeFileSync(join(OUT, `${tag}-${name}.png`), await shoot(p, ''));
      };
      const mouseDrag = async (from, to) => {
        const a = await box(p, chipSel(from)), b = await box(p, chipSel(to));
        await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: a.x, y: a.y });
        await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: a.x, y: a.y, button: 'left', buttons: 1, clickCount: 1 });
        for (let i = 1; i <= 12; i++) { await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: a.x + (b.x - a.x) * i / 12, y: a.y + (b.y - a.y) * i / 12, button: 'left', buttons: 1 }); await sleep(16); }
        return async () => p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: b.x, y: b.y, button: 'left', buttons: 0, clickCount: 1 });
      };
      const touchDrag = async (from, to) => {
        const a = await box(p, chipSel(from)), b = await box(p, chipSel(to));
        await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: a.x, y: a.y }] });
        await sleep(450);                               // the hold (HOLD_MS 320) before a finger may drag
        for (let i = 1; i <= 12; i++) { await p.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: a.x + (b.x - a.x) * i / 12, y: a.y + (b.y - a.y) * i / 12 }] }); await sleep(16); }
        return async () => p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
      };
      const tapOn = async (sel) => {
        const r = await box(p, sel);
        if (touch) { await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: r.x, y: r.y }] }); await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); }
        else { await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: r.x, y: r.y }); await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', buttons: 1, clickCount: 1 }); await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 }); }
        await sleep(120);
      };
      try {
        await pair(p, lang);
        await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'AgentJ' });
        await fake.addTurn({ k: 'phone', dev: 'other', name: 'iPhone', text: lang === 'zh' ? '把麦克风放到最左边' : 'Put the mic on the far left' },
          lang === 'zh' ? '好的，设置里可以拖动这四个按钮。' : 'Sure: drag the four buttons in Settings.');
        await waitFor(p, `${$('words')}.textContent.length>0`);
        await save('chat-before');

        await step('a', async () => {
          check(eq(await composer(p), DEF), 'default composer order ' + tag);
          await openSet(p);
          check(eq(await chipsOf(p), DEF), 'Settings row = the composer order');
          check(await evaluate(p, `${$('set-tools-lab')}.textContent`) === s.label, 'label ' + lang);
          check(await evaluate(p, `document.querySelector(${JSON.stringify(chipSel('file'))}).getAttribute('aria-label')`) === s.chip0, 'chip name + place');
          check(await evaluate(p, `${$('set-tools-reset')}.disabled`), 'reset disabled at the default');
          check(await evaluate(p, `${$('set-tools-local')}.hidden`), 'no device-only note on a host that takes the key');
          const sizes = await evaluate(p, `[...document.querySelectorAll('#set-tools button[data-tool]')].map(b=>{const r=b.getBoundingClientRect();return [r.width,r.height]})`);
          check(sizes.every(([bw, bh]) => bw >= 44 && bh >= 44), 'chips ≥ 44 px: ' + JSON.stringify(sizes));
          const rr = await box(p, '#set-tools-reset');
          check(rr.w >= 44 && rr.h >= 44, 'reset ≥ 44 px');
          check(await evaluate(p, `document.documentElement.scrollWidth<=innerWidth && ${$('set-tools')}.scrollWidth<=${$('set-tools')}.clientWidth+1`), 'no sideways overflow');
          check(await evaluate(p, `(()=>{const l=document.querySelector('#set-tools li:last-child').getBoundingClientRect(),c=${$('set-tools-row')}.getBoundingClientRect();return l.right<=c.right+1})()`), 'four chips fit the row at ' + w);
          await save('settings-before');
        });

        await step('b', async () => {
          const n0 = sets(fake).length;
          const release = await (touch ? touchDrag : mouseDrag)('mic', 'file');
          check(await evaluate(p, `document.querySelector(${JSON.stringify(chipSel('mic'))}).classList.contains('dragging')`), 'the chip follows the pointer');
          check(eq(await chipsOf(p), MIC_LEFT), 'the others make room while dragging: ' + await chipsOf(p));
          if (full) await save('settings-dragging');
          await release();
          await waitFor(p, `!document.querySelector('#set-tools .dragging')`);
          check(eq(await chipsOf(p), MIC_LEFT), 'dropped on the far left');
          check(eq(await composer(p), MIC_LEFT), 'the message box shows it at once');
          await settle();
          check(eq(sets(fake).slice(n0), [MIC_LEFT]), 'one pref_set with the new order: ' + JSON.stringify(sets(fake).slice(n0)));
          check(eq(fake.st.prefs.ui.composer_buttons, MIC_LEFT), 'the computer holds it');
          check(!(await evaluate(p, `${$('set-tools-reset')}.disabled`)), 'reset enabled');
          check(await evaluate(p, `!document.querySelector('#set-tools [aria-pressed="true"]')`), 'a drag is not a tap (nothing picked up)');
          await save('settings-after');
          await closeSet(p);
          await save('chat-after');
          const mic = await box(p, '#mic'), doc = await box(p, '#tDoc');
          check(mic.l < doc.l, 'mic is left of file in the message box');
        });

        if (full) {
          await step('c', async () => {
            await openSet(p);
            const n0 = sets(fake).length;
            await evaluate(p, `document.querySelector(${JSON.stringify(chipSel('mic'))}).focus()`);
            await key(p, 'ArrowRight'); await key(p, 'ArrowRight');
            check(eq(await chipsOf(p), ['file', 'image', 'mic', 'camera']), '→ → moves it two places');
            check(await evaluate(p, `document.activeElement.dataset.tool`) === 'mic', 'focus stays on the moved chip');
            await key(p, 'End');
            check(eq(await chipsOf(p), DEF), 'End = the far right');
            await key(p, 'Home');
            check(eq(await chipsOf(p), MIC_LEFT), 'Home = the far left');
            await key(p, 'ArrowLeft');
            check(eq(await chipsOf(p), MIC_LEFT), '← at the left edge does nothing');
            check(await waitFor(p, `${$('set-tools-live')}.textContent.includes(${JSON.stringify(s.mic)})`, 2000).catch(() => false), 'the move is announced');
            await key(p, 'ArrowDown');
            check(eq(await chipsOf(p), ['file', 'mic', 'image', 'camera']), '↓ = one to the right');
            await settle();
            check(eq(sets(fake).slice(n0), [['file', 'mic', 'image', 'camera']]), 'one save for the burst: ' + JSON.stringify(sets(fake).slice(n0)));
            check(eq(await composer(p), ['file', 'mic', 'image', 'camera']), 'composer follows');
            check(await evaluate(p, `!${$('settings')}.hidden`), 'arrow keys never leave Settings');
          });

          await step('d', async () => {
            const n0 = sets(fake).length;
            await tapOn(chipSel('camera'));
            check(await evaluate(p, `document.querySelector(${JSON.stringify(chipSel('camera'))}).getAttribute('aria-pressed')`) === 'true', 'tap = picked up');
            if (w < 500) await save('settings-picked');
            await tapOn(chipSel('file'));
            check(eq(await chipsOf(p), ['camera', 'file', 'mic', 'image']), 'second tap puts it there: ' + await chipsOf(p));
            check(await evaluate(p, `!document.querySelector('#set-tools [aria-pressed="true"]')`), 'nothing picked any more');
            await tapOn(chipSel('image'));
            await evaluate(p, `document.querySelector(${JSON.stringify(chipSel('image'))}).focus()`);
            await key(p, 'Escape');
            check(await evaluate(p, `!document.querySelector('#set-tools [aria-pressed="true"]')`), 'Esc puts it down');
            check(await evaluate(p, `!${$('settings')}.hidden`), 'that Esc did not close Settings');
            await settle();
            check(eq(sets(fake).slice(n0), [['camera', 'file', 'mic', 'image']]), 'saved: ' + JSON.stringify(sets(fake).slice(n0)));
          });
        }

        await step('e', async () => {
          await openSet(p);
          const n0 = sets(fake).length;
          await tapOn('#set-tools-reset');
          check(eq(await chipsOf(p), DEF), 'reset → default');
          check(eq(await composer(p), DEF), 'composer back to default');
          await settle();
          check(eq(sets(fake).slice(n0), [DEF]), 'pref_set with the default');
          check(await evaluate(p, `${$('set-tools-reset')}.disabled`), 'reset disabled again');
          await closeSet(p);
        });

        if (full) {
          await step('f', async () => {
            const q = await newPage(B, 390, 844, theme, { touch: true, allow: [web.url, fake.relay + '/'] });
            try {
              await pair(q, lang);
              await waitFor(q, `!!${$('tools')}`);
              check(eq(await composer(q), DEF), 'the other remote starts at the default');
              await openSet(p);
              const release = await (touch ? touchDrag : mouseDrag)('mic', 'file');
              await release();
              await settle();
              await waitFor(q, `[...${$('tools')}.children].map(n=>n.id).join()==='mic,tDoc,tPhoto,tCamera'`);
              check(eq(await composer(q), MIC_LEFT), 'the other remote follows the broadcast');
              writeFileSync(join(OUT, `${tag}-other-remote-synced.png`), await shoot(q, ''));
              await fake.send({ t: 'preferences', value: { ...fake.st.prefs, ui: { ...fake.st.prefs.ui, composer_buttons: ['mic', 'mic', 'image', 'camera'] } }, problem: null,
                host: { version: '0.23.0a1', language_at: 0, settable: [KEY, 'appearance.language'], devices: [] } });
              await waitFor(q, `[...${$('tools')}.children].map(n=>n.id).join()==='tDoc,tPhoto,tCamera,mic'`);
              check(eq(await composer(q), DEF), 'an invalid order from the computer = the default');
            } finally { await q.dispose(); }
            await closeSet(p);
          });

          await step('g', async () => {
            await fake.send({ t: 'preferences', value: { appearance: fake.st.prefs.appearance }, problem: null, host: { version: '0.22.2a1', language_at: 0, settable: ['appearance.language'], devices: [] } });
            await sleep(200);
            await openSet(p);
            const n0 = fake.st.prefSets.length;
            check(!(await evaluate(p, `${$('set-tools-local')}.hidden`)), 'the device-only note shows');
            const was = await chipsOf(p), LOCAL = [was[1], was[0], was[2], was[3]];
            await evaluate(p, `document.querySelector(${JSON.stringify(chipSel(was[0]))}).focus()`);
            await key(p, 'ArrowRight');
            await settle();
            check(fake.st.prefSets.length === n0, 'nothing sent to an older computer');
            check(eq(await composer(p), LOCAL), 'still shown at once');
            check(await evaluate(p, `${$('set-pref-cmd')}.hidden && ${$('set-lang-cmd')}.hidden`), 'no command line for a setting that never needs the computer');
            if (w < 500) await save('settings-old-host');
            await closeSet(p);
            fake.st.prefSet = false; fake.st.prefs = { appearance: fake.st.prefs.appearance };   // the older computer after the reload too
            await navigate(p, web.url + '?lang=' + lang);
            const ids = LOCAL.map((x) => ({ file: 'tDoc', image: 'tPhoto', camera: 'tCamera', mic: 'mic' })[x]).join();
            await waitFor(p, `!!${$('mic')} && [...${$('tools')}.children].map(n=>n.id).filter(x=>['tDoc','tPhoto','tCamera','mic'].includes(x)).join()===${JSON.stringify(ids)}`, 10000);
            check(eq(await composer(p), LOCAL), 'this device keeps it across a reload');
          });
        }
        if (!full) {          // shots only: the order after a keyboard move, so every width shows both states
          check(p.problems.length === 0, 'no page problems: ' + p.problems.join(' | '));
        }
      } catch (e) { console.error(tag, 'page problems:', p.problems); throw e; } finally { await p.dispose(); }
    }
    console.log(`p340_e: ${checks} checks passed, shots → ${OUT}`);
  } finally { await B.close(); await fake.stop(); await web.stop(); rec.write(); }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await runP340E();
