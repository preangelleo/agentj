#!/usr/bin/env node
// P412 (#174 / #175): the one-line message box, the full-screen editor, the terminal keys, the uncapped wide layout and the
// 「/」 list mirroring the harness. The real web client against fakehost.mjs in its own headless Chromium (never :9222).
// Desktop 1600 × 900 and 2560 × 1200 (mouse), phone 390 × 844 (touch, with and without a simulated soft keyboard).
// `node test/p412.mjs before` = shots only (from a tree without P412); no tag = shots + checks.
//   → reports/qa/p412/<tag>/<shot>.png
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { parityRecorder } from '../../parity/lib.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep, key, swipe, rect } from './browser.mjs';

const tag = process.argv[2] || 'after', CHECK = tag === 'after';
const OUT = process.env.P412_OUT || fileURLToPath(new URL(`../../../reports/qa/p412/${tag}/`, import.meta.url));
mkdirSync(OUT, { recursive: true });
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
const ev = (p, s) => evaluate(p, s);
let checks = 0;
// P417: features.json names these checks by their static words — record the label as written, plus the label without its
// measured tail 「 (…)」 and without a leading issue tag 「#177 ②: 」 (fail sticks; aliases like the node-test path/name pair).
const rec = parityRecorder('web/test/p412.mjs');
const caseNames = (label) => { const a = label.replace(/^#\d+[^:]*:\s*/, ''), cut = (s) => s.endsWith(')') && s.includes(' (') ? s.slice(0, s.indexOf(' (')) : s;
  return [...new Set([label, a, cut(label), cut(a)])]; };
const check = (v, label, also) => { if (process.env.P412_TRACE) console.error("check", label); if (!CHECK) return;
  for (const n of [...caseNames(label), ...(also ? [also] : [])]) rec.record(n, !!v); assert.ok(v, label); checks++; };
const PREFS = { appearance: { language: 'zh', theme: 'light' }, voice: { speak_replies: false, wake_enabled: false }, agent: { high_risk_warnings: false, session_mode: 'independent' } };
const CMDS = ['clear', 'compact', 'model', 'context', 'cost', 'usage', 'status', 'help', 'stop'];
// what serve's menu_get answers per harness (host harness_cmds.py; a /compact the host would never repeat is put in to prove
// the page shows a name once — Agent J's)
// #175 定稿: run (straight through) · agent (the Agent does it) · none (greyed, with the reason after 「 · 」)
const MENU = {
  claude: { kind: 'claude', native: [{ cmd: '/init', desc: '生成本项目的 CLAUDE.md', state: 'run' }, { cmd: '/compact', desc: 'dup', state: 'run' }, { cmd: '/mcp', desc: '管理 MCP 服务器', state: 'agent' }, { cmd: '/exit', desc: '退出 · 网页里不用退出：要停下这一轮用 /stop，要新对话用 /clear', state: 'none' }] },
  codex: { kind: 'codex', native: [{ cmd: '/approvals', desc: '审批方式', state: 'agent' }, { cmd: '/diff', desc: '看改动 diff', state: 'agent' }, { cmd: '/quit', desc: '退出 · 网页里不用退出：要停下这一轮用 /stop，要新对话用 /clear', state: 'none' }] },
  opencode: { kind: 'opencode', native: [{ cmd: '/sessions', desc: '会话列表', state: 'agent' }, { cmd: '/undo', desc: '撤销上一步', state: 'agent' }, { cmd: '/themes', desc: '主题 · 网页的外观在设置里换，或说「换个主题」', state: 'none' }] },
};
// a row as the page drew it: "/cmd" + "#run" / "#agent" (its tag) or "#tui" (greyed)
const ROW = (sel) => `[...document.querySelectorAll('${sel}')].map(b => [...b.querySelector('b').childNodes].filter(n => !n.classList?.contains('ntag')).map(n => n.textContent).join('') + (b.classList.contains('tui') ? '#tui' : b.querySelector('.ntag') ? '#' + b.querySelector('.ntag').dataset.via : ''))`;
const menuFor = (k) => ({ source: 'default', items: [{ cmd: '/goal', desc: '目标', group: '主 Agent' }], skills: k === 'claude' ? [{ cmd: '/review-pr', desc: '' }] : [], cmds: CMDS, ...MENU[k] });

async function paired(w, h, { touch = false, kind = 'claude', lang = 'zh' } = {}) {
  fake.reset();
  fake.st.prefs = structuredClone(PREFS);
  fake.st.prefs.appearance.language = lang;
  fake.st.menu = menuFor(kind);
  const p = await newPage(B, w, h, 'light', { touch, allow: [web.url, fake.relay + '/'] });
  await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: kind, name: 'Agent J' });
  await ev(p, `document.getElementById('phone-env-dismiss')?.click()`);
  await fake.addTurn({ k: 'phone', text: '输入框能不能做成像终端一样？' }, '收到。#174 拆成三块：终端快捷键继承、输入框固定一行、全屏编辑器。\n\n宽屏下这张卡片会一直跟着浏览器拉宽，您把窗口拉到多宽，阅读区就有多宽。', 'done');
  await sleep(600);
  return p;
}
const snap = async (p, n) => writeFileSync(`${OUT}/${n}.png`, await shoot(p, ''));
const IN = `document.getElementById('input')`, FS = `document.getElementById('fsText')`;
const setIn = (p, v) => ev(p, `(() => { const i = ${IN}; i.focus(); i.value = ${JSON.stringify(v)}; i.dispatchEvent(new Event('input')); i.setSelectionRange(i.value.length, i.value.length); })()`);
const fsOpen = (p) => ev(p, `!document.getElementById('fsEd').hidden`);
const insert = (p, text) => p.send('Input.insertText', { text });
const mouse = async (p, x, y, clickCount = 1) => {
  for (let c = 1; c <= clickCount; c++) {
    await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: c });
    await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: c });
  }
};
const DRAFT = '## 周报草稿\n\n本周：\n1. 输入框改单行\n2. 全屏编辑器出稿';

