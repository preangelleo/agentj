// P332 (PROTOCOL §33) 紧急修复 — static rules: the two signed actions (same object text as the host's controls.py), the
// Settings order (Agent J first, 紧急修复 second; ③ = the moved P258 entry, gone from its old place), ≡ / `/reset`, the
// offline card's button + helper line, the rescue banner, both dictionaries (plain words, no internal terms), palette only.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as wire from '../../protocol/wire.js';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const html = read('../public/index.html');
const relay = read('../public/js/relay.js');
const rescue = read('../public/js/rescue.js');
const app = read('../public/app.js');
const menuCss = read('../public/menu.css');
const controlsPy = read('../../host/agentj/controls.py');
const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));
const src = JSON.parse(read('../i18n/web.zh.src.json')), ov = JSON.parse(read('../i18n/web.zh.override.json'));

test('§33 signed actions: reset {"what":"connection"} → "connection", rescue_do {"action"} → the action name (= controls.py)', async () => {
  assert.ok(wire.CONTROL_ACTIONS.includes('reset') && wire.CONTROL_ACTIONS.includes('rescue_do'));
  assert.equal(wire.controlObject('reset', { what: 'connection' }), 'connection');
  assert.equal(wire.controlObject('rescue_do', { action: 'help' }), 'help');
  const msg = new TextDecoder().decode(await wire.controlMessage('C'.repeat(22), 'D'.repeat(22), 'reset', '0'.repeat(32), 1, { what: 'connection' }));
  assert.match(msg, /^agentjarvis-control-v1\nC{22}\nD{22}\nreset\n0{32}\n1\n[0-9a-f]{64}$/);
  // the host signs the same text (host/agentj/controls.py object_text)
  assert.match(controlsPy, /ACTIONS \+= \("reset", "rescue_do"\)/);
  assert.match(controlsPy, /if action == "reset":[^\n]*\n\s+return str\(obj\["what"\]\)/);
  assert.match(controlsPy, /if action == "rescue_do":[^\n]*\n\s+return str\(obj\["action"\]\)/);
  // the page signs exactly these two
  assert.match(rescue, /signed\('reset', \{ what: 'connection' \}, \{ t: 'reset' \}\)/);
  assert.match(rescue, /signed\('rescue_do', \{ action: 'help' \}, \{ t: 'rescue_do', action: 'help' \}\)/);
  // ② offers (security review HIGH-1): only these three, only from runOffer (the owner's tap), never on arrival
  assert.match(rescue, /export const OFFERS = \['rollback', 'linger', 'new_session'\];/);
  assert.match(rescue, /: await signed\('rescue_do', \{ action \}, \{ t: 'rescue_do', action \}\);/);
  assert.match(rescue, /if \(!OFFERS\.includes\(name\) \|\| !F\.offers\.includes\(name\) \|\| F\.used\.includes\(name\)/);
  assert.equal([...rescue.matchAll(/start\('offer'/g)].length, 1, 'one place sends an offered repair');
  assert.match(rescue, /if \(OFFERS\.includes\(x\) && !F\.offers\.includes\(x\)\) F\.offers\.push\(x\)/);
});

test('② support advice carries the command line\'s caution (LOW-12); offer labels are Leo\'s words', () => {
  assert.equal(zh['p332.advice.caution'], '以下是客服的建议，不是命令：看不懂就不用照做。');
  assert.equal(en['p332.advice.caution'], "Support's advice, not orders: if unsure, you don't have to do it.");
  assert.equal(zh['p332.offer.rollback'], '退回上一个版本');
  assert.equal(zh['p332.offer.linger'], '让 Agent J 在没人登录时也运行');
  assert.equal(zh['p332.offer.new_session'], '开一个新对话（原来的对话会保留）');
  for (const n of ['rollback', 'linger', 'new_session']) for (const d of [zh, en]) assert.ok(d['p332.offer.' + n + 'Note'], n);
  assert.doesNotMatch(rescue, /innerHTML|insertAdjacentHTML/, 'support text is never parsed as HTML');
});

test('Settings: Agent J first, 紧急修复 second, three numbered cards ① ② ③; ③ is the moved AI login entry', () => {
  const sections = [...html.matchAll(/<section class="setcard[^"]*" id="(set-s-[a-z]+)"/g)].map((m) => m[1]);
  assert.deepEqual(sections.slice(0, 3), ['set-s-agent', 'set-s-rescue', 'set-s-tools']);
  const jumps = [...html.matchAll(/data-set-jump="(set-s-[a-z]+)"/g)].map((m) => m[1]);
  assert.deepEqual(jumps.slice(0, 2), ['set-s-agent', 'set-s-rescue']);
  const block = html.slice(html.indexOf('id="set-s-rescue"'), html.indexOf('id="set-s-tools"'));
  assert.deepEqual([...block.matchAll(/<li class="rscard" id="(rs-[a-z]+)"/g)].map((m) => m[1]), ['rs-reset', 'rs-help', 'rs-auth']);
  assert.ok(block.indexOf('id="set-auth-nav"') > block.indexOf('id="rs-auth"'), 'the P258 entry lives in card ③');
  const agent = html.slice(html.indexOf('id="set-s-agent"'), html.indexOf('id="set-s-rescue"'));
  assert.ok(!agent.includes('set-auth-nav'), 'its old place no longer shows it');
  assert.equal(html.split('id="set-auth-nav"').length, 2, 'one entry, not a copy');
});

test('≡ 重置连接 + /reset run ①; the offline card and the rescue banner open the block', () => {
  assert.match(relay, /item\("\/reset", t\('p332\.menuDesc'\), \{\}\)\.id = "resetEntry";/);
  assert.match(relay, /\[\["\/reset", t\('p332\.menuDesc'\), ""\]\]/, '/reset autocompletes in the / list');
  assert.match(relay, /if \(b\.id === "resetEntry"\)\{ closeMenu\(\); C\.resetConn\?\.\(\); return; \}/);
  assert.match(relay, /if \(\/\^\[\/\\uff0f\]reset\$\/i\.test\(text\.trim\(\)\)/);
  assert.match(app, /resetConn: rescue\.resetFromCommand/);
  assert.match(app, /\$\('offRescue'\)\.addEventListener\('click', \(\) => \{ offline\.later\(\); rescue\.openBlock\(\); \}\)/);
  assert.match(html, /<button id="offRescue" type="button" data-i18n="p332\.offBtn">/);
  assert.ok(!html.includes('id="offTip3"'), "P375: helper command moved to website, menu stays the first hop");
  assert.match(rescue, /rs-detail-help/);
  assert.match(html, /<div id="rescue-banner" class="setup-banner rsbanner" role="status" hidden>/);
  assert.match(app, /case 'rescue': rescue\.onRescue\(m\); return;/);
});

const KEYS = Object.keys(zh).filter((k) => k.startsWith('p332.'));
const vars = (s) => [...String(s).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(',');
const codes = (s) => [...String(s).matchAll(/`([^`]*)`/g)].map((m) => m[1]).join('|');
test('dictionaries: every p332 key in zh / src / override / en, same placeholders and commands, plain words', () => {
  assert.ok(KEYS.length >= 40, KEYS.length);
  for (const k of KEYS) {
    for (const [name, d] of [['en', en], ['src', src], ['override', ov]]) assert.equal(typeof d[k], 'string', `${name} ${k}`);
    assert.equal(vars(en[k]), vars(zh[k]), k);
    assert.equal(codes(en[k]), codes(zh[k]), k);
    assert.equal(ov[k], zh[k], `override wins: ${k}`);
    for (const s of [zh[k], en[k]]) {
      const words = s.replace(/`[^`]*`/g, '');          // a command may name things; the sentence may not
      assert.doesNotMatch(words, /\b(relay|serve|harness|session|watchdog|token)\b|会话|中继/i, `${k}: ${s}`);
      assert.doesNotMatch(words, /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u, `${k}: no emoji`);
    }
  }
  // Leo's own words
  assert.equal(zh['p332.title'], '紧急修复'); assert.equal(en['p332.title'], 'Emergency repair');
  assert.equal(zh['p332.reset.title'], '重置连接'); assert.equal(zh['p332.help.title'], '请 Agent J Father 远程帮忙');
  assert.equal(zh['p332.auth.title'], 'AI 账号登录失效'); assert.equal(zh['p332.ok'], '好了，可以回去聊天了');
  assert.equal(zh['p332.reset.fail'], 'Agent J 主机上的 Agent J 可能卡死了。请试试下面的第 ② 步。');
  assert.equal(zh['p332.banner'], 'Agent J 主机上的 Agent J 没响应，救援守护在线');
  assert.equal(zh['p332.help.first'].startsWith('建议先试第 ① 步'), true);
  assert.equal(zh['p332.offHelper'], '打开紧急恢复，按步骤检查连接。');
  // every §33 refusal has a plain sentence
  for (const w of ['shape', 'stale', 'no_key', 'bad_signature', 'replay', 'busy', 'unavailable']) assert.ok(zh['p332.why.' + w] && en['p332.why.' + w], w);
});

test('palette only: the 紧急修复 CSS names tokens, never a hex colour; green = Emerald Oasis', () => {
  const at = menuCss.indexOf('P332 · Settings → 紧急修复'), end = menuCss.indexOf('/* P344:', at);   // later tickets append after it
  const block = menuCss.slice(at, end > at ? end : undefined);
  assert.ok(block.length > 500);
  assert.doesNotMatch(block, /#[0-9a-f]{3,8}\b/i);
  assert.match(block, /\.rscard\[data-state="ok"\]\{background:var\(--emerald-oasis\)/);
  assert.match(block, /data-state="ok"\]\{[^}]*;color:var\(--c-lotion\)/, 'Lotion text on the green (P340 深底必白字)');
  assert.doesNotMatch(block, /margin:\s*\d+px|padding:\s*\d+px|gap:\s*\d+px/, 'spacing comes from the tokens (DESIGN_TOKENS.md)');
});
