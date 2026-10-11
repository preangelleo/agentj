// P127 (追加6/7): what the composer treats as an echo of the message just sent — a dictation 「。」 or an IME re-commit of the
// same words that lands after the field was cleared must not put the sent text back.
import test from 'node:test';
import assert from 'node:assert/strict';
import { isEcho, paint } from '../public/js/outbox.js';

test('the sent words, give or take trailing punctuation / spaces, are an echo', () => {
  const s = '你必须有一个规则，对不对？';
  assert.equal(isEcho(s, s), true);
  assert.equal(isEcho(s + '。', s), true);                 // Mac dictation appends 「。」 after the send
  assert.equal(isEcho(s + ' 。 ', s), true);
  assert.equal(isEcho('他去研究了', '他去研究了。'), true);
  assert.equal(isEcho('  hello.', 'hello'), true);
  assert.equal(isEcho('。', s), true);                     // a lone punctuation commit after the clear
  assert.equal(isEcho('', s), false);                      // nothing to drop
});

test('new words are never an echo', () => {
  assert.equal(isEcho('他去研究了，然后呢', '他去研究了'), false);
  assert.equal(isEcho('另一句话', '他去研究了'), false);
  assert.equal(isEcho('他去', '他去研究了'), false);
  assert.equal(isEcho(null, 'x'), false);
  assert.equal(isEcho('x', null), false);
});

test('a message queued behind a running turn shows in the composer as a pending line with its withdraw note', () => {
  const made = [];
  const mk = (tag) => {
    const n = { tag, className: '', dataset: {}, children: [], textContent: '', attrs: {},
      setAttribute(k, v) { this.attrs[k] = v; }, append(...c) { this.children.push(...c); } };
    made.push(n); return n;
  };
  globalThis.document = { createElement: mk };
  try {
    const box = { hidden: true, kids: [], replaceChildren(...k) { this.kids = k; } };
    paint(box, [{ sid: 'a'.repeat(22), text: '他去研究了', att: [{ name: 'x.png', kind: 'image' }], queued: true }],
      { line: 'offline', lostNote: 'lost', queuedNote: '还没交给 Agent，点「取消」可撤回', isLost: () => false, showLine: false });
    assert.equal(box.hidden, false);
    assert.equal(box.kids.length, 1);
    const d = box.kids[0];
    assert.equal(d.dataset.queued, '1');
    assert.deepEqual(d.children.map((c) => c.textContent), ['他去研究了', '还没交给 Agent，点「取消」可撤回']);
  } finally { delete globalThis.document; }
});
