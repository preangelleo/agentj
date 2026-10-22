// P184 (PROTOCOL §25): 「转发到邮箱」 and the contact card — real page + local fake host, own headless Chromium (never :9222).
//   forward-me      a data-mailfwd button opens the centred dialog; 「发给我自己」 is ticked with the owner's address; 发送 →
//                   mail_fwd {to:[owner], turn}; P295 result in the same dialog (已发到 + address); Esc / outside close; touch targets ≥ 44 px; no sideways overflow
//   forward-other   发给别人: the book sorted 最近 / 最常用 / A–Z (letter headers); search goes to the host (q); picking
//                   someone unticks 「发给我自己」; 新增 (address + nickname) → ct_save, selected; 发送 → exactly the picked
//                   addresses; a file card's mid is forwarded with its name shown
//   forward-no-mail no mailbox → the dialog says so and 去设置 opens Settings → 邮件通道
//   contact-card    the host's ct_card (pre-filled) → edit; no e-mail / phone → refused on the phone; 保存 → ct_card_answer
//                   with the edited contact; ct_card_done closes; update mode marks what the Agent added; Esc = 不保存
// zh / en × light / dark at 390×844; shots → reports/qa/p184/screens/.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = join(ROOT, 'reports/qa/p184/screens');
const $ = (id) => `document.getElementById('${id}')`;
const OWNER = 'leo@corp.example';
const BOOK = [
  { id: 'c000000000001', name: '张三', nicks: ['张总'], emails: [{ v: 'zhangsan@hongyuan.cn', label: 'work' }], phones: [], send_count: 5, last_sent_at: 300, letter: 'Z' },
  { id: 'c000000000002', name: '李四', nicks: [], emails: [{ v: 'lisi@163.com', label: '' }, { v: 'li4@corp.cn', label: 'work' }], phones: [], send_count: 9, last_sent_at: 100, letter: 'L' },
  { id: 'c000000000003', name: 'Anna Lee', nicks: [], emails: [{ v: 'anna@contoso.com', label: '' }], phones: [], send_count: 1, last_sent_at: 200, letter: 'A' },
];
const ORDER = { recent: ['c000000000001', 'c000000000003', 'c000000000002'], frequent: ['c000000000002', 'c000000000001', 'c000000000003'],
  name: ['c000000000003', 'c000000000002', 'c000000000001'] };
const S = { zh: { dup: '刚刚已经发过了', again: '再发一次', fail: '发送失败', retry: '重试', title: '转发到邮箱', me: '发给我自己', other: '发给别人', sent: '已发到', noMail: '还没设置发信邮箱', card: '存成新联系人', need: '至少填一个邮箱或电话', saved: '已保存', upd: '补全联系人' },
  en: { dup: 'Already sent just now', again: 'Send again', fail: 'Sending failed', retry: 'Retry', title: 'Forward by e-mail', me: 'To me', other: 'To someone else', sent: 'Sent to', noMail: 'No mailbox to send from yet', card: 'Save as a new contact', need: 'at least one e-mail or phone', saved: 'Saved', upd: 'Complete contact' } };
export const CASES = { a: 'forward-me', b: 'forward-other', c: 'forward-no-mail', d: 'contact-card', e: 'forward-dup-and-retry' };
const esc = (p) => p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
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

