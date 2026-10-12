// P179: remotes renamed by the owner. Real page + local fake host, independent headless Chromium (never :9222).
//   a) another remote's message shows its CURRENT label from the host (「王小明（老板） · iOS Safari」), not the name the page
//      was stamped with; a rename broadcast (`preferences`) relabels the open page at once, no new message needed
//   b) touch long-press (≥ 500 ms) on the card's top-left source label opens the global centred dialog; a short tap and a
//      double tap do not (the double tap still jumps to the first message); ESC / 取消 close it
//   c) 保存 sends `dev_rename {r, name}` — no device field: only this remote — and closes on `dev_rename_res ok`; a refusal
//      stays open with the reason; a mouse button held ≥ 500 ms opens it too; this remote's own label shows its original
//   d) settings → paired phones: 「（名字是设备自己填的）」 / 「（主人改的名字）」 (+ the device's own label as a tooltip)
// Shots → reports/qa/p179/screens/<lang>-<state>.png
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep, key } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = join(ROOT, 'reports/qa/p179/screens');
const $ = (id) => `document.getElementById('${id}')`;
const OTHER = 'otherDevice00001';
export const CASES = { a: 'remote-live-label', b: 'source-long-press-dialog', c: 'dev-rename-own-only', d: 'settings-name-note' };
const S = {
  zh: { title: '给这台遥控器改名', other: '是另一台遥控器', ownerNote: '（主人改的名字）', devNote: '（名字是设备自己填的）', orig: '设备自己填的原名：网页 · Android Chrome', bad: '这个名字用不了' },
  en: { title: 'Rename this remote', other: 'is another remote', ownerNote: '(name set by the owner)', devNote: '(name set by the device)', orig: 'Name the device gave itself: 网页 · Android Chrome', bad: "That name can't be used" },
};
const box = (p, sel) => evaluate(p, `(()=>{const r=document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2}})()`);
async function touchHold(p, sel, ms) {
  const { x, y } = await box(p, sel);
  await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
  await sleep(ms);
  await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
}
async function mouseHold(p, sel, ms) {
  const { x, y } = await box(p, sel);
  await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
  await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', buttons: 1, clickCount: 1 });
  await sleep(ms);
  await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', buttons: 0, clickCount: 1 });
}
const dlgOpen = (p) => evaluate(p, `!${$('renameDlg')}.hidden`);

