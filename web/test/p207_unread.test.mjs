// P207 (追加 2 / 追加 3): the per-device unread mark (js/unread.js) and the generated key table (interactions.gen.js +
// tools/interactions check). Pure logic; the page itself is driven in test/p207_unread.mjs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const mem = new Map();
globalThis.localStorage = { getItem: (k) => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => mem.set(k, String(v)), removeItem: (k) => mem.delete(k) };
const { createUnread, UNREAD_KEY } = await import('../public/js/unread.js');
const gen = await import('../public/js/interactions.gen.js');
const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const HAVE_IX_TOOL = existsSync(ROOT + 'agentjarvis/tools/interactions') && existsSync(ROOT + 'web/interactions.json');   // P218: the source of truth and its tool stay private

test('first run: everything already on the phone counts as read; only later replies are unread', () => {
  mem.clear();
  const u = createUnread();
  u.sync(3, [1, 2, 5]);
  assert.equal(u.mark(), 5);
  assert.deepEqual(u.list([1, 2, 5, 7, 9]), [7, 9]);
  assert.deepEqual(JSON.parse(mem.get(UNREAD_KEY)), { e: 3, m: 5, s: [] }, 'numbers only — no text is stored');
});

test('seeing replies in order moves the mark; a gap keeps the later ones as seen-above-the-mark', () => {
  mem.clear();
  const u = createUnread();
  u.sync(1, [10]);
  const ids = [10, 11, 12, 13, 14];
  assert.equal(u.see(14, ids, true), true, 'the newest page shown on opening the app');
  assert.deepEqual(u.list(ids), [11, 12, 13], 'the night\'s replies before it are still unread');
  u.see(11, ids, true);
  assert.equal(u.mark(), 11);
  u.see(12, ids, true); u.see(13, ids, true);
  assert.equal(u.mark(), 14, 'read up to the newest: no badge');
  assert.deepEqual(u.list(ids), []);
});

test('the mark survives a reload (another createUnread reads localStorage) and does not move without the loaded range', () => {
  mem.clear();
  const a = createUnread();
  a.sync(1, [20]);
  a.see(25, [24, 25], false);          // older pages between 20 and 24 not loaded yet
  assert.equal(a.mark(), 20, 'no advance across unloaded turns');
  const b = createUnread();
  assert.deepEqual(b.list([21, 22, 24, 25]), [21, 22, 24]);
});

test('another epoch (a /clear on the Agent J host): every reply of the new session is unread until seen', () => {
  mem.clear();
  const u = createUnread();
  u.sync(1, [40]);
  u.sync(2, [1, 2]);
  assert.equal(u.mark(), 0);
  assert.deepEqual(u.list([1, 2]), [1, 2]);
});

test('a broken stored record starts over instead of throwing', () => {
  mem.clear(); mem.set(UNREAD_KEY, '{nope');
  const u = createUnread();
  assert.equal(u.ready(), false);
  u.sync(1, [3]);
  assert.equal(u.mark(), 3);
});

const key = (k, mods = {}) => ({ key: k, code: mods.code || '', altKey: !!mods.alt, shiftKey: !!mods.shift, ctrlKey: !!mods.ctrl, metaKey: !!mods.meta });
test('generated key table: plain keys by KeyboardEvent.key, Alt by code, Ctrl / ⌘ never in the table', () => {
  assert.deepEqual(gen.lookup(key('u')), { id: 'page.first_unread', repeat: false });
  assert.deepEqual(gen.lookup(key('G')), { id: 'page.earliest', repeat: false });
  assert.deepEqual(gen.lookup(key(' ', { shift: true })), { id: 'page.scroll', repeat: true });
  assert.deepEqual(gen.lookup(key('µ', { alt: true, code: 'KeyM' })), { id: 'app.model_step', repeat: false }, 'macOS Alt+M types µ: matched by code');
  assert.deepEqual(gen.lookup(key('M', { alt: true, shift: true, code: 'KeyM' })), { id: 'app.model_default', repeat: false });
  assert.equal(gen.lookup(key('u', { ctrl: true })), null);
  assert.equal(gen.lookup(key('k', { meta: true })), null);
  assert.equal(gen.lookup(key('m')), null, 'm stays the talk key (hold / double tap), handled in code');
  assert.equal(gen.lookup(key('toString')), null, 'no prototype keys');
  assert.deepEqual(gen.kbdTokens('Mod+K', true), ['⌘', 'K']);
  assert.deepEqual(gen.kbdTokens('Mod+K', false), ['Ctrl', 'K']);
  assert.deepEqual(gen.kbdTokens('Alt+Shift+M', true), ['⌥', 'Shift', 'M']);
  assert.deepEqual(gen.kbdTokens('Shift+Space', false), ['Shift', '']);
});

test('every new 0.20.2 gesture of P207 has a key in the table (追加 3)', () => {
  const want = { 'page.first_unread': 'u', 'app.rename_agent': 'n', 'app.load': 'w', 'app.busy_list': 'b', 'page.newest': 'g', 'page.earliest': 'G',
    'card.mail': 'e', 'card.forward': 't', 'card.speak': 'l', 'card.copy': 'y', 'card.reply': 'r', 'file.mail': 'E', 'file.forward': 'T' };
  for (const [id, k] of Object.entries(want)) assert.equal(gen.KEYMAP[k][0], id, `${k} → ${id}`);
});

test('tools/interactions check passes on the tree (JSON ↔ relay.js ACT ↔ @ix markers ↔ generated files)', { skip: !HAVE_IX_TOOL && 'no agentjarvis/tools/interactions (public export)' }, () => {
  const r = spawnSync('python3', [ROOT + 'agentjarvis/tools/interactions', 'check'], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stdout + r.stderr);
});

test('tools/interactions add refuses a colliding or reserved key and an id outside the groups (dry run, nothing written)', { skip: !HAVE_IX_TOOL && 'no agentjarvis/tools/interactions (public export)' }, () => {
  const before = readFileSync(ROOT + 'web/interactions.json', 'utf8');
  const base = ['add', '--target-selector', '#x', '--target-zh', '某处', '--target-en', 'Somewhere', '--zh', '测试', '--en', 'Test',
    '--gesture', 'tap', '--gesture-zh', '点一下', '--gesture-en', 'Tap', '--dry-run'];
  const run = (...a) => spawnSync('python3', [ROOT + 'agentjarvis/tools/interactions', ...base, ...a], { encoding: 'utf8' });
  let r = run('--id', 'page.test_dup', '--key', 'u');
  assert.notEqual(r.status, 0); assert.match(r.stderr, /collides with page\.first_unread/);
  r = run('--id', 'page.test_res', '--key', 'Alt+F');
  assert.notEqual(r.status, 0); assert.match(r.stderr, /taken by the browser/);
  r = run('--id', 'nope.test', '--key', 'Alt+J');
  assert.notEqual(r.status, 0); assert.match(r.stderr, /not in groups/);
  r = run('--id', 'page.test_nokey');
  assert.notEqual(r.status, 0); assert.match(r.stderr, /every gesture needs a shortcut/);
  r = run('--id', 'page.test_ok', '--key', 'Alt+J');
  assert.equal(r.status, 0, r.stderr); assert.match(r.stdout, /"combo": "Alt\+J"/);
  assert.equal(readFileSync(ROOT + 'web/interactions.json', 'utf8'), before, '--dry-run writes nothing');
});
