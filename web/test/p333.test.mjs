// P333: 「new on this remote」 is kept apart from the shared unread count — another remote's receipt clears the count,
// never this remote's own alert for a reply that arrived live while it sat on an older page.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createUnread} from '../public/js/unread.js';

const live = (u, ids) => { u.sync(1, ids); u.arrivals(ids, true); };

test('a live arrival stays new here after another remote read it', () => {
  const u = createUnread(); u.bind('H'); u.owner(1, 2);
  live(u, [1, 2]);                       // first paint: what is already there is the base, nothing new
  assert.deepEqual(u.alerts([1, 2]), []);
  live(u, [1, 2, 3, 4]);                 // two replies arrive while this remote sits on page 1
  assert.deepEqual(u.alerts([1, 2, 3, 4]), [3, 4]);
  u.owner(1, 4);                          // the other remote, on the newest page, read them
  assert.deepEqual(u.list([1, 2, 3, 4]), [], 'the shared count follows the host');
  assert.deepEqual(u.alerts([1, 2, 3, 4]), [3, 4], 'the local alert does not');
  u.look(3); assert.deepEqual(u.alerts([1, 2, 3, 4]), [4], 'paging onto one clears it and what is before it');
  u.look(4); assert.deepEqual(u.alerts([1, 2, 3, 4]), []);
});
test('a background remote (not visible) follows the host only — P247 / P321 unchanged', () => {
  const u = createUnread(); u.bind('H'); u.owner(1, 2);
  u.arrivals([1, 2], true); u.arrivals([1, 2, 3], false);
  u.owner(1, 3);
  assert.deepEqual(u.alerts([1, 2, 3]), []);
});
test('a head fetch (reload, foreground, reconnect) is not a live arrival', () => {
  const u = createUnread(); u.bind('H'); u.owner(1, 9);
  u.rebase([1, 5, 9]); u.arrivals([1, 5, 9], true);
  assert.deepEqual(u.alerts([1, 5, 9]), []);
  u.arrivals([1, 5, 9, 10], true); u.rebase([1, 5, 9, 10, 11]); u.arrivals([1, 5, 9, 10, 11], true);
  assert.deepEqual(u.fresh(), [10], 'a rebase never drops what already arrived live');
});
test('a reply already shown here does not come back as new; another host / epoch forgets', () => {
  const u = createUnread(); u.bind('H'); u.owner(1, 0);
  u.arrivals([1], true); u.look(1); u.arrivals([1], true);
  assert.deepEqual(u.fresh(), []);
  u.arrivals([1, 2], true); assert.deepEqual(u.fresh(), [2]);
  u.bind('K'); assert.deepEqual(u.fresh(), []);
  u.bind('H'); u.owner(1, 0); u.arrivals([1, 2], true); u.arrivals([1, 2, 3], true); assert.deepEqual(u.fresh(), [3]);
  u.owner(2, 0); assert.deepEqual(u.fresh(), [], 'a /clear (new epoch) starts over');
});
test('the unread badge of an old host (no owner mark) is the union too', () => {
  const u = createUnread(); u.bind('old'); live(u, [1]); live(u, [1, 2]);
  assert.deepEqual(u.alerts([1, 2]), [2]);
  u.see(2, [1, 2], true); u.look(2); assert.deepEqual(u.alerts([1, 2]), []);
});
test('the pulse is for replies that came in live, not for older unread a first-unread jump steps over', () => {
  const u = createUnread(); u.bind('H'); u.owner(1, 5);
  u.rebase([5, 6, 7, 8]); u.arrivals([5, 6, 7, 8], true);   // 6..8 were already there at load: unread, not news
  u.look(6); u.see(6, [5, 6, 7, 8], true);
  assert.equal(u.newer([5, 6, 7, 8], 6), false, 'no pulse on 6 for 7 and 8');
  assert.deepEqual(u.alerts([5, 6, 7, 8]), [7, 8], 'the badge still counts them');
  u.arrivals([5, 6, 7, 8, 9], false);                       // 9 comes while in the background, nobody reads it
  assert.equal(u.newer([5, 6, 7, 8, 9], 6), true, 'back in front: 9 relights it');
  u.owner(1, 9); assert.equal(u.newer([5, 6, 7, 8, 9], 6), false, 'read elsewhere while in the background: no pulse');
  u.arrivals([5, 6, 7, 8, 9, 10], true); u.owner(1, 10);
  assert.equal(u.newer([5, 6, 7, 8, 9, 10], 6), true, 'arrived while on screen: stays news here');
});
