#!/usr/bin/env node
// P547 phone gate (p44 `p547-phone`): REAL web client in headless Chromium, synthetic Noise host (fakehost.mjs) speaking the
// P547 messages, a CDP virtual platform authenticator standing in for Face ID. Walks:
//   requester  「设置 Face ID」 → pk_peer_req → waiting sheet with the 6-digit code (online 0 / n) → 「取消」 = pk_peer_cancel ·
//              done ok + pk_offer{peer} → 「已批准 · 点「保存」」 → tap → create() only (no get(): no old-key check, no
//              cross-device QR) → pk_reg without replace_fa · done denied → the plain refusal
//   approver   pk_peer_ask → card (name, time, code) → 「批准（Face ID）」 → get() in the tap → pk_peer_answer {ok, fa} ·
//              「拒绝」 → {ok:false} · pk_peer_close → the card goes
//   seat       Settings → 「转移席位到另一台 Agent J 主机」 → what happens → no key: passkey_needed → 「在本机设置 Face ID（需另一台
//              批准）」 starts pk_peer_req · with a key: Face ID → the prompt masked, 「复制」 = the full prompt, three steps,
//              「撤销」 = signed seat_transfer_cancel · the row's state line + 「撤销」 from seat_xfer_state
//   host_moved a bad one is ignored · a good one → first the sheet 「席位已转到新主机「名」（HH:MM）。让这台遥控器切过去吗？」:
//              「留在这里」 changes nothing (and the same move is not asked again on this page; a reload asks again) ·
//              「切过去」 → the stored pairing points at the new host and the page is ready there ·
//              an unreachable one (after 「切过去」) → 「扫码配对新主机」 → the pairing screen
// Shots (masked code only, no address bar) → reports/qa/p547/shots/. Usage: node web/test/p547.mjs
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { randomBytes } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';

const OUT = process.env.P547_OUT || fileURLToPath(new URL('../../../reports/qa/p547/shots/', import.meta.url));
mkdirSync(OUT, { recursive: true });
const b64u = (b) => Buffer.from(b).toString('base64url');
const checks = [];
const ok = (cond, msg) => { assert.ok(cond, msg); checks.push(msg); };

const web = await startWebServer();
web.url = web.url.replace('127.0.0.1', 'localhost');     // WebAuthn wants a host name, not an IP
const fake = await startFakeHost(), fake2 = await startFakeHost(), B = await launch();

function host(f) {
  const H = { reqs: [], cancels: [], answers: [], regs: [], seat: [], online: 0, peerRes: null, state: 'none', noKey: false, credId: null, token: null };
  f.st.onApp = async (c, m, reply) => {
    if (m.t === 'pk_peer_req') {
      H.reqs.push(m);
      const id = b64u(randomBytes(16));
      H.lastId = id;
      await reply(H.peerRes ? { t: 'pk_peer_res', r: m.r, ...H.peerRes } : { t: 'pk_peer_res', r: m.r, ok: true, mode: 'peer', id, code: '482913', exp: Math.floor(Date.now() / 1000) + 300, online: H.online });
      return true;
    }
    if (m.t === 'pk_peer_cancel') { H.cancels.push(m); return true; }
    if (m.t === 'pk_peer_answer') { H.answers.push(m); await reply({ t: 'pk_peer_answer_res', id: m.id, ok: true }); return true; }
    if (m.t === 'pk_reg') { H.regs.push(m); H.credId = m.id; await reply({ t: 'pk_reg_res', ok: true }); return true; }
    if (m.t === 'seat_xfer_state') { await reply({ t: 'seat_xfer_state', r: m.r, state: H.state, expires_at: H.state === 'issued' ? Math.floor(Date.now() / 1000) + 86400 : null }); return true; }
    if (m.t === 'seat_transfer' || m.t === 'seat_transfer_cancel') {
      const sigOk = await f.verifyControl(c, m.t, m, {});
      H.seat.push({ t: m.t, sigOk, fa: !!m.fa });
      const res = (o) => reply({ t: 'ctl_res', r: m.r, action: m.t, ...o });
      if (!sigOk) return res({ ok: false, why: 'bad_signature' }), true;
      if (m.t === 'seat_transfer_cancel') { H.state = 'cancelled'; await res({ ok: true, status: 'cancelled' }); return true; }
      if (H.noKey) { await res({ ok: false, why: 'passkey_needed' }); return true; }
      if (!m.fa) { await res({ ok: false, why: 'passkey_required', fa: H.credId, fa_ch: b64u(randomBytes(32)) }); return true; }
      H.token = 'ajt_' + b64u(randomBytes(32));
      H.state = 'issued';
      const exp = Math.floor(Date.now() / 1000) + 86400;
      await res({ ok: true, token: H.token, expires_at: exp,
        prompt_zh: `请帮我在这台电脑上安装 Agent J，并把它绑定到我的席位。\n1. 打开 agentj.app 的安装说明，照着装好。\n2. 运行：agentj seat bind ${H.token}\n转移码 24 小时内有效，只能用一次。`,
        prompt_en: `Please install Agent J on this computer and bind it to my seat.\n1. Follow the install guide on agentj.app.\n2. Run: agentj seat bind ${H.token}\nThe transfer code is valid for 24 hours and works once.` });
      return true;
    }
    return false;
  };
  return H;
}

