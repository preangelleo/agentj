// #584: a reply code block's 「执行」 — routing, provenance gate, wiring and the punctuation rule for text after a block.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const src = read('../public/js/termcommands.js');
const box = {t: (k, v) => k + (v ? JSON.stringify(v) : '')};
vm.runInNewContext(src.replace(/^import .*;$/gm, '').replace(/export /g, '')
  + '\nglobalThis.api={ownReply,command,dangerous,needsOwner,routeOf,hostPage,headText,askText,HUMAN}', box);
const A = box.api;

test('only shell-like fences become commands (powershell / console included)', () => {
  for (const l of ['sh', 'bash', 'zsh', 'shell', 'console', 'powershell', 'pwsh']) assert.equal(A.command('agentj --version', l), 'agentj --version', l);
  for (const l of ['python', 'text', 'js', '', null]) assert.equal(A.command('ls', l), null, String(l));
});
test('provenance: own settled replies only — friend, channel, quote, streaming and host pages get no 「执行」', () => {
  const p = {id: 3, end: 'done', source: {k: 'phone'}};
  assert.equal(A.ownReply(p), true);
  for (const k of ['friend_report', 'telegram', 'wechat', 'email', 'task', 'auto', 'agent', 'sys', 'cmd']) assert.equal(A.ownReply({...p, source: {k}}), false, k);
  assert.equal(A.ownReply({...p, end: 'open'}), false);
  assert.equal(A.ownReply({...p, source: {k: 'phone', quote: {id: 1}}}), false);
  assert.equal(A.hostPage({id: 4, end: 'done', source: {k: 'sys'}}), true);
  assert.equal(A.hostPage({id: 4, end: 'done', source: {k: 'friend_report'}}), false);
});
test('routes: whitelisted name → named; sudo / TUI → terminal open (or Agent); else terminal run or Agent', () => {
  const named = (c) => (c === 'agentj --version' ? 'version' : null);
  const P = (usable, canOpen) => ({named, usable: () => usable, canOpen: () => canOpen});
  assert.equal(A.routeOf('agentj --version', P(false, false)), 'named');
  assert.equal(A.routeOf('sudo pacman -Syu', P(true, true)), 'open');
  assert.equal(A.routeOf('sudo pacman -Syu', P(false, false)), 'agent');
  for (const c of ['vim a.txt', 'ssh box', 'gh auth login', 'top', '/usr/bin/sudo ls']) assert.equal(A.needsOwner(c), true, c);
  for (const c of ['ls -la', 'agentj status', 'echo topic', 'git log --oneline']) assert.equal(A.needsOwner(c), false, c);
  assert.equal(A.routeOf('ls -la', P(true, true)), 'term');
  assert.equal(A.routeOf('ls -la', P(false, true)), 'agent');
});
test('result head: exit code and time, a refused confirm says it did not run, the terminal route says it has no exit code', () => {
  assert.match(A.headText({st: 'done', code: 0, ms: 1234}, 'named'), /^p584\.ok.*p584\.took.*1\.2/);
  assert.match(A.headText({st: 'fail', why: 'refused'}, 'term'), /^p584\.why\.refused$/);
  assert.match(A.headText({st: 'done', out: 'x', ms: 50}, 'term'), /p584\.noCode/);
  assert.match(A.headText({st: 'run'}, 'term'), /p584\.running/);
});
test('问 Agent quotes the command and the (already masked) output, never breaking out of its fence', () => {
  const s = A.askText('ls', {out: 'a\n```\nb', code: 2});
  assert.match(s, /```sh\nls\n```/);
  assert.equal((s.match(/```/g) || []).length, 4);
  assert.ok(A.askText('ls', {out: 'x'.repeat(9000)}).length < 3400);
});
test('wiring: button left of copy, one tap one run, offline grey, run rides the 「!」 path with run:1', () => {
  assert.match(src, /bar\.insertBefore\(b, bar\.querySelector\('\.copy'\)\)/);
  assert.match(src, /if \(runs\.get\(key\)\?\.st === 'run'\) return;/);
  assert.match(src, /p584\.offline/);
  const term = read('../public/js/terminal.js');
  assert.match(term, /run: 1, \.\.\.\(Number\.isInteger\(run\.from\)/);
  assert.match(term, /\.\.\.\(bangSid && bangRun \? runFields\(bangRun\) : \{\}\)/);   // the signed resend keeps run
  const relay = read('../public/js/relay.js');
  assert.match(relay, /function enhanceReplySurface\(root,p,reader=false\)/);
  assert.match(relay, /terminalCommands\(root,p\)/);
  assert.match(relay, /enhanceReplySurface\(el\("words"\),p\)/);
  assert.match(relay, /enhanceReplySurface\(el\("rdWords"\),p,true\)/);
});
test('every new string exists in both languages with the same placeholders', () => {
  const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));
  const keys = [...new Set([...src.matchAll(/'(p584\.[\w.]+)'/g)].map((m) => m[1]))].filter((k) => !k.endsWith('.'));
  assert.ok(keys.length > 20);
  for (const k of keys) {
    assert.equal(typeof zh[k], 'string', k); assert.equal(typeof en[k], 'string', k);
    const ph = (s) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join();
    assert.equal(ph(zh[k]), ph(en[k]), k);
  }
});

// #584 ⑩ (董事长 17:13 「前面就是个 Code Block，或者说前面就是个按钮……在开头放个逗号就非常的不专业了」): text that follows a
// block (a code fence, a button, a card) never starts with punctuation. Two checks over every dictionary and host template:
// (1) after a closing ``` the next line does not start with punctuation; (2) a dictionary value may start with punctuation
// only when it is one of the existing in-line joiners listed here (a separator glued after a word, never after a block).
const LEAD = /^\s*[，。、；：,;:！？!?）)]/;
const JOINERS = new Set(['act.k.secs', 'tasks.textOnRo', 'cron.daySep', 'r.list.sep', 'r.list.sep2', 'r.dot', 'p181.name.suggest',
  'p339.howAdd', 'form.colon', 'form.sep', 'p208.holdHint', 'tasks.next']);
function* flat(d, pre = '') { for (const [k, v] of Object.entries(d)) { if (v && typeof v === 'object') yield* flat(v, pre + k + '.'); else if (typeof v === 'string') yield [pre + k, v]; } }
test('no text after a block starts with punctuation (dictionaries + host templates)', () => {
  const bad = [];
  for (const f of ['../i18n/web.zh.src.json', '../i18n/web.zh.override.json', '../i18n/web.zh.json', '../i18n/web.en.json']) {
    for (const [k, v] of flat(JSON.parse(read(f)))) {
      if (LEAD.test(v) && !JOINERS.has(k)) bad.push(f + ' ' + k + ': ' + v.slice(0, 30));
      if (/```[^\n]*\n[\s\S]*?\n```\s*\n\s*[，。、；：,;:]/.test(v)) bad.push(f + ' ' + k + ' (after a fence)');
    }
  }
  assert.deepEqual(bad, []);
});
test('host message templates: no line after a closing fence starts with punctuation', async () => {
  const {readdirSync} = await import('node:fs');
  const root = new URL('../../host/agentj/', import.meta.url), bad = [];
  for (const f of readdirSync(root, {recursive: true})) {
    if (!/\.(py|md|json)$/.test(f)) continue;
    const s = readFileSync(new URL(f, root), 'utf8');
    for (const m of s.matchAll(/```(?:\\n|\n)\s*[，。、；：]/g)) bad.push(f + '@' + m.index);
  }
  assert.deepEqual(bad, []);
});