export async function runP184() {
  mkdirSync(OUT, { recursive: true });
  const rec = parityRecorder('web/test/p184.mjs');
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
      const host = { mailReady: true, me: [OWNER], book: BOOK.map((c) => ({ ...c })), fwd: [] };
      fake.st.caps = ['contacts'];
      fake.st.onApp = async (c, m, send) => {
        if (!/^(ct_|mail_fwd)/.test(m.t)) return false;
        got.push(m);
        if (m.t === 'ct_list') {
          let list = ORDER[m.sort || 'recent'].map((id) => host.book.find((x) => x.id === id)).filter(Boolean);
          list = list.concat(host.book.filter((x) => !list.includes(x)));
          if (m.q) list = host.book.filter((x) => m.q === 'zhangsan' ? x.name === '张三' : x.name.includes(m.q));
          await send({ t: 'ct_res', r: m.r, ok: true, contacts: list, count: list.length, me: host.me, mail: host.mailReady ? { ready: true } : { ready: false, why: 'mail_not_configured' } });
        } else if (m.t === 'ct_save') {
          const n = { id: 'c00000000000' + (host.book.length + 1), name: m.contact.name, nicks: m.contact.nicks, emails: m.contact.emails.map((v) => ({ v: v.toLowerCase(), label: '' })), phones: [], send_count: 0, last_sent_at: null, letter: 'W' };
          host.book.unshift(n);
          await send({ t: 'ct_res', r: m.r, ok: true, result: 'added', contact: n });
        } else if (m.t === 'mail_fwd') {
          const mode = host.fwd.shift();                       // P295: scripted answers for the dup / failure case
          if (mode === 'dup' && !m.again) await send({ t: 'mail_fwd_res', r: m.r, ok: false, why: 'dup', ago: 95 });
          else if (mode === 'fail') await send({ t: 'mail_fwd_res', r: m.r, ok: false, why: 'send_failed', detail: 'smtp' });
          else await send({ t: 'mail_fwd_res', r: m.r, ok: true, sent: m.to.length, files: m.mid ? 1 : 0 });
        }
        return true;
      };
      const p = await newPage(B, 390, 844, theme, { touch: true, allow: [web.url, fake.relay + '/'] });
      const save = async (name) => writeFileSync(join(OUT, `${lang}-${theme}-${name}.png`), await shoot(p, ''));
      const last = (t) => got.filter((m) => m.t === t).at(-1);
      try {
        await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
        await evaluate(p, `${$('phone-env-dismiss')}?.click()`);
        await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'AgentJ' });
        await fake.addTurn({ k: 'phone', dev: 'other', text: lang === 'zh' ? '把周报整理一下' : 'Write up the weekly report' }, lang === 'zh' ? '周报整理好了：report.pdf' : 'The weekly report is ready: report.pdf');
        await waitFor(p, `window.AgentJMailForward && window.AgentJMailForward.available()`);
        // the entry other cards use (P183): any element with data-mailfwd
        await evaluate(p, `(()=>{const b=document.createElement('button');b.id='mf-test';b.dataset.mailfwd='reply';b.dataset.turn='1';b.textContent='✉';b.style.cssText='position:fixed;left:8px;top:120px;width:48px;height:48px;z-index:5';document.body.append(b);})()`);

        await step('a', async () => {
          await tapEl(p, '#mf-test');
          await waitFor(p, `!${$('ctFwd')}.hidden && document.querySelector('#ctFwd .ct-mode[data-mode=me]')?.getAttribute('aria-checked')==='true' && document.querySelector('#ctFwd .ct-mode[data-mode=me]').textContent.includes('${OWNER}')`);
          check(await evaluate(p, `document.getElementById('ctFwd-title').textContent`) === s.title, 'title ' + lang);
          check((await evaluate(p, `document.querySelector('#ctFwd .ct-mode[data-mode=me]').textContent`)).includes(OWNER), 'owner address shown');
          const sq = await box(p, '#ctFwd .sq');
          check(Math.abs(sq.l + sq.w / 2 - 195) < 2 && sq.t > 0 && sq.b < 844, `centred (${sq.l},${sq.t},${sq.w}x${sq.h})`);
          check(await evaluate(p, `document.documentElement.scrollWidth<=innerWidth`), 'no sideways overflow');
          const small = await evaluate(p, `[...document.querySelectorAll('#ctFwd button')].filter(b=>b.offsetParent).map(b=>b.getBoundingClientRect()).filter(r=>r.height<44).length`);
          check(small === 0, 'touch targets ≥ 44 px');
          await save('forward-me');
          await esc(p); await waitFor(p, `${$('ctFwd')}.hidden`); check(true, 'Esc closes');
          await tapEl(p, '#mf-test'); await waitFor(p, `!${$('ctFwd')}.hidden && !${$('ctFwd-send')}.disabled`);
          await tapEl(p, '#ctFwd-send');
          await waitFor(p, `${$('ctFwd-to')}?.textContent.includes(${JSON.stringify(OWNER)})`);
          await tapEl(p, '#ctFwd-send'); await sleep(100);
          check(!(await evaluate(p, `${$('ctFwd')}.hidden`)), 'P295: a second tap right away does not close the result');
          const f = last('mail_fwd');
          check(f && f.turn === 1 && f.to.length === 1 && f.to[0].addr === OWNER && !f.mid, 'mail_fwd to the owner for turn 1 ' + JSON.stringify(f));
          check((await evaluate(p, `${$('ctFwd-what')}.textContent`)).includes(s.sent), 'P295: 已发到 in the centred dialog');
          check(!(await evaluate(p, `${$('toast')}.classList.contains('on')`)), 'P295: no toast');
          await save('forward-sent');
          await sleep(650); await tapEl(p, '#ctFwd-send'); await waitFor(p, `${$('ctFwd')}.hidden`);
        });

        await step('b', async () => {
          await tapEl(p, '#mf-test'); await waitFor(p, `!${$('ctFwd')}.hidden && !${$('ctFwd-send')}.disabled`);
          await tapEl(p, '#ctFwd .ct-mode[data-mode=other]');
          await waitFor(p, `document.querySelectorAll('#ctFwd-list .ct-person').length===4`);
          const order = await evaluate(p, `[...document.querySelectorAll('#ctFwd-list .ct-person')].map(b=>b.dataset.addr)`);
          check(order[0] === 'zhangsan@hongyuan.cn' && order.length === 4, 'recent first; one row per address ' + order);
          check(await evaluate(p, `${$('ctFwd-send')}.disabled`), 'nobody picked → no send');
          await tapEl(p, '#ctFwd .ct-sorts [data-sort=frequent]');
          await waitFor(p, `document.querySelector('#ctFwd-list .ct-person')?.dataset.addr==='lisi@163.com'`);
          await tapEl(p, '#ctFwd .ct-sorts [data-sort=name]');
          await waitFor(p, `document.querySelector('#ctFwd-list li')?.classList.contains('ct-letter')`);
          check(await evaluate(p, `[...document.querySelectorAll('#ctFwd-list .ct-letter')].map(x=>x.textContent).join('')`) === 'ALZ', 'letter headers A L Z');
          check(last('ct_list').sort === 'name', 'sort asked of the host');
          await tapEl(p, '#ctFwd-list [data-addr="anna@contoso.com"]');
          await waitFor(p, `document.querySelector('#ctFwd-list [data-addr="anna@contoso.com"]').getAttribute('aria-checked')==='true'`);
          check(await evaluate(p, `document.querySelector('#ctFwd .ct-mode[data-mode=me]').getAttribute('aria-checked')`) === 'false', 'picking someone else unticks 发给我自己');
          await evaluate(p, `(()=>{const i=${$('ctFwd-q')};i.focus();i.value='zhangsan';i.dispatchEvent(new Event('input'))})()`);
          await waitFor(p, `document.querySelectorAll('#ctFwd-list .ct-person').length===1`);
          check(last('ct_list').q === 'zhangsan', 'search on the Agent J host (pinyin)');
          await tapEl(p, '#ctFwd-list [data-addr="zhangsan@hongyuan.cn"]');
          await evaluate(p, `(()=>{const i=${$('ctFwd-q')};i.value='王总';i.dispatchEvent(new Event('input'))})()`);
          await waitFor(p, `${$('ctFwd-add')}?.open && ${$('ctFwd-nick')}.value==='王总'`);
          check(true, 'no hit → 新增 opens with the nickname filled');
          await evaluate(p, `${$('ctFwd-addr')}.value='Wang@X.cn'`);
          await save('forward-add');
          await tapEl(p, '#ctFwd-addgo');
          await waitFor(p, `document.querySelector('#ctFwd-list [data-addr="wang@x.cn"]')?.getAttribute('aria-checked')==='true'`);
          const sv = last('ct_save');
          check(sv.contact.emails[0] === 'Wang@X.cn' && sv.contact.nicks[0] === '王总', 'ct_save address + nickname');
          check((await evaluate(p, `document.querySelector('#ctFwd .ct-mode[data-mode=other]').textContent`)).includes('3'), 'three picked');
          await save('forward-other');
          await sendOk(p);
          const f = last('mail_fwd');
          check(JSON.stringify(f.to.map((x) => x.addr).sort()) === JSON.stringify(['anna@contoso.com', 'wang@x.cn', 'zhangsan@hongyuan.cn']), 'exactly the picked ' + JSON.stringify(f.to));
          await evaluate(p, `window.AgentJMailForward.open({turn:1, mid:'AAAAAAAAAAAAAAAAAAAAAA', name:'report.pdf'})`);
          await waitFor(p, `!${$('ctFwd')}.hidden && ${$('ctFwd-what')}.textContent.includes('report.pdf') && !${$('ctFwd-send')}.disabled`);
          await sendOk(p);
          check(last('mail_fwd').mid === 'AAAAAAAAAAAAAAAAAAAAAA', 'a file card forwards its mid');
          await tapEl(p, '#mf-test'); await waitFor(p, `!${$('ctFwd')}.hidden`);
          await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: 195, y: 838 }] });
          await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
          await waitFor(p, `${$('ctFwd')}.hidden`); check(true, 'outside closes');
        });

        await step('c', async () => {
          host.mailReady = false;
          await tapEl(p, '#mf-test'); await waitFor(p, `!${$('ctFwd')}.hidden && document.querySelector('#ctFwd .ct-setup:not(.ct-wait)')`);
          check((await evaluate(p, `document.querySelector('#ctFwd .ct-setup:not(.ct-wait)').textContent`)).includes(s.noMail), 'says no mailbox');
          await save('forward-no-mail');
          await tapEl(p, '#ctFwd-send');
          await waitFor(p, `${$('ctFwd')}.hidden && !${$('settings')}.hidden && !${$('set-mail')}.hidden`);
          check(true, '去设置 → Settings → 邮件通道');
          await evaluate(p, `${$('set-back')}.click()`); await esc(p); await waitFor(p, `${$('settings')}.hidden`);
          host.mailReady = true;
        });

        await step('e', async () => {        // P295: a second send of the same page asks first; a failure offers 重试
          host.fwd = ['dup', 'ok'];
          await tapEl(p, '#mf-test'); await waitFor(p, `!${$('ctFwd')}.hidden && !${$('ctFwd-send')}.disabled`);
          await tapEl(p, '#ctFwd-send');
          await waitFor(p, `${$('ctFwd-title')}.textContent===${JSON.stringify(s.dup)}`);
          check((await evaluate(p, `${$('ctFwd-body')}.textContent`)).includes(lang === 'zh' ? '2 分钟' : '2 min'), 'how long ago');
          check(await evaluate(p, `${$('ctFwd-send')}.textContent`) === s.again && !(await evaluate(p, `${$('ctFwd-cancel')}.hidden`)), '再发一次 + 关闭');
          check(!(await evaluate(p, `${$('toast')}.classList.contains('on')`)), 'no toast');
          await save('forward-dup');
          await sleep(650); await tapEl(p, '#ctFwd-send');
          await waitFor(p, `!!${$('ctFwd-to')}`);
          check(last('mail_fwd').again === true, '再发一次 → again:true');
          await esc(p); await waitFor(p, `${$('ctFwd')}.hidden`); check(true, 'Esc closes the result');
          host.fwd = ['fail', 'ok'];
          await tapEl(p, '#mf-test'); await waitFor(p, `!${$('ctFwd')}.hidden && !${$('ctFwd-send')}.disabled`);
          await tapEl(p, '#ctFwd-send');
          await waitFor(p, `${$('ctFwd-title')}.textContent===${JSON.stringify(s.fail)}`);
          check(await evaluate(p, `${$('ctFwd-send')}.textContent`) === s.retry, '重试');
          await save('forward-failed');
          await sleep(650); await tapEl(p, '#ctFwd-send');
          await waitFor(p, `!!${$('ctFwd-to')}`);
          check(!last('mail_fwd').again, 'a retry after a failure is not again:true (the host still guards double sends)');
          const small = await evaluate(p, `[...document.querySelectorAll('#ctFwd button')].filter(b=>b.offsetParent).map(b=>b.getBoundingClientRect()).filter(r=>r.height<44).length`);
          check(small === 0, 'result buttons ≥ 44 px');
          const wide = await evaluate(p, `document.querySelector('#ctFwd .sq').scrollWidth > document.querySelector('#ctFwd .sq').clientWidth + 1`);
          check(!wide, 'no sideways overflow');
          await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: 195, y: 838 }] });
          await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
          await waitFor(p, `${$('ctFwd')}.hidden`); check(true, 'outside closes the result');
        });

        await step('d', async () => {
          const id = 'ab'.repeat(16), n = 'n1';
          await fake.send({ t: 'ct_card', id, n, mode: 'new', contact: { name: '赵六', nicks: ['六哥'] }, base: null, why: lang === 'zh' ? '主人要给赵六发邮件' : 'Owner wants to mail Zhao Liu', ttl: 600 });
          await waitFor(p, `!!${$('ctCard')} && !${$('ctCard')}.hidden && ${$('ct-f-name')}?.value==='赵六'`);
          check(await evaluate(p, `${$('ctCard-title')}.textContent`) === s.card, 'new card title');
          check(await evaluate(p, `${$('ct-f-nick')}.value`) === '六哥', 'pre-filled nickname');
          await tapEl(p, '#ctCard-save');
          await waitFor(p, `${$('ctCard-note')}.textContent.includes(${JSON.stringify(s.need)})`);
          check(!got.some((m) => m.t === 'ct_card_answer'), 'nothing sent without an e-mail or phone');
          await evaluate(p, `${$('ct-f-emails-0')}.value='zhaoliu@163.com'`);
          await evaluate(p, `document.querySelector('.ct-multi[data-k=phones] .ct-more').click()`);
          await evaluate(p, `${$('ct-f-phones-1')}.value='13700001234'`);
          await save('contact-card');
          await tapEl(p, '#ctCard-save');
          await sleep(200);
          const a = last('ct_card_answer');
          check(a && a.id === id && a.n === n && a.ok === true && a.contact.emails[0].v === 'zhaoliu@163.com' && a.contact.phones[0].v === '13700001234', 'answer ' + JSON.stringify(a));
          await fake.send({ t: 'ct_card_done', id, result: 'saved' });
          await waitFor(p, `${$('ctCard')}.hidden && ${$('toast')}.textContent.includes(${JSON.stringify(s.saved)})`);
          const id2 = 'cd'.repeat(16);
          await fake.send({ t: 'ct_card', id: id2, n: 'n2', mode: 'update', contact: { phones: [{ v: '13900001111', label: '' }] },
            base: { id: 'c000000000002', name: '李四', nicks: [], emails: [{ v: 'lisi@163.com', label: '' }], phones: [] }, why: '', ttl: 600 });
          await waitFor(p, `!${$('ctCard')}.hidden && ${$('ctCard-title')}.textContent.includes(${JSON.stringify(s.upd)})`);
          check(await evaluate(p, `document.querySelector('#ct-f-phones-0').closest('.ct-field').classList.contains('fresh')`), 'what the Agent added is marked');
          check(await evaluate(p, `${$('ct-f-emails-0')}.value`) === 'lisi@163.com', 'existing values kept');
          await save('contact-update');
          await esc(p); await sleep(200);
          const a2 = last('ct_card_answer');
          check(a2.id === id2 && a2.ok === false && !a2.contact, 'Esc = 不保存');
          await waitFor(p, `${$('ctCard')}.hidden`);
        });
      } catch (e) { console.error('page problems:', p.problems, await evaluate(p, 'document.body.dataset.uiError||""').catch(()=>'')); throw e; } finally { await p.dispose(); }
    }
    console.log(`p184: ${checks} checks passed`);
  } finally { await B.close(); await fake.stop(); await web.stop(); rec.write(); }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await runP184();