const visible = (sel) => `(() => { const e = document.querySelector(${JSON.stringify(sel)}); return !!e && !e.hidden && !e.closest('[hidden]') && e.getClientRects().length > 0; })()`;
const txt = (sel) => `(document.querySelector(${JSON.stringify(sel)})?.textContent || '')`;
const click = (p, sel) => evaluate(p, `document.querySelector(${JSON.stringify(sel)}).click()`);
async function tapSel(p, sel) {                            // a real pointer tap: WebAuthn wants user activation from it
  const r = await evaluate(p, `(() => { const e = document.querySelector(${JSON.stringify(sel)}); e.scrollIntoView({ block: 'center' }); const b = e.getBoundingClientRect(); return { x: b.x + b.width / 2, y: b.y + b.height / 2 }; })()`);
  await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
  await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 });
}
const tr = (p, key, vars) => evaluate(p, `import('./js/t.js').then(({ t }) => t(${JSON.stringify(key)}, ${JSON.stringify(vars || null)} || undefined))`);
const rec = (p) => evaluate(p, `import('./js/store.js').then(async (s) => { const h = await s.dbGet('host'); return h && { channel: h.channel, relay: h.relay, pk: h.pk, pkAsked: h.pkAsked, approved: h.approved, dk: h.dk, hp: new Uint8Array(h.hostPub).length }; })`);
// keep: the notice this step is about (host_moved) · otherwise an earlier step's passing notice is put away first
async function snap(p, tag, name, keep = false) {
  if (!keep) await evaluate(p, `import('./js/ui.js').then((m) => m.toastOff())`);
  writeFileSync(`${OUT}${tag}-${name}.png`, await shoot(p, ''));
}
async function noToken(p, H) { if (H.token) assert.ok(!(await evaluate(p, `document.body.innerText.includes(${JSON.stringify(H.token)})`)), 'no code on screen before a shot'); }

async function phone(width, scheme, lang) {
  const p = await newPage(B, width, width < 500 ? 844 : 900, scheme, { touch: width < 500, ua: width < 500 ? 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1' : undefined,
    allow: [web.url, fake.relay + '/', fake2.relay + '/', 'ws://127.0.0.1:1/'] });
  await navigate(p, fake.newPairing(web.url + '?lang=' + lang));
  await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready'); await sleep(400);
  await p.send('WebAuthn.enable', { enableUI: false });
  await p.send('WebAuthn.addVirtualAuthenticator', { options: { protocol: 'ctap2', transport: 'internal', hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true } });
  await evaluate(p, `(() => { PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable = async () => true; window.__cred = [];
    const c = navigator.credentials, g = c.get.bind(c), cr = c.create.bind(c);
    c.get = (o) => { window.__cred.push('get'); return g(o); }; c.create = (o) => { window.__cred.push('create'); return cr(o); }; return true; })()`);
  return p;
}

