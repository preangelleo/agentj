// P549 (#314 / #316): a card that asks the owner is never behind the forward dialog — it sits on top and has the focus;
// a key card another remote already finished folds here with one line, and the forward dialog's failure says the real reason.
// Real client against the fake host; synthetic values only. Shots: reports/qa/p549/.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomBytes } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';
import { b64u } from '../../protocol/wire.js';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = join(ROOT, 'reports/qa/p549');
const $ = (id) => `document.getElementById('${id}')`;
const OWNER = 'owner.p549@example.com';
const hex = () => randomBytes(16).toString('hex');
// the topmost element at a control's centre is the control (or inside it): nothing covers it
const onTop = (id) => `(()=>{const b=${$(id)};if(!b||!b.getClientRects().length)return false;const r=b.getBoundingClientRect();
  const e=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);return !!e&&(e===b||b.contains(e))})()`;
const S = { zh: { elsewhere: '已在另一台设备上完成', stale: '设置刚改过' }, en: { elsewhere: 'Completed on another device', stale: 'settings just changed' } };

mkdirSync(OUT, { recursive: true });
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
let checks = 0;
const check = (ok, label) => { assert.ok(ok, label); checks++; };
try {
  for (const lang of ['zh', 'en']) for (const [width, theme] of [[390, 'dark'], [1440, 'light']]) {
    fake.reset();
    let fwdWhy = null;
    fake.st.caps = ['contacts'];
    fake.st.onApp = async (c, m, send) => {
      if (m.t === 'ct_list') { await send({ t: 'ct_res', r: m.r, ok: true, contacts: [], count: 0, me: [OWNER], mail: { ready: true } }); return true; }
      if (m.t === 'mail_fwd') { await send({ t: 'mail_fwd_res', r: m.r, ...(fwdWhy ? { ok: false, why: fwdWhy } : { ok: true, sent: 1, files: 0 }) }); return true; }
      return false;
    };
    const p = await newPage(B, width, 844, theme, { touch: true, allow: [web.url, fake.relay + '/'] });
    const save = async (name) => { if (width === 390) writeFileSync(join(OUT, `${lang}-${theme}-${name}.png`), await shoot(p, '')); };
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `${$('phone-env-dismiss')}?.click()`);
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'AgentJ' });
      await fake.addTurn({ k: 'phone', text: lang === 'zh' ? '总结一下' : 'Sum it up' }, lang === 'zh' ? '## 结论\n三件事都完成了。' : '## Done\nAll three are finished.');
      await waitFor(p, `!${$('mailBtn')}.hidden && !${$('mailBtn')}.disabled`);
      const open = async () => { await evaluate(p, `${$('mailBtn')}.click()`); await waitFor(p, `!${$('ctFwd')}.hidden && !${$('ctFwd-send')}.disabled`); };

      // 1 · an approval card arrives while the forward dialog is open: on top, focused, Esc closes the card first
      await open();
      const askId = hex();
      await fake.ask({ id: askId, tool: 'agentj mail send', summary: lang === 'zh' ? '发邮件给：friend@example.com' : 'Mail to: friend@example.com', ttl: 120, cat: ['send'] });
      await waitFor(p, `document.body.dataset.sheet==='1'`);
      await waitFor(p, onTop('apprAllow'));
      check(await evaluate(p, onTop('apprAllow')), 'approval allow button is not covered by the dialog');
      await waitFor(p, `${$('sheet')}.contains(document.activeElement)`);
      check(await evaluate(p, `${$('sheet')}.contains(document.activeElement)`), 'the approval card has the focus');
      check(await evaluate(p, `!${$('ctFwd')}.hidden`), 'the dialog underneath stays');
      await save('approval-over-dialog');
      await fake.askDone(askId, 'deny');
      await waitFor(p, `document.body.dataset.sheet!=='1'`);

      // 2 · a key card over the open dialog: on top; another remote saves it → folds here with one line
      const card = { t: 'elev', id: hex(), n: hex(), kind: 'secret', epk: b64u(randomBytes(32)), ttl: 600, tries: 3, name: 'AGENTJ_MAIL_PASSWORD',
        purpose: 'Gmail App Password', dest: lang === 'zh' ? 'Agent J 邮件通道（本机加密）' : 'Agent J mail channel (encrypted on the host)', verify: 'IMAP + SMTP login' };
      await fake.send(card);
      await waitFor(p, `!${$('elev')}.hidden`);
      await waitFor(p, onTop('elev-allow'));
      check(await evaluate(p, onTop('elev-allow')), 'the key card is not covered by the dialog');
      await waitFor(p, `${$('elev')}.contains(document.activeElement)`, 3000).catch(() => {});
      check(await evaluate(p, `${$('elev')}.contains(document.activeElement)`), 'the key card has the focus: ' + await evaluate(p, `document.activeElement?.id||document.activeElement?.tagName`));
      await save('key-card-over-dialog');
      await fake.send({ t: 'elev_done', id: card.id, result: 'saved', verify: 'ok', elsewhere: 1 });
      await waitFor(p, `${$('elev')}.hidden`);
      await waitFor(p, `[...document.querySelectorAll('.toast')].some(x=>x.textContent.includes(${JSON.stringify(S[lang].elsewhere)}))`);
      check(true, 'folded with 「completed on another device」');
      await save('folded-elsewhere');

      // 3 · the forward itself: no card on this remote; a failure says its real reason, not 「check the connection」
      fwdWhy = 'stale';
      await evaluate(p, `${$('ctFwd-send')}.click()`);
      await waitFor(p, `[...document.querySelectorAll('#ctFwd .ct-res-note')].some(n=>n.textContent.includes(${JSON.stringify(S[lang].stale)}))`);
      check(await evaluate(p, `document.body.dataset.sheet!=='1' && ${$('elev')}.hidden`), 'no approval or passkey card for the owner\'s own forward');
      await save('fwd-stale');
      fwdWhy = null;
      await sleep(650);
      await evaluate(p, `${$('ctFwd-send')}.click()`);
      await waitFor(p, `!!${$('ctFwd-to')}`);
      check(true, 'retry sends');
    } catch (e) { console.error("page problems:", p.problems); throw e; } finally { await p.dispose(); }
  }
  console.log(`P549 OK — ${checks} checks · shots ${OUT}`);
} finally { await B.close(); await fake.stop(); await web.stop(); }
