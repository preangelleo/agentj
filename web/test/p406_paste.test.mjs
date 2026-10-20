// P406 #168 (Leo): where pasted text goes — characters only, never the field's height.
// Words already in the box → the paste joins them unless together they pass one message's limit (then this paste alone is an
// attachment); an empty box → over CLIP_TEXT_MAX characters is an attachment, anything shorter goes into the field.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {pasteRoute, CLIP_TEXT_MAX} from '../public/js/paste-route.js';

const MAX = 20000;
const addr = '东京都港区六本木 6-10-1\n六本木ヒルズ森タワー 32 階\nAgent J 收件处';
test('#168: text in the box (a /command too) — a short address joins it, never an attachment', () => {
  assert.equal(pasteRoute({value: '/agentj-add-note ', start: 17, end: 17, text: addr, max: MAX}), 'field');
  assert.equal(pasteRoute({value: 'x', text: 'y'.repeat(5000), max: MAX}), 'field', 'over 2000 but the box has words: still the field');
});
test('#168: together over the single-message limit → this paste becomes the attachment', () => {
  assert.equal(pasteRoute({value: 'a'.repeat(MAX - 50), text: 'b'.repeat(51), max: MAX}), 'over');
  assert.equal(pasteRoute({value: 'a'.repeat(MAX - 50), text: 'b'.repeat(50), max: MAX}), 'field', 'exactly at the limit still fits');
  assert.equal(pasteRoute({value: 'a'.repeat(MAX - 50), start: 0, end: 100, text: 'b'.repeat(120), max: MAX}), 'field', 'the selection it replaces is not counted');
});
test('#168: an empty box decides by the paste\'s characters alone', () => {
  assert.equal(pasteRoute({value: '', text: 'z'.repeat(2500), max: MAX}), 'file');
  assert.equal(pasteRoute({value: '', text: 'z'.repeat(CLIP_TEXT_MAX), max: MAX}), 'field');
  assert.equal(pasteRoute({value: '', text: 'line of ten\n'.repeat(25), max: MAX}), 'field', 'a tall multi-line paste goes into the field (it scrolls)');
  assert.equal(pasteRoute({value: 'all of it', start: 0, end: 9, text: 'z'.repeat(2500), max: MAX}), 'file', 'replacing everything = an empty box');
  assert.equal(pasteRoute({value: '', text: '\u{1F600}'.repeat(1500), max: MAX}), 'field', 'characters, not UTF-16 units, against CLIP_TEXT_MAX');
  assert.equal(pasteRoute({value: '', text: '', max: MAX}), 'none');
});
test('#168: no paste decision uses the field height any more', () => {
  const src = readFileSync(new URL('../public/js/relay.js', import.meta.url), 'utf8');
  const uses = src.split('\n').filter((l) => l.includes('overflowsField(') && !/^const overflowsField|fieldCapacity|sample\.repeat/.test(l.trim()));
  assert.deepEqual(uses, [], 'overflowsField only measures the voice threshold (fieldCapacity)');
  assert.equal((src.match(/pasteRoute\(\{/g) || []).length, 2, 'the box paste and the hold-to-paste button share one rule');
});