async function requester(p, H, tag, full) {
  // waiting sheet, nobody online
  H.online = 0;
  await click(p, '#setup-faceid');
  await waitFor(p, `${visible('#pkp')} && document.getElementById('pkp').dataset.mode === 'wait'`);
  ok((await evaluate(p, txt('#pkp-code'))).includes('482913'), tag + ' waiting sheet shows the 6-digit code');
  ok((await evaluate(p, txt('#pkp'))).includes(await tr(p, 'pkp.noneOnline')), tag + ' online 0 → open the page on that device');
  ok((await evaluate(p, txt('#pkp'))).includes(await tr(p, 'pkp.eachOwn')), tag + ' each device has its own key');
  ok(H.reqs.length === 1, tag + ' 「设置 Face ID」 sends pk_peer_req (not pk_offer_req)');
  await snap(p, tag, '1-requester-wait');
  await click(p, '#pkp-cancel');
  await waitFor(p, `!${visible('#pkp')}`);
  await waitFor(p, 'true'); await sleep(150);
  ok(H.cancels.length === 1 && H.cancels[0].id === H.lastId, tag + ' 「取消」 = pk_peer_cancel {id}');
  // approved → peer offer → 保存 (create only)
  H.online = 2;
  await click(p, '#setup-faceid');
  await waitFor(p, `${visible('#pkp')} && document.getElementById('pkp').dataset.mode === 'wait'`);
  ok((await evaluate(p, txt('#pkp'))).includes(await tr(p, 'pkp.online', { n: 2 })), tag + ' online n → sent to n devices');
  if (full) await snap(p, tag, '1b-requester-wait-online');
  await fake.send({ t: 'pk_peer_done', id: H.lastId, ok: true, by: 'iPhone Safari' });
  await fake.send({ t: 'pk_offer', n: b64u(randomBytes(32)), peer: H.lastId });
  await waitFor(p, `document.getElementById('pkp').dataset.mode === 'approved' && ${visible('#pkp-save')}`);
  ok((await evaluate(p, txt('#pkp-title'))) === await tr(p, 'pkp.approvedTitle'), tag + ' 「已批准 · 点「保存」在本机设置」');
  await snap(p, tag, '2-requester-approved');
  await evaluate(p, 'window.__cred.length = 0');
  await tapSel(p, '#pkp-save');
  await waitFor(p, `window.__cred.length > 0`);
  for (let i = 0; i < 100 && !H.regs.length; i++) await sleep(50);
  ok(H.regs.length === 1 && !('replace_fa' in H.regs[0]), tag + ' pk_reg after the peer approval, no replace_fa');
  ok(JSON.stringify(await evaluate(p, 'window.__cred')) === '["create"]', tag + ' 「保存」 → create() only: no get() on the old key, no cross-device QR');
  // denied
  await click(p, '#setup-faceid');
  await waitFor(p, `document.getElementById('pkp').dataset.mode === 'wait'`);
  await fake.send({ t: 'pk_peer_done', id: H.lastId, ok: false, why: 'denied' });
  await waitFor(p, `document.getElementById('pkp').dataset.mode === 'result'`);
  ok((await evaluate(p, txt('#pkp'))).includes(await tr(p, 'pkp.r.denied')), tag + ' a declined request says so in plain words');
  if (full) await snap(p, tag, '3-requester-denied');
  await click(p, '#pkp-ok');
  await waitFor(p, `!${visible('#pkp')}`);
}

