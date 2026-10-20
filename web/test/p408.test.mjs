// P408 (PROTOCOL §38) the web terminal — static rules: dictionaries complete in zh and en; the page never sends a line with a
// control character (TAB included); keys are int / quit only; the signed object texts are the host's (controls.py) in both
// wire copies; the login card is the §11 seal with kind `login`; the terminal keeps nothing (no storage, no history data, no
// HTML); the page module stays out of the first screen (loaded by termhook.js as a module script); the row sits in 安全 and
// cannot switch the terminal on. Rendering and the real flow: p408.mjs (real browser, real Noise, virtual authenticator).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as wire from '../../protocol/wire.js';
import * as pageWire from '../public/proto/wire.js';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const TERM = read('../public/js/terminal.js'), HOOK = read('../public/js/termhook.js'), CMD = read('../public/js/cmdrun.js');
const APP = read('../public/app.js'), SET = read('../public/js/settings.js'), HTML = read('../public/index.html'), CSS = read('../public/terminal.css');
const controlsPy = read('../../host/agentj/controls.py'), elevatePy = read('../../host/agentj/elevate.py');
const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));
const zhSrc = JSON.parse(read('../i18n/web.zh.src.json')), zhOv = JSON.parse(read('../i18n/web.zh.override.json'));
const code = (s) => s.replace(/\/\/.*$/gm, '').replace(/\/\*[\s\S]*?\*\//g, '');

test('dictionaries: every p408 key the code uses exists in zh and en (and the zh source / override)', () => {
  const used = new Set();
  for (const src of [TERM, HOOK, CMD]) for (const m of src.matchAll(/t\('(p408\.[A-Za-z_.]+)'/g)) if (!m[1].endsWith('.')) used.add(m[1]);
  for (const m of HTML.matchAll(/data-i18n="(p408\.[A-Za-z_.]+)"/g)) used.add(m[1]);
  for (const k of ['prf', 'local', 'session', 'none']) used.add('p408.mode.' + k);
  const close = TERM.match(/const CLOSE_WHY = \[([^\]]+)\]/)[1].match(/'(\w+)'/g).map((x) => x.slice(1, -1));
  const open = TERM.match(/const OPEN_WHY = \[([^\]]+)\]/)[1].match(/'(\w+)'/g).map((x) => x.slice(1, -1));
  assert.deepEqual(close, ['owner', 'timeout', 'idle', 'background', 'relay_lost', 'estop', 'host', 'disabled'], '§38 close reasons');
  for (const w of ['disabled', 'os', 'device_not_allowed', 'web_not_allowed', 'no_passkey', 'passkey_failed', 'stale', 'replay', 'bad_signature', 'busy', 'estop', 'locked', 'bad_password', 'cancelled', 'shape'])
    assert.ok(open.includes(w), '§38 open refusal ' + w);
  for (const w of close) used.add('p408.why.' + w);
  for (const w of open) used.add('p408.no.' + w);
  assert.ok(used.size > 60, 'the scan found the keys');
  for (const k of used) {
    for (const [name, d] of [['zh', zh], ['en', en], ['zh.src', zhSrc], ['zh.override', zhOv]]) assert.ok(typeof d[k] === 'string' && d[k].length > 1, `${name} ${k}`);
    assert.equal(zh[k], zhOv[k], 'zh = the override (written by hand, no model call) ' + k);
    assert.doesNotMatch(en[k], /[㐀-鿿]/, 'en has no Chinese ' + k);
  }
  const vals = Object.entries(zh).filter(([k]) => k.startsWith('p408.')).map(([, v]) => v).join('\n') + Object.entries(en).filter(([k]) => k.startsWith('p408.')).map(([, v]) => v).join('\n');
  assert.doesNotMatch(vals, /试用|trial|relay|harness|PTY|Noise|握手/i, 'plain words only');
});

