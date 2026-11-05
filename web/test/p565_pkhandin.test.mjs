// P565 追加 A (华为 Pad 10-28 13:16, 董事长真机两次「没存上」): the fingerprint sheet sent the page to the background, the socket
// dropped for 16–20 s, and the made key was either refused on the dead socket (「没存上」 at once) or handed in after the reconnect
// and refused by the host (no_offer — fixed host-side, test_p565_pk_resubmit.py). Here the page side (js/pkhandin.js):
// (1) create() finished while offline → 「正在补交…」, sent on the next ready connection, never an early 「没存上」;
// (2) sent on a socket that died without an answer → sent again after the reconnect; (3) answered → 「已设好」 when it waited;
// (4) another computer / the window ends → 「没存上」 once; (5) the 「没存上」 notice sits below an open dialog, never over it.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const K = await import('../public/js/pkhandin.js');
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const tick = (ms = 300) => new Promise((r) => setTimeout(r, ms));

function harness({ ch = 'c1' } = {}) {
  const h = { gen: null, ch, sent: [], waiting: 0, failed: 0, refuse: false };
  K.configure({
    gen: () => h.gen, channel: () => h.ch,
    send: async (reg, g) => { if (h.refuse) throw new Error('socket closed'); h.sent.push([reg.id, g]); },
    waiting: () => { h.waiting++; }, failed: () => { h.failed++; },
  });
  return h;
}
beforeEach(() => K._reset());
const REG = { t: 'pk_reg', id: 'k1' };

test('connected: sent at once, nothing shown, answered without 「已设好」 wording', async () => {
  const h = harness();
  h.gen = { n: 1 };
  await K.handIn(REG);
  assert.deepEqual(h.sent, [['k1', h.gen]]);
  assert.equal(h.waiting, 0);
  assert.deepEqual(K.answered(), { waited: false });
  assert.equal(K.busy(), false);
});

test('create() finished during a 16 s drop: 「正在补交…」, then sent on the new connection, then 「已设好」', async () => {
  const h = harness();
  h.gen = null;                                       // dev_gone
  const p = K.handIn(REG);
  await tick();
  assert.equal(h.waiting, 1);
  assert.equal(h.failed, 0);                          // never an early 「没存上」
  assert.equal(K.busy(), true);
  h.gen = { n: 2 };                                   // resume_ok
  await p;
  assert.deepEqual(h.sent, [['k1', h.gen]]);
  assert.deepEqual(K.answered(), { waited: true });
});

test('the old socket refused it (closed under the page): waits for a NEW generation, not the dead one', async () => {
  const h = harness();
  const dead = { n: 1 };
  h.gen = dead; h.refuse = true;
  const p = K.handIn(REG);
  await tick();
  assert.equal(h.sent.length, 0);
  assert.equal(h.waiting, 1);
  h.refuse = false;
  await tick();
  assert.equal(h.sent.length, 0);                     // the dead generation is not retried
  h.gen = { n: 2 };
  await p;
  assert.deepEqual(h.sent, [['k1', h.gen]]);
});

test('sent, then the answer was lost with the socket: sent once more on the next ready', async () => {
  const h = harness();
  h.gen = { n: 1 };
  await K.handIn(REG);
  K.again();                                          // same connection: nothing
  await tick(20);
  assert.equal(h.sent.length, 1);
  h.gen = { n: 2 };
  K.again();
  await tick(20);
  assert.deepEqual(h.sent.map((x) => x[1].n), [1, 2]);
  K.answered();
  h.gen = { n: 3 };
  K.again();
  await tick(20);
  assert.equal(h.sent.length, 2);                     // answered: never again
});

test('another computer on the reconnect: not sent there, 「没存上」 once', async () => {
  const h = harness();
  h.gen = null;
  const p = K.handIn(REG);
  await tick();
  h.ch = 'c2'; h.gen = { n: 2 };
  await p;
  assert.equal(h.sent.length, 0);
  assert.equal(h.failed, 1);
  assert.equal(K.busy(), false);
});

test('no connection within the window: 「没存上」 once', async () => {
  const h = harness();
  h.gen = null;
  await K.handIn(REG, Date.now() - K.HANDIN_MS + 400);
  assert.equal(h.failed, 1);
  assert.equal(h.sent.length, 0);
});

test('app.js: both paths hand in through pkhandin; no new offer sheet while one is open; 「已设好」 on the answer', () => {
  const app = read('../public/app.js');
  assert.match(app, /pkhandin\.handIn\(reg, Date\.now\(\), h\.channel\);/);             // direct offer (P566 #363: its computer)
  assert.match(app, /o\.t === 'pk_reg' \? pkhandin\.handIn\(o\)/);                  // peer-approved offer
  assert.doesNotMatch(app, /session\.sendApp\(reg, g\)\.catch\(\(\) => toast\(t\('pk\.saveFail'\)\)\)/);
  assert.match(app, /async function offerPasskey\(m\) \{\n  if \(pkhandin\.busy\(\)\) return;/);
  assert.match(app, /pkhandin\.again\(\);/);
  assert.match(app, /pkhandin\.answered\(\)/);
  assert.match(app, /'p565\.pk\.handedIn'/);
});

test('words: zh / en, the {bio} word, no trial wording', () => {
  const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));
  for (const k of ['p565.pk.handing', 'p565.pk.handedIn']) {
    assert.ok(zh[k] && en[k], k);
    assert.match(zh[k], /\{bio\}/); assert.match(en[k], /\{bio\}/);
  }
  assert.match(zh['p565.pk.handing'], /^正在补交…/);
  assert.match(zh['p565.pk.handedIn'], /^已设好/);
});

test('a centred notice sits below an open dialog (never over its text and buttons)', () => {
  const css = read('../public/app.css');
  const rule = /body:has\(\.ajmodal\.dlgcard:not\(\[hidden\]\),#confirm:not\(\[hidden\]\)\) \.toast:is\(\.circle,\.square\)\{top:auto;bottom:/;
  assert.match(css, rule);
  assert.match(css, /body:has\(\.ajmodal\.dlgcard:not\(\[hidden\]\),#confirm:not\(\[hidden\]\)\) \.toast:is\(\.circle,\.square\)\.on\{transform:translate\(-50%,0\)\}/);
});
