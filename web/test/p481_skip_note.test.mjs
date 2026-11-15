// #481: a reply linking a file that cannot be sent never shows an internal file name and never leaves an orphan 「。」.
// Incident (Mac@M1, 0.26.8): 「依据：release-summary.json 不在工作目录里，没有发。」 followed by a lone 「。」.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';

if (!globalThis.crypto) globalThis.crypto = webcrypto;
globalThis.window ??= {};
const M = await import('../public/js/media.js');
import vm from 'node:vm';
import {retainedUiSource} from './release-ui-source.mjs';
const orphanSource=retainedUiSource('public/js/media.js').match(/export function orphanPunct\([\s\S]*?\n\}/)[0].replace('export ','');
const orphanPunct=vm.runInNewContext(orphanSource+';orphanPunct');

test('the host-neutral name reads as a plain sentence in both languages', () => {
  const zh = M.skipText({ name: '这份资料', why: 'private' });
  assert.ok(!/release-summary|\.json/.test(zh), zh);
  assert.match(zh, /^这份资料/);
});

test('the reply punctuation right after a slot note is dropped, other text is kept', () => {
  const note = M.skipText({ name: '这份资料', why: 'private' });
  assert.equal(orphanPunct(note, '。'), 1);
  assert.equal(orphanPunct(note, '。后面还有话'), 1);
  assert.equal(orphanPunct(note, ' ，然后'), 2);
  assert.equal(orphanPunct(note, '然后'), 0);
  assert.equal(orphanPunct('half a note', '。'), 0);
  assert.equal(orphanPunct("This file is in Agent J's internal folder and doesn't go to others.", '.'), 1);
});
