#!/usr/bin/env node
// P539 (#300 / #299): the real web client against fakehost.mjs, own headless Chromium (never :9222).
//   a. Android (OPPO) UA: Settings 「本机 Touch ID：未设」 row + the pk.notSaved toast (no Apple / iCloud words)
//   b. Mac Chrome UA: an approval card with fa_s=borrowed → the quiet line + 「在本机设置」; a refused passkey → reason +
//      「再试一次」 + 「在本机设置 Face ID（需另一台批准）」 (re-tap re-runs approve from the tap)
//   c. the same card closed with why=timeout
//   d. the preauth checkbox on a computer.control card (ticked → allow_add carries preauth:computer.control)
//   e. Settings → 安全 → 「免问的动作」 from host `preauth`, 「取消免问」 → preauth_revoke {action, device}
//   f. draft_restore → composer + toast; empty → 「请重新输入要发的话」
//   g. a `fa`-carrying card outside the approval sheet: Settings → 紧急修复 → 「执行」 (cmd_run) with fa_s=borrowed → the shared
//      confirm sheet's quiet line + 「在本机设置」 (steps the sheet aside, sends pk_offer_req); a refused passkey → the result says
//      why + 「再试一次」 (the same sheet again) + 「在本机设置 Face ID（需另一台批准）」
// Shots: 390 × 844 zh / en (light, + dark for b), 1440 × 900 for b → reports/qa/p539/shots/<shot>-<w>-<scheme>-<lang>.png
//   node web/test/p539.mjs            (checks + shots)
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep } from './browser.mjs';

const OUT = fileURLToPath(new URL('../../../reports/qa/p539/shots/', import.meta.url));
mkdirSync(OUT, { recursive: true });
const UA = {
  android: 'Mozilla/5.0 (Linux; Android 14; PJZ110) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Mobile Safari/537.36',
  mac: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
};
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
const ev = (p, s) => evaluate(p, s);
let checks = 0;
const check = (v, label) => { assert.ok(v, label); checks++; };
const hex = (n) => n.toString(16).padStart(32, '0');
const b64 = (u8) => Buffer.from(u8).toString('base64url');
const shot = async (p, name) => writeFileSync(OUT + name + '.png', await shoot(p, ''));
// what the page sent to the host (fakehost keeps every app message it decoded)
const sent = (t) => (fake.st.appLog || []).filter((m) => m.t === t);

async function paired(w, h, scheme, lang, ua, before = null) {
  fake.reset();
  if (before) before();
  const p = await newPage(B, w, h, scheme, { ua, touch: w < 500 && ua !== UA.mac, allow:   // a Mac with touch = an iPad (ios)
     [web.url, fake.relay + '/'] });
  await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await ev(p, `document.getElementById('phone-env-dismiss')?.click()`);
  await sleep(300);
  return p;
}
const openSettings = async (p, section) => {
  await ev(p, `document.getElementById('settings').hidden && document.getElementById('setBtn').click()`);
  await waitFor(p, `!document.getElementById('settings').hidden`);
  await ev(p, `document.getElementById(${JSON.stringify(section)}).scrollIntoView({block:'start'})`);
  await sleep(300);
};
const closeSettings = (p) => ev(p, `document.getElementById('settings').hidden || document.getElementById('setBtn').click()`);
const tall = (p, sel) => ev(p, `[...document.querySelectorAll(${JSON.stringify(sel)})].filter(b=>b.offsetParent).every(b=>b.getBoundingClientRect().height>=44)`);

