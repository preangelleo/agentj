// 0.26.9 #463 (ADR-0269-w-term, contract §A): the full web terminal — the pure parts and the static rules, in node (web-unit).
// The browser half (a fake host, xterm on the real page, 390 + 1440) is test/p0269-term.mjs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const TERM = read('../public/js/terminal.js'), PTY = read('../public/js/termpty.js'), HOOK = read('../public/js/termhook.js');
const CSS = read('../public/terminal.css'), HTML = read('../public/index.html'), APP = read('../public/app.js'), SW = read('../public/sw.js');
const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));
const zhSrc = JSON.parse(read('../i18n/web.zh.src.json')), zhOv = JSON.parse(read('../i18n/web.zh.override.json'));
const code = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '').replace(/\s\/\/ .*$/gm, '');
/** `export function name(` … up to the closing brace at column 0, run in a vm → the function. */
function fn(src, name, ctx = {}) {
  const i = src.indexOf(`export function ${name}(`);
  assert.ok(i >= 0, name);
  const eol = src.indexOf('\n', i), line = src.slice(i, eol);
  const body = line.trimEnd().endsWith('}') ? line.slice(7) : src.slice(i + 7, src.indexOf('\n}\n', i) + 2);   // a one-line function or a block
  const box = { ...ctx };
  vm.runInNewContext(body + `\nglobalThis.${name} = ${name};`, box);
  return box[name];
}
function constFn(src, name) {
  const m = src.match(new RegExp(`export const ${name} = (\\([^)]*\\) => [^\\n]+);`));
  assert.ok(m, name);
  return vm.runInNewContext(m[1]);
}

test('sticky Ctrl / Alt: C0 for @ A–Z [ \\ ] ^ _ in either case, NUL, DEL; Alt = ESC prefix; pastes untouched', () => {
  const withMods = fn(PTY, 'withMods');
  assert.equal(withMods('c', { ctrl: true }), '\x03');
  assert.equal(withMods('C', { ctrl: true }), '\x03');
  assert.equal(withMods('d', { ctrl: true }), '\x04');
  assert.equal(withMods('z', { ctrl: true }), '\x1a');
  assert.equal(withMods('[', { ctrl: true }), '\x1b');
  assert.equal(withMods(' ', { ctrl: true }), '\x00');
  assert.equal(withMods('?', { ctrl: true }), '\x7f');
  assert.equal(withMods('x', { alt: true }), '\x1bx');
  assert.equal(withMods('b', { ctrl: true, alt: true }), '\x1b\x02');
  assert.equal(withMods('ls -la', { ctrl: true, alt: true }), 'ls -la', 'a paste / IME word is never modified');
  assert.equal(withMods('中', { ctrl: true }), '中');
  assert.equal(withMods('q', {}), 'q');
});

test('arrows: CSI normally, SS3 in application cursor mode, CSI 1;m with modifiers', () => {
  const arrowSeq = fn(PTY, 'arrowSeq');
  assert.equal(arrowSeq('A', false), '\x1b[A');
  assert.equal(arrowSeq('A', true), '\x1bOA');
  assert.equal(arrowSeq('D', true, { ctrl: true }), '\x1b[1;5D');
  assert.equal(arrowSeq('C', false, { alt: true }), '\x1b[1;3C');
  assert.equal(arrowSeq('B', false, { ctrl: true, alt: true }), '\x1b[1;7B');
});