test('a line with a control character (TAB, CR, LF, ESC, DEL, C1, U+2028/9) is never sent; ≤ 4096 bytes', () => {
  const src = TERM.match(/export const CTRL = \/(.+)\/;/)[1];
  const CTRL = new RegExp(src);
  for (const bad of ['a\tb', 'a\nb', 'a\rb', '\x1b[A', 'x\x7f', '\x00', '\x85', 'a b', 'a b', '\x03', '\x04', '\x1a', '\x1c']) assert.ok(CTRL.test(bad), JSON.stringify(bad));
  for (const ok of ['ls -la | grep x ~/a/b', 'echo "你好" && cd -', '', 'sudo apt install htop', '😀']) assert.ok(!CTRL.test(ok), JSON.stringify(ok));
  const send = TERM.slice(TERM.indexOf('function sendLine()'), TERM.indexOf('function badLine('));
  assert.ok(send.indexOf('CTRL.test(line)') > 0 && send.indexOf('CTRL.test(line)') < send.indexOf("t: 'term_line'"), 'checked before it is sent');
  assert.ok(send.indexOf('LINE_MAX') > 0 && send.indexOf('LINE_MAX') < send.indexOf("t: 'term_line'"), 'byte limit before it is sent');
  assert.match(TERM, /const LINE_MAX = 4096/);
  assert.match(send, /send\(\{ t: 'term_line', id: S\.id, seq, line \}\)/, 'term_line {id, seq, line}');
  assert.equal([...code(TERM).matchAll(/t: 'term_line'/g)].length, 2, 'two places send a line: the plain one and the confirmed one');
  assert.match(TERM, /send\(\{ t: 'term_line', id: S\.id, seq: m\.seq, line, \.\.\.signed, fa \}\)/, 'the confirmed line = the line the host showed');
  assert.match(TERM, /i\.value = String\(v\)\.replace\(CTRL, ' '\)/, 'a prefilled command cannot carry one either');
});

test('keys: Ctrl+C → int and Ctrl+\\ → quit only (no Ctrl+D, no Ctrl+Z, no TAB key)', () => {
  assert.match(TERM, /export const KEYS = \{ int: 'Ctrl\+C', quit: 'Ctrl\+\\\\' \};/);
  assert.match(TERM, /function sendKey\(key\) \{ if \(S\.phase === 'ready' && Object\.hasOwn\(KEYS, key\)\) send\(\{ t: 'term_keys', id: S\.id, key \}\); \}/);
  assert.equal([...code(TERM).matchAll(/t: 'term_keys'/g)].length, 1);
  assert.doesNotMatch(code(TERM), /'eof'|'susp'|Ctrl\+D|Ctrl\+Z|'Tab'/);
  assert.match(TERM, /const INSERT = \['\|', '~', '\/', '-'\];/, 'the hard-to-type characters only insert text');
});