try {
  for (const lang of ['zh', 'en']) {
    const L = (zh, en) => (lang === 'en' ? en : zh);
    // ---------------------------------------------------------------- a. Android
    {
      const p = await paired(390, 844, 'light', lang, UA.android);
      await fake.send({ t: 'pk_state', s: 'none' });
      await openSettings(p, 'set-h-agent');
      await waitFor(p, `!document.getElementById('set-pk-row').hidden`);
      const row = await ev(p, `document.getElementById('set-pk-row').innerText`);
      check(row.includes(L('本机 Touch ID：未设', "This device's Touch ID: not set")), 'a: state label ' + lang);   // P559: Touch ID
      check(!/iCloud|Apple|屏幕锁：|screen lock:/.test(row) && !/Face ID：|Face ID:/.test(row), 'a: Android words only ' + lang);
      check(await ev(p, `!document.getElementById('set-pk-setup').hidden && document.getElementById('setup-faceid').hidden`), 'a: one setup button, the old entry folded in ' + lang);
      check(await tall(p, '#set-pk-setup'), 'a: 44 px ' + lang);
      await shot(p, `a-android-pkrow-390-light-${lang}`);
      await closeSettings(p);
      // the save sheet → the system refuses → pk.notSaved.android
      await ev(p, `PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable=async()=>true;navigator.credentials.create=async()=>{throw new DOMException('x','NotAllowedError')}`);
      await fake.send({ t: 'pk_offer', n: b64(Buffer.alloc(32, 1)) });
      await waitFor(p, `!document.getElementById('confirm').hidden`);
      const sheet = await ev(p, `document.getElementById('confirm').innerText`);
      check(!sheet.includes('Face ID') && sheet.includes('Touch ID'), 'a: the save sheet names the Android unlock ' + lang);
      await ev(p, `document.getElementById('confirm-yes').click()`);
      await waitFor(p, `document.getElementById('toast').classList.contains('on') && document.getElementById('toast').textContent.includes('Google')`);
      const toast = await ev(p, `document.getElementById('toast').textContent`);
      check(!/Apple|iCloud|iPhone|Face ID/.test(toast), 'a: pk.notSaved on Android has no Apple words ' + lang);
      await shot(p, `a-android-notsaved-390-light-${lang}`);
      await p.dispose();
    }
    // ---------------------------------------------------------------- b / c / d. Mac, borrowed passkey
    for (const [w, h, scheme] of lang === 'zh' ? [[390, 844, 'light'], [390, 844, 'dark'], [1440, 900, 'light']] : [[390, 844, 'light']]) {
      const name = `${w}-${scheme}-${lang}`;
      const p = await paired(w, h, scheme, lang, UA.mac);
      await fake.send({ t: 'pk_state', s: 'borrowed' });
      const id = hex(5390 + w);
      await fake.ask({ t: 'ask', id, tool: 'mcp__computer-use__left_click', summary: 'left_click (640, 412)', ttl: 120, cat: [], why: '',
        passkey_required: true, fa: 'Y3JlZC0x', fa_ch: b64(Buffer.alloc(32, 7)), fa_s: 'borrowed', allow_offer: ['preauth:computer.control', 'preauth:browser.control'] });
      await waitFor(p, `!document.getElementById('sheet').hidden && !document.getElementById('apprPk').hidden`);
      const pk = await ev(p, `document.getElementById('apprPk').innerText`);
      check(pk.includes(L('这台遥控器还没有自己的 Touch ID 钥匙', 'This remote has no Touch ID key of its own yet')) && pk.includes(L('在本机设置', 'Set up here')), 'b: borrowed hint + 在本机设置 ' + name);
      const boxes = await ev(p, `[...document.querySelectorAll('#apprMailAllow label')].map(l=>l.innerText)`);
      check(boxes.length === 1 && boxes[0].includes('Computer Use'), 'd: one preauth box, unknown action not rendered ' + name);
      check(await ev(p, `!document.querySelector('#apprMailAllow input').checked`), 'd: unticked by default ' + name);
      check(await tall(p, '#apprPk button'), 'b: 44 px text buttons ' + name);
      await shot(p, `b-mac-borrowed-card-${name}`);
      // the system refuses (phone scan cancelled / timed out) → reason + 再试一次; the card stays
      await ev(p, `window.__gets=0;navigator.credentials.get=()=>{window.__gets++;return Promise.reject(new DOMException('x','NotAllowedError'))}`);
      await ev(p, `document.querySelector('#apprMailAllow input').click()`);
      await ev(p, `document.getElementById('apprAllow').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:0}))`);
      await waitFor(p, `document.getElementById('apprPk').dataset.state==='fail'`);
      const fail = await ev(p, `document.getElementById('apprPk').innerText`);
      check(fail.includes(L('这台遥控器上没有这把通行密钥', "This remote doesn't hold the passkey")) && fail.includes(L('再试一次', 'Try again')) && fail.includes(L('在本机设置 Touch ID（需另一台批准）', 'Set up here (approve elsewhere)')), 'b: reason + retry + setup ' + name);
      check(await ev(p, `!document.getElementById('sheet').hidden && !document.getElementById('apprBtns').hidden`), 'b: the card stays ' + name);
      await shot(p, `b-mac-passkey-failed-${name}`);
      await ev(p, `document.querySelector('#apprPk .pkassist__retry').click()`);
      await waitFor(p, `window.__gets===2`);
      check(true, 'b: 再试一次 re-runs the passkey from the tap ' + name);
      // d: a passkey that works → the signed answer carries the ticked preauth line
      if (w === 390 && scheme === 'light') {
        fake.st.holdAnswers = true;   // the test closes the card itself (c)
        await ev(p, `navigator.credentials.get=async()=>({rawId:new Uint8Array([1,2,3]).buffer,response:{clientDataJSON:new Uint8Array([1]).buffer,authenticatorData:new Uint8Array([2]).buffer,signature:new Uint8Array([3]).buffer}})`);
        await waitFor(p, `document.getElementById('apprPk').dataset.state==='fail'`);
        await ev(p, `document.querySelector('#apprPk .pkassist__retry').click()`);
        await waitFor(p, `document.getElementById('sheet').hidden || document.getElementById('apprBtns').hidden`, 8000);
        const ans = (fake.st.answers || []).find((a) => a.id === id) || null;
        check(ans && Array.isArray(ans.allow_add) && ans.allow_add.includes('preauth:computer.control') && !!ans.fa, 'd: allow_add + fa signed ' + name);
      }
      // c: closed by the host with why=timeout
      fake.forget(id);
      await fake.send({ t: 'ask_done', id, result: 'timeout', why: 'timeout' });
      await waitFor(p, `document.getElementById('askState').textContent.includes(${JSON.stringify(L('等太久没批准，已替你拒绝', 'Waited too long for approval'))})`);
      await shot(p, `c-closed-timeout-${name}`);
      // Settings row for a borrowed record (Mac → Touch ID)
      if (w === 390) {
        await openSettings(p, 'set-h-agent');
        const row = await ev(p, `(() => { const r = document.getElementById('set-pk-row').cloneNode(true); r.querySelector('#set-pk-sub')?.remove(); return r.innerText || r.textContent; })()`);   // P559: the explanation line names all three
        check(row.includes(L('本机 Touch ID：用的是别的设备上的钥匙', "This device's Touch ID: using another device's key")) && row.includes('Touch ID') && !row.includes('Face ID'), 'Mac borrowed row ' + name + ' ' + JSON.stringify(row));
        await shot(p, `b-mac-pkrow-borrowed-${name}`);
        await closeSettings(p);
      }
      await p.dispose();
    }
    // ---------------------------------------------------------------- e / f
    {
      const p = await paired(390, 844, 'light', lang, UA.mac);
      await openSettings(p, 'set-h-safety');
      check(await ev(p, `document.getElementById('set-preauth-row').hidden`), 'e: empty list → hidden ' + lang);
      await fake.send({ t: 'preauth', items: [{ action: 'computer.control', device: 'AJ-MAC1-0001', name: 'MacBook · Chrome', at: 1761400000 }] });
      await waitFor(p, `!document.getElementById('set-preauth-row').hidden`);
      await ev(p, `document.getElementById('set-preauth-row').scrollIntoView({block:'center'})`); await sleep(200);
      const pre = await ev(p, `document.getElementById('set-preauth-row').innerText`);
      check(pre.includes(L('免问的动作', 'Actions that skip approval')) && pre.includes('MacBook · Chrome') && pre.includes(L('取消免问', 'Ask again')), 'e: list row ' + lang);
      check(await tall(p, '#set-preauth-list button'), 'e: 44 px ' + lang);
      // the card's hint names the path as rendered: 设置 / 安全 / 免问的动作 (Settings / Safety / Actions that skip approval)
      const path = await ev(p, `[document.getElementById('set-title').textContent.trim(), document.getElementById('set-h-safety').textContent.trim(), document.querySelector('#set-preauth-row .setlab').textContent.trim()]`);
      const hint = await ev(p, `import('./js/t.js').then(m=>m.t('p539.pre.hint'))`);
      check(hint.includes(path.join(' → ')), 'e: p539.pre.hint names the real Settings labels ' + lang + ' ' + JSON.stringify(path));
      await shot(p, `e-preauth-list-390-light-${lang}`);
      await ev(p, `document.querySelector('#set-preauth-list button').click()`);
      await waitFor(p, `document.getElementById('set-preauth-row').hidden`);
      await closeSettings(p);
      // f
      await ev(p, `document.getElementById('input').value=''`);
      await fake.send({ t: 'draft_restore', text: L('帮我给房东写一封邮件，说下个月搬走', 'Write my landlord an email saying I move out next month') });
      await waitFor(p, `document.getElementById('toast').classList.contains('on') && document.getElementById('toast').textContent.includes(${JSON.stringify(L('原话已放回输入框', 'Your message is back in the box'))})`);
      check((await ev(p, `document.getElementById('input').value`)).includes(L('房东', 'landlord')), 'f: composer holds the text ' + lang);
      check(await ev(p, `document.activeElement===document.getElementById('input')`), 'f: composer focused ' + lang);
      await shot(p, `f-draft-restore-390-light-${lang}`);
      await fake.send({ t: 'draft_restore', text: '' });
      await waitFor(p, `document.getElementById('toast').textContent.includes(${JSON.stringify(L('请重新输入要发的话', 'Please type your message again'))})`);
      await shot(p, `f-draft-empty-390-light-${lang}`);
      await p.dispose();
    }
    // ---------------------------------------------------------------- g. cmd_run (Settings → 紧急修复), fa_s = borrowed
    for (const scheme of lang === 'zh' ? ['light', 'dark'] : ['light']) {
      const name = `390-${scheme}-${lang}`;
      const runs = [], offers = [];
      const p = await paired(390, 844, scheme, lang, UA.mac, () => {
        fake.setUp(true); fake.st.runCmds = ['doctor']; fake.st.prefs = { appearance: { language: lang, theme: scheme } };
        fake.st.onApp = async (c, m, send) => {
          if (m.t === 'pk_offer_req' || m.t === 'pk_peer_req') offers.push(m);   // P550: the setup asks for another remote's approval first (P547)
          if (m.t !== 'cmd_run') return false;
          runs.push(!!m.fa);
          await send({ t: 'ctl_res', r: m.r, action: 'cmd_run', cmd: m.cmd, ok: false, why: 'passkey_required', fa: 'Y3JlZC0x', fa_s: 'borrowed', fa_ch: b64(Buffer.alloc(32, 9)) });
          return true;
        };
      });
      await ev(p, `PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable=async()=>true;window.__gets=0;navigator.credentials.get=()=>{window.__gets++;return Promise.reject(new DOMException('x','NotAllowedError'))}`);
      await ev(p, `import('./js/settings.js').then(m=>{m.openRescue();return true})`);
      await waitFor(p, `!document.getElementById('settings').hidden && !!document.getElementById('rs-cmd-doctor')`);
      await ev(p, `document.getElementById('rs-cmd-doctor').click()`);
      await waitFor(p, `!document.getElementById('confirm').hidden && !document.getElementById('confirm-pk').hidden`);
      const hintTxt = await ev(p, `document.getElementById('confirm-pk').innerText`);
      check(hintTxt.includes(L('这台遥控器还没有自己的 Touch ID 钥匙', 'This remote has no Touch ID key of its own yet')) && hintTxt.includes(L('在本机设置', 'Set up here')), 'g: the sheet says borrowed + 在本机设置 ' + name);
      check(await tall(p, '#confirm-pk button'), 'g: 44 px ' + name);
      check(await ev(p, `(()=>{const a=document.querySelector('#confirm .confirm-row').getBoundingClientRect(),b=document.getElementById('confirm-pk').getBoundingClientRect();return b.top>=a.bottom})()`), 'g: the line sits under the buttons ' + name);
      await shot(p, `g-cmdrun-borrowed-${name}`);
      await ev(p, `document.getElementById('confirm-yes').click()`);
      await waitFor(p, `document.getElementById('rs-cmd-pk')?.dataset.state==='fail'`);
      const fail = await ev(p, `document.getElementById('rs-cmd-out').innerText`);
      check(fail.includes(L('这台遥控器上没有这把通行密钥', "This remote doesn't hold the passkey")) && fail.includes(L('再试一次', 'Try again')) && fail.includes(L('在本机设置 Touch ID（需另一台批准）', 'Set up here (approve elsewhere)')), 'g: reason + retry + setup ' + name);
      check(await ev(p, `document.getElementById('confirm-pk').hidden && !document.getElementById('confirm-pk').childElementCount`), 'g: a closed sheet keeps no line ' + name);
      check(await tall(p, '#rs-cmd-pk button'), 'g: 44 px result buttons ' + name);
      await ev(p, `document.getElementById('rs-cmd-out').scrollIntoView({block:'center'})`); await sleep(200);
      await shot(p, `g-cmdrun-failed-${name}`);
      // 再试一次 → the same sheet (a fresh signed cmd_run), its 在本机设置 puts the sheet away and asks for the F20 offer
      await ev(p, `document.querySelector('#rs-cmd-pk .pkassist__retry').click()`);
      await waitFor(p, `!document.getElementById('confirm').hidden && !document.getElementById('confirm-pk').hidden`);
      check(runs.length === 2 && runs.every((x) => !x), 'g: retry signs a new cmd_run ' + name);
      await ev(p, `document.querySelector('#confirm-pk .pkassist__setup').click()`);
      await waitFor(p, `document.getElementById('confirm').hidden`);
      for (let i = 0; i < 40 && !offers.length; i++) await sleep(50);
      check(offers.length === 1 && offers[0].t === 'pk_peer_req', 'g: 在本机设置 → pk_peer_req (the P547 setup: another remote approves) ' + name);
      check(await ev(p, `window.__gets===1`), 'g: no passkey asked on the way out ' + name);
      fake.st.onApp = null; fake.st.runCmds = null;
      await p.dispose();
    }
  }
} finally { await B.close(); await fake.stop(); await web.stop(); }
console.log(`PASS p539 · ${checks} checks · shots → ${OUT}`);