test('the extra-keys bar: the ordered keys with the right bytes; sticky modifiers only for Ctrl / Alt', () => {
  const BAR = JSON.parse(JSON.stringify(vm.runInNewContext('(' + PTY.slice(PTY.indexOf('export const BAR = [') + 'export const BAR = '.length, PTY.indexOf('];', PTY.indexOf('export const BAR = [')) + 1) + ')')));
  assert.deepEqual(BAR.map((k) => k.id), ['paste', 'enter', 'esc', 'tab', 'ctrl', 'alt', 'up', 'down', 'left', 'right', 'intr', 'eof', 'susp', 'pipe', 'tilde', 'slash']);
  const by = Object.fromEntries(BAR.map((k) => [k.id, k]));
  assert.equal(by.esc.seq, '\x1b'); assert.equal(by.tab.seq, '\t');
  assert.equal(by.intr.seq, '\x03'); assert.equal(by.eof.seq, '\x04'); assert.equal(by.susp.seq, '\x1a');
  assert.deepEqual([by.pipe.ins, by.tilde.ins, by.slash.ins], ['|', '~', '/']);
  assert.deepEqual(BAR.filter((k) => k.mod).map((k) => k.mod), ['ctrl', 'alt']);
  assert.deepEqual([by.up.arrow, by.down.arrow, by.left.arrow, by.right.arrow], ['A', 'B', 'D', 'C']);
  assert.match(PTY, /b\.addEventListener\('pointerdown', \(e\) => \{ if \(e\.pointerType !== 'mouse'\) e\.preventDefault\(\); \}\);/, 'a bar tap keeps the soft keyboard where it is');
  assert.match(CSS, /\.term-xkey\{/); assert.match(CSS, /\.term-key\{flex:none;min-width:44px;min-height:44px;/, 'the bar reuses the ≥ 44 px key');
});

test('term_in frames: ≤ 8 KiB UTF-8, cut between code points; ≤ 64 KiB/s; size clamp 20..400 × 5..200', () => {
  const utf8Len = fn(TERM, 'utf8Len');
  const cut0 = fn(TERM, 'cut', { utf8Len }), cut = (a, b) => [...cut0(a, b)];   // vm arrays → this realm
  assert.equal(utf8Len('a中😀'), 1 + 3 + 4);
  assert.deepEqual(cut('abc', 2), ['ab', 'c']);
  assert.deepEqual(cut('中中', 4), ['中', '中'], 'never half a character');
  assert.deepEqual(cut('😀x', 3), ['', '😀x']);
  const big = 'x'.repeat(9000) + '中'.repeat(3000);
  let rest = big, parts = [];
  while (rest) { const [a, b] = cut(rest, 8192); assert.ok(a.length > 0); parts.push(a); rest = b; }
  assert.ok(parts.every((p) => utf8Len(p) <= 8192)); assert.equal(parts.join(''), big);
  assert.match(TERM, /export const PTY_FRAME = 8192, PTY_RATE = 65536, PTY_QUEUE = 1 << 20;/);
  assert.match(TERM, /const \[part, rest\] = cut\(S\.inQ\[0\], Math\.min\(PTY_FRAME, Math\.floor\(S\.budget\)\)\);/, 'a frame never exceeds the cap or the bucket');
  assert.match(TERM, /send\(\{ t: 'term_in', id: S\.id, seq: \+\+S\.inSeq, d: part \}\);/);
  const clamp0 = constFn(TERM, 'clampSize'), clampSize = (a, b) => ({ ...clamp0(a, b) });
  assert.deepEqual(clampSize(5, 1), { cols: 20, rows: 5 });
  assert.deepEqual(clampSize(9999, 9999), { cols: 400, rows: 200 });
  assert.deepEqual(clampSize(80.7, 24.2), { cols: 80, rows: 24 });
  assert.deepEqual(clampSize(NaN, undefined), { cols: 80, rows: 24 });
  assert.match(TERM, /send\(\{ t: 'term_resize', id: S\.id, cols: z\.cols, rows: z\.rows \}\);/);
});

test('open: only with "pty" in the host block; signed over {"mode":"pty"} with the size; a 「!」 line stays line mode; first enable can use full PTY', () => {
  assert.match(HOOK, /pty: Array\.isArray\(x\.modes\) && x\.modes\.includes\('pty'\)/);
  assert.match(TERM, /S\.pty = !!info\?\.pty && !o\.bang;/);
  assert.match(TERM, /const obj = \{ mode: S\.enable \? 'enable' : S\.pty \? 'pty' : 'line', \.\.\.\(S\.resume \? \{resume: S\.resume\} : \{\}\) \};/);
  assert.match(TERM, /S\.pty \? \{ t: 'term_open', \.\.\.ptyOpen\(\) \}/, 'step 1 names the mode and the size');
  assert.match(TERM, /function ptyOpen\(\) \{ const g = S\.size \|\| ptyMod\?\.guess\(\) \|\| \{ cols: 80, rows: 24 \}; return \{ mode: 'pty', \.\.\.clampSize\(g\.cols, g\.rows\), \.\.\.\(S\.resume \? \{resume: S\.resume\} : \{\}\) \}; \}/);
  // Loading errors stop the full terminal rather than silently switching input models.
  assert.match(TERM, /closeSheet\(\); abandon\(\); toast\(t\('terminal\.loadFailed'\)/);
  assert.match(TERM, /!o\.bang && !info\?\.pty/);
});

test('lazy + ours only: termpty.js loads by a module script from terminal.js; xterm only from vendor/; nothing on first paint', () => {
  assert.match(TERM, /s\.type = 'module'; s\.src = new URL\('\.\/termpty\.js', import\.meta\.url\)\.href;/);
  assert.match(PTY, /^import \{ Terminal \} from '\.\.\/vendor\/xterm\/xterm\.mjs';$/m);
  assert.match(PTY, /^import \{ FitAddon \} from '\.\.\/vendor\/xterm\/addon-fit\.mjs';$/m);
  assert.match(PTY, /l\.href = new URL\('\.\.\/vendor\/xterm\/xterm\.css', import\.meta\.url\)\.href;/);
  for (const [name, src] of [['index.html', HTML], ['app.js', APP], ['sw.js', SW], ['termhook.js', HOOK]]) assert.doesNotMatch(src, /termpty|xterm/, name + ' never loads the full terminal');
  for (const src of [TERM, PTY]) assert.doesNotMatch(code(src), /localStorage|sessionStorage|indexedDB|innerHTML|insertAdjacentHTML|outerHTML|document\.write|console\.|\bimport\s*\(|\bfetch\s*\(|https?:\/\/(?!agentj\.app)/);
  // no web-links addon (a plain URL in the output is never a link); OSC 8 hyperlinks go only through our https-only handler
  // (0.26.9 security L-2: xterm's default is confirm() + window.open of any http(s) link — js/termlink.js, test/p0269-w2.test.mjs)
  assert.doesNotMatch(PTY, /addon-web-links|registerLinkProvider/);
  assert.equal((PTY.match(/linkHandler/g) || []).length, 3, 'one import, one option: linkHandler: linkHandler()');
  assert.match(PTY, /import \{ linkHandler \} from '\.\/termlink\.js';/);
});

test('Esc / Tab inside the terminal belong to the program (window capture, before the page layers); Ctrl+D / Ctrl+Z only in the PTY module', () => {
  assert.match(PTY, /addEventListener\('keydown', keys, true\);/);
  assert.match(PTY, /if \(e\.isComposing \|\| e\.ctrlKey \|\| e\.metaKey \|\| !host\.contains\(e\.target\)\) return;/, 'only inside the terminal; outside, Esc still collapses the page');
  assert.match(PTY, /e\.preventDefault\(\); e\.stopImmediatePropagation\(\);/);
  assert.match(PTY, /removeEventListener\('keydown', keys, true\);/, 'gone with the terminal');
  assert.doesNotMatch(code(TERM), /Ctrl\+D|Ctrl\+Z|'eof'|'susp'/, 'line mode still has no EOF / suspend (P408 N3)');
});

test('confirm in the PTY: the same signed term_line; 取消 / expiry → Ctrl+U; no note drawn into the program screen', () => {
  assert.match(TERM, /onNo: \(\) => \{ out\(t\('p408\.notRun'\) \+ '\\n'\); notRun\(m\.seq\); ptyRefuse\(\);/);
  assert.match(TERM, /onExpire: \(\) => \{ out\(t\('p408\.expired'\) \+ '\\n'\); notRun\(m\.seq\); ptyRefuse\(true\);/);
  assert.match(TERM, /send\(\{ t: 'term_in', id: S\.id, seq: \+\+S\.inSeq, d: '\\u0015' \}\);/);
  assert.match(TERM, /if \(S\.px\) return;   \/\/ 0\.26\.9: never drawn into the program's screen/);
  assert.match(TERM, /if \(m\.t === 'term_out' && S\.pty\) \{/);
  assert.match(TERM, /const d = typeof m\.d === 'string' \? m\.d : typeof m\.data === 'string' \? m\.data : '';/, 'contract §A field `d` (the line-mode `data` accepted too)');
  // the Agent's line / a prefill is typed in only on the owner's tap, never with Enter
  assert.match(TERM, /if \(S\.pty\) \{ S\.go = g; paintGo\(\); return; \}/);
  assert.match(TERM, /if \(S\.pty\) \{ ptyType\(g\.cmd\); return; \}/);
  assert.match(PTY, /type: \(s\) => term\.paste\(String\(s\)\)/, 'a paste (bracketed when the program asks) — no Enter');
  assert.match(TERM, /ptyDrop\(\);   \/\/ 0\.26\.9 #463: the xterm buffer/, 'wiped with the session');
  assert.match(PTY, /term\.clear\(\); term\.reset\(\);/);
});

test('theme: the closed palette only; every ANSI foreground ≥ 4.5:1 on Noir (black is a ground); CSS keeps the P414 colour set', () => {
  const THEME = vm.runInNewContext('(' + PTY.slice(PTY.indexOf('export const THEME = {') + 'export const THEME = '.length, PTY.indexOf('};', PTY.indexOf('export const THEME = {')) + 1) + ')');
  const palette = new Set(['#faf9f5', '#f0eee6', '#e3dacc', '#858585', '#2c2b26', '#141414', '#d97757', '#6a9bcc', '#827dbd', '#629887', '#ebc9b7', '#c7dedc', '#cacadb',
    '#ffe292', '#f7827e', '#fcbe7c', '#f7e17c', '#b7e396', '#78dedc', '#71bbde', '#d1a7e8', '#f0aad4', '#dbb293', '#fbc8c6', '#fee2c5', '#fbf2c5', '#dff3d1', '#c4f0f0',
    '#c1e1f0', '#ebd8f5', '#f8daec', '#efddcf', '#f2f2f2', '#ededed', '#e5e5e5', '#acacac', '#888888']);
  const lum = (h) => { const c = [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)); return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]; };
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
  for (const [k, v] of Object.entries(THEME)) {
    if (v.startsWith('#')) assert.ok(palette.has(v), `${k} ${v} in the palette`);
    else { const [r, g, b] = v.match(/\d+/g).map(Number); const h = '#' + [r, g, b].map((x) => x.toString(16).padStart(2, '0')).join(''); assert.ok(palette.has(h), `${k} ${v} alpha of a palette colour`); }
  }
  for (const k of ['foreground', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white', 'brightBlack', 'brightRed', 'brightGreen', 'brightYellow', 'brightBlue', 'brightMagenta', 'brightCyan', 'brightWhite'])
    assert.ok(ratio(THEME[k], THEME.background) >= 4.5, `${k} ${THEME[k]} on Noir ${ratio(THEME[k], THEME.background).toFixed(2)}`);
  assert.equal(THEME.background, '#2c2b26');
  const hexes = [...new Set([...CSS.matchAll(/#[0-9a-f]{6}\b/gi)].map((m) => m[0].toLowerCase()))].sort();
  assert.deepEqual(hexes, ['#141414', '#2c2b26', '#71bbde', '#858585', '#f7827e', '#faf9f5', '#ffe292']);
});

test('dictionaries: every pty.* key in zh / en / zh source / zh override; zh = the override; plain words, no trial words', () => {
  const used = new Set();
  for (const src of [TERM, PTY]) for (const m of src.matchAll(/t\('(pty\.[A-Za-z_.]+)'/g)) if (!m[1].endsWith('.')) used.add(m[1]);
  for (const m of PTY.matchAll(/\{ id: '(\w+)', label:/g)) used.add('pty.k.' + m[1]);
  for (const k of ['pty.ownChip', 'pty.goChip']) used.add(k);
  assert.ok(used.size >= 20, 'found ' + used.size);
  for (const k of used) {
    for (const [name, d] of [['zh', zh], ['en', en], ['zh.src', zhSrc], ['zh.override', zhOv]]) assert.ok(typeof d[k] === 'string' && d[k].length > 1, `${name} ${k}`);
    assert.equal(zh[k], zhOv[k], 'zh written by hand ' + k);
    assert.doesNotMatch(en[k], /[㐀-鿿]/, 'en has no Chinese ' + k);
  }
  const vals = [...used].flatMap((k) => [zh[k], en[k]]).join('\n');
  assert.doesNotMatch(vals, /试用|trial|relay|harness|\bPTY\b|Noise|握手|xterm/i);
  for (const k of ['pty.goChip', 'pty.ownChip']) { assert.match(zh[k], /\{cmd\}/); assert.match(en[k], /\{cmd\}/); }
  for (const k of ['pty.k.pipe', 'pty.k.tilde', 'pty.k.slash']) { assert.match(zh[k], /\{c\}/); assert.match(en[k], /\{c\}/); }
  assert.match(zh['pty.lineOnly'], /vim、top、claude/);
});
