// 0.26.9 #468 (CONTRACT §B3 — §B2's terminal is void; ADR-0269-w-assist / w2-web) — official remote assist, the phone side, on the
// real page against the local fake host (real Noise, the §8 control signatures checked like the host does, every Face ID
// challenge checked), own headless Chromium (never :9222) with a CDP virtual platform authenticator as Face ID's stand-in. No
// customer machine, no support machine, no credentials (the assist key id / seat / tasks are made up).
// Checks:
//   · negative controls: a host without the `assist1` cap → no step in ②; no `assist_state` → no banner; `active:false` or a
//     past `until` → no banner; a task without a grant → not in the feed; `assist:true` terminal frames → nowhere
//   · ② 「请 Agent J 官方远程协助」: the six plain lines (tasks to YOUR Agent · normal rules · risky steps still ask · live ·
//     take back · ends by itself), 15 / 30 / 60 chips with 30 checked by default; step 1 = an unsigned `assist_grant {minutes}`
//     only; the Face ID tap → `assist_grant {minutes, n, ts, sig, fa}` signed (§8, action assist_grant) over {minutes, key_id,
//     seat} with the host's nonce + a real assertion over the host's challenge; a 15 pick is what gets signed (one combo)
//   · while active: the banner on the chat page AND over Settings 「官方远程协助中 · 剩 N 分钟」, countdown follows `until`
//     (30 → 5 → 「不到 1 分钟」), every control ≥ 44 px
//   · 「看实时画面」 = the read-only task feed: `assist_task` entries (words + state queued → running → done / refused), the reply
//     of the task's turn in the normal chat (labelled 「官方远程协助」 there), no xterm, nothing sent from it; Esc closes it
//   · 收回 → signed `assist_revoke` (object {}), the banner goes at once, 「已收回…」, the feed is wiped
//   · host notices: open / refused / expired render; expiry (state inactive) removes the banner
//   · no console errors, no off-site requests, nothing about assist in storage
// Shots (zh / en × light / dark × 390, zh-light / en-dark × 1440) → reports/qa/p0269-assist/ (P0269_ASSIST_OUT overrides).
// AJ_P0269_BEFORE=1 + AJ_P0269_PUBLIC=<base public/> draws the ② card on the base build (before shots, no new-UI checks).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { generateKeyPairSync, randomBytes, webcrypto } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep, rect, key } from './browser.mjs';
import { b64u } from '../../protocol/wire.js';

const subtle = webcrypto.subtle;
const enc = new TextEncoder();
const before = process.env.AJ_P0269_BEFORE === '1';
const OUT = process.env.P0269_ASSIST_OUT || fileURLToPath(new URL('../../../reports/qa/p0269-assist/', import.meta.url));
const hex = (n) => randomBytes(n).toString('hex');
const $ = (id) => `document.getElementById('${id}')`;
const KEY_ID = 'aj-assist-2026a', SEAT = 'seat-p0269';
const W = {
  zh: { tasks: { one: '看一下 Agent J 服务为什么连不上', oneReply: '服务已经重启，现在连上了。', two: '把整个硬盘列出来', early: '先看一下日志', three: '检查一下网络设置' },
    st: { done: '已完成', refused: '已拦下', running: '正在做' }, whyLong: '任务里有不该转交的内容（比如像密码或密钥的文字），被拦下了。', replyWait: '还在做，回复会显示在这里。', src: '官方远程协助',
    banner30: '官方远程协助中 · 剩 30 分钟', banner5: '官方远程协助中 · 剩 5 分钟', last: '官方远程协助中 · 不到 1 分钟', revoked: '已收回远程协助', expired: '协助时间到了', refused: '被拦下了', title: '请 Agent J 官方远程协助' },
  en: { tasks: { one: 'Find out why the Agent J service is down', oneReply: 'The service was restarted and is connected now.', two: 'List the whole disk', early: 'Look at the log first', three: 'Check the network settings' },
    st: { done: 'Done', refused: 'Blocked', running: 'Working' }, whyLong: 'The task contained something that may not be passed on (such as text that looks like a password or key), so it was blocked.', replyWait: 'Still working. The reply shows here.', src: 'Agent J support',
    banner30: 'Agent J support helping · 30 min left', banner5: 'Agent J support helping · 5 min left', last: 'under 1 min left', revoked: 'You took it back', expired: 'Time is up', refused: 'was blocked', title: 'Ask Agent J support to help remotely' },
};

