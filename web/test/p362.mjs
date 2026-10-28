// P362 「模型和 API Key」 finds keys the computer already has — the real page against the local fake host (real Noise), own
// headless Chromium (never :9222), a CDP virtual platform authenticator as Face ID. Checks:
//   · 「电脑上已有 · 来源 X」 under a row (tail, same / different, 正在使用), 「收进 Agent J」 only where Agent J has none
//   · the Claude sign-in row says what is really used: 订阅 token CLAUDE_CODE_OAUTH_TOKEN · 来源 environment.d (not 「直接登录」)
//   · the places checked, a skipped one with its reason
//   · 收进: confirmation (old source kept by default) → Face ID in the 「收进来」 tap → keys_op import {src, keep:true, fa};
//     ticking the box → keep:false; the systemd source has no box; cancel sends nothing; afterwards the row is Agent J's
// Shots (fake data only): reports/p362/chromium/<theme>-<w>x<h>[-<part>].png at 390×844, 690×900, 1440×900, light + dark,
// plus the confirmation and en at 390.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { generateKeyPairSync, randomBytes } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep, key, rect } from './browser.mjs';
import { keysHandler, ENVD, FILE, FP } from './p362-fixture.mjs';

const OUT = new URL('../reports/p362/chromium/', import.meta.url);
mkdirSync(OUT, { recursive: true });
const SIZES = [[390, 844], [690, 900], [1440, 900]];
let n = 0;
const ok = (c, name) => { assert.ok(c, name); n++; console.log('PASS ' + name); };
const $ = (sel) => `document.querySelector(${JSON.stringify(sel)})`;

const web = await startWebServer({});
const PAGE = web.url.replace('127.0.0.1', 'localhost');
const fake = await startFakeHost();
const B = await launch();
async function click(p, sel) {
  await evaluate(p, `${$(sel)}.scrollIntoView({block:'center'})`); await sleep(80);
  const r = await rect(p, sel);
  await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
  await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 });
}
let lang = 'zh';
async function view(p, w, h, scheme) {
  await p.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: w < 500 });
  await p.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: scheme }] });
  await fake.send({ t: 'preferences', value: { appearance: { language: lang, theme: scheme }, voice: { speak_replies: false, wake_enabled: false } }, problem: null });
  await waitFor(p, `document.documentElement.getAttribute('data-theme')===${JSON.stringify(scheme)}`);
  await sleep(250);
}
const save = async (p, name) => writeFileSync(new URL(name + '.png', OUT), await shoot(p, ''));
async function openModels(p) {
  await evaluate(p, `document.getElementById('settings').hidden && document.getElementById('setBtn').click()`); await sleep(250);
  await click(p, '#open-models');
  await waitFor(p, `document.body.dataset.view==='models'`);
}
const scrollTo = (p, sel) => evaluate(p, `${$(sel)}.scrollIntoView({block:'start'})`);