async function approver(p, H, tag, full) {
  const now = Math.floor(Date.now() / 1000);
  const ask = (id, code) => ({ t: 'pk_peer_ask', id, device: 'D2', name: 'Mac Chrome', at: now, exp: now + 300, code, fa: H.credId, fa_ch: b64u(randomBytes(32)) });
  const id1 = b64u(randomBytes(16));
  await fake.send(ask(id1, '715204'));
  await waitFor(p, `document.getElementById('pkp')?.dataset.mode === 'ask' && ${visible('#pkp-approve')}`);
  ok((await evaluate(p, txt('#pkp-title'))) === await tr(p, 'pkp.askTitle', { name: 'Mac Chrome' }), tag + ' card 「「Mac Chrome」请求在本机设置 Face ID」');
  ok((await evaluate(p, txt('#pkp-code'))).includes('715204') && (await evaluate(p, txt('#pkp'))).includes(await tr(p, 'pkp.askAt', { time: '' }).then((s) => s.trim())), tag + ' card shows the code and the time');
  await snap(p, tag, '4-approver-card');
  await evaluate(p, 'window.__cred.length = 0');
  await tapSel(p, '#pkp-approve');
  for (let i = 0; i < 100 && !H.answers.length; i++) await sleep(50);
  const a = H.answers[0];
  ok(a && a.id === id1 && a.ok === true && a.fa && a.fa.id === H.credId && a.fa.sig, tag + ' 「批准（Face ID）」 → pk_peer_answer {ok:true, fa: this remote\'s own key}');
  ok(JSON.stringify(await evaluate(p, 'window.__cred')) === '["get"]', tag + ' the assertion ran from the tap');
  await waitFor(p, `!${visible('#pkp')}`);
  const id2 = b64u(randomBytes(16));
  await fake.send(ask(id2, '220011'));
  await waitFor(p, `${visible('#pkp-deny')}`);
  await click(p, '#pkp-deny');
  for (let i = 0; i < 100 && H.answers.length < 2; i++) await sleep(50);
  ok(H.answers[1].id === id2 && H.answers[1].ok === false && !H.answers[1].fa, tag + ' 「拒绝」 → {ok:false} without Face ID');
  const id3 = b64u(randomBytes(16));
  await fake.send(ask(id3, '330022'));
  await waitFor(p, `${visible('#pkp-approve')}`);
  await fake.send({ t: 'pk_peer_close', id: id3 });
  await waitFor(p, `!${visible('#pkp')}`);
  ok(true, tag + ' pk_peer_close puts the card away');
  if (full) checks.push(tag + ' approver walk complete');
}

