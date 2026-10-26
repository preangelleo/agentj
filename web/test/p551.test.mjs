// P551 static half (web-unit). Browser behaviour: test/p551.mjs (gate p551-phone).
//  #316 #318 confirmation cards are ONE tier above every other layer and the first layer Esc reaches;
//  #320 the 4th approval level 「跟随 AI 编程工具（Harness）」 in the same picker, always behind Face ID on the host;
//  #318 ③ unbinding ANOTHER remote / WeChat asks Face ID (passkey_required → the same signed object + fa; no_passkey said plainly).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const css = Object.fromEntries(['app.css', 'menu.css', 'five-pages.css', 'terminal.css', 'cards.css', 'controls.css'].map((f) => [f, read('../public/' + f)]));
const js = (f) => read('../public/js/' + f);
const app = read('../public/app.js'), html = read('../public/index.html');
const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));
const rules = (s) => [...s.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => ({ sel: m[1].trim().replace(/\s+/g, ' '), body: m[2] }));

test('confirmation cards use the one tier, never an ad-hoc number', () => {
  const tier = rules(css['app.css']).filter((r) => /z-index:\s*calc\(var\(--z-confirm\)/.test(r.body)).map((r) => r.sel).join(' | ');
  for (const sel of ['.ajconfirm', '.ajmodal.elev', '.ajmodal.lqr', '.ajmodal.sout', '.auth-approval.scrim', '.auth-approval.sheet', '.scrim.ontop-float', '.sheet.ontop-float']) {
    assert.ok(tier.split(' | ').some((s) => s.split(',').map((x) => x.trim()).includes(sel)), `${sel} on --z-confirm (have: ${tier})`);
  }
  assert.match(css['app.css'], /:root\{--z-confirm:150;--z-notice-top:250;--z-chrome:300\}/);
  assert.match(css['app.css'], /body\[data-ontop="1"\] #toast\{z-index:var\(--z-notice-top\)!important\}/);
});

test('no other layer reaches the confirmation tier (150)', () => {
  const ALLOWED = new Set(['#iosdiag']);            // the on-device layout diagnostic (opt-in, never in normal use)
  for (const [f, s] of Object.entries(css)) {
    for (const r of rules(s)) {
      const z = /z-index:\s*(-?\d+)/.exec(r.body);
      if (z && Number(z[1]) >= 150) assert.ok(ALLOWED.has(r.sel), `${f}: ${r.sel} z-index ${z[1]} is at/above the confirmation tier`);
    }
  }
});

test('Esc / Back reach a card first: TOP_RANK on every card layer', () => {
  assert.match(js('layers.js'), /export const TOP_RANK=1000;/);
  assert.match(app, /layer\('confirm', closeConfirm, 'setBtn', TOP_RANK\)/);
  assert.match(js('relay.js'), /registerLayer\(\{element:\(\)=>el\('sheet'\),open:\(\)=>document\.body\.dataset\.sheet==='1',close:\(\)=>el\('sheetClose'\)\.click\(\),fallback:\(\)=>el\('pendTag'\),rank:TOP_RANK\}\)/);
  for (const f of ['elevate.js', 'logincard.js', 'secretout.js']) assert.match(js(f), /registerLayer\(\{ element: \(\) => root, open: \(\) => !!root && !root\.hidden, rank: TOP_RANK,/, f);
  assert.match(js('auth-overlay.js'), /rank:TOP_RANK\}\)/);
  assert.match(js('mail-servers.js'), /rank: TOP_RANK,/);
});

test('ontop.js: started right after layers.js, moves the card above a modal dialog, restores focus', () => {
  const o = js('ontop.js');
  assert.match(app, /initLayers\(\{onEscape:relay\.resetEscape\}\);\n  initOntop\(\);/);
  assert.match(o, /const SURFACES = '#confirm, \.ajmodal\.elev, \.ajmodal\.lqr, \.ajmodal\.sout';/);
  assert.match(o, /d\.matches\(':modal'\)/);
  assert.match(o, /focusDraft\(prev, \{ preventScroll: true \}\)/);           // the composer: never pops the keyboard on touch
  assert.match(js('relay.js'), /el\("sheet"\)\.dataset\.card = shows;/);
  assert.match(js('relay.js'), /\(dialogOpen\(\) && topCard\(\) !== el\("sheet"\)\)/);   // ⌘↩ works for the card in front
});

test('「跟随 AI 编程工具」: the 4th button of the same picker, a red confirm, no_passkey in plain words', () => {
  const s = js('settings.js');
  assert.match(s, /const LEVELS = \['safe', 'standard', 'full', 'harness'\];/);
  assert.equal((html.match(/data-set-level="/g) || []).length, 4);
  assert.match(html, /<button type="button" data-set-level="harness" aria-pressed="false" data-i18n="p336\.harness">跟随 AI 编程工具<\/button><\/div>/);
  assert.match(s, /\|\| harness, goal = key === 'approval\.goal_nocard';/);    // red, like 全放行
  assert.match(s, /harness \? t\('p336\.note\.harness'\) \+ ' ' \+ t\('p551\.harnessSign'\)/);
  // P550: no passkey → the plain words + 「在本机设置 Face ID（需另一台批准）」 (P551 待拍板 3: every widening is ③)
  assert.match(s, /if \(m\.why === 'no_passkey'\) toastAction\(t\('p551\.noPasskey'\), t\('pkp\.setupHere'\), \(\) => \{ close\(\); H\.setupFaceId\?\.\(\); \}, 10000\);/);
  assert.match(read('../public/menu.css'), /#settings #set-level-row>\.segrow\{display:grid;grid-template-columns:1fr 1fr;/);
});

test('texts: both languages, the owner\'s words; full access now says Face ID', () => {
  for (const k of ['p336.harness', 'p336.note.harness', 'p551.harnessSign', 'p551.harnessYes', 'p551.noPasskey', 'p551.harnessName',
    'p551.unbindFa', 'p551.faUnbindTitle', 'p551.faUnbindText', 'p551.unbindNoPasskey']) {
    assert.ok(zh[k] && en[k], k);
    assert.ok(!/[一-鿿]/.test(en[k]), 'en has no Chinese: ' + k);
  }
  assert.equal(zh['p336.harness'], '跟随 AI 编程工具');
  assert.equal(en['p336.harness'], 'Follow AI tool');
  assert.equal(zh['p551.harnessName'], '跟随 AI 编程工具（Harness）');           // = the host's approval_level.NAME
  assert.equal(en['p551.harnessName'], 'Follow your AI coding tool (harness)');
  assert.equal(zh['p336.confirmTitle'].replace('{name}', zh['p551.harnessName']), '改成「跟随 AI 编程工具（Harness）」档？');
  assert.equal(zh['p551.harnessYes'], '改成跟随 AI 编程工具');
  assert.ok(zh['p336.note.harness'].startsWith('审批跟随底层 AI 编程工具（技术上叫 Harness）：Agent J 不再额外加审批卡（危险提示、档位、类别授权都不加）；') && zh['p336.note.harness'].endsWith('高手模式：你自己承担风险，换效率。'));
  assert.ok(en['p336.note.harness'].includes('(technically, a harness)'));
  assert.equal(zh['p551.unbindFa'], '用 Face ID 解绑');
  assert.ok(zh['p551.unbindNoPasskey'].startsWith('这台设备还没有设置 Face ID / 指纹钥匙。解绑属于不可逆操作，需要本人验证：'));
  assert.ok(zh['p551.noPasskey'].startsWith('这台设备还没有设置 Face ID / 指纹钥匙，不能打开这个模式。'));
  assert.ok(zh['p336.fullText'].includes('这几类仍然要你用 Face ID 一张张确认：疑似被攻击、改动或查看密码和密钥、花钱、改价、删除。'));
  assert.ok(en['p336.fullText'].includes('Face ID'));
  for (const d of [zh, en]) for (const k of ['p336.note.harness', 'p551.noPasskey']) assert.doesNotMatch(d[k], /试用|\btrial\b/i, k);
});

test('#318 ③ unbinding another remote / WeChat: the Face ID two-step, no_passkey in plain words', () => {
  const r = js('remotes.js'), w = js('wechat.js'), s = js('settings.js');
  assert.match(s, /export async function faceIdStep\(res, txt = \{\}\)/);
  assert.match(r, /export const FA_UNBIND = \{ title: 'p551\.faUnbindTitle', text: 'p551\.faUnbindText', yes: 'p551\.unbindFa' \};/);
  assert.match(r, /if \(w === 'no_passkey'\) return t\('p551\.unbindNoPasskey'\);/);
  // THIS remote unbinding itself: unchanged (plain signedWrite); another remote: signedWriteFa + 「用 Face ID 解绑」
  assert.match(r, /const res = self \? await signedWrite\('dev_unbind', \{ device: d\.id \}, \{ t: 'dev_unbind', device: d\.id \}\)\n\s+: await signedWriteFa\('dev_unbind', \{ device: d\.id \}, \{ t: 'dev_unbind', device: d\.id \}, faceIdUnbind\);/);
  assert.match(r, /t\(self \? 'rm\.unbind' : 'p551\.unbindFa'\), true\);/);
  // WeChat: only `unbind` goes through Face ID; setup / verify stay plain signed writes
  assert.match(w, /const r = op === 'unbind' \? await signedWriteFa\('wechat', target, \{t:'wechat', \.\.\.target\}, faceIdUnbind\)\n\s+: await signedWrite\('wechat', target, \{t:'wechat', \.\.\.target\}\);/);
});