export async function runP0269Assist({ B, web, fake, out = OUT }) {
  mkdirSync(out, { recursive: true });
  let checks = 0;
  const check = (ok, label) => { assert.ok(ok, label); checks++; };
  const PAGE = web.url.replace('127.0.0.1', 'localhost');
  const H = {};
  const resetH = () => Object.assign(H, { fa: null, ch: new Map(), grants: [], step1: [], revokes: [], ins: [], resizes: [], other: [], errors: [], until: 0, active: false });
  resetH();
  const faOk = (fa, ch) => { try { const cd = JSON.parse(Buffer.from(fa.cd, 'base64url').toString()); return cd.type === 'webauthn.get' && cd.challenge === ch && fa.id === H.fa; } catch { return false; } };
  const state = (x = {}) => fake.send({ t: 'assist_state', active: true, until: Math.floor(H.until / 1000), id: 'g-p0269', key_id: KEY_ID, tasks: [], ...x });
  fake.st.onApp = async (c, m, send) => {
    try {
      if (m.t === 'assist_grant' && !m.sig) {
        H.step1.push({ minutes: m.minutes, keys: Object.keys(m).sort().join(',') });
        const n = hex(16), ch = b64u(Buffer.from(await subtle.digest('SHA-256', enc.encode(['p0269-assist', n].join('\n')))));
        H.ch.set(n, { ch, used: false });
        return send({ t: 'ctl_res', r: m.r, action: 'assist_grant', ok: false, why: 'passkey_required', n, fa: H.fa, fa_ch: ch, key_id: KEY_ID, seat: SEAT }), true;
      }
      if (m.t === 'assist_grant') {
        const x = H.ch.get(m.n);
        const sigOk = await fake.verifyControl(c, 'assist_grant', m, { minutes: m.minutes, key_id: KEY_ID, seat: SEAT });
        const fa = !!x && faOk(m.fa, x.ch);
        H.grants.push({ minutes: m.minutes, sigOk, fa, reuse: !!x?.used });
        if (!x || x.used || !sigOk || !fa || ![15, 30, 60].includes(m.minutes)) return send({ t: 'ctl_res', r: m.r, action: 'assist_grant', ok: false, why: 'bad_signature' }), true;
        x.used = true;
        H.until = Date.now() + m.minutes * 60000 - 20000; H.active = true;
        await send({ t: 'ctl_res', r: m.r, action: 'assist_grant', ok: true });
        await state();
        await fake.send({ t: 'assist_notice', what: 'grant', minutes: m.minutes });
        return true;
      }
      if (m.t === 'assist_revoke') {
        const sigOk = await fake.verifyControl(c, 'assist_revoke', m, {});
        H.revokes.push({ sigOk, fa: m.fa !== undefined });
        if (!sigOk) return send({ t: 'ctl_res', r: m.r, action: 'assist_revoke', ok: false, why: 'bad_signature' }), true;
        H.active = false;
        await send({ t: 'ctl_res', r: m.r, action: 'assist_revoke', ok: true });
        await fake.send({ t: 'assist_state', active: false });
        await fake.send({ t: 'assist_notice', what: 'revoked' });
        return true;
      }
      if (m.t === 'term_in' || m.t === 'term_open' || m.t === 'term_line') { H.ins.push(m); return true; }
      if (m.t === 'term_resize') { H.resizes.push(m); return true; }
      if (/^(assist_|term_)/.test(m.t)) H.other.push(m.t);
    } catch (e) { H.errors.push(String(e && e.stack || e)); }
    return false;
  };

  const combos = before ? [['zh', 'light', 390], ['zh', 'dark', 390]]
    : [['zh', 'light', 390], ['zh', 'dark', 390], ['en', 'light', 390], ['en', 'dark', 390], ['zh', 'light', 1440], ['en', 'dark', 1440]];
  for (const [lang, theme, w] of combos) {
    const tag = `${lang}-${theme}-${w}`, narrow = w < 500, X = W[lang];
    fake.reset(); fake.setUp(true); fake.st.prefs = { appearance: { language: lang, theme } };
    fake.st.extraCaps = ['assist1'];
    resetH();
    const h0 = narrow ? 844 : 900;
    const p = await newPage(B, w, h0, theme, { touch: narrow, allow: [web.url, PAGE, fake.relay + '/'] });
    await p.send('Page.addScriptToEvaluateOnNewDocument', { source: `(() => { const W = window.__w = { ls: [], idb: 0 };
      const si = Storage.prototype.setItem; Storage.prototype.setItem = function (k, v) { W.ls.push(String(k)); return si.call(this, k, v); };
      for (const f of ['put', 'add']) { const o = IDBObjectStore.prototype[f]; IDBObjectStore.prototype[f] = function (...a) { W.idb++; return o.apply(this, a); }; } })()` });
    await p.send('WebAuthn.enable', { enableUI: false });
    const { authenticatorId } = await p.send('WebAuthn.addVirtualAuthenticator', { options: { protocol: 'ctap2', ctap2Version: 'ctap2_1', transport: 'internal',
      hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true, hasPrf: false } });
    const save = async (name) => writeFileSync(resolve(out, `${name}-${tag}${before ? '-before' : ''}.png`), await shoot(p, ''));
    const click = async (id) => {
      await evaluate(p, `${$(id)}.scrollIntoView({block:'nearest'})`); await sleep(60);
      const r = await rect(p, '#' + id);
      await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
      await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 });
    };
    const dbg = async () => evaluate(p, `(async()=>{const m=await import('./js/assist.js');return JSON.stringify(m._debug())})()`);
    const shown = (id) => evaluate(p, `(()=>{const e=${$(id)};return !!e&&!e.hidden&&e.getClientRects().length>0})()`);
    const txt = (id) => evaluate(p, `${$(id)}?.textContent ?? null`);
    const toastText = () => evaluate(p, `document.getElementById('toast')?.textContent || ''`);
    const small = (sel) => evaluate(p, `[...document.querySelectorAll(${JSON.stringify(sel)})].filter(b=>b.getClientRects().length).map(b=>{const r=b.getBoundingClientRect();return [b.id||b.className,Math.round(r.width),Math.round(r.height)]}).filter(x=>x[1]<44||x[2]<44)`);
    try {
      await navigate(p, fake.newPairing(PAGE + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `import('./js/settings.js').then(m=>{m.applyThemeLocal('${theme}');return true})`);
      await evaluate(p, `if(!${$('a2hs')}.hidden)${$('a2hs-ok')}.click(); ${$('phone-env-dismiss')}?.click(); true`);
      const credId = randomBytes(16); H.fa = b64u(credId);
      const priv = generateKeyPairSync('ec', { namedCurve: 'P-256' }).privateKey.export({ type: 'pkcs8', format: 'der' }).toString('base64');
      await p.send('WebAuthn.addCredential', { authenticatorId, credential: { credentialId: credId.toString('base64'), isResidentCredential: true,
        rpId: 'localhost', privateKey: priv, userHandle: Buffer.from('p0269-assist-fixture').toString('base64'), signCount: 0 } });
      await sleep(400);

      // ---- negative control: no assist_state → no banner anywhere
      check(!await shown('assist-banner'), 'no assist_state → no banner ' + tag);
      await save('0-chat-none');
      const openRescue = async () => { await evaluate(p, `import('./js/settings.js').then(m=>{m.openRescue();return true})`); await waitFor(p, `!${$('settings')}.hidden`); await sleep(300); };
      await openRescue();
      if (before) {
        await evaluate(p, `${$('rs-help')}.scrollIntoView({block:'start'})`); await sleep(200);
        await save('1-card');
        console.log('PASS p0269-assist (before) ' + tag);
        continue;
      }
      // ---- ② the step
      await waitFor(p, `!!${$('rs-assist-go')}`, 5000).catch(async (e) => { throw new Error(e.message + ' ' + JSON.stringify({ problems: p.problems, dbg: await dbg() })); });
      check(await evaluate(p, `${$('rs-help')}.contains(${$('rs-assist')})`) && (await txt('rs-assist-h')) === X.title, 'the step sits inside ② ' + tag);
      check(H.step1.length === 0, 'nothing sent before the owner asks ' + tag);
      await evaluate(p, `${$('rs-help')}.scrollIntoView({block:'start'})`); await sleep(150);
      await save('1-card');
      await click('rs-assist-go');
      await waitFor(p, `!!${$('rs-assist-yes')} && !${$('rs-assist-yes')}.disabled`, 8000)
        .catch(async (e) => { throw new Error(e.message + ' ' + JSON.stringify({ step1: H.step1, errors: H.errors, problems: p.problems, dbg: await dbg() })); });
      check(H.step1.length === 1 && H.step1[0].minutes === 30 && H.step1[0].keys === 'minutes,r,t', 'step 1 = unsigned {minutes:30} ' + tag + JSON.stringify(H.step1));
      const lines = await evaluate(p, `[...document.querySelectorAll('#rs-assist-list li')].map(l=>l.dataset.k)`);
      check(JSON.stringify(lines) === JSON.stringify(['what', 'rules', 'ask', 'see', 'back', 'long']), 'six plain lines ' + tag + JSON.stringify(lines));
      const chips = await evaluate(p, `[...document.querySelectorAll('#rs-assist-min [role=radio]')].map(c=>[c.dataset.min,c.getAttribute('aria-checked')])`);
      check(JSON.stringify(chips) === JSON.stringify([['15', 'false'], ['30', 'true'], ['60', 'false']]), '15 / 30 / 60, 30 by default ' + tag + JSON.stringify(chips));
      const sm1 = await small('#rs-assist button');
      check(!sm1.length, 'every control in the step ≥ 44 px ' + tag + JSON.stringify(sm1));
      const pick15 = tag === 'en-light-390';
      if (pick15) { await click('rs-assist-min-15'); await sleep(100); check(await evaluate(p, `${$('rs-assist-min-15')}.getAttribute('aria-checked')`) === 'true', 'a chip can be picked ' + tag); }
      await evaluate(p, `${$('rs-assist')}.scrollIntoView({block:'center'})`); await sleep(150);
      await save('2-explain');
      await click('rs-assist-yes');
      await waitFor(p, `document.body.hasAttribute('data-assist')`, 10000)
        .catch(async (e) => { throw new Error(e.message + ' ' + JSON.stringify({ grants: H.grants, errors: H.errors, problems: p.problems, dbg: await dbg() })); });
      const g = H.grants.at(-1);
      const want = pick15 ? 15 : 30;
      check(H.grants.length === 1 && g.minutes === want && g.sigOk && g.fa && !g.reuse, 'the grant is signed over {minutes, key_id, seat} + Face ID ' + tag + JSON.stringify(H.grants));
      // ---- the banner, over Settings and on the chat page
      await sleep(250);
      const top = (id) => evaluate(p, `(()=>{const e=${$(id)};const r=e.getBoundingClientRect();const x=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);return !!x&&e.contains(x)})()`);
      check(await shown('assist-float') && await top('assist-float'), 'banner over Settings (the floating copy, on top) ' + tag);
      const smf = await small('#assist-float button');
      check(!smf.length, 'floating banner controls ≥ 44 px ' + tag + JSON.stringify(smf));
      check(await shown('rs-assist-on') && await shown('rs-assist-revoke'), '② says it is running, with 收回 ' + tag);
      check((await txt('assist-banner-text')) === (want === 30 ? X.banner30 : X.banner30.replace('30', '15')), 'banner countdown ' + tag + ' ' + await txt('assist-banner-text'));
      check((await toastText()).length > 0, 'grant notice shown ' + tag);
      await save('3-granted-settings');
      await evaluate(p, `import('./js/settings.js').then(m=>{m.close();return true})`); await sleep(400);
      check(await shown('assist-banner') && await top('assist-banner') && !await shown('assist-float'), 'banner on the chat page (in the banner stack) ' + tag);
      const hb = await evaluate(p, `(()=>{const h=document.querySelector('header.top,header');const b=${$('assist-banner')}.getBoundingClientRect();return {hb:h?h.getBoundingClientRect().bottom:0,bt:b.top}})()`);
      check(hb.bt >= hb.hb, 'the banner never covers the header ' + tag + JSON.stringify(hb));
      const sm2 = await small('#assist-banner button');
      check(!sm2.length, 'banner controls ≥ 44 px ' + tag + JSON.stringify(sm2));
      const bb = await evaluate(p, `(()=>{const r=${$('assist-banner')}.getBoundingClientRect();return {l:r.left,r:r.right,t:r.top}})()`);
      check(bb.l >= 0 && bb.r <= w && bb.t >= 0, 'banner inside the screen ' + tag + JSON.stringify(bb));
      // countdown follows `until`
      H.until = Date.now() + 4.5 * 60000; await state(); await sleep(200);
      check((await txt('assist-banner-text')) === X.banner5, '4.5 min left → 5 ' + tag + ' ' + await txt('assist-banner-text'));
      await sleep(6400);   // let the grant notice go before the chat-page shot
      await save('4-banner-chat');
      H.until = Date.now() + 50000; await state(); await sleep(200);
      check((await txt('assist-banner-text')).includes(X.last), '< 1 min → 「不到 1 分钟」 ' + tag);
      H.until = Date.now() + 25 * 60000; await state(); await sleep(200);

      // ---- 「看实时画面」 = the read-only task feed
      const T = X.tasks;
      // a first task, later stopped by a take-back on the host side (no grant → no task is the negative control at the end)
      await fake.send({ t: 'assist_task', id: 'tx-early', ts: Date.now(), text: X.tasks.early, state: 'queued' });
      await fake.send({ t: 'term_out', id: 'as1', seq: 1, assist: true, d: 'helper terminal frame' });                 // nothing terminal-shaped
      await sleep(200);
      check(!await evaluate(p, `!!document.getElementById('term') && !document.getElementById('term').hidden`), 'assist terminal frames never open the owner\'s terminal ' + tag);
      check(!await evaluate(p, `document.body.textContent.includes('helper terminal frame')`), 'assist terminal frames are dropped ' + tag);
      await fake.send({ t: 'assist_task', id: 'tx-early', ts: Date.now(), state: 'stopped', why: 'ended' });
      await click('assist-banner-view');
      await waitFor(p, `!!document.getElementById('assist-view') && !document.getElementById('assist-view').hidden`, 5000);
      check(!await shown('assist-banner') && await shown('assist-view'), 'the view replaces the banner ' + tag);
      check(!await evaluate(p, `!!document.querySelector('#assist-view .xterm, #assist-view textarea, #assist-view input')`), 'no terminal, no input in the view ' + tag);
      // the tasks arrive and move; their turns run in the normal chat
      const t1 = Date.now();
      await fake.send({ t: 'assist_task', id: 'tk-1', ts: t1, text: T.one, state: 'queued' });
      const turn1 = await fake.addTurn({ k: 'task', via: 'official_assist', text: T.one, assist: 'tk-1' }, '', 'open');
      await fake.send({ t: 'assist_task', id: 'tk-1', ts: t1, state: 'running' });
      await fake.updateTurn(turn1.id, { reply: { text: T.oneReply.slice(0, 12) } });
      await fake.updateTurn(turn1.id, { reply: { text: T.oneReply }, end: 'done' });
      await fake.send({ t: 'assist_task', id: 'tk-1', ts: t1, state: 'done' });
      await fake.send({ t: 'assist_task', id: 'tk-2', ts: Date.now(), text: T.two, state: 'refused', why: 'admission_filter' });
      await fake.send({ t: 'assist_task', id: 'tk-3', ts: Date.now(), text: T.three, state: 'running' });
      await fake.addTurn({ k: 'official_assist', text: T.three }, '', 'open');
      await fake.send({ t: 'assist_state', active: true, until: Math.floor(H.until / 1000), id: 'g-p0269', key_id: KEY_ID, tasks: [{ id: 'tx-early', state: 'stopped' }, { id: 'tk-1', state: 'done' }, { id: 'tk-2', state: 'refused' }, { id: 'tk-3', state: 'running' }] });
      await waitFor(p, `document.querySelectorAll('#assist-feed .asfeed__item').length===4 && document.querySelector('#assist-feed [data-id="tk-1"] .asfeed__reply')?.textContent===${JSON.stringify(T.oneReply)}`, 6000)
        .catch(async (e) => { throw new Error(e.message + ' ' + JSON.stringify({ problems: p.problems, dbg: await dbg(), html: await evaluate(p, `document.getElementById('assist-feed')?.textContent`) })); });
      const items = await evaluate(p, `[...document.querySelectorAll('#assist-feed .asfeed__item')].map(li=>[li.dataset.id,li.dataset.state,li.querySelector('.asfeed__text').textContent,li.querySelector('.asfeed__st').textContent])`);
      check(JSON.stringify(items.map((x) => [x[0], x[1]])) === JSON.stringify([['tx-early', 'stopped'], ['tk-1', 'done'], ['tk-2', 'refused'], ['tk-3', 'running']]), 'every task, oldest first, with its state ' + tag + JSON.stringify(items));
      check(items[1][2] === T.one && items[1][3] === X.st.done && items[2][3] === X.st.refused && items[3][3] === X.st.running, 'task words + state words ' + tag + JSON.stringify(items));
      check((await evaluate(p, `document.querySelector('#assist-feed [data-id="tk-2"] .asfeed__why')?.textContent`)) === X.whyLong, 'a blocked task says why ' + tag);
      check((await evaluate(p, `document.querySelector('#assist-feed [data-id="tk-3"] .asfeed__reply')?.textContent`)) === X.replyWait, 'a running task: its reply comes here ' + tag);
      check(!await shown('assist-view-wait'), 'tasks → no waiting line ' + tag);
      const sm3 = await small('#assist-view button');
      check(!sm3.length, 'viewer controls ≥ 44 px ' + tag + JSON.stringify(sm3));
      await waitFor(p, `!(document.getElementById('toast')?.textContent||'').trim() || document.getElementById('toast').hidden`, 8000).catch(() => {});
      await save('5-view');
      // typing at the feed (focus off the buttons, or Enter would press 「关闭」) reaches nobody
      const ni = H.ins.length, nr = H.resizes.length, no = H.other.length;
      await evaluate(p, `(()=>{document.activeElement?.blur();document.getElementById('assist-feed').setAttribute('tabindex','-1');document.getElementById('assist-feed').focus();return 1})()`);
      await p.send('Input.insertText', { text: 'rm -rf ~' }); await key(p, 'Enter', { text: '\r' }); await sleep(200);
      check(H.ins.length === ni && H.resizes.length === nr && H.other.length === no, 'the feed sends nothing ' + tag);
      check(await shown('assist-view'), 'the feed stays open ' + tag);
      // Esc closes the view; the banner is back; the task turn carries 「官方远程协助」 in the normal chat
      await key(p, 'Escape'); await sleep(300);
      check(!await shown('assist-view') && await shown('assist-banner'), 'Esc closes the view ' + tag);
      await evaluate(p, `(()=>{for(let i=0;i<8;i++)document.getElementById('pgPrev').click();return 1})()`); await sleep(300);
      for (let i = 0; i < 8 && (await evaluate(p, `document.getElementById('omText').textContent`)) !== T.one; i++) { await evaluate(p, `document.getElementById('pgNext').click()`); await sleep(150); }
      check((await evaluate(p, `document.getElementById('omLabel').textContent`)) === X.src, 'the task\'s turn in the chat: 「官方远程协助」 ' + tag + ' ' + await evaluate(p, `document.getElementById('omLabel').textContent`));
      check((await evaluate(p, `document.getElementById('words').textContent`)).includes(T.oneReply), 'its reply in the chat ' + tag);
      await save('5b-chat-label');
      // a red-line refusal notice
      await fake.send({ t: 'assist_notice', what: 'refused' }); await sleep(250);
      check((await toastText()).includes(X.refused), 'refused notice ' + tag + ' ' + await toastText());
      await save('6-refused');

      // ---- 收回 → signed revoke, banner gone at once
      await sleep(3200);
      await click('assist-banner-revoke');
      await waitFor(p, `!document.body.hasAttribute('data-assist')`, 8000)
        .catch(async (e) => { throw new Error(e.message + ' ' + JSON.stringify({ revokes: H.revokes, errors: H.errors, dbg: await dbg() })); });
      check(H.revokes.length === 1 && H.revokes[0].sigOk && !H.revokes[0].fa, 'revoke = signed over {} (no Face ID) ' + tag + JSON.stringify(H.revokes));
      check(!await shown('assist-banner'), 'banner gone after 收回 ' + tag);
      check((await toastText()).includes(X.revoked), 'revoked notice ' + tag + ' ' + await toastText());
      check(JSON.parse(await dbg()).feed.length === 0, 'the feed is wiped ' + tag);
      await save('7-revoked');

      // ---- expiry: another grant (host-driven), then the host says it is over
      H.until = Date.now() + 10 * 60000; await state(); await sleep(250);
      check(await shown('assist-banner'), 'a host-side grant shows the banner ' + tag);
      await fake.send({ t: 'assist_state', active: false }); await fake.send({ t: 'assist_notice', what: 'expired' }); await sleep(250);
      check(!await shown('assist-banner') && (await toastText()).includes(X.expired), 'expiry: banner gone + notice ' + tag);
      // negative controls on the state itself
      await fake.send({ t: 'assist_state', active: true, until: Math.floor(Date.now() / 1000) - 5 }); await sleep(200);
      check(!await shown('assist-banner'), 'a past until → no banner ' + tag);
      await fake.send({ t: 'assist_state', active: 'yes', until: Math.floor(Date.now() / 1000) + 600 }); await sleep(200);
      check(!await shown('assist-banner'), 'active must be true → no banner ' + tag);
      await fake.send({ t: 'assist_task', id: 'tk-late', ts: Date.now(), text: 'late task', state: 'queued' }); await sleep(150);
      check(JSON.parse(await dbg()).feed.length === 0, 'tasks without an active grant are dropped ' + tag);

      const st = await evaluate(p, `({ls:window.__w.ls.filter(k=>/assist|term|xterm/i.test(k))})`);
      check(!st.ls.length, 'nothing about assist in localStorage ' + tag + JSON.stringify(st));
      check(!H.errors.length, 'fake host saw no malformed message ' + tag + ' ' + H.errors.join(' | '));
      const problems = p.problems.filter((x) => !/WebSocket|ERR_CONNECTION|net::/i.test(x));
      check(!problems.length && !p.offsite.length, 'no console errors / off-site requests ' + tag + ' ' + JSON.stringify([problems.slice(0, 3), p.offsite.slice(0, 3)]));
      console.log('PASS p0269-assist ' + tag);
    } finally { await p.dispose(); }
  }

  // ---- negative control: a host without the cap → no step in ② (one run)
  if (!before) {
    fake.reset(); fake.setUp(true); fake.st.prefs = { appearance: { language: 'zh', theme: 'light' } }; resetH();
    const p = await newPage(B, 390, 844, 'light', { touch: true, allow: [web.url, PAGE, fake.relay + '/'] });
    try {
      await navigate(p, fake.newPairing(PAGE + '?lang=zh')); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `import('./js/settings.js').then(m=>{m.openRescue();return true})`); await sleep(500);
      check(await evaluate(p, `!!${$('rs-help-body')} && !${$('rs-assist')}`), 'no assist1 cap → no 「请 Agent J 官方远程协助」 ');
      check(await evaluate(p, `!${$('assist-banner')} || ${$('assist-banner')}.hidden`), 'no cap, no state → no banner');
    } finally { await p.dispose(); }
  }
  fake.st.onApp = null;
  console.log(`p0269-assist: ${checks} checks; shots → ${out}`);
  return checks;
}
if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split('/').pop())) {
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*', ...(process.env.AJ_P0269_PUBLIC ? { publicDir: process.env.AJ_P0269_PUBLIC } : {}) });
  const fake = await startFakeHost(), B = await launch();
  try { await runP0269Assist({ B, web, fake }); } finally { await B.close(); await fake.stop(); await web.stop(); }
}