export async function runP179() {
  mkdirSync(OUT, { recursive: true });
  const rec = parityRecorder('web/test/p179.mjs');
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
  const fake = await startFakeHost();
  const B = await launch();
  let checks = 0;
  const check = (ok, label) => { assert.ok(ok, label); checks++; };
  const step = async (c, f) => { try { await f(); rec.record(CASES[c], true); } catch (e) { rec.record(CASES[c], false); throw e; } };
  try {
    for (const lang of ['zh', 'en']) {
      const s = S[lang];
      fake.reset();
      const renames = [];
      let answer = { ok: true };
      fake.st.onApp = async (c, m, reply) => {
        if (m.t !== 'dev_rename') return false;
        renames.push({ ...m, from: c.devId });
        await reply({ t: 'dev_rename_res', r: m.r, ...answer, ...(answer.ok ? { name: m.name } : {}) });
        return true;
      };
      const p = await newPage(B, 390, 844, 'light', { touch: true, allow: [web.url, fake.relay + '/'] });
      const save = async (name) => writeFileSync(join(OUT, `${lang}-${name}.png`), await shoot(p, ''));
      try {
        await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
        await evaluate(p, `${$('phone-env-dismiss')}?.click()`);
        await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent-PA' });
        const me = fake.devs.at(-1).id;                    // this language's page (the previous page may still be connected)
        const prefs = (other, mine = { name: '网页 · Android Chrome', named_by: 'device', self_name: '网页 · Android Chrome', label: '网页 · Android Chrome' }) => ({
          t: 'preferences', problem: null,
          value: { appearance: { language: lang, theme: 'system' }, voice: { speak_replies: false, wake_enabled: false }, agent: { high_risk_warnings: false, session_mode: 'shared' } },
          host: { version: '0.18.4a1', language_at: 0, settable: ['appearance.language'], devices: [
            { id: me, online: true, paired_at: 1, ...mine }, { id: OTHER, online: true, paired_at: 2, ...other }] } });
        await fake.send(prefs({ name: '王小明（老板）', named_by: 'owner', self_name: '网页 · iOS Safari', label: '王小明（老板） · iOS Safari' }));
        await fake.addTurn({ k: 'phone', dev: OTHER, name: '网页 · iOS Safari', text: lang === 'zh' ? '我现在有几个同事在跟你合作？' : 'How many colleagues work with you now?' }, 'ok');
        await fake.addTurn({ k: 'phone', dev: OTHER, name: '网页 · iOS Safari', text: lang === 'zh' ? '第二条' : 'Second' }, 'ok 2');

        await step('a', async () => {
          await waitFor(p, `${$('omLabel')}.textContent==='王小明（老板） · iOS Safari'`);
          check(true, 'live label, not the stamped 「网页 · iOS Safari」 ' + lang);
          await save('source-owner-label');
          await fake.send(prefs({ name: '王小明（董事长）', named_by: 'owner', self_name: '网页 · iOS Safari', label: '王小明（董事长） · iOS Safari' }));
          await waitFor(p, `${$('omLabel')}.textContent==='王小明（董事长） · iOS Safari'`);
          check(true, 'a rename broadcast relabels the open page at once ' + lang);
        });

        await step('b', async () => {
          await touchHold(p, '#omFirst', 120); await sleep(250);
          check(!(await dlgOpen(p)), 'a short tap opens nothing ' + lang);
          const before = await evaluate(p, `${$('pg')}.textContent`);
          await touchHold(p, '#omFirst', 60); await sleep(60); await touchHold(p, '#omFirst', 60); await sleep(300);
          check(!(await dlgOpen(p)), 'a double tap opens nothing ' + lang);
          const after = await evaluate(p, `${$('pg')}.textContent`);
          check(before !== after && /^1\s*\//.test(after.trim()), `the double tap still jumps to the first message (${before} → ${after}) ` + lang);
          await touchHold(p, '#omFirst', 650);
          await waitFor(p, `!${$('renameDlg')}.hidden`);
          check(await evaluate(p, `${$('renameTitle')}.textContent===${JSON.stringify(s.title)}`), 'title ' + lang);
          check(await evaluate(p, `${$('renameSub')}.textContent.includes(${JSON.stringify(s.other)}) && ${$('renameSub')}.textContent.includes('王小明（董事长）')`), 'another remote: says so ' + lang);
          const geo = await evaluate(p, `(()=>{const r=document.querySelector('#renameDlg .sq').getBoundingClientRect();return {cx:r.left+r.width/2,cy:r.top+r.height/2,w:innerWidth,h:innerHeight}})()`);
          check(Math.abs(geo.cx - geo.w / 2) < 2 && Math.abs(geo.cy - geo.h / 2) < 40, 'centred square ' + lang);
          check(await evaluate(p, `document.activeElement===${$('renameInput')}`), 'focus in the name field ' + lang);
          await save('rename-dialog-other');
          await key(p, 'Escape', { code: 'Escape', keyCode: 27 });
          await waitFor(p, `${$('renameDlg')}.hidden`);
          check(true, 'ESC closes ' + lang);
          await touchHold(p, '#omFirst', 650); await waitFor(p, `!${$('renameDlg')}.hidden`);
          await evaluate(p, `${$('renameCancel')}.click()`);
          check(await evaluate(p, `${$('renameDlg')}.hidden`), '取消 closes ' + lang);
          check(renames.length === 0, 'nothing sent while closing ' + lang);
        });

        await step('c', async () => {
          await mouseHold(p, '#omFirst', 650);
          await waitFor(p, `!${$('renameDlg')}.hidden`);
          check(true, 'mouse held ≥ 500 ms opens it ' + lang);
          answer = { ok: false, why: 'bad_name' };
          await evaluate(p, `${$('renameInput')}.value='殷丹阳（助理）'; ${$('renameSave')}.click()`);
          await waitFor(p, `${$('renameMsg')}.textContent.includes(${JSON.stringify(s.bad)})`);
          check(await dlgOpen(p), 'a refusal keeps the dialog open with the reason ' + lang);
          answer = { ok: true };
          await evaluate(p, `${$('renameSave')}.click()`);
          await waitFor(p, `${$('renameDlg')}.hidden`);
          check(renames.length === 2 && renames.every((m) => m.from === me && Object.keys(m).sort().join() === 'from,name,r,t' && m.name === '殷丹阳（助理）'),
            'dev_rename {r, name} only — no target: this remote only ' + lang);
          // this remote is now owner-named: its own page label + its original in the dialog
          await fake.send(prefs({ name: '王小明（董事长）', named_by: 'owner', self_name: '网页 · iOS Safari', label: '王小明（董事长） · iOS Safari' },
            { name: '殷丹阳（助理）', named_by: 'owner', self_name: '网页 · Android Chrome', label: '殷丹阳（助理） · Android Chrome' }));
          await fake.addTurn({ k: 'phone', dev: me, name: '殷丹阳（助理） · Android Chrome', text: lang === 'zh' ? '我自己发的' : 'Mine' }, 'ok 3');
          await sleep(300);
          await touchHold(p, '#omFirst', 650); await waitFor(p, `!${$('renameDlg')}.hidden`);
          check(await evaluate(p, `${$('renameInput')}.value==='殷丹阳（助理）' && !${$('renameOrig')}.hidden && ${$('renameOrig')}.textContent===${JSON.stringify(s.orig)}`),
            'own remote: current owner name prefilled, the device\'s own label shown ' + lang);
          await save('rename-dialog-own');
          await key(p, 'Escape', { code: 'Escape', keyCode: 27 });
          await waitFor(p, `${$('renameDlg')}.hidden`);
        });

        await step('d', async () => {
          // P165: the remotes list lives on P171's 遥控器管理 page (the settings phone list is gone); same notes, same data
          await evaluate(p, `document.getElementById('setBtn').click()`);
          await waitFor(p, `!document.getElementById('settings').hidden`);
          await evaluate(p, `document.getElementById('set-remotes-open').click()`);
          await waitFor(p, `document.querySelectorAll('#rm-list .name-note').length===2`, 15000);
          const notes = await evaluate(p, `[...document.querySelectorAll('#rm-list .name-note')].map(n=>[n.textContent,n.title])`);
          check(notes.every(([t]) => t === s.ownerNote), 'both owner-named ' + lang);
          check(notes.some(([, tt]) => tt.includes('网页 · iOS Safari')), 'the device\'s own label on request ' + lang);
          await fake.send(prefs({ name: '网页 · iOS Safari', named_by: 'device', self_name: '网页 · iOS Safari', label: '网页 · iOS Safari' }));
          await waitFor(p, `[...document.querySelectorAll('#rm-list .name-note')].some(n=>n.textContent===${JSON.stringify(s.devNote)})`, 15000);
          check(true, 'device-named note ' + lang);
          await save('settings-phones');
        });
      } finally { await p.close?.(); }
    }
    console.log(`P179 phone: ${checks} checks ok; shots → ${OUT}`);
  } finally { await B.close(); await fake.stop(); await web.stop(); rec.write(); }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await runP179();
