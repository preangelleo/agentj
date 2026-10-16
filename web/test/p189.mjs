// P189: 「转发到邮箱」 on the reply card's bottom row — real page + local fake host, own headless Chromium (never :9222).
//   reply-bar-mail   with the host's `contacts` cap the row gets a mail icon right after 转发 (Telegram), same 44 px button,
//                    no sideways overflow at 390 px; without the cap it stays hidden
//   reply-mail-turn  tap → P184's dialog for the whole turn (「你的提问和这条回复」); 发给我自己 ticked; 发送 → mail_fwd
//                    {turn, to:[owner]} with no `input` key (the message goes along); a skipped file → P295 its reason,
//                    size and the limit in the same centred result dialog
//   reply-mail-noinput 「不带我的提问」 → subtitle becomes the reply only; mail_fwd carries input:false; reopening resets it
//   file-card-mail   the file cards' mail icon (P183 mailfwd.js) is registered on a contacts host (opens the same dialog)
// zh / en × light / dark at 390×844; shots → reports/qa/p189/screens/.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = join(ROOT, 'reports/qa/p189/screens');
const $ = (id) => `document.getElementById('${id}')`;
const OWNER = 'leo@corp.example';
const S = { zh: { icon: '转发到邮箱', turn: '你的提问和这条回复', reply: '这条回复', noInput: '不带我的提问', skip: '超过单封邮件的附件上限' },
  en: { icon: 'Forward by e-mail', turn: 'Your message and this reply', reply: 'This reply', noInput: 'Leave out my message', skip: 'attachment limit of one mail' } };
export const CASES = { a: 'reply-bar-mail', b: 'reply-mail-turn', c: 'reply-mail-noinput', d: 'file-card-mail' };
const box = (p, sel) => evaluate(p, `(()=>{const r=document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect();return {l:r.left,r:r.right,t:r.top,b:r.bottom,w:r.width,h:r.height}})()`);
async function tapEl(p, sel) {
  const r = await box(p, sel), x = r.l + r.w / 2, y = r.t + r.h / 2;
  await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
  await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
}
// P295: the result shows in the same centred dialog (已发到 + addresses, never a toast); 知道了 closes it
async function sendOk(p) {
  await tapEl(p, '#ctFwd-send');
  await waitFor(p, `!!${$('ctFwd-to')} && !${$('ctFwd')}.hidden`);
  await sleep(650);                                   // the result ignores taps for 600 ms (the sending tap's ghost click)
  await tapEl(p, '#ctFwd-send'); await waitFor(p, `${$('ctFwd')}.hidden`);
}