// a card that needs Face ID on a remote without a key (the F32 pickup card): the button says another remote approves
async function card(p, H, tag) {
  const id = randomBytes(16).toString('hex');
  await fake.send({ t: 'secret_out', id, n: randomBytes(16).toString('hex'), kind: 'text', name: 'PROXY_LINK', purpose: 'p547', size: 42, ttl: 300 });
  await waitFor(p, visible('#sout-setup'));
  ok((await evaluate(p, txt('#sout-setup'))) === await tr(p, 'pkp.setupHere'), tag + ' pickup card without a key: 「在本机设置 Face ID（需另一台批准）」');
  ok(await evaluate(p, `(() => { const b = document.getElementById('sout-setup').getBoundingClientRect(), c = document.querySelector('#sout .box').getBoundingClientRect(); return b.left >= c.left && b.right <= c.right + 0.5; })()`), tag + ' the long label stays inside the card');
  await snap(p, tag, '4b-card-setup-here');
  const reqs = H.reqs.length;
  await click(p, '#sout-setup');
  await waitFor(p, `document.getElementById('pkp')?.dataset.mode === 'wait' && ${visible('#pkp')}`);
  ok(H.reqs.length === reqs + 1, tag + ' the card button starts pk_peer_req');
  await click(p, '#pkp-cancel');
  await click(p, '#sout-decline');
  await sleep(200);
}
async function seat(p, H, tag, full) {
  H.state = 'none';
  await evaluate(p, `import('./js/settings.js').then((m) => m.open())`);
  await waitFor(p, visible('#set-seat-xfer'));
  ok(!(await evaluate(p, visible('#set-seat-xfer-cancel'))), tag + ' row without a code: no 「撤销」');
  await evaluate(p, `document.getElementById('set-seat-xfer').scrollIntoView({ block: 'center' })`);
  await snap(p, tag, '5-settings-row');
  // no key on this remote → passkey_needed → peer setup offered
  H.noKey = true;
  const reqs = H.reqs.length;
  await click(p, '#set-seat-xfer');
  await waitFor(p, `!document.getElementById('confirm').hidden && document.getElementById('confirm-title').textContent === ${JSON.stringify(await tr(p, 'sxf.explainTitle'))}`);
  ok((await evaluate(p, txt('#confirm-text'))).includes('24'), tag + ' explain sheet: what happens, 24 h');
  await snap(p, tag, '6-seat-explain');
  await click(p, '#confirm-yes');
  await waitFor(p, `!document.getElementById('confirm').hidden && document.getElementById('confirm-title').textContent === ${JSON.stringify(await tr(p, 'sxf.pkTitle'))}`);
  ok((await evaluate(p, txt('#confirm-yes'))) === await tr(p, 'pkp.setupHere') && (await evaluate(p, txt('#confirm-no'))) === await tr(p, 'sxf.pkElsewhere'),
    tag + ' passkey_needed → 「在本机设置 Face ID（需另一台批准）」 / 「去已设好 Face ID 的设备操作」');
  await snap(p, tag, '7-seat-passkey-needed');
  await click(p, '#confirm-yes');
  await waitFor(p, `document.getElementById('pkp')?.dataset.mode === 'wait' && ${visible('#pkp')}`);
  ok(H.reqs.length === reqs + 1, tag + ' 「在本机设置」 starts the peer-approved setup');
  await click(p, '#pkp-cancel');
  await waitFor(p, `!${visible('#pkp')}`);
  // with a key: Face ID → the masked prompt
  H.noKey = false;
  await evaluate(p, `import('./js/settings.js').then((m) => m.open())`);
  await waitFor(p, visible('#set-seat-xfer'));
  await click(p, '#set-seat-xfer');
  await waitFor(p, `!document.getElementById('confirm').hidden`);
  await click(p, '#confirm-yes');
  await waitFor(p, `!document.getElementById('confirm').hidden && document.getElementById('confirm-title').textContent === ${JSON.stringify(await tr(p, 'p336.faTitle'))}`);
  await evaluate(p, 'window.__cred.length = 0');
  await tapSel(p, '#confirm-yes');
  await waitFor(p, visible('#sxf-prompt'), 10000);
  const sig = H.seat.filter((x) => x.t === 'seat_transfer');
  ok(sig.length >= 2 && sig.every((x) => x.sigOk) && sig.at(-1).fa, tag + ' seat_transfer signed; the same object resent with this remote\'s Face ID');
  ok(JSON.stringify(await evaluate(p, 'window.__cred')) === '["get"]', tag + ' Face ID started from the confirm tap');
  const shown = await evaluate(p, txt('#sxf-prompt'));
  ok(!shown.includes(H.token) && shown.includes('ajt_••••…' + H.token.slice(-4)), tag + ' the code is masked by default');
  ok((await evaluate(p, `document.querySelectorAll('#sxf-steps li').length`)) === 3 && (await evaluate(p, txt('#sxf-steps'))).includes(await tr(p, 'sxf.step2')), tag + ' three paste steps incl. Olares 「绑定你的席位」');
  ok(await evaluate(p, visible('#sxf-undo')), tag + ' 「撤销」 on the result');
  await noToken(p, H);
  await snap(p, tag, '8-seat-result-masked');
  if (full) {
    await evaluate(p, `document.getElementById('sxf-prompt').scrollIntoView({ block: 'start' })`);
    await click(p, '#sxf-copy');
    await waitFor(p, `document.getElementById('sxf-copy').dataset.done === '1' || document.getElementById('sxf-copy').dataset.done === 'fail'`);
    const copied = await evaluate(p, `navigator.clipboard.readText().catch(() => '')`);
    ok(copied.includes(H.token) && copied.includes('agentj seat bind'), tag + ' 「复制」 copies the whole prompt with the code');
    await click(p, '#sxf-eye');
    ok((await evaluate(p, txt('#sxf-prompt'))).includes(H.token), tag + ' the eye shows the code');
    await click(p, '#sxf-eye');
    ok(!(await evaluate(p, txt('#sxf-prompt'))).includes(H.token), tag + ' and hides it again');
  }
  await click(p, '#sxf-done');
  await waitFor(p, `!${visible('#sxf')}`);
  // the row: state line + 「撤销」
  await evaluate(p, `import('./js/settings.js').then((m) => { m.close(); m.open(); })`);
  await waitFor(p, visible('#set-seat-xfer-cancel'));
  ok((await evaluate(p, txt('#set-seat-xfer-state'))).length > 0, tag + ' seat_xfer_state issued → the line and 「撤销」');
  await evaluate(p, `document.getElementById('set-seat-xfer').scrollIntoView({ block: 'center' })`);
  await snap(p, tag, '9-settings-row-issued');
  await click(p, '#set-seat-xfer-cancel');
  await waitFor(p, `!document.getElementById('confirm').hidden`);
  await click(p, '#confirm-yes');
  for (let i = 0; i < 100 && !H.seat.some((x) => x.t === 'seat_transfer_cancel'); i++) await sleep(50);
  ok(H.seat.some((x) => x.t === 'seat_transfer_cancel' && x.sigOk && !x.fa), tag + ' 「撤销」 = signed seat_transfer_cancel (no Face ID)');
  await waitFor(p, `!${visible('#set-seat-xfer-cancel')}`);
  await evaluate(p, `import('./js/settings.js').then((m) => m.close())`);
}

