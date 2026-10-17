// P332 (PROTOCOL §33) 紧急修复 on the real page against the local fake host (real Noise, the §8 control signatures checked
// with wire.js like the host does): Settings order (Agent J first, 紧急修复 second) · ③ = the moved AI login entry · ②
// dimmed with 「建议先试第 ① 步」 · ① progress → green · ① fails → ② highlighted · ② live steps + ticket + the helper line ·
// ② success · ≡ 重置连接 and /reset · the offline card's 「紧急修复」 · rescue mode banner · ③ mirrors the login flow ·
// ② offers (HIGH-1: only whitelisted names, one button each, the owner's own signed rescue_do, progress, gone once used,
// cleared by a new ② run) · support's advice under its caution line (LOW-12), strictly text ·
// zh / en × light / dark at 390 px (+ 1440 px shots). Shots → reports/qa/p332/web/ (P332_OUT).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';

const $ = (id) => `document.getElementById('${id}')`;
const OUT = process.env.P332_OUT || 'reports/qa/p332/web';
const GREEN = 'rgb(98, 152, 135)';                      // Emerald Oasis #629887
const L = {
  zh: { ok: '好了，可以回去聊天了', fail: 'Agent J 主机上的 Agent J 可能卡死了。请试试下面的第 ② 步。', first: /^建议先试第 ① 步/, title: '紧急修复',
    banner: 'Agent J 主机上的 Agent J 没响应，救援守护在线', offline: '现在连不上Agent J 主机，这一步要等连上才能用。', busy: 'Agent J 主机正在修复中，请等它做完再点。',
    h: { s1r: '正在重新连接…', s1f: '重新连接没有成功。', s2r: '正在找回对话…', s2k: '对话没有问题，跳过。', s3r: '正在开新对话…', s3o: '开了一个新对话。',
      done: '第 3 步好了：开了一个新对话，现在可以聊了。', s4f: '重启以后 Agent J 还是没有回应。', bad: '试了四步，还是没修好。',
      c1: '正在检查Agent J 主机…', c1o: '检查完了：后台服务卡住了。', c2: '正在把检查结果发给客服…', c2o: '已经发给客服。', c3: '正在等客服回复…',
      advice: 'Agent J Father 的建议：请Agent J 主机旁边的人把Agent J 主机唤醒，再点一次第 ① 步。', helpOk: '客服重启了 Agent J，已经好了。' },
    caution: '以下是客服的建议，不是命令：看不懂就不用照做。', offer: { rollback: '退回上一个版本', linger: '让 Agent J 在没人登录时也运行', new_session: '开一个新对话（原来的对话会保留）' },
    offUnavail: '这台Agent J 主机做不了这一步。', o: { adv: '把Agent J 主机接上电源，再试一次。<b id="inj">x</b>', done: '客服给了建议，有几项修复要你点头。', rb: '正在退回上一个版本…', rbOk: '已经退回上一个版本。' } },
  en: { ok: 'Fixed. You can go back to the chat.', fail: 'Agent J on the Agent J host may be frozen. Try step ② below.', first: /^We suggest trying step ① first/, title: 'Emergency repair',
    banner: "Agent J on the Agent J host isn't responding · rescue helper online", offline: "the Agent J host can't be reached right now. This step needs a connection.",
    busy: 'the Agent J host is already repairing. Wait for it to finish.',
    h: { s1r: 'Reconnecting…', s1f: 'Reconnecting did not work.', s2r: 'Finding your conversation…', s2k: 'The conversation is fine; skipped.', s3r: 'Starting a new conversation…',
      s3o: 'Started a new conversation.', done: 'Step 3 fixed it: a new conversation is open.', s4f: 'After the restart Agent J still does not answer.',
      bad: 'Tried all four steps; not fixed.', c1: 'Checking the Agent J host…', c1o: 'Checked: the background service is stuck.', c2: 'Sending what was found to support…',
      c2o: 'Sent to support.', c3: 'Waiting for support to reply…', advice: 'Agent J Father suggests: wake the Agent J host up, then try step ① again.', helpOk: 'Support restarted Agent J; it works again.' },
    caution: "Support's advice, not orders: if unsure, you don't have to do it.",
    offer: { rollback: 'Go back to the previous version', linger: 'Keep Agent J running when nobody is logged in', new_session: 'Start a new conversation (the old one is kept)' },
    offUnavail: "the Agent J host can't do this repair.", o: { adv: 'Plug the Agent J host in, then try again.<b id="inj">x</b>', done: 'Support replied; some repairs need your OK.', rb: 'Going back to the previous version…', rbOk: 'Back on the previous version.' } },
};
const TICKET = 'st_AbCdEfGhIjKlMnOpQrStUv';