test('§38 signed object texts = the host (controls.py object_text), in both wire copies; the open is over the HOST nonce', async () => {
  for (const w of [wire, pageWire]) {
    for (const a of ['term_open', 'term_line', 'term_sudo', 'term_forget']) assert.ok(w.CONTROL_ACTIONS.includes(a), a);
    assert.equal(w.controlObject('term_open', { mode: 'line' }), 'line');
    assert.equal(w.controlObject('term_line', { id: '0123456789abcdef', line: 'rm -rf build' }), '0123456789abcdef\nrm -rf build');
    assert.equal(w.controlObject('term_sudo', { id: '0123456789abcdef', rid: 'r1' }), '0123456789abcdef\nr1');
    assert.equal(w.controlObject('term_forget', {}), 'forget');
    assert.deepEqual(w.elevateFields({ kind: 'login', term: 'abcd' }), ['abcd'], 'login card: the pending term_open r');
    assert.match(await w.elevateDigest('login', ['abcd']), /^[0-9a-f]{64}$/);
  }
  assert.match(controlsPy, /ACTIONS \+= \("term_open", "term_line", "term_sudo", "term_forget"\)/);
  assert.match(controlsPy, /if action == "term_open":[^\n]*\n\s+return str\(obj\["mode"\]\)/);
  assert.match(controlsPy, /if action == "term_line":[^\n]*\n\s+return f"\{obj\['id'\]\}\\n\{obj\['line'\]\}"/);
  assert.match(controlsPy, /if action == "term_sudo":[^\n]*\n\s+return f"\{obj\['id'\]\}\\n\{obj\['rid'\]\}"/);
  assert.match(controlsPy, /if action == "term_forget":[^\n]*\n\s+return "forget"/);
  assert.match(elevatePy, /if card\["kind"\] == "login":\n\s+return \[card\["term"\]\]/, 'host hashes the same login field');
  const msg = new TextDecoder().decode(await wire.controlMessage('C'.repeat(22), 'D'.repeat(22), 'term_open', 'a'.repeat(32), 1, { mode: 'line' }));
  assert.match(msg, /^agentjarvis-control-v1\nC{22}\nD{22}\nterm_open\na{32}\n1\n[0-9a-f]{64}$/);
  assert.match(TERM, /signCtl\('term_open', obj, c\.n\)/, 'the host nonce from step 1');
  assert.match(TERM, /const obj = \{ mode: 'line' \};/);
  assert.match(TERM, /signCtl\('term_line', \{ id: S\.id, line \}, m\.n\)/);
  assert.match(TERM, /const obj = \{ id: S\.id, rid \};\n\s+const signed = await signCtl\('term_sudo', obj, m\.n\);/);
  assert.match(HOOK, /signedWrite\('term_forget', \{\}, \{ t: 'term_forget' \}\)/, 'forget: signed, no Face ID');
  // PRF: WebAuthn prf eval.first = the host salt; sealed with the §11 seal, kind term_prf, id = nonce = n; zeroed
  assert.match(TERM, /publicKey\.extensions = \{ prf: \{ eval: \{ first: salt \} \} \};/);
  assert.match(TERM, /sealValue\(unb64u\(epk\), prf, channel\(\), await myDeviceId\(\), n, 'term_prf', n, digest\)/);
  assert.match(TERM, /\} finally \{ prf\.fill\(0\); \}/);
  assert.match(TERM, /\{ t: 'term_open', \.\.\.signed, fa, \.\.\.sealed \}/);
  // the login password: sealed + signed exactly like a sudo card (kind login), the bytes zeroed, the field cleared
  assert.match(TERM, /sealValue\(c\.epk, value, ch, dev, c\.id, 'login', c\.n, c\.digest\)/);
  assert.match(TERM, /elevateMessage\(ch, dev, c\.id, 'login', ok \? 'allow' : 'deny', c\.n, ts, c\.digest, ctSha\)/);
  assert.match(TERM, /\} finally \{ value\?\.fill\(0\); \}/);
});

