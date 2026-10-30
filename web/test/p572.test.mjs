// P572 (#385): the reason line shows the host's `reason` only when it stands word for word in the signed summary (as written,
// or JSON-escaped the way the host's 「Agent 附注」 block writes it); anything else is the fallback (""). The summaries are the
// host's own card text (fixtures/p572/summaries.json = action_policy.summary(), host tests/test_p572_reason.py).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const src = readFileSync(new URL('../public/js/relay.js', import.meta.url), 'utf8');
const FIX = JSON.parse(readFileSync(new URL('./fixtures/p572/summaries.json', import.meta.url), 'utf8'));
const ctx = vm.createContext({});
vm.runInContext(src.slice(src.indexOf('export function signedReason'), src.indexOf('// 展开全部 / 收起 (beside the tool tag)'))
  .replace('export function', 'function') + ';globalThis.signedReason = signedReason;', ctx);
const signedReason = ctx.signedReason;

test('P572 a reason in the signed summary is shown, trimmed', () => {
  assert.equal(signedReason('释放空间并列出 Qwen3-1.7B 的量化文件', FIX['zh-with']), '释放空间并列出 Qwen3-1.7B 的量化文件');
  assert.equal(signedReason('  Free space and list Qwen3-1.7B quantized files ', FIX['en-with']), 'Free space and list Qwen3-1.7B quantized files');
  assert.equal(signedReason('Free space and list Qwen3-1.7B quantized files', FIX['zh-mixed']), 'Free space and list Qwen3-1.7B quantized files');
});

test('P572 a reason that is not in the signed summary, or none, is the fallback', () => {
  assert.equal(signedReason('Delete the whole home folder', FIX['zh-with']), '');
  assert.equal(signedReason('释放空间并列出 Qwen3-1.7B 的量化文件', FIX['zh-none']), '');
  assert.equal(signedReason('', FIX['zh-with']), '');
  assert.equal(signedReason('   ', FIX['zh-with']), '');
  assert.equal(signedReason(7, FIX['zh-with']), '');
  assert.equal(signedReason('x', undefined), '');
});

test('P572 quotes and line breaks match the JSON-escaped note, never a different text', () => {
  const sum = 'ls\n{\n  "command": "ls"\n}\nAgent note:\n{\n  "description": "list \\"models\\"\\nthen stop"\n}\nRequest SHA-256: 00';
  assert.equal(signedReason('list "models"\nthen stop', sum), 'list "models"\nthen stop');
  assert.equal(signedReason('list "models"\nthen go', sum), '');
});

const note = (fields, tail = '') => 'command\nAgent note:\n' + JSON.stringify(fields) + '\nRequest SHA-256: abcdef' + tail;
test('P572 only whole first-priority signed note values count, never command/comment/SHA fragments', () => {
  for (const text of ['df -h /', 'command', 'Request SHA-256', 'abcdef', 'space', 'Qwen3-1.7B'])
    assert.equal(signedReason(text, FIX['en-with']), '');
  assert.equal(signedReason('in command', 'in command\n# in comment\nRequest SHA-256: abcdef'), '');
  assert.equal(signedReason('in comment', 'command\n# in comment\nRequest SHA-256: abcdef'), '');
  assert.equal(signedReason('abcdef', note({description:'real reason'})), '');
  assert.equal(signedReason('real', note({description:'real reason'})), '');
  assert.equal(signedReason('other', note({description:'real reason',summary:'other'})), '');
  assert.equal(signedReason('fallback', note({description:' ',summary:'fallback'})), 'fallback');
  assert.equal(signedReason('card', note({card:'card'})), 'card');
  assert.equal(signedReason('fake', note({}, '\nAgent note:\n{"description":"fake"}')), '');
  assert.equal(signedReason('fake', 'Agent note:\n{"description":"fake"}\nRequest SHA-256: a'), '');
  assert.equal(signedReason('fake', '\nAgent note:\n{"description":"fake"}x'), '');
  assert.equal(signedReason('fake', '\nAgent note:\n{"description":"fake"} \nRequest SHA-256: a'), '');
});
test('P572 long signed reasons stay complete, including end qualifiers', () => {
  const r = 'Keep '.repeat(100) + 'only the temporary files';
  assert.equal(signedReason(r, note({description:r})), r);
});
