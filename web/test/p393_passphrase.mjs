#!/usr/bin/env node
// #118: `agentj passphrase reset --phone` puts a `passphrase_reset` card on every paired remote. The phone shows it as an
// Agent J card (title 「重设遥控器配对密码」 / "Reset the remote pairing password", the host's text with the match code in
// full, a pairing-password tag), never a field to type a password into; one tap sends a signed allow (fakehost verifies the
// signature, the same bytes as serve._answer); Deny sends a signed deny. A card with passkey_required gets the
// 「需验证本人」 tag and label. The real web client against the fake host, headless Chromium (never :9222), 390×844, zh/en.
//   node web/test/p393_passphrase.mjs   → reports/qa/p393/passphrase-<lang>.png
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { webcrypto } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, sleep } from './browser.mjs';
import { parityRecorder } from '../../parity/lib.mjs';

const OUT = fileURLToPath(new URL('../../../reports/qa/p393/', import.meta.url));
mkdirSync(OUT, { recursive: true });
const hex = (n) => Array.from(webcrypto.getRandomValues(new Uint8Array(n)), (b) => b.toString(16).padStart(2, '0')).join('');
const SUMMARY = {
  zh: '重设这台主机的遥控器配对密码\n有人在这台Agent J 主机的终端运行了 agentj passphrase reset --phone。确认后，新的遥控器配对密码要在那台主机的终端里输入；所有遥控器保持配对。\n对照码：482 913（终端上应显示同一串数字；不一致或不是你本人操作就点拒绝）',
  en: "Reset this host's remote pairing password\nSomeone ran agentj passphrase reset --phone in a terminal on this Agent J host. After you confirm, the new remote pairing password is typed in that terminal; every remote stays paired.\nMatch code: 482 913 (the terminal shows the same digits; if not, or it isn't you, tap Deny)",
};
const WANT = {
  zh: { title: '重设遥控器配对密码', tag: '遥控器配对密码', allow: '确认', where: '新密码在Agent J 主机上输入' },
  en: { title: 'Reset the remote pairing password', tag: 'Pairing password', allow: 'Confirm', where: 'typed on the Agent J host' },
};

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const Br = await launch();
const fails = [];
const check = (ok, what) => { if (!ok) fails.push(what); };
try {
  for (const lang of ['zh', 'en']) {
    fake.reset();
    const p = await newPage(Br, 390, 844, 'light', { touch: true, allow: [web.url, fake.relay + '/'] });
    const base = web.url + (lang === 'en' ? '?lang=en' : '');
    await navigate(p, base);
    await waitState(p, 'idle');
    await navigate(p, 'about:blank');
    await navigate(p, fake.newPairing(base));
    await waitState(p, 'awaiting-approval');
    await fake.approve();
    await waitState(p, 'ready');
    await evaluate(p, `(() => { const n = document.getElementById('confirm-no'); if (n && !document.getElementById('confirm').hidden) n.click();
      const a = document.getElementById('a2hs-ok'); if (a && !document.getElementById('a2hs').hidden) a.click(); })()`);
    await fake.send({ t: 'status', s: 'waiting', agent: 'claude', name: 'AgentJ' });
    const tap = async (buttonId, cancel = false) => {
      await evaluate(p, `document.getElementById('${buttonId}').scrollIntoView({block:'center'})`);
      const point = await evaluate(p, `(() => { const b = document.getElementById('${buttonId}'), r = b.getBoundingClientRect(), x = r.left+r.width/2, y = r.top+r.height/2;
        return {x,y,hit:document.elementFromPoint(x,y)?.closest('button')===b}; })()`);
      if (!point.hit) throw new Error(buttonId + ': the approval control is not reachable');
      const before = fake.st.answers.length;
      await p.send('Input.dispatchTouchEvent', {type:'touchStart',touchPoints:[{x:point.x,y:point.y}]});
      await sleep(100);
      check(fake.st.answers.length === before, `${lang}: a press alone sends no approval`);
      await p.send('Input.dispatchTouchEvent', {type:cancel?'touchCancel':'touchEnd',touchPoints:[]});
      if (cancel) {
        await sleep(100);
        check(fake.st.answers.length === before, `${lang}: a cancelled touch sends no approval`);
        check(await evaluate(p, `document.body.dataset.sheet==='1' && !document.getElementById('sheet').hidden`), `${lang}: cancel keeps the approval visible`);
      }
    };
    const W = WANT[lang];
    const last = async (n) => { for (let i = 0; i < 40 && fake.st.answers.length === n; i++) await sleep(50); return fake.st.answers[fake.st.answers.length - 1]; };
    // 1. the card: Agent J's own title, the host text in full (match code), a tag, no input field; tap → signed allow
    let id = hex(16);
    await fake.ask({ id, tool: 'passphrase_reset', summary: SUMMARY[lang], ttl: 300, cat: [], why: '' });
    await waitFor(p, `document.body.dataset.sheet === '1' && !document.getElementById('apprBtns').hidden`);
    await sleep(300);
    const seen = JSON.parse(await evaluate(p, `JSON.stringify({title: document.getElementById('sheetTitle').textContent,
      tool: document.getElementById('tool').textContent, cmd: document.getElementById('cmd').textContent,
      tags: [...document.getElementById('askTags').querySelectorAll('.atag')].map((x) => x.textContent),
      allow: document.getElementById('apprAllowLbl').textContent, where: document.getElementById('elsewhereText').textContent,
      danger: document.getElementById('sheet').classList.contains('danger'),
      inputs: document.getElementById('sheet').querySelectorAll('input[type=password], input[type=text], textarea').length})`));
    check(seen.title === W.title, `${lang}: title (${seen.title})`);
    check(seen.tool === 'Agent J', `${lang}: shown as Agent J's own card (${seen.tool})`);
    check(seen.cmd.includes('482 913') && seen.cmd.includes('agentj passphrase reset --phone'), `${lang}: the host text in full (${seen.cmd})`);
    check(seen.tags.includes(W.tag), `${lang}: tag (${JSON.stringify(seen.tags)})`);
    check(seen.allow === W.allow, `${lang}: allow label (${seen.allow})`);
    check(seen.where.includes(W.where), `${lang}: says the new password is typed on the computer (${seen.where})`);
    check(seen.danger === true, `${lang}: shown as a careful card`);
    check(seen.inputs === 0, `${lang}: nothing to type a password into on the phone (${seen.inputs})`);
    const { data } = await p.send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(join(OUT, `passphrase-${lang}.png`), Buffer.from(data, 'base64'));
    let n = fake.st.answers.length;
    await tap('apprAllow', true); // A cancelled press must leave this password-reset card untouched.
    await tap('apprAllow');
    await waitFor(p, `document.body.dataset.sheet==='0' && document.getElementById('sheet').hidden`, 3000);
    let ans = await last(n);
    check(ans && ans.id === id && ans.ok === true, `${lang}: tap → allow (${JSON.stringify(ans)})`);
    check(fake.st.sigOk[fake.st.sigOk.length - 1] === true, `${lang}: the allow is signed by this paired remote`);
    // 2. Deny → a signed deny
    id = hex(16);
    await fake.ask({ id, tool: 'passphrase_reset', summary: SUMMARY[lang], ttl: 300, cat: [], why: '' });
    await waitFor(p, `document.body.dataset.sheet === '1' && !document.getElementById('apprBtns').hidden`);
    await sleep(300);
    n = fake.st.answers.length;
    await tap('apprDeny');
    await waitFor(p, `document.body.dataset.sheet==='0' && document.getElementById('sheet').hidden`, 3000);
    ans = await last(n);
    check(ans && ans.id === id && ans.ok === false, `${lang}: deny (${JSON.stringify(ans)})`);
    check(fake.st.sigOk[fake.st.sigOk.length - 1] === true, `${lang}: the deny is signed`);
    // 3. a remote with a passkey: the 「verify it's you」 tag and label
    id = hex(16);
    await fake.ask({ id, tool: 'passphrase_reset', summary: SUMMARY[lang], ttl: 300, cat: [], why: '', passkey_required: true });
    await waitFor(p, `document.body.dataset.sheet === '1' && document.getElementById('apprAllowLbl').textContent !== ''`);
    await sleep(300);
    const face = JSON.parse(await evaluate(p, `JSON.stringify({tags: [...document.getElementById('askTags').querySelectorAll('.atag')].map((x) => x.textContent),
      title: document.getElementById('sheetTitle').textContent})`));
    check(face.tags.some((x) => x.includes(lang === 'zh' ? '需验证本人' : "verify it's you")), `${lang}: passkey tag (${JSON.stringify(face.tags)})`);
    check(await evaluate(p, `document.getElementById('apprAllowLbl').textContent`) === (lang === 'zh' ? '确认（验证本人）' : "Confirm (verify it's you)"), `${lang}: user-verification confirmation label remains explicit`);
    check(face.title === W.title, `${lang}: never the sandbox title on this card (${face.title})`);
    await p.dispose();
  }
} finally {
  await Br.close(); await fake.stop(); await web.stop();
}
const rec = parityRecorder('web/test/p393_passphrase.mjs');
rec.record('passphrase-reset-card', !fails.length);
rec.write();
if (fails.length) { console.error('P393 passphrase card FAIL\n' + fails.join('\n')); process.exit(1); }
console.log('P393 passphrase card OK: zh/en, Agent J card with match code, tag, no password field, signed allow/deny, passkey tag; screenshots in', OUT);