try {
  // ---------------------------------------------------------------- desktop 1600 × 900
  let p = await paired(1600, 900);
  const one = await ev(p, `${IN}.getBoundingClientRect().height`);
  await setIn(p, DRAFT);
  await sleep(200);
  let r = await ev(p, `({h: ${IN}.getBoundingClientRect().height, badge: document.getElementById('tFullN').hidden ? '' : document.getElementById('tFullN').textContent,
    last: document.getElementById('tools').lastElementChild.id, cols: getComputedStyle(document.getElementById('tools')).gridTemplateColumns.split(' ').length})`);
  check(Math.abs(r.h - one) < 1, `the box stays one line (${one} → ${r.h})`);
  check(r.badge === '5 行', `「5 行」 on the full-screen button (${r.badge})`);
  check(r.last === 'tFull' && r.cols === 5, 'the fifth tool is full screen');
  await snap(p, 'desktop-1600-oneline-badge');
  // the owner's reorder keeps full screen last
  await ev(p, `import('./js/toolorder.js').then(m => m.set(['mic', 'file', 'image', 'camera']))`);
  r = await ev(p, `[...document.getElementById('tools').children].map(n => n.id).join(' ')`);
  check(r === 'mic tDoc tPhoto tCamera tFull', `reorder keeps full screen last (${r})`);
  await ev(p, `import('./js/toolorder.js').then(m => m.set(['file', 'image', 'camera', 'mic']))`);
  // Ctrl+G from the box: full screen with the same words and caret
  await ev(p, `${IN}.focus(); ${IN}.setSelectionRange(5, 5)`);
  await key(p, 'g', { ctrl: true });
  await waitFor(p, `!document.getElementById('fsEd').hidden`, 3000);
  r = await ev(p, `({v: ${FS}.value, at: ${FS}.selectionStart, focus: document.activeElement.id, pos: document.getElementById('fsPos').textContent})`);
  check(r.v === DRAFT && r.at === 5 && r.focus === 'fsText', 'Ctrl+G opens full screen with the words and the caret');
  check(r.pos.includes('第 1 行') && r.pos.includes('共 5 行'), `position line (${r.pos})`);
  // Enter = new line (nothing sent); typing is mirrored into the box
  const says0 = fake.st.says.length;
  await ev(p, `${FS}.setSelectionRange(${FS}.value.length, ${FS}.value.length)`);
  await key(p, 'Enter', { text: '\r' }); await insert(p, '3. 底部 /命令 按钮把命令插到光标处');
  await sleep(150);
  r = await ev(p, `({fs: ${FS}.value, box: ${IN}.value})`);
  check(fake.st.says.length === says0, 'Enter in full screen sends nothing');
  check(r.fs.endsWith('\n3. 底部 /命令 按钮把命令插到光标处') && r.box === r.fs, 'Enter = new line, the box follows every keystroke');
  // terminal keys in the editor: Ctrl+A (line start), Ctrl+K (kill to end), Ctrl+Y (yank), Ctrl+D (delete next)
  await key(p, 'a', { ctrl: true });
  r = await ev(p, `(() => { const v = ${FS}.value, at = ${FS}.selectionStart; return at === v.lastIndexOf('\\n') + 1; })()`);
  check(r, 'Ctrl+A → start of the line');
  await key(p, 'd', { ctrl: true });
  r = await ev(p, `${FS}.value.split('\\n').at(-1)`);
  check(r === '. 底部 /命令 按钮把命令插到光标处', `Ctrl+D deletes the next character (${r})`);
  await key(p, 'k', { ctrl: true }); await key(p, 'y', { ctrl: true });
  r = await ev(p, `${FS}.value.split('\\n').at(-1)`);
  check(r === '. 底部 /命令 按钮把命令插到光标处', 'Ctrl+K then Ctrl+Y puts it back');
  await snap(p, 'desktop-1600-fullscreen');
  // 「/」: the list (harness commands greyed), filter, Enter inserts at the caret
  await ev(p, `${FS}.setSelectionRange(${FS}.value.length, ${FS}.value.length)`);
  await ev(p, `document.getElementById('fsSlash').click()`);
  await waitFor(p, `!document.getElementById('fsCmds').hidden`, 2000);
  r = await ev(p, ROW('#fsCmdList button'));
  check(r.includes('/compact') && r.filter((x) => x.startsWith('/compact')).length === 1 && !r.includes('/compact#run'), 'same name once — Agent J first');
  check(r.includes('/init#run') && r.includes('/mcp#agent') && r.includes('/exit#tui') && r.includes('/review-pr') && r.includes('/goal'), `harness commands with their route after Agent J's (${r.join(' ')})`);
  check(await ev(p, `document.querySelector('#fsCmdList .ntag[data-via=agent]').textContent === '交给 Agent 代办' && document.querySelector('#fsCmdList .ntag[data-via=run]').textContent === '直接执行'`), 'tags 直接执行 / 交给 Agent 代办');
  await ev(p, `document.getElementById('fsCmdList').scrollTop = 1e6`);
  await snap(p, 'desktop-1600-fullscreen-commands');
  await insert(p, 'goa'); await sleep(100);
  await key(p, 'Enter');
  await sleep(150);
  r = await ev(p, `({v: ${FS}.value, open: !document.getElementById('fsCmds').hidden, focus: document.activeElement.id})`);
  check(r.v.endsWith('光标处 /goal ') && !r.open && r.focus === 'fsText', `a command goes in at the caret (${JSON.stringify(r.v.slice(-12))})`);
  // #175 定稿: a harness command the Agent does goes in like any other; one no web page can do says why, nothing inserted
  await ev(p, `document.getElementById('fsSlash').click()`);
  await insert(p, 'mcp'); await sleep(100); await key(p, 'Enter'); await sleep(200);
  check((await ev(p, `${FS}.value`)).endsWith('/goal /mcp '), 'an 「交给 Agent 代办」 command is inserted at the caret');
  await ev(p, `(() => { const t = ${FS}; t.value = t.value.slice(0, -'/mcp '.length); t.dispatchEvent(new Event('input')); t.setSelectionRange(t.value.length, t.value.length); })()`);
  await ev(p, `document.getElementById('fsSlash').click()`);
  await insert(p, 'exit'); await sleep(100); await key(p, 'Enter'); await sleep(200);
  r = await ev(p, `({v: ${FS}.value, toast: document.getElementById('toast')?.textContent || ''})`);
  check(!r.v.includes('/exit') && r.toast.includes('/stop'), `a command no web page can do: its reason, nothing inserted (${r.toast})`, 'a command no web page can do');
  await key(p, 'Escape'); await sleep(100);
  check(await fsOpen(p) && await ev(p, `document.getElementById('fsCmds').hidden`), 'Esc closes the list first');
  // clear + undo (5 s)
  const before = await ev(p, `${FS}.value`);
  await ev(p, `document.getElementById('fsClear').click()`);
  r = await ev(p, `({fs: ${FS}.value, box: ${IN}.value})`);
  check(r.fs === '' && r.box === '', 'clear empties the editor and the box');
  await snap(p, 'desktop-1600-fullscreen-cleared');
  await ev(p, `document.querySelector('#toast .undo')?.click()`);
  await sleep(150);
  check(await ev(p, `${FS}.value`) === before, 'undo brings the words back');
  // double click on words selects, on blank space leaves
  const tr = await rect(p, '#fsText');
  await mouse(p, tr.left + 80, tr.top + 14, 2); await sleep(150);
  check(await fsOpen(p), 'a double click on words keeps full screen');
  await mouse(p, tr.left + tr.w - 60, tr.top + tr.h - 40, 2); await sleep(200);
  check(!await fsOpen(p), 'a double click on blank space leaves full screen');
  r = await ev(p, `({box: ${IN}.value, focus: document.activeElement.id})`);
  check(r.box === before && r.focus === 'input', 'the words are back in the box, the caret in it');
  // Esc leaves too; ⌘/Ctrl+Enter sends
  await key(p, 'g', { ctrl: true }); await waitFor(p, `!document.getElementById('fsEd').hidden`, 2000);
  await key(p, 'Escape'); await sleep(150);
  check(!await fsOpen(p) && await ev(p, `${IN}.value`) === before, 'Esc leaves, words kept');
  // #174 补 2: Ctrl+G again leaves; the grabber pulled down leaves (mouse), short of the threshold it springs back
  await key(p, 'g', { ctrl: true }); await waitFor(p, `!document.getElementById('fsEd').hidden`, 2000);
  await key(p, 'g', { ctrl: true }); await sleep(150);
  check(!await fsOpen(p), 'Ctrl+G again leaves full screen');
  await key(p, 'g', { ctrl: true }); await waitFor(p, `!document.getElementById('fsEd').hidden`, 2000);
  const gb = await rect(p, '#fsGrab');
  const drag = async (dy, steps = 8) => {
    const x = gb.left + gb.w / 2, y = gb.top + gb.h / 2;
    await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', buttons: 1, clickCount: 1 });
    for (let i = 1; i <= steps; i++) { await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y: y + dy * i / steps, button: 'left', buttons: 1 }); await sleep(30); }
    const mid = await ev(p, `document.getElementById('fsEd').style.transform`);
    await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y: y + dy, button: 'left', buttons: 0, clickCount: 1 });
    await sleep(320);
    return mid;
  };
  let mid = await drag(60);
  check(mid === 'translateY(60px)' && await fsOpen(p) && await ev(p, `!document.getElementById('fsEd').style.transform`), `a short pull follows the mouse, then springs back (${mid})`);
  await snap(p, 'desktop-1600-fullscreen-grabber');
  mid = await drag(300);
  check(!await fsOpen(p) && await ev(p, `${IN}.value`) === before, `pulled down past the threshold: leaves, words kept (${mid})`);
  await key(p, 'g', { ctrl: true }); await waitFor(p, `!document.getElementById('fsEd').hidden`, 2000);
  await ev(p, `document.getElementById('fsGrab').focus()`); await key(p, 'Enter'); await sleep(150);
  check(!await fsOpen(p), 'Enter on the focused grabber leaves (keyboard)');
  await key(p, 'g', { ctrl: true }); await waitFor(p, `!document.getElementById('fsEd').hidden`, 2000);
  await key(p, 'Enter', { ctrl: true });
  await waitFor(p, `document.getElementById('fsEd').hidden`, 2000);
  await sleep(600);
  check(fake.st.says.length === says0 + 1 && fake.st.says.at(-1).text === before, 'Ctrl+Enter sends the full text');
  check(await ev(p, `${IN}.value`) === '', 'the box is empty after sending');
  // message-box terminal keys: Ctrl+J, \ + Enter, Ctrl+D, Ctrl+S, Ctrl+R
  await setIn(p, 'abc');
  await key(p, 'j', { ctrl: true }); await insert(p, 'x\\');
  await key(p, 'Enter'); await sleep(100);
  r = await ev(p, `${IN}.value`);
  check(r === 'abc\nx\n' && fake.st.says.length === says0 + 1, `Ctrl+J and \\ + Enter add lines, nothing sent (${JSON.stringify(r)})`);
  await ev(p, `${IN}.setSelectionRange(0, 0)`); await key(p, 'd', { ctrl: true });
  check(await ev(p, `${IN}.value`) === 'bc\nx\n', 'Ctrl+D in the box');
  await key(p, 's', { ctrl: true }); await sleep(100);
  check(await ev(p, `${IN}.value`) === '', 'Ctrl+S puts the draft aside');
  await key(p, 's', { ctrl: true }); await sleep(100);
  check(await ev(p, `${IN}.value`) === 'bc\nx\n', 'Ctrl+S brings it back');
  await setIn(p, '周报'); await key(p, 'r', { ctrl: true }); await sleep(100);
  check(await ev(p, `${IN}.value`) === before, 'Ctrl+R finds the sent message containing the words');
  await setIn(p, '');
  // #175: the 「/」 type-ahead
  await setIn(p, '/'); await sleep(400);
  r = await ev(p, ROW('#sug button'));
  check(r.includes('/init#run') && r.includes('/mcp#agent') && r.includes('/exit#tui') && r.filter((x) => x.startsWith('/compact')).length === 1, `claude: harness commands in the list, /compact once (${r.length})`);
  await ev(p, `document.getElementById('sug').scrollTop = 1e6`);
  await snap(p, 'desktop-1600-slash-claude');
  await setIn(p, '/in'); await sleep(200);
  r = await ev(p, `(b => b && [...b.querySelector('b').childNodes].filter(n => !n.classList?.contains('ntag')).map(n => n.textContent).join(''))(document.querySelector('#sug button'))`);
  check(r === '/init', 'prefix completion finds the harness command');
  await key(p, 'Tab'); await sleep(150);
  check(await ev(p, `${IN}.value`) === '/init ', 'Tab completes a 「直接执行」 harness command');
  await setIn(p, '/ex'); await sleep(200); await key(p, 'Tab'); await sleep(150);
  check(await ev(p, `${IN}.value`) === '/ex', 'Tab on a greyed one inserts nothing');
  // typed and sent as is — the host decides (straight through / to the Agent); the page never rewrites it
  await setIn(p, '/mcp list'); await key(p, 'Enter'); await sleep(600);
  check(fake.st.says.at(-1)?.text === '/mcp list', 'a typed harness command is sent as typed');
  await setIn(p, '');
  // harness switch → the list follows
  fake.st.menu = menuFor('codex');
  await fake.send({ t: 'status', s: 'idle', agent: 'codex', name: 'Agent J' });
  await sleep(800);
  await setIn(p, ''); await setIn(p, '/'); await sleep(400);
  r = await ev(p, ROW('#sug button'));
  check(r.includes('/approvals#agent') && r.includes('/quit#tui') && !r.includes('/init#run') && !r.includes('/review-pr'), `codex after the switch (${r.join(' ')})`);
  await ev(p, `document.getElementById('sug').scrollTop = 1e6`);
  await snap(p, 'desktop-1600-slash-codex');
  fake.st.menu = menuFor('opencode');
  await fake.send({ t: 'status', s: 'idle', agent: 'opencode', name: 'Agent J' });
  await sleep(800);
  await setIn(p, ''); await ev(p, `document.getElementById('slashBtn').click()`); await sleep(600);
  r = await ev(p, ROW('#menuList button'));
  const grp = await ev(p, `[...document.querySelectorAll('#menuList .menugroup')].map(g => g.textContent).join(' | ')`);
  check(r.includes('/sessions#agent') && r.includes('/themes#tui') && !r.some((x) => x.startsWith('/approvals')) && grp.includes('OpenCode 自带命令'), `opencode in the ≡ sheet (${grp})`);
  await snap(p, 'desktop-1600-menu-opencode');
  await p.close?.();

  // ---------------------------------------------------------------- very wide: no 960 px cap
  p = await paired(2560, 1200);
  r = await ev(p, `({deck: document.getElementById('deck').getBoundingClientRect().width, comp: document.querySelector('.composer').getBoundingClientRect().width, vw: innerWidth})`);
  check(r.deck > 2300 && r.comp > 2300, `card and composer follow the window (${r.deck} / ${r.comp} of ${r.vw})`);
  await setIn(p, DRAFT);
  await snap(p, 'desktop-2560-wide');
  await p.close?.();

  // ---------------------------------------------------------------- phone 390 × 844 (touch)
  p = await paired(390, 844, { touch: true });
  await setIn(p, DRAFT); await ev(p, `${IN}.blur()`);
  await snap(p, 'phone-oneline-badge');
  const f = await rect(p, '.composer .field');
  await swipe(p, f.x, f.y, f.x + 4, f.y - 260, 16, 320);   // scrolls the box's lines to their end, then opens
  await waitFor(p, `!document.getElementById('fsEd').hidden`, 2000);
  check(true, 'a swipe up on the box opens full screen');
  await snap(p, 'phone-fullscreen-nokeyboard');
  // soft keyboard up: visualViewport 508 px tall → editor + footer above it
  await ev(p, `void Object.defineProperty(window,'visualViewport',{configurable:true,value:Object.assign(new EventTarget(),{offsetTop:0,offsetLeft:0,height:508,width:innerWidth,scale:1,pageTop:0})})`);
  await ev(p, `dispatchEvent(new Event('resize'))`); await sleep(150);
  r = await ev(p, `({h: document.getElementById('fsEd').getBoundingClientRect().height, send: document.getElementById('fsSend').getBoundingClientRect().bottom, slash: document.getElementById('fsSlash').getBoundingClientRect().bottom})`);
  check(Math.abs(r.h - 508) < 1 && r.send <= 508 && r.slash <= 508, `editor above the keyboard (${JSON.stringify(r)})`);
  await ev(p, `(() => { const k = document.createElement('div'); k.id = 'fakeKb'; k.style.cssText = 'position:fixed;left:0;right:0;bottom:0;height:336px;background:#1b1b1b;color:#858585;font:13px sans-serif;display:grid;place-items:center;z-index:99'; k.textContent = 'soft keyboard (simulated)'; document.body.append(k); })()`);
  await snap(p, 'phone-fullscreen-keyboard');
  await ev(p, `document.getElementById('fakeKb').remove(); delete window.visualViewport; dispatchEvent(new Event('resize'))`);
  // double tap on blank space leaves (touch), the words stay
  const tr2 = await rect(p, '#fsText');
  const bx = tr2.left + tr2.w - 40, by = tr2.top + tr2.h - 60;
  for (let i = 0; i < 2; i++) { await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: bx, y: by }] }); await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); await sleep(80); }
  await sleep(200);
  check(!await fsOpen(p) && await ev(p, `${IN}.value`) === DRAFT, 'a double tap on blank space leaves; words kept');
  await ev(p, `document.getElementById('tFull').click()`); await sleep(150);
  check(await fsOpen(p), 'the fifth button opens it on the phone');
  await ev(p, `document.getElementById('fsSlash').click()`); await sleep(200);
  await snap(p, 'phone-fullscreen-commands');
  await key(p, 'Escape'); await sleep(100);
  const pg = await rect(p, '#fsGrab');
  await swipe(p, pg.left + pg.w / 2, pg.top + pg.h / 2, pg.left + pg.w / 2, pg.top + 360, 12, 260);
  await sleep(350);
  check(!await fsOpen(p) && await ev(p, `${IN}.value`) === DRAFT, 'a finger pulling the grabber down leaves; words kept');
  await p.close?.();

  // ---------------------------------------------------------------- English
  p = await paired(1600, 900, { lang: 'en', kind: 'opencode' });
  await setIn(p, DRAFT);
  r = await ev(p, `document.getElementById('tFullN').textContent`);
  check(r === '5 lines', `English badge (${r})`);
  await key(p, 'g', { ctrl: true }); await waitFor(p, `!document.getElementById('fsEd').hidden`, 2000);
  r = await ev(p, `({pos: document.getElementById('fsPos').textContent, exit: document.getElementById('fsExit').getAttribute('aria-label')})`);
  check(r.pos.startsWith('Line ') && r.exit === 'Exit full screen (Esc)', `English editor (${r.pos})`);
  check(await ev(p, `document.getElementById('fsGrab').getAttribute('aria-label')`) === 'Pull down to close (leave full screen)', 'English grabber label');
  await snap(p, 'desktop-1600-fullscreen-en');
  await p.close?.();
  if (CHECK) console.log(`p412: ${checks} checks ok → ${OUT}`);
  else console.log(`p412 ${tag}: shots → ${OUT}`);
} finally {
  if (CHECK) rec.write();   // 'before' = shots only, nothing checked
  await B.close?.();
  await fake.close?.();
  await web.close?.();
}
process.exit(0);