try {
  const credId = randomBytes(16);
  const priv = generateKeyPairSync('ec', { namedCurve: 'P-256' }).privateKey.export({ type: 'pkcs8', format: 'der' }).toString('base64');
  const calls = keysHandler(fake.st, { fa: credId.toString('base64url') });
  const p = await newPage(B, 390, 844, 'light', { touch: false, allow: [web.url, PAGE, fake.relay + '/'] });
  await p.send('WebAuthn.enable', { enableUI: false });
  const { authenticatorId } = await p.send('WebAuthn.addVirtualAuthenticator', { options: { protocol: 'ctap2', transport: 'internal',
    hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true } });
  await p.send('WebAuthn.addCredential', { authenticatorId, credential: { credentialId: credId.toString('base64'), isResidentCredential: true,
    rpId: 'localhost', privateKey: priv, userHandle: Buffer.from('p362-fixture').toString('base64'), signCount: 0 } });
  await navigate(p, fake.newPairing(PAGE + '?lang=zh')); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await evaluate(p, `document.getElementById('phone-env-dismiss')?.click()`);
  await openModels(p);
  await waitFor(p, `document.querySelectorAll('#keys-box .kfound').length>0`);
  await sleep(500);   // the import challenges are armed

  // ---- what the page says
  const use = await evaluate(p, `${$('.krow[data-name="claude"] .kuseline')}.textContent`);
  ok(use === '正在用：订阅 token（CLAUDE_CODE_OAUTH_TOKEN） · 来源 ~/.config/environment.d/50-claude-oauth.conf', 'Claude row: the subscription token from environment.d is the one in use — ' + use);
  ok(await evaluate(p, `${$('.krow[data-name="codex"] .kuseline')}.textContent==='正在用：账号登录'`), 'Codex row: account sign-in in use');
  const sub = await evaluate(p, `${$('.krow[data-name="CLAUDE_CODE_OAUTH_TOKEN"] .kfound')}.textContent`);
  ok(sub.startsWith('电脑上已有 · 来源 ~/.config/environment.d/50-claude-oauth.conf · ••••k7Qz') && sub.includes('正在使用') && sub.endsWith('收进 Agent J'), 'subscription row: found in environment.d, tail, in use, 收进 — ' + sub);
  const oa = await evaluate(p, `${$('.krow[data-name="OPENAI_API_KEY"] .kfound')}.textContent`);
  ok(oa === '电脑上另有一份 · 来源 ~/projects/.env · ••••9a9a · 和 Agent J 的不同，以 Agent J 的为准', 'a set row: the other copy differs, no 收进 — ' + oa);
  ok(await evaluate(p, `!${$('.krow[data-name="OPENAI_API_KEY"] .kimp')}`), 'no 收进 button when Agent J already has it');
  ok(await evaluate(p, `${$('.krow[data-name="GITHUB_TOKEN"] .kfound')}.textContent.includes('来源 systemd 用户环境')`), 'systemd source named for people');
  const src = await evaluate(p, `[...document.querySelectorAll('.ksrc-i')].map(x=>x.textContent)`);
  ok(src.length === 4 && src[2] === '~/projects/.env · 3 个名字' && src[3] === '~/shared/team.env · 跳过：路径里有符号链接，不读', 'places checked, a skipped one with its reason — ' + JSON.stringify(src));
  ok(await evaluate(p, `!${$('.ksrc-hint')}`), 'no 「add a file」 hint once a file is registered');
  const imp = await evaluate(p, `[...document.querySelectorAll('.kimp')].map(b=>{const r=b.getBoundingClientRect();return r.height})`);
  ok(imp.length === 3 && imp.every((h) => h >= 44), '收进 buttons: 3, ≥ 44 px tall');
  for (const scheme of ['light', 'dark']) for (const [w, h] of SIZES) {
    await view(p, w, h, scheme);
    await scrollTo(p, '#keys-box'); await sleep(150);
    await save(p, `${scheme}-${w}x${h}`);
    if (w === 390) { await scrollTo(p, '.kvendor--third'); await sleep(150); await save(p, `${scheme}-${w}x${h}-third`); }
  }
  await view(p, 390, 844, 'light');

  // ---- 收进 with the default (keep the old source); cancel first
  await click(p, '.krow[data-name="CLAUDE_CODE_OAUTH_TOKEN"] .kimp');
  await waitFor(p, `!document.getElementById('confirm').hidden`);
  ok(await evaluate(p, `document.getElementById('confirm-title').textContent==='把 CLAUDE_CODE_OAUTH_TOKEN 收进 Agent J 管理？' && document.getElementById('confirm-text').textContent.includes('Key 不经过手机') && document.getElementById('confirm-yes').textContent==='收进来'`), 'confirmation: what moves where, never through the phone');
  ok(await evaluate(p, `!document.getElementById('kimp-comment').checked`), 'comment-out box unticked by default (old source kept)');
  await save(p, 'light-390x844-confirm');
  await view(p, 390, 844, 'dark'); await save(p, 'dark-390x844-confirm'); await view(p, 390, 844, 'light');
  await click(p, '#confirm-no'); await sleep(300);
  ok(!calls.some((c) => c.op === 'import') && !(await evaluate(p, `!!document.getElementById('kimp-comment')`)), 'cancel: nothing sent, the box is gone');
  await click(p, '.krow[data-name="CLAUDE_CODE_OAUTH_TOKEN"] .kimp');
  await waitFor(p, `!document.getElementById('confirm').hidden`);
  await click(p, '#confirm-yes');
  await waitFor(p, `document.getElementById('toast').textContent.includes('已收进 Agent J')`, 10000);
  const c0 = calls.find((c) => c.op === 'import');
  ok(c0 && c0.name === 'CLAUDE_CODE_OAUTH_TOKEN' && c0.src === ENVD && c0.keep === true && c0.fp === FP && c0.fa, 'import: NAME + environment.d source + keep, Face ID assertion');
  await waitFor(p, `${$('.krow[data-name="claude"] .kuseline')}?.textContent.includes('来源 Agent J 的密钥文件')`, 5000);
  ok(await evaluate(p, `${$('.krow[data-name="CLAUDE_CODE_OAUTH_TOKEN"] .kstate')}.textContent.startsWith('••••••••k7Qz') && !${$('.krow[data-name="CLAUDE_CODE_OAUTH_TOKEN"] .kimp')}`), 'after: Agent J has it; the found line says same, no 收进');
  await save(p, 'light-390x844-imported');

  // ---- 收进 with the box ticked → keep:false
  await sleep(400);
  await click(p, '.krow[data-name="ELEVENLABS_API_KEY"] .kimp');
  await waitFor(p, `!document.getElementById('confirm').hidden`);
  await click(p, '#kimp-comment');
  ok(await evaluate(p, `document.getElementById('kimp-comment').checked`), 'the box ticks');
  await click(p, '#confirm-yes');
  await waitFor(p, `document.getElementById('toast').textContent.includes('注释掉')`, 10000);
  ok(calls.some((c) => c.op === 'import' && c.name === 'ELEVENLABS_API_KEY' && c.src === FILE && c.keep === false && c.fa), 'ticked: keep:false (comment out the old line)');

  // ---- the systemd source cannot be commented out: no box
  await sleep(400);
  await click(p, '.krow[data-name="GITHUB_TOKEN"] .kimp');
  await waitFor(p, `!document.getElementById('confirm').hidden`);
  ok(await evaluate(p, `!document.getElementById('kimp-comment')`), 'systemd source: no comment-out box');
  await click(p, '#confirm-no'); await sleep(200);

  // ---- en
  await evaluate(p, `window.AJLang.set('en')`); lang = 'en'; await sleep(300);
  ok(await evaluate(p, `${$('.krow[data-name="GITHUB_TOKEN"] .kimp')}.textContent==='Move into Agent J' && document.querySelector('.ksrc-h').textContent==='Places checked on this computer'`), 'en strings');
  await view(p, 390, 844, 'dark'); await scrollTo(p, '#keys-box'); await save(p, 'en-dark-390x844');
  await view(p, 390, 844, 'light');
  await evaluate(p, `window.AJLang.set('zh')`); lang = 'zh'; await sleep(200);

  await evaluate(p, `document.activeElement?.blur()`);
  await key(p, 'Escape'); await sleep(300);
  ok(p.problems.length === 0, 'no page errors ' + JSON.stringify(p.problems));
  ok(p.offsite.length === 0, 'nothing off-site ' + JSON.stringify(p.offsite));
  console.log(`p362 chromium: ${n} checks passed; shots → ${OUT.pathname}`);
} finally { await B.close(); await fake.stop(); await web.stop(); }