async function moved(p, tag) {
  const before = await rec(p);
  await fake.send({ t: 'host_moved', relay: 'wss:' + '//evil.example', channel: fake2.channel, hostPub: fake2.hostPub, name: 'X' });
  await fake.send({ t: 'host_moved', relay: fake2.relay, channel: fake2.channel.slice(1), hostPub: fake2.hostPub, name: 'X' });
  await sleep(400);
  ok((await rec(p)).channel === before.channel, tag + ' a host_moved with another relay / a bad channel is ignored');
  fake2.trust(fake.allowList());                       // the old host handed this remote over (handover list)
  const move = { t: 'host_moved', relay: fake2.relay, channel: fake2.channel, hostPub: fake2.hostPub, name: 'Mac mini' };
  const sheet = `!document.getElementById('confirm').hidden && document.getElementById('confirm-yes').textContent === ${JSON.stringify(await tr(p, 'hmv.go'))}`;
  // review M2: never silent — the sheet first; 「留在这里」 keeps this remote on the old host
  await fake.send(move);
  await waitFor(p, sheet, 5000);
  const askText = await evaluate(p, txt('#confirm-text'));
  const askM = askText.match(/「([^」]*Mac mini)」（(\d\d:\d\d)）/);   // the name as every Agent name is shown (displayAgentName)
  ok(!!askM && askText === await tr(p, 'hmv.ask', { name: askM[1], time: askM[2] }),
    tag + ' host_moved → 「席位已转到新主机「Mac mini」（HH:MM）。让这台遥控器切过去吗？」');
  ok((await evaluate(p, txt('#confirm-no'))) === await tr(p, 'hmv.stay'), tag + ' buttons 「切过去」 / 「留在这里」');
  ok((await rec(p)).channel === before.channel && fake2.conns === 0, tag + ' nothing switched while the sheet is open');
  await snap(p, tag, '10a-host-moved-confirm');          // the earlier 「已撤销」 notice is put away first
  await click(p, '#confirm-no');
  await sleep(600);
  ok((await rec(p)).channel === before.channel && fake2.conns === 0 && (await evaluate(p, 'window.__ajState')) === 'ready', tag + ' 「留在这里」 → still paired with the old host, nothing changed');
  await fake.send(move);                               // the old host says it again (e.g. after a reconnect)
  await sleep(600);
  ok(await evaluate(p, `document.getElementById('confirm').hidden`), tag + ' the same move is not asked again on this page');
  // a reload asks again; 「切过去」 follows it
  { const load = p.waitFor('Page.loadEventFired'); await p.send('Page.reload'); await load; }
  await waitState(p, 'ready', 15000); await sleep(300);
  await fake.send(move);
  await waitFor(p, sheet, 5000);
  ok((await rec(p)).channel === before.channel, tag + ' after a reload the sheet comes again, still unswitched');
  await click(p, '#confirm-yes');
  await waitFor(p, `(document.getElementById('toast')?.textContent || '').includes('Mac mini')`, 5000);
  await snap(p, tag, '10-host-moved-toast', true);
  for (let i = 0; i < 100 && fake2.conns === 0; i++) await sleep(50);
  await waitState(p, 'ready', 15000);
  const r = await rec(p);
  ok(r.channel === fake2.channel && r.relay === fake2.relay && r.pk === false && r.pkAsked === false && r.dk === before.dk && r.approved, tag + ' host_moved → the record points at the new host, same device key, pk:false');
  ok(fake2.conns > 0, tag + ' and the page is ready on the new host');
  // the next move does not connect → 「扫码配对新主机」
  await fake2.send({ t: 'host_moved', relay: 'ws:' + '//127.0.0.1:1', channel: fake.channel, hostPub: fake.hostPub, name: 'Gone' });
  await waitFor(p, sheet, 5000);
  await click(p, '#confirm-yes');
  await waitFor(p, `!document.getElementById('confirm').hidden && document.getElementById('confirm-yes').textContent === ${JSON.stringify(await tr(p, 'hmv.rescan'))}`, 40000);
  ok(await evaluate(p, `(() => { const y = document.getElementById('confirm-yes').getBoundingClientRect(); return document.elementFromPoint(y.x + y.width / 2, y.y + y.height / 2)?.id === 'confirm-yes'; })()`), tag + ' 「扫码配对新主机」 is on top (not under the reconnecting card)');
  await snap(p, tag, '11-host-moved-failed', true);
  await click(p, '#confirm-yes');
  await waitFor(p, `document.body.dataset.view === 'pair'`, 8000);
  ok(true, tag + ' 「扫码配对新主机」 → the pairing screen');
}

