// P194: slot 0 of the address book = the owner — real page + local fake host, own headless Chromium (never :9222).
//   owner-card      the host's ct_card {mode: owner} (after the first welcome / `agentj contacts owner set`): own title, only
//                   name + main e-mail + optional phone, pre-filled suggestion; no e-mail → refused on the phone; 保存 →
//                   ct_card_answer; 跳过 → ok:false; touch targets ≥ 44 px, no sideways overflow
//   owner-settings  Settings → 我的联系方式 (only with the contacts cap) → a local owner card read from ct_list.owner;
//                   保存 → ct_save {id: slot 0}; toast; Esc = 跳过 (nothing sent)
// zh / en × light / dark at 390×844; shots → reports/qa/p194/screens/.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = join(ROOT, 'reports/qa/p194/screens');
const $ = (id) => `document.getElementById('${id}')`;
const SLOT0 = 'c000000000000';
const S = { zh: { title: '你的联系方式', skip: '跳过', main: '主邮箱', need: '请至少填一个主邮箱', saved: '联系人已保存', row: '我的联系方式' },
  en: { title: 'Your contact details', skip: 'Skip', main: 'Main e-mail', need: 'Please enter your main e-mail', saved: 'Contact saved', row: 'My contact details' } };
export const CASES = { a: 'owner-card', b: 'owner-settings' };
const esc = (p) => p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
const box = (p, sel) => evaluate(p, `(()=>{const r=document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect();return {l:r.left,r:r.right,t:r.top,b:r.bottom,w:r.width,h:r.height}})()`);
async function tapEl(p, sel) {
  const r = await box(p, sel), x = r.l + r.w / 2, y = r.t + r.h / 2;
  await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
  await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
}

