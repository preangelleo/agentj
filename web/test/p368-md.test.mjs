// P368 (#78): md.js turns [label](reply:words) into an inert rlink (the label as words) — never an <a>, never a button
// here (only relay.js's replybtn.js does that, on the Agent's own words). Injection shapes stay text.
import test from 'node:test';
import assert from 'node:assert/strict';
import MD from '../public/js/md.js';
const rl = (s) => MD.parseInline(s).filter((n) => n.tag === 'rlink').map((n) => n.say);
test('P368: reply: links become rlink nodes with the words a tap would send', () => {
  assert.deepEqual(rl('派不派？[派](reply:派) [先不派](reply:先不派)'), ['派', '先不派']);
  assert.deepEqual(rl('[用方案 A](reply:用方案 A)'), ['用方案 A'], 'spaces inside the words');
  assert.deepEqual(rl('[Yes](reply:)'), ['Yes'], 'empty words → the label');
  assert.deepEqual(rl('[ok](reply:%E5%A5%BD)'), ['好'], 'percent-encoded');
  assert.deepEqual(rl('[A](REPLY:a)'), ['a']);
});
test('P368: refused shapes stay plain text, never a link', () => {
  for (const s of ['[停](reply:/stop)', '[停](reply:／stop)', '[x](reply:a%0Ab)', '[x](reply:a%07)', '[派](reply:%E2%80%AE派)', '[长](reply:' + '长'.repeat(201) + ')', '[](reply:)', '[x](reply:a\nb)']) {
    const out = MD.parseInline(s);
    assert.equal(out.some((n) => n.tag === 'rlink' || n.tag === 'a'), false, s.slice(0, 30));
  }
  assert.deepEqual(rl('[长](reply:' + '长'.repeat(200) + ')'), ['长'.repeat(200)], '200 字 is the limit');
  assert.equal(MD.REPLY_MAX, 200);
  assert.equal(MD.replyRef('https://x.example', 'x'), null);
});
test('P368: inside code or an image it is not a reply link; a normal link next to it is unchanged', () => {
  assert.deepEqual(rl('`[x](reply:x)`'), []);
  assert.deepEqual(rl('![x](reply:x)'), []);
  const out = MD.parseInline('[site](https://example.com) [go](reply:go)');
  assert.equal(out.filter((n) => n.tag === 'a').length, 1);
  assert.deepEqual(rl('[site](https://example.com) [go](reply:go)'), ['go']);
  const blk = MD.parse('> [删库](reply:删库)\n\n```\n[运行](reply:运行)\n```');
  const s = JSON.stringify(blk);
  assert.ok(!s.includes('"tag":"pre"') || !/"tag":"pre"[^}]*rlink/.test(s), 'fenced code holds no rlink');
});
