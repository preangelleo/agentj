// P131 (Leo 10-11): the first-run checklist never blocks the chat — static guards on the page / banner code and its strings.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const SRC = readFileSync(new URL('../public/js/setup.js', import.meta.url), 'utf8');
const APP = readFileSync(new URL('../public/app.js', import.meta.url), 'utf8');
const HTML = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
const CSS = readFileSync(new URL('../public/app.css', import.meta.url), 'utf8');
const FIX = JSON.parse(readFileSync(new URL('./fixtures/p131-setup.json', import.meta.url), 'utf8'));
const zh = JSON.parse(readFileSync(new URL('../i18n/web.zh.json', import.meta.url), 'utf8'));
const en = JSON.parse(readFileSync(new URL('../i18n/web.en.json', import.meta.url), 'utf8'));

test('checklist is a page behind a closable banner, never a dialog over the chat', () => {
  const code = SRC.split('\n').filter((l) => !/^\s*\/\//.test(l)).join('\n');
  for (const bad of ['ajmodal', 'aria-modal', "role', 'dialog'", 'innerHTML', 'fetch(']) assert.ok(!code.includes(bad), bad);
  // 追加2: the only thing stored on the device is the owner's ✕ (a flag, never the snapshot)
  assert.deepEqual([...code.matchAll(/localStorage\.(\w+)\(([^,)]*)/g)].map((m) => m[1] + ' ' + m[2]).sort(),
    ['getItem HIDE_KEY', 'removeItem HIDE_KEY', 'setItem HIDE_KEY']);
  assert.match(SRC, /const show = !!S && !S\.closed && !hiddenHere\(\);/);
  assert.match(SRC, /if \(reopen\) hideHere\(false\);/);
  assert.ok(!/\.setupcard|ajmodal\.setup/.test(CSS), 'no modal CSS left');
  // a snapshot never opens the page; only the banner / the menu do
  assert.match(SRC, /export function card\(m\)[\s\S]*?render\(\);\n\}/);
  assert.ok(!/export function card\(m\)[\s\S]*?openPanel[\s\S]*?\n\}/.test(SRC.split('export function clear')[0]), 'card() never navigates');
  assert.match(SRC, /sendApp\(\{ t: 'setup_close', r: randHex\(8\) \}\)/);
  assert.match(SRC, /\{ open: true \}/, 'the menu reopens via setup_get open');
  assert.match(HTML, /id="setup-banner"[^>]*hidden/);
  assert.match(HTML, /id="setup-view" class="aj-flow view sub" hidden/);
  assert.match(HTML, /id="open-setup"/);
  assert.ok(APP.includes("setup: 'setup-view'") && APP.includes("$('open-setup').addEventListener('click', fromSettings(() => setupcard.open(true)));"));
  assert.match(CSS, /\.setup-banner__x\{[^}]*min-height:44px/);
  assert.match(CSS, /\.setup-sticky\{position:sticky/);
});

test('every row says where it is done; a failure says what, what it affects and what to tap', () => {
  assert.equal(zh.setup.sticky, '这些步骤在Agent J 主机上做；手机上看进度。卡住了直接在聊天里告诉我。');
  assert.deepEqual(zh.setup.where, { auto: '我来做', local: '在Agent J 主机上做', phone: '在手机上点', unsupported: '本版还不支持' });
  assert.equal(zh.setup.next.open_login, '在Agent J 主机上打开并登录');
  assert.match(zh.setup.act.opened, /请在刚弹到前面的 Agent J 浏览器里登录/);
  for (const k of ['banner', 'bannerClose', 'closeList', 'menu']) assert.equal(typeof en.setup[k], 'string', k);
  assert.ok(!JSON.stringify(zh.setup).includes('这一项没通过，只会暂停依赖它的事'), 'the ambiguous sentence is gone');
  const b = FIX.items.find((i) => i.id === 'browser');
  assert.deepEqual(b.explain.next, ['adopt_browser', 'install_browser']);
  assert.match(b.explain.fact.zh, /端口 9222/);
  assert.equal(FIX.items.find((i) => i.id === 'gmail').reason, 'needs_browser');
  assert.ok(Number.isInteger(FIX.progress.done) && Number.isInteger(FIX.progress.total));
  assert.ok(!/\/home\/|\/Users\/|AJ-[A-Z0-9]{4}-/.test(JSON.stringify(FIX)));
});