export async function runP194() {
  mkdirSync(OUT, { recursive: true });
  const rec = parityRecorder('web/test/p194.mjs');
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
  const fake = await startFakeHost();
  const B = await launch();
  let checks = 0;
  const check = (ok, label) => { assert.ok(ok, label); checks++; };
  const step = async (c, f) => { try { await f(); rec.record(CASES[c], true); } catch (e) { rec.record(CASES[c], false); throw e; } };
  try {
    for (const lang of ['zh', 'en']) for (const theme of ['light', 'dark']) {
      const s = S[lang];
      fake.reset();
      const got = [];
      const host = { owner: { id: SLOT0, slot: 0, name: '王董', nicks: [], emails: [{ v: 'boss@corp.example', label: '' }], phones: [], letter: 'W' } };
      fake.st.caps = ['contacts'];
      fake.st.onApp = async (c, m, send) => {
        if (!/^ct_/.test(m.t)) return false;
        got.push(m);
        if (m.t === 'ct_list') await send({ t: 'ct_res', r: m.r, ok: true, contacts: [host.owner], count: 1, me: ['boss@corp.example'], owner: host.owner, mail: { ready: true } });
        else if (m.t === 'ct_save') await send({ t: 'ct_res', r: m.r, ok: true, result: 'updated', contact: { ...host.owner, emails: m.contact.emails } });
        return true;
      };
      const p = await newPage(B, 390, 844, theme, { touch: true, allow: [web.url, fake.relay + '/'] });
      const save = async (name) => writeFileSync(join(OUT, `${lang}-${theme}-${name}.png`), await shoot(p, ''));
      const last = (t) => got.filter((m) => m.t === t).at(-1);
      try {
        await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
        await evaluate(p, `${$('phone-env-dismiss')}?.click()`);
        await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'AgentJ' });
        await waitFor(p, `window.AgentJMailForward && window.AgentJMailForward.available()`);

        await step('a', async () => {
          const id = 'ef'.repeat(16), n = 'n1';
          await fake.send({ t: 'ct_card', id, n, mode: 'owner', contact: { emails: [{ v: 'leo.alt@corp.example', label: '' }] }, base: null, why: '', ttl: 600 });
          await waitFor(p, `!!${$('ctCard')} && !${$('ctCard')}.hidden && ${$('ct-f-emails-0')}?.value==='leo.alt@corp.example'`);
          check(await evaluate(p, `${$('ctCard-title')}.textContent`) === s.title, 'owner card title');
          check(await evaluate(p, `${$('ctCard-no')}.textContent`) === s.skip, '跳过');
          check(await evaluate(p, `!${$('ct-f-nick')} && !${$('ct-f-org')} && !${$('ct-f-note')} && !document.querySelector('.ct-multi[data-k=addresses]')`), 'only name, e-mail, phone');
          check((await evaluate(p, `${$('ct-f-emails-0')}.previousElementSibling.textContent`)) === s.main, 'main e-mail label');
          check((await evaluate(p, `${$('ctCard-why')}.textContent`)).length > 10, 'says why');
          const small = await evaluate(p, `[...document.querySelectorAll('#ctCard button')].filter(b=>b.offsetParent).map(b=>b.getBoundingClientRect()).filter(r=>r.height<44).length`);
          check(small === 0, 'touch targets ≥ 44 px');
          check(await evaluate(p, `document.documentElement.scrollWidth<=innerWidth`), 'no sideways overflow');
          await save('owner-card');
          await evaluate(p, `${$('ct-f-emails-0')}.value=''`);
          await evaluate(p, `${$('ct-f-phones-0')}.value='13800001111'`);
          await tapEl(p, '#ctCard-save');
          await waitFor(p, `${$('ctCard-note')}.textContent.includes(${JSON.stringify(s.need)})`);
          check(!got.some((m) => m.t === 'ct_card_answer'), 'a phone alone is not enough for slot 0');
          await evaluate(p, `${$('ct-f-name')}.value='王董'; ${$('ct-f-emails-0')}.value='leo@corp.example'`);
          await tapEl(p, '#ctCard-save'); await sleep(200);
          const a = last('ct_card_answer');
          check(a && a.id === id && a.ok === true && a.contact.emails[0].v === 'leo@corp.example' && a.contact.phones[0].v === '13800001111', 'answer ' + JSON.stringify(a));
          await fake.send({ t: 'ct_card_done', id, result: 'saved' });
          await waitFor(p, `${$('ctCard')}.hidden`);
          const id2 = 'fe'.repeat(16);
          await fake.send({ t: 'ct_card', id: id2, n: 'n2', mode: 'owner', contact: {}, base: null, why: '', ttl: 600 });
          await waitFor(p, `!${$('ctCard')}.hidden`);
          await tapEl(p, '#ctCard-no'); await sleep(200);
          const a2 = last('ct_card_answer');
          check(a2.id === id2 && a2.ok === false, '跳过 = ok:false');
          await waitFor(p, `${$('ctCard')}.hidden`);
        });

        await step('b', async () => {
          await evaluate(p, `${$('setBtn')}.click()`);
          await waitFor(p, `!${$('settings')}.hidden && !${$('set-owner-nav')}.hidden`);
          check((await evaluate(p, `${$('set-owner-nav')}.textContent`)).includes(s.row), 'Settings row');
          await evaluate(p, `${$('set-owner-nav')}.scrollIntoView()`);
          await save('owner-settings-row');
          await tapEl(p, '#set-owner-nav');
          await waitFor(p, `!${$('ctCard')}.hidden && ${$('ct-f-emails-0')}?.value==='boss@corp.example' && ${$('ct-f-name')}.value==='王董'`);
          check(true, 'local card filled from slot 0');
          await save('owner-settings-card');
          await esc(p); await sleep(200);
          check(!got.some((m) => m.t === 'ct_save'), 'Esc = 跳过, nothing saved');
          await waitFor(p, `${$('ctCard')}.hidden`);
          await tapEl(p, '#set-owner-nav');
          await waitFor(p, `!${$('ctCard')}.hidden && ${$('ct-f-emails-0')}?.value==='boss@corp.example'`);
          await evaluate(p, `${$('ct-f-emails-0')}.value='chair@corp.example'`);
          await tapEl(p, '#ctCard-save');
          await waitFor(p, `${$('ctCard')}.hidden && ${$('toast')}.textContent.includes(${JSON.stringify(s.saved)})`);
          const sv = last('ct_save');
          check(sv && sv.id === SLOT0 && sv.contact.emails[0].v === 'chair@corp.example', 'ct_save slot 0 ' + JSON.stringify(sv));
          await esc(p); await waitFor(p, `${$('settings')}.hidden`);
        });
      } catch (e) { console.error('page problems:', p.problems, await evaluate(p, 'document.body.dataset.uiError||""').catch(()=>'')); throw e; } finally { await p.dispose(); }
    }
    console.log(`p194: ${checks} checks passed`);
  } finally { await B.close(); await fake.stop(); await web.stop(); rec.write(); }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await runP194();