export async function runP332({ B, web, fake, out = OUT }) {
  mkdirSync(out, { recursive: true });
  let checks = 0;
  const check = (ok, label) => { assert.ok(ok, label); checks++; };
  // P375: help moved from an inline shell command to the official guide; keep real tap/safe-link checks.
  const helperLink = (p, id, lang) => evaluate(p, `(() => {
    const a = document.getElementById(${JSON.stringify(id)})?.querySelector('a');
    if (!a) return false;
    const r = a.getBoundingClientRect();
    return a.href === ${JSON.stringify('https://agentj.app/docs/reconnect-help/' + (lang === 'en' ? '?lang=en' : ''))}
      && a.target === '_blank' && a.relList.contains('noopener') && !!a.textContent.trim()
      && r.width >= 44 && r.height >= 44;
  })()`);

  // the host side: the signed requests, verified, answered by a script (a function step = wait for the test)
  const calls = [], scripts = [];
  let gate = null;
  const hold = () => { let open; const p = new Promise((r) => { open = r; }); gate = { p, open }; return () => p; };
  const release = () => { const g = gate; gate = null; g?.open(); };
  fake.st.onApp = async (c, m, send) => {
    if (m.t !== 'reset' && m.t !== 'rescue_do') return false;
    const target = m.t === 'reset' ? { what: 'connection' } : { action: m.action };
    const sigOk = await fake.verifyControl(c, m.t, m, target);
    calls.push({ t: m.t, action: m.action, sigOk, r: m.r });
    const flow = m.t === 'reset' ? 'reset' : m.action === 'help' ? 'help' : m.action;   // §33: flow = the rescue_do action
    if (!sigOk) { await send({ t: 'reset_done', r: m.r, flow, ok: false, why: 'bad_signature' }); return true; }
    const script = scripts.shift() || [{ t: 'reset_done', ok: true, step: 1, text: 'ok' }];
    (async () => { for (const x of script) { if (typeof x === 'function') await x(); else await send({ flow, ...x, r: m.r }); await sleep(40); } })().catch(() => {});
    return true;
  };
  const S = (step, state, text, extra = {}) => ({ t: 'reset_step', step, state, text, ...extra });
  const D = (ok, step, text, extra = {}) => ({ t: 'reset_done', ok, step, text, ...extra });

  const combos = [['zh', 'light', 390], ['zh', 'dark', 390], ['en', 'light', 390], ['en', 'dark', 390], ['zh', 'light', 1440], ['zh', 'dark', 1440]];
  for (const [lang, theme, w] of combos) {
    const s = L[lang], h = s.h, tag = `${lang}-${theme}-${w}`, full = w === 390;
    fake.reset(); fake.setUp(true); calls.length = 0; scripts.length = 0;
    const p = await newPage(B, w, w === 390 ? 844 : 900, theme, { touch: w === 390, allow: [web.url, fake.relay + '/'] });
    const save = async (name) => writeFileSync(resolve(out, `${tag}-${name}.png`), await shoot(p, ''));
    const view = (id) => evaluate(p, `${$(id)}.scrollIntoView({block:'start'}); true`).then(() => sleep(250));
    const st = (id) => evaluate(p, `${$(id)}.dataset.state`);
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `if(!${$('a2hs')}.hidden)${$('a2hs-ok')}.click(); ${$('phone-env-dismiss')}?.click(); true`);
      await evaluate(p, `import('./js/rescue.js').then(m=>{m._reset();return true})`);

      // ---- order: Agent J first, 紧急修复 second; ③ holds the moved AI login entry; idle ② is dimmed
      await evaluate(p, `${$('setBtn')}.click()`); await waitFor(p, `!${$('settings')}.hidden`);
      const order = await evaluate(p, `[...document.querySelectorAll('#set-main .setcard')].filter(c=>!c.hidden).map(c=>c.id)`);
      check(order[0] === 'set-s-agent' && order[1] === 'set-s-rescue', 'Agent J first, 紧急修复 second ' + tag + ' ' + order.slice(0, 3));
      check(await evaluate(p, `${$('set-h-rescue')}.textContent`) === s.title, 'block title ' + tag);
      check(await evaluate(p, `!!${$('set-auth-nav')}.closest('#rs-auth') && !${$('set-s-agent')}.querySelector('#set-auth-nav')`), '③ = the moved AI login entry ' + tag);
      check(await evaluate(p, `[...document.querySelectorAll('#set-s-rescue .rscard')].map(c=>c.id).join()`) === 'rs-reset,rs-help,rs-auth', 'cards ① ② ③ ' + tag);
      check(await evaluate(p, `${$('rs-help')}.hasAttribute('data-dim') && !${$('rs-help')}.hasAttribute('data-hl')`), '② dimmed before ① failed ' + tag);
      if (w >= 900) check(await evaluate(p, `[...document.querySelectorAll('.setside [data-set-jump]')].filter(b=>!b.hidden).map(b=>b.dataset.setJump).slice(0,2).join()`) === 'set-s-agent,set-s-rescue', 'side navigation: 紧急修复 second ' + tag);
      // every pressable thing in the block ≥ 44 px, nothing wider than the screen
      const audit = async (label) => {
        const a = await evaluate(p, `(()=>{const b=document.getElementById('set-s-rescue');const small=[...b.querySelectorAll('button,summary,a')].filter(e=>e.getClientRects().length&&!e.closest('[hidden]')).map(e=>{const r=e.getBoundingClientRect();return {id:e.id||e.className||e.tagName,w:Math.round(r.width),h:Math.round(r.height)}}).filter(x=>x.h<44||x.w<44);return {small,over:b.scrollWidth>b.clientWidth+1||document.getElementById('set-main').scrollWidth>document.getElementById('set-main').clientWidth+1}})()`);
        check(!a.small.length && !a.over, `${label} taps ≥ 44 px, no overflow ${tag} ${JSON.stringify(a)}`);
      };
      await audit('idle');
      await view('set-s-rescue'); await save('1-idle');

      // ---- ② before ①: 「建议先试第 ① 步」, nothing sent; 「先试第 ① 步」 runs ① (held → running shot) → green
      await evaluate(p, `${$('rs-help-go')}.click()`);
      await waitFor(p, `!!${$('rs-help-ask')}`);
      check(s.first.test(await evaluate(p, `${$('rs-help-ask')}.textContent`)), '「建议先试第 ① 步」 ' + tag);
      check(calls.length === 0, 'nothing sent before the owner chose ' + tag);
      scripts.push([S(1, 'run', h.s1r), S(1, 'fail', h.s1f, { detail: 'relay link: closed 1006' }), S(2, 'run', h.s2r), hold(), S(2, 'skip', h.s2k), S(3, 'run', h.s3r), S(3, 'ok', h.s3o),
        D(true, 3, h.done, { old_session: '7f3a9c21' })]);
      await evaluate(p, `${$('rs-help-first')}.click()`);
      await waitFor(p, `${$('rs-reset')}.dataset.state==='run' && ${$('rs-reset-now')}?.textContent===${JSON.stringify(h.s2r)}`);
      check(calls.length === 1 && calls[0].t === 'reset' && calls[0].sigOk, 'signed reset {what:connection} verified ' + tag);
      check(Number(await evaluate(p, `${$('rs-reset-bar')}.getAttribute('aria-valuenow')`)) > 0, 'progress bar ' + tag);
      await view('set-s-rescue'); if (full || theme === 'light') await save('2-reset-running');
      release();
      await waitFor(p, `${$('rs-reset')}.dataset.state==='ok'`);
      await waitFor(p, `getComputedStyle(${$('rs-reset')}).backgroundColor===${JSON.stringify(GREEN)}`, 3000).then(() => true, () => false).then((ok) => check(ok, 'success = the whole card green ' + tag));
      check(await evaluate(p, `${$('rs-reset-body')}.querySelector('.rsok').textContent`) === s.ok, '「好了，可以回去聊天了」 ' + tag);
      check(await evaluate(p, `${$('rs-reset-body')}.textContent.includes(${JSON.stringify(h.done)})`), 'the host sentence ' + tag);
      check(await evaluate(p, `${$('rs-reset-old')}.querySelector('code').textContent`) === 'agentj rescue undo', 'how to get the old conversation back ' + tag);
      check(await evaluate(p, `(()=>{const d=document.querySelector('#rs-reset .rsmore');return !!d && !d.open && d.textContent.includes('relay link: closed 1006')})()`), 'technical detail only in the folded 详情 ' + tag);
      check(await evaluate(p, `!${$('rs-reset-body')}.querySelector(':scope > :not(.rsmore)')?.textContent.match(/relay|session id|serve/i)`), 'no internal words outside 详情 ' + tag);
      check(!(await evaluate(p, `${$('rs-help')}.hasAttribute('data-hl')`)), '② not highlighted after a success ' + tag);
      await view('set-s-rescue'); await save('3-reset-ok');
      await audit('success');

      // back to chat
      await evaluate(p, `${$('rs-reset-chat')}.click()`);
      await waitFor(p, `${$('settings')}.hidden && document.body.dataset.view==='chat'`);

      if (full) {
        // ---- ≡ 重置连接 → Settings at the block, ① runs
        await evaluate(p, `${$('slashBtn')}.click()`); await waitFor(p, `!!${$('resetEntry')} && !${$('menu')}.hidden`);
        check(/\/reset/.test(await evaluate(p, `${$('resetEntry')}.textContent`)), '≡ has 重置连接 ' + tag);
        scripts.push([S(1, 'run', h.s1r), S(1, 'ok', h.s1r), D(true, 1, h.done)]);
        await evaluate(p, `${$('resetEntry')}.click()`);
        await waitFor(p, `!${$('settings')}.hidden && ${$('rs-reset')}.dataset.state==='ok'`);
        check(calls.length === 2 && calls[1].sigOk, '≡ → a signed reset ' + tag);
        check(await evaluate(p, `(()=>{const r=document.getElementById('set-s-rescue').getBoundingClientRect();return r.top>=0&&r.top<260})()`), 'Settings opened at the block ' + tag);
        await evaluate(p, `import('./js/settings.js').then(m=>{m.close();return true})`);
        // ---- /reset autocompletes, and sent = ①
        await evaluate(p, `${$('input')}.focus(); ${$('input')}.value='/res'; ${$('input')}.dispatchEvent(new Event('input'))`);
        await waitFor(p, `!${$('sug')}.hidden && [...${$('sug')}.querySelectorAll('[data-sug]')].some(b=>/^\\/reset/.test(b.textContent))`);
        scripts.push([S(1, 'run', h.s1r), D(true, 1, h.done)]);
        await evaluate(p, `${$('input')}.value='/reset'; ${$('input')}.dispatchEvent(new Event('input')); ${$('send')}.click()`);
        await waitFor(p, `!${$('settings')}.hidden && ${$('rs-reset')}.dataset.state==='ok'`);
        check(calls.length === 3 && calls[2].t === 'reset' && calls[2].sigOk, '/reset → a signed reset ' + tag);
        check(await evaluate(p, `${$('input')}.value.trim()===''`), '/reset is not sent to the Agent as text ' + tag);
        check(!fake.st.says.some((x) => /\/reset/.test(x.text || '')), 'no say carried /reset ' + tag);
      }

      // ---- ① fails → 「可能卡死了」, ② highlighted (no longer dimmed)
      await evaluate(p, `import('./js/settings.js').then(m=>{m.openRescue();return true})`);
      await waitFor(p, `!${$('settings')}.hidden`);
      scripts.push([S(1, 'run', h.s1r), S(1, 'fail', h.s1f), S(2, 'run', h.s2r), S(2, 'skip', h.s2k), S(3, 'run', h.s3r), S(3, 'fail', h.s4f), S(4, 'run', L[lang].h.s4f), S(4, 'fail', h.s4f, { detail: 'serve did not answer status' }), D(false, 4, h.bad)]);
      await evaluate(p, `${$('rs-reset-go')}.click()`);
      await waitFor(p, `${$('rs-reset')}.dataset.state==='fail'`);
      check(await evaluate(p, `${$('rs-reset-msg')}.textContent`) === s.fail, '① failed: 「可能卡死了，试试第 ② 步」 ' + tag);
      check(await evaluate(p, `${$('rs-help')}.hasAttribute('data-hl') && !${$('rs-help')}.hasAttribute('data-dim')`), '② highlighted ' + tag);
      check(await evaluate(p, `${$('rs-help-go')}.classList.contains('aj-btn--primary')`), '② is now the primary action ' + tag);
      await view('set-s-rescue'); await save('4-reset-fail');
      await audit('fail');

      // ---- ② runs (no 「先试 ①」 now): live steps; the end = support's advice + ticket + the helper line
      scripts.push([S(1, 'run', h.c1), S(1, 'ok', h.c1o), S(2, 'run', h.c2), S(2, 'ok', h.c2o), S(3, 'run', h.c3), hold(), S(3, 'ok', h.advice), D(false, 3, h.advice, { ticket: TICKET, category: 'sleep' })]);
      await evaluate(p, `${$('rs-help-go')}.click()`);
      await waitFor(p, `${$('rs-help')}.dataset.state==='run' && ${$('rs-help-steps')}?.children.length===3`);
      check(calls.at(-1).t === 'rescue_do' && calls.at(-1).action === 'help' && calls.at(-1).sigOk, 'signed rescue_do help verified ' + tag);
      check(await evaluate(p, `[...${$('rs-help-steps')}.children].map(li=>li.dataset.state).join()`) === 'ok,ok,run', 'live list of what support does ' + tag);
      await view('rs-help'); await save('5-help-running');
      release();
      await waitFor(p, `${$('rs-help')}.dataset.state==='fail'`);
      check(await evaluate(p, `${$('rs-help-ticket')}.querySelector('code').textContent`) === TICKET, 'ticket shown, copyable code ' + tag);
      check(await evaluate(p, `!!${$('rs-help-ticket')}.querySelector('code[data-cc]')`), 'ticket copy wired ' + tag);
      check(await evaluate(p, `${$('rs-help-body')}.textContent.includes(${JSON.stringify(h.advice)})`), "support's advice shown " + tag);
      check(await evaluate(p, `(()=>{const b=document.querySelector('#rs-help-steps .rsadvbox');return !!b && b.firstElementChild.className==='rscaution' && b.firstElementChild.textContent===${JSON.stringify(s.caution)} && b.lastElementChild.textContent===${JSON.stringify(h.advice)}})()`), 'caution line above the advice ' + tag);
      check(await evaluate(p, `getComputedStyle(document.querySelector('#rs-help-steps .rscaution')).color!==getComputedStyle(document.querySelector('#rs-help-steps .rsadvtext')).color`), 'caution line muted ' + tag);
      check(!(await evaluate(p, `!!${$('rs-offer')}`)), 'no offers → no offer buttons ' + tag);
      check(await helperLink(p, 'rs-help-helper', lang), 'official help link, safe new tab and 44 px target ' + tag);
      await view('rs-help'); if (full) await save('6-help-fail');

      // ---- ② with offers (HIGH-1): rollback / linger / new_session only on the owner's tap; unknown names ignored
      {
        const o = s.o, adv = (lang === 'zh' ? 'Agent J Father 的建议：' : 'Agent J Father suggests: ') + o.adv;
        scripts.push([S(1, 'run', h.c1), S(1, 'ok', h.c1o), S(3, 'ok', o.adv, { advice: true, offer: ['rollback', 'reboot', 'new_session', 'rollback'] }),
          S(3, 'ok', adv, { offer: ['rm -rf ~', 42] }), hold(), D(false, 3, o.done, { ticket: TICKET, offer: ['linger', 'restart', 'rollback', '__proto__'] })]);
        const before = calls.length;
        await evaluate(p, `${$('rs-help-go')}.click()`);
        await waitFor(p, `${$('rs-help')}.dataset.state==='run' && ${$('rs-help-steps')}?.children.length===3`);
        check(!(await evaluate(p, `!!${$('rs-offer')}`)), 'offers wait until ② has ended ' + tag);
        release();
        await waitFor(p, `${$('rs-help')}.dataset.state==='fail' && !!${$('rs-offer')}`);
        check(calls.length === before + 1 && calls.at(-1).action === 'help', 'nothing auto-sent for the offers ' + tag);
        const names = await evaluate(p, `[...${$('rs-offer')}.querySelectorAll('button[data-offer]')].map(b=>b.dataset.offer).join()`);
        check(names === 'rollback,new_session,linger', 'one button per whitelisted offer, unknown names ignored ' + tag + ' ' + names);
        for (const [k, v] of Object.entries(s.offer)) check(await evaluate(p, `${$('rs-offer-' + k)}.textContent`) === v, 'offer label ' + k + ' ' + tag);
        check(await evaluate(p, `[...${$('rs-offer')}.querySelectorAll('.rsoffitem')].every(i=>i.querySelector('.rsnote')?.textContent.length>8)`), 'one-line explanation per offer ' + tag);
        check(await evaluate(p, `document.querySelectorAll('#rs-help-steps .rsadvbox').length===2 && [...document.querySelectorAll('#rs-help-steps .rsadvbox .rscaution')].every(c=>c.textContent===${JSON.stringify(s.caution)})`), 'caution above both advice rows (marker + prefix) ' + tag);
        check(await evaluate(p, `!document.getElementById('inj') && ${$('rs-help-steps')}.textContent.includes('<b id="inj">x</b>')`), "support's text rendered strictly as text " + tag);
        await audit('offers');
        check(await evaluate(p, `!${$('rs-help-body')}.querySelector(':scope > .rsadvbox')`), 'a final text that is not support\'s advice carries no caution ' + tag);
        if (full) { await evaluate(p, `${$('rs-offer')}.scrollIntoView({block:'end'}); true`); await sleep(250); await save('11-help-offers'); }
        // tap rollback → the owner's own signed rescue_do rollback; its progress in the card; the button is gone
        scripts.push([S(1, 'run', o.rb), hold(), S(1, 'ok', o.rbOk), D(true, 1, o.rbOk)]);
        await evaluate(p, `${$('rs-offer-rollback')}.click()`);
        await waitFor(p, `${$('rs-help')}.dataset.state==='run' && !!${$('rs-offer-bar')} && ${$('rs-offer-now')}.textContent===${JSON.stringify(o.rb)}`);
        check(calls.length === before + 2 && calls.at(-1).t === 'rescue_do' && calls.at(-1).action === 'rollback' && calls.at(-1).sigOk, 'tap → signed rescue_do rollback verified ' + tag);
        check(await evaluate(p, `!${$('rs-offer-rollback')} && ${$('rs-offer-linger')}.disabled && ${$('rs-offer-new_session')}.disabled`), 'used button gone, the others wait ' + tag);
        if (full) { await view('rs-help'); await save('12-offer-running'); }
        release();
        await waitFor(p, `${$('rs-help')}.dataset.state==='ok'`);
        check(await evaluate(p, `${$('rs-offer-res')}.querySelector('.rsok').textContent`) === s.ok && await evaluate(p, `${$('rs-offer-steps')}.textContent.includes(${JSON.stringify(o.rbOk)})`), 'offer result shown ' + tag);
        check(await evaluate(p, `!${$('rs-offer-rollback')} && !${$('rs-offer-linger')}.disabled`), 'rollback stays gone, the others are open again ' + tag);
        // linger: the Agent J host cannot → plain words; that button is gone as well
        scripts.push([D(false, 0, '', { why: 'unavailable' })]);
        await evaluate(p, `${$('rs-offer-linger')}.click()`);
        await waitFor(p, `${$('rs-help')}.dataset.state==='fail' && !!${$('rs-offer-msg')}`);
        check(calls.at(-1).action === 'linger' && calls.at(-1).sigOk, 'signed rescue_do linger ' + tag);
        check(await evaluate(p, `${$('rs-offer-msg')}.textContent`) === s.offUnavail && !(await evaluate(p, `!!${$('rs-offer-linger')}`)), 'unavailable → plain words, button gone ' + tag);
      }

      // ---- ② success: green like ①
      scripts.push([S(1, 'run', h.c1), S(3, 'ok', h.helpOk), D(true, 3, h.helpOk, { ticket: TICKET })]);
      await evaluate(p, `${$('rs-help-go')}.click()`);
      await waitFor(p, `${$('rs-help')}.dataset.state==='ok'`);
      await waitFor(p, `getComputedStyle(${$('rs-help')}).backgroundColor===${JSON.stringify(GREEN)}`, 3000).then(() => true, () => false).then((ok) => check(ok, '② success green ' + tag));
      if (full && lang === 'zh') { await view('rs-help'); await save('7-help-ok'); }
      check(!(await evaluate(p, `!!${$('rs-offer')} || !!${$('rs-offer-new_session')}`)), 'a new ② run clears the offers ' + tag);

      // ---- a refusal from §33 gets a plain sentence
      scripts.push([D(false, 0, '', { why: 'busy' })]);
      await evaluate(p, `${$('rs-reset-go')}.click()`);
      await waitFor(p, `${$('rs-reset')}.dataset.state==='fail'`);
      check(await evaluate(p, `${$('rs-reset-msg')}.textContent`) === s.busy, 'busy → plain words ' + tag);

      // ---- ③ follows the moved login flow: running → green
      const now = Math.floor(Date.now() / 1000);
      await fake.send({ t: 'auth_rescue', view: { dot: true, harnesses: [{ kind: 'claude', label: 'Claude Code', installed: true, current: true, state: 'login', routes: ['setup_token'] }],
        flow: { kind: 'claude', route: 'setup_token', stage: 'url', url: 'https://claude.ai/oauth/authorize', expires_at: now + 600, round: 1, rounds: 3 } } });
      await waitFor(p, `${$('rs-auth')}.dataset.state==='run' && !!${$('rs-auth-bar')}`);
      check(await evaluate(p, `!${$('ar-dot')}.hidden`), 'the red dot moved with the entry ' + tag);
      await fake.send({ t: 'auth_rescue', view: { dot: false, harnesses: [{ kind: 'claude', label: 'Claude Code', installed: true, current: true, state: 'ok', routes: ['setup_token'] }],
        flow: { kind: 'claude', route: 'setup_token', stage: 'done' } } });
      await waitFor(p, `${$('rs-auth')}.dataset.state==='ok'`);
      await waitFor(p, `getComputedStyle(${$('rs-auth')}).backgroundColor===${JSON.stringify(GREEN)}`, 3000).then(() => true, () => false).then((ok) => check(ok, '③ success green ' + tag));
      if (full && lang === 'zh') { await view('rs-auth'); await save('8-auth-ok'); }
      await fake.send({ t: 'auth_rescue', view: { dot: false, harnesses: [], flow: null } });
      await evaluate(p, `${$('set-auth-nav')}.click()`); await waitFor(p, `!${$('set-auth')}.hidden`);
      check(true, 'the P258 page still opens from ③ ' + tag);
      await evaluate(p, `import('./js/settings.js').then(m=>{m.close();return true})`);

      if (full) {
        // ---- offline: the card's 「紧急修复」 + the helper line; it opens the block; ① says what to do
        fake.setUp(false);
        await waitState(p, 'waiting-host');
        await waitFor(p, `!${$('offDlg')}.hidden`, 12000);
        check(await evaluate(p, `!${$('offRescue')}.hidden && ${$('offRescue')}.getBoundingClientRect().height>=44`), 'offline card: 紧急修复 button ' + tag);
        check(await evaluate(p, `!${$('offDlg')}.textContent.includes('curl ') && ${$('offTip2')}.textContent===${JSON.stringify(lang === 'zh' ? '检查遥控器网络，或点「立即重试」。' : 'Check this remote’s network, or tap Try now.')}`), 'offline card: network/retry guidance before technical help ' + tag);
        await save('9-offline-card');
        await evaluate(p, `${$('offRescue')}.click()`);
        await waitFor(p, `${$('offDlg')}.hidden && !${$('settings')}.hidden`);
        check(await evaluate(p, `(()=>{const r=document.getElementById('set-s-rescue').getBoundingClientRect();return r.top>=0&&r.top<260})()`), 'offline → Settings at the block ' + tag);
        await evaluate(p, `${$('rs-reset-go')}.click()`);
        await waitFor(p, `${$('rs-reset')}.dataset.state==='fail'`);
        check(await evaluate(p, `${$('rs-reset-msg')}.textContent`) === s.offline, 'offline: plain reason ' + tag);
        check(await helperLink(p, 'rs-reset-helper', lang), 'official help link, safe new tab and 44 px target ' + tag);
        await evaluate(p, `import('./js/settings.js').then(m=>{m.close();return true})`);
        fake.setUp(true);
        await waitState(p, 'ready', 20000);

        // ---- rescue mode: ready {mode:"rescue"} + rescue {on} → one banner → the block (with its note)
        fake.st.readyMode = 'rescue';
        fake.kick();
        await waitState(p, 'ready', 20000);
        await fake.send({ t: 'rescue', on: true, why: 'serve_down', since: Date.now() - 60000, actions: ['reset', 'help'] });
        await waitFor(p, `!${$('rescue-banner')}.hidden`);
        check(await evaluate(p, `${$('rescue-banner-text')}.textContent`) === s.banner, 'rescue banner text ' + tag);
        check(await evaluate(p, `${$('rescue-banner-go')}.getBoundingClientRect().height>=44`), 'banner button ≥ 44 px ' + tag);
        await save('10-rescue-banner');
        await evaluate(p, `${$('rescue-banner-go')}.click()`);
        await waitFor(p, `!${$('settings')}.hidden && !${$('rs-mode')}.hidden`);
        await evaluate(p, `import('./js/settings.js').then(m=>{m.close();return true})`);
        fake.st.readyMode = null;
        fake.kick();
        await waitState(p, 'ready', 20000);
        await waitFor(p, `${$('rescue-banner')}.hidden`);
        check(true, 'back to a normal Agent J: no banner ' + tag);
      }
      const problems = p.problems.filter((x) => !/WebSocket|ERR_CONNECTION|net::/i.test(x));
      check(!problems.length && !p.offsite.length, 'no console errors / off-site requests ' + tag + ' ' + JSON.stringify([problems.slice(0, 3), p.offsite.slice(0, 3)]));
      console.log('PASS P332 ' + tag);
    } finally { release(); await p.dispose(); }
  }
  fake.st.onApp = null;
  console.log(`P332: ${checks} checks; shots → ${out}`);
  return checks;
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' }), fake = await startFakeHost(), B = await launch();
  try { await runP332({ B, web, fake }); } finally { await B.close(); await fake.stop(); await web.stop(); }
}