const runs = [
  { width: 390, scheme: 'dark', lang: 'zh', full: true },
  { width: 390, scheme: 'light', lang: 'zh', full: false },
  { width: 390, scheme: 'light', lang: 'en', full: false },
  { width: 1440, scheme: 'dark', lang: 'zh', full: false },
];
try {
  for (const run of runs) {
    fake.reset(); fake2.reset();
    const H = host(fake); host(fake2);
    const tag = `${run.lang}-${run.width}-${run.scheme}`;
    const p = await phone(run.width, run.scheme, run.lang);
    try {
      await requester(p, H, tag, run.full);
      await approver(p, H, tag, run.full);
      await card(p, H, tag);
      await seat(p, H, tag, run.full);
      if (run.full) await moved(p, tag);
      const problems = p.problems.filter((x) => !/WebSocket|ERR_CONNECTION_REFUSED|127\.0\.0\.1:1|favicon/i.test(x));
      assert.deepEqual(problems, [], tag + ' console / CSP clean');
      assert.deepEqual(p.offsite.filter((u) => !u.startsWith('ws:' + '//127.0.0.1:1')), [], tag + ' nothing off-site');
    } finally { await p.dispose(); }
  }
} finally { await B.close(); await fake.stop(); await fake2.stop(); await web.stop(); }
writeFileSync(`${OUT}result.json`, JSON.stringify({ ok: true, checks, scope: 'Chromium + synthetic Noise host + CDP virtual authenticator; no physical Face ID / WebKit claim' }, null, 2) + '\n');
if (process.env.AJ_PARITY_OUT) writeFileSync(process.env.AJ_PARITY_OUT, JSON.stringify({ suite: 'web/test/p547.mjs', results: { 'p547-phone': 'pass' } }));
console.log('P547 phone PASS', checks.length, 'checks · shots:', OUT);
