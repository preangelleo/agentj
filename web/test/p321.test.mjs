// P321: the owner mark the host has not confirmed is pending — sent again a few times per id, never past the host.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createUnread} from '../public/js/unread.js';

test('a seen reply above the host mark is pending until the host confirms it', () => {
  const u = createUnread(); u.bind('H'); u.owner(1, 120);
  assert.equal(u.pending(), null, 'nothing above the host mark');
  assert.deepEqual(u.list([119, 120, 121, 122]), [121, 122]);
  u.see(122, [121, 122], true);
  assert.equal(u.pending(), 122);
  u.owner(1, 122);
  assert.equal(u.pending(), null, 'confirmed');
  assert.deepEqual(u.list([121, 122]), []);
});
test('a refused id is retried at most three times; a newer id starts again', () => {
  const u = createUnread(); u.bind('H'); u.owner(1, 0); u.see(5, [5], true);
  assert.deepEqual([u.pending(), u.pending(), u.pending(), u.pending()], [5, 5, 5, null]);
  u.see(6, [5, 6], true); assert.equal(u.pending(), 6);
});
test('another host or epoch forgets the confirmation', () => {
  const u = createUnread(); u.bind('A'); u.owner(1, 9); u.see(10, [10], true); u.pending();
  u.bind('B'); assert.equal(u.pending(), null); u.owner(1, 3); assert.equal(u.pending(), null);
  u.see(4, [4], true); assert.equal(u.pending(), 4);
  u.owner(2, 0); assert.equal(u.mark(), 0); assert.equal(u.pending(), null, 'a new epoch starts confirmed at the host value');
});
test('a host mark of 100+ replies never shows 99+ once another remote read them', () => {
  const ids = Array.from({length: 150}, (_, i) => i + 1);
  const phone = createUnread(); phone.bind('H'); phone.owner(1, 0);
  assert.equal(phone.list(ids).length, 150);
  phone.owner(1, 149);   // the pad read to the end but one
  assert.deepEqual(phone.list(ids), [150]);
});
test('no owner (an old host): pending is always null', () => {
  const u = createUnread(); u.bind('old'); u.sync(1, [1, 2]); u.see(3, [1, 2, 3], true);
  assert.equal(u.pending(), null);
});
