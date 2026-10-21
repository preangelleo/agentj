// P424 #142 — a key-like refusal note on the phone says which line (and a two-character masked hint), and nothing more.
// The wire sanitiser keeps `line` / `hint` only together, only as a positive integer and a ≤ 4-character string; every other
// note reads exactly as before. Script files are ordinary text cards (the host types them text/plain, §13): no new kind.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';

if (!globalThis.crypto) globalThis.crypto = webcrypto;
globalThis.window ??= {};
const M = await import('../public/js/media.js');
const W = await import('../public/js/mediawire.js');

test('sanitize keeps a refusal position only in its safe shape', () => {
  const { skip } = W.sanitize({ media_skip: [
    { name: 'leak.sh', why: 'key_content', line: 3, hint: 'sk…' },
    { name: 'a.txt', why: 'secret', line: 0, hint: 'ab…' },
    { name: 'b.txt', why: 'secret', line: 4, hint: 'sk-proj-LONGVALUE' },
    { name: 'c.txt', why: 'secret', line: '5', hint: 'ab…' },
    { name: 'd.txt', why: 'secret', line: 6 },
  ] });
  assert.deepEqual(skip, [
    { name: 'leak.sh', why: 'key_content', bytes: 0, line: 3, hint: 'sk…' },
    { name: 'a.txt', why: 'secret', bytes: 0 },
    { name: 'b.txt', why: 'secret', bytes: 0 },
    { name: 'c.txt', why: 'secret', bytes: 0 },
    { name: 'd.txt', why: 'secret', bytes: 0 },
  ]);
});

test('the note names the line and the masked hint; other notes are unchanged', () => {
  const zh = M.skipText({ name: 'leak.sh', why: 'key_content', bytes: 0, line: 3, hint: 'sk…' });
  assert.match(zh, /^leak\.sh 里有真实的密钥/);
  assert.match(zh, /（第 3 行：sk…）$/);
  assert.equal(M.skipText({ name: 'a.png', why: 'outside' }), 'a.png 不在工作目录里，没有发。');
  assert.equal(M.skipText({ name: 'x.txt', why: 'secret' }), 'x.txt 的文件名或内容像密钥，所以没发；去掉密钥或改个名后让 Agent J 再发。');
});

test('a script is previewed as text or code, never as something to run', () => {
  for (const [name, kind] of [['ark-check.sh', 'code'], ['probe.py', 'code'], ['setup.ps1', 'text'], ['run.bat', 'text']]) {
    assert.equal(W.previewKind({ name, kind: 'file', mime: 'text/plain' }), kind, name);
  }
});