test('Face ID starts inside the tap (no await before get()); no passkey → the 「设置 Face ID」 offer, no fallback', () => {
  for (const f of ['function introYes()', "onYes: () => {\n      let p;\n      try { p = assertFa(m.fa_ch, S.fa, null)", "try { p = assertFa(m.fa_ch, S.fa, salt)"]) assert.ok(TERM.includes(f), f);
  const intro = TERM.slice(TERM.indexOf('function introYes()'), TERM.indexOf('function openStep2('));
  assert.ok(intro.indexOf('assertFa(') < intro.indexOf('await'), 'assertion before the first await');
  assert.doesNotMatch(intro.slice(0, intro.indexOf('assertFa(')), /await|async/);
  const login = TERM.slice(TERM.indexOf('function loginAnswer('), TERM.indexOf('// ----', TERM.indexOf('function loginAnswer(')));
  assert.ok(login.indexOf('approve(c.faCh, c.fa)') < login.indexOf('await'), 'login: Face ID before any await');
  assert.match(TERM, /function noPasskey\(\) \{\n\s+if \(phoneConfirmation\(true\)\) return;\n\s+toastAction\(t\('p408\.no\.no_passkey'\), t\('sout\.setup'\), \(\) => H\.setupFaceId\(\), 8000\);/);
  assert.match(TERM, /if \(why === 'no_passkey'\) \{ noPasskey\(\); return; \}/);
});

test('the terminal keeps nothing: no storage, no history data, no HTML, no logging; output as text, wiped on close', () => {
  for (const src of [TERM, HOOK]) {
    const c = code(src);
    assert.doesNotMatch(c, /localStorage|sessionStorage|indexedDB|dbPut|putSealed|caches\.|innerHTML|insertAdjacentHTML|outerHTML|document\.write|console\.|\bimport\s*\(|\bfetch\s*\(/);
  }
  assert.deepEqual([...code(TERM).matchAll(/history\.pushState\(([^;]+)\);/g)].map((m) => m[1]), ["{ ...(history.state || {}), ajTerm: 1 }, ''", "{ ...(history.state || {}), ajTerm: 1 }, ''"], 'the history entry carries a flag only');
  assert.match(TERM, /done_\.appendData\(flushed\)/, 'output = text nodes');
  assert.match(TERM, /function wipe\(\) \{\n\s+const out = \$\('term-out'\);\n\s+out\.firstChild\.data = ''; out\.lastChild\.textContent = '';/);
  const closed = TERM.slice(TERM.indexOf('function closed('), TERM.indexOf('function dropped('));
  assert.match(closed, /wipe\(\);/, 'closed → wiped');
  assert.match(TERM.slice(TERM.indexOf('function dropped('), TERM.indexOf('function onTick(')), /wipe\(\);/, 'dropped → wiped');
  assert.match(TERM, /if \(gen\(\) !== S\.G\) return dropped\(\);/, 'a new Noise generation = the terminal is gone (never pretend)');
  assert.match(APP, /termhook\.gone\(\);/);
  assert.match(TERM, /HIDDEN_MS = 60000/);
  assert.match(TERM, /send\(\{ t: 'term_close', id: S\.id, why: 'background' \}\); closed\('background'\);/, 'hidden 60 s → term_close');
});

test('lazy: the page module and its CSS stay out of the first screen; routing; the login card never reaches the chat', () => {
  assert.doesNotMatch(code(APP) + code(SET) + code(CMD), /terminal\.js|terminal\.css/);
  assert.doesNotMatch(HTML, /(?:src|href)="[^"]*terminal\.(js|css)/);
  assert.match(HOOK, /s\.type = 'module'; s\.src = new URL\('\.\/terminal\.js', import\.meta\.url\)\.href;/);
  assert.match(TERM, /l\.href = new URL\('\.\.\/terminal\.css', import\.meta\.url\)\.href;/);
  assert.match(APP, /case 'elev': if \(m\.kind === 'login'\) termhook\.msg\(m\); else elevate\.add\(m\); return;/);
  assert.match(APP, /case 'term_out': case 'term_confirm': case 'term_sudo': case 'term_close': termhook\.msg\(m\); return;/);
  assert.match(TERM, /if \(S\.phase !== 'opening' \|\| !S\.r2 \|\| m\.term !== S\.r2 \|\| m\.state === 'running'\) return;/, 'only this device\'s own pending open');
  // colours: the closed palette only
  const hexes = [...new Set([...CSS.matchAll(/#[0-9a-f]{6}\b/gi)].map((m) => m[0].toLowerCase()))].sort();
  assert.deepEqual(hexes, ['#141414', '#2c2b26', '#71bbde', '#858585', '#f7827e', '#faf9f5', '#ffe292']);
  for (const m of CSS.matchAll(/rgba\((\d+),(\d+),(\d+),/g)) assert.ok(['250,249,245', '20,20,20', '44,43,38', '113,187,222', '247,130,126'].includes(`${m[1]},${m[2]},${m[3]}`), m[0]);
  assert.match(CSS, /min-height:44px/);
});

test('Settings → 安全: the row is hidden until the host block says so and can never switch the terminal on', () => {
  const safety = HTML.slice(HTML.indexOf('id="set-s-safety"'), HTML.indexOf('id="set-s-app"'));
  const row = safety.slice(safety.indexOf('id="set-term-row"'), safety.indexOf('</div>\n', safety.indexOf('id="set-term-acts"') + 400));
  assert.match(safety, /<div class="setrow setterm" data-paired id="set-term-row" hidden>/);
  for (const id of ['set-term-state', 'set-term-cmd', 'set-term-open', 'set-term-forget', 'set-term-note', 'set-term-last']) assert.ok(row.includes(`id="${id}"`), id);
  assert.doesNotMatch(row, /role="switch"|data-pref=/, 'no switch, no preference');
  assert.match(HOOK, /s === 'off' \? 'agentj terminal enable'/);
  assert.match(HOOK, /if \(!info \|\| !info\.os\) return 'none';/, 'no block / no platform → no row');
  assert.match(SET, /termhook\.render\(hostInfo\);/);
  assert.match(CMD, /if \(term\) \{/, '「在终端里打开」 only while usable');
  assert.match(CMD, /o\.addEventListener\('click', \(\) => H\.openTerminal\(line\(name\)\)\);/, 'it opens with the command, never runs it');
  const open = TERM.slice(TERM.indexOf('function open(prefill, info)'), TERM.indexOf('function abandon('));
  assert.doesNotMatch(open, /term_line/, 'opening never sends a line');
});