export async function runP189() {
  mkdirSync(OUT, { recursive: true });
  const rec = parityRecorder('web/test/p189.mjs');
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
      let skipped = 0;
      fake.st.caps = ['contacts', 'tgfwd'];
      fake.st.tgfwd = true;
      fake.st.onApp = async (c, m, send) => {
        if (!/^(ct_|mail_fwd)/.test(m.t)) return false;
        got.push(m);
        if (m.t === 'ct_list') await send({ t: 'ct_res', r: m.r, ok: true, contacts: [], count: 0, me: [OWNER], mail: { ready: true } });
        else if (m.t === 'mail_fwd') await send({ t: 'mail_fwd_res', r: m.r, ok: true, sent: m.to.length, files: 1,
          ...(skipped ? { skipped, skips: [{ name: 'big.zip', bytes: 31457280, why: 'too_big' }], limit: 20971520 } : {}) });
        return true;
      };
      const p = await newPage(B, 390, 844, theme, { touch: true, allow: [web.url, fake.relay + '/'] });
      const save = async (name) => writeFileSync(join(OUT, `${lang}-${theme}-${name}.png`), await shoot(p, ''));
      const last = (t) => got.filter((m) => m.t === t).at(-1);
      try {
        await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
        await evaluate(p, `${$('phone-env-dismiss')}?.click()`);
        await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'AgentJ' });
        await fake.addTurn({ k: 'phone', dev: 'other', name: 'iPhone', text: lang === 'zh' ? '把这周的竞品动态整理成表' : 'Put this week\'s competitor moves in a table' },
          lang === 'zh' ? '## 本周竞品\n| 公司 | 动态 |\n|---|---|\n| A | 2.0 |' : '## This week\n| Company | Move |\n|---|---|\n| A | 2.0 |');
        await waitFor(p, `!${$('mailBtn')}.hidden && !${$('mailBtn')}.disabled`);

        await step('a', async () => {
          check(await evaluate(p, `${$('fwdBtn')}.nextElementSibling===${$('mailBtn')}`), 'right after 转发 (Telegram)');
          check(await evaluate(p, `${$('mailBtn')}.getAttribute('aria-label')`) === s.icon, 'label ' + lang);
          const b = await box(p, '#mailBtn');
          // P340 #38: the box is glyph + one gap wide; the 44 px target is its ::after (width:max(100%,44px))
          const tw = await evaluate(p, `parseFloat(getComputedStyle(${$('mailBtn')}, '::after').width) || 0`);
          check(Math.max(b.w, tw) >= 44 && b.h >= 44, `44 px target (${b.w}x${b.h}, ::after ${tw})`);
          const bar = await box(p, '#rmbar'), first = await box(p, '#readBtn');
          check(first.l >= bar.l && b.r <= bar.r + 0.5, 'the six icons fit the row');
          check(await evaluate(p, `document.documentElement.scrollWidth<=innerWidth`), 'no sideways overflow');
          check(await evaluate(p, `[...${$('rmbar')}.querySelectorAll('.act')].filter(x=>!x.hidden).length`) === 6, 'six icons');
          await save('reply-bar');
        });

        await step('b', async () => {
          await tapEl(p, '#mailBtn');
          await waitFor(p, `!${$('ctFwd')}.hidden && !${$('ctFwd-send')}.disabled`);
          check((await evaluate(p, `${$('ctFwd-what')}.textContent`)).includes(s.turn), 'the whole turn');
          check(await evaluate(p, `${$('ctFwd-noinput')}.getAttribute('aria-checked')`) === 'false', '不带我的提问 unticked by default');
          check(await evaluate(p, `document.querySelector('#ctFwd .ct-mode[data-mode=me]').getAttribute('aria-checked')`) === 'true', '发给我自己 by default');
          check(await evaluate(p, `[...document.querySelectorAll('#ctFwd button')].filter(b=>b.offsetParent).every(b=>b.getBoundingClientRect().height>=44)`), 'touch ≥ 44 px');
          await save('dialog-turn');
          await sendOk(p);
          const f = last('mail_fwd');
          check(f && Number.isInteger(f.turn) && f.to.length === 1 && f.to[0].addr === OWNER && !('input' in f) && !f.mid, 'mail_fwd whole turn ' + JSON.stringify(f));
          skipped = 1;
          await tapEl(p, '#mailBtn'); await waitFor(p, `!${$('ctFwd')}.hidden && !${$('ctFwd-send')}.disabled`);
          await tapEl(p, '#ctFwd-send');
          await waitFor(p, `[...document.querySelectorAll('#ctFwd .ct-res-note')].some(n=>n.textContent.includes(${JSON.stringify(s.skip)}))`);
          const note = await evaluate(p, `document.querySelector('#ctFwd .ct-res-note').textContent`);
          check(note.includes('big.zip') && note.includes('30.0\u00a0MB') && note.includes('20.0\u00a0MB'), 'P295: the real reason, size and limit ' + note);
          await save('dialog-skipped');
          await sleep(650); await tapEl(p, '#ctFwd-send'); await waitFor(p, `${$('ctFwd')}.hidden`);
          skipped = 0;
        });

        await step('c', async () => {
          await sleep(200);
          await tapEl(p, '#mailBtn'); await waitFor(p, `!${$('ctFwd')}.hidden && !${$('ctFwd-send')}.disabled`);
          await tapEl(p, '#ctFwd-noinput');
          await waitFor(p, `${$('ctFwd-noinput')}.getAttribute('aria-checked')==='true'`);
          check((await evaluate(p, `${$('ctFwd-what')}.textContent`)).startsWith(s.reply), 'subtitle → the reply only');
          check((await evaluate(p, `${$('ctFwd-noinput')}.textContent`)).includes(s.noInput), 'toggle text');
          await save('dialog-noinput');
          await sendOk(p);
          check(last('mail_fwd').input === false, 'input:false sent');
          await tapEl(p, '#mailBtn'); await waitFor(p, `!${$('ctFwd')}.hidden && ${$('ctFwd-noinput')}`);
          check(await evaluate(p, `${$('ctFwd-noinput')}.getAttribute('aria-checked')`) === 'false', 'reopening resets the toggle');
          await evaluate(p, `${$('ctFwd-cancel')}.click()`);
        });

        await step('d', async () => {
          check(await evaluate(p, `import('./js/mailfwd.js').then(m=>m.canMailForward())`), 'file cards: mail icon registered');
        });
      } catch (e) { console.error('page problems:', p.problems, await evaluate(p, 'document.body.dataset.uiError||""').catch(() => '')); throw e; } finally { await p.dispose(); }
    }
    // without the cap: no mail icon on the row
    fake.reset();
    fake.st.caps = [];
    const p = await newPage(B, 390, 844, 'light', { touch: true, allow: [web.url, fake.relay + '/'] });
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=zh')); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await fake.addTurn({ k: 'phone', dev: 'other', text: '你好' }, '你好！');
      await sleep(400);
      check(await evaluate(p, `${$('mailBtn')}.hidden && ${$('mailBtn')}.offsetParent===null`), 'older host: hidden');
      check(!(await evaluate(p, `import('./js/mailfwd.js').then(m=>m.canMailForward())`)), 'older host: file cards keep it hidden');
    } finally { await p.dispose(); }
    console.log(`p189: ${checks} checks passed`);
  } finally { await B.close(); await fake.stop(); await web.stop(); rec.write(); }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await runP189();
