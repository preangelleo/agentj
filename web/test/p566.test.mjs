// P566 (#364 / #363 / #354 / #357 / #350 / #346, 0.25.19): a Face ID / Touch ID result survives the socket the system sheet
// drops, on every get() path; a key card's failure is always visible; an expired card goes; 「找不到了」 only after a clear miss;
// Settings can test / redo 「已设好」; the dialog's primary button stays readable when hovered / focused.
// The browser legs (real page, fake host, socket dropped mid-get()) are web/test/p566.mjs.
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

// ---------------------------------------------------------------- #363: the drop-reconnect gap (channel() = null)
test('#363 a hand-in started in the gap binds to the first channel it sees, not to null', async () => {
  const h = harness({ ch: null });
  h.gen = null;                                       // the gap: no connection, no channel
  const p = K.handIn({ t: 'pk_reg', id: 'k1' });
  await tick();
  assert.equal(h.waiting, 1);
  h.ch = 'c1'; h.gen = { n: 2 };                      // reconnected to the same computer
  await p;
  assert.deepEqual(h.sent.map((x) => x[0]), ['k1']);
  assert.equal(h.failed, 0, 'never 「没存上」 for the gap');
});

test('#363 a channel that is null while a connection is up is still the gap — it waits', async () => {
  const h = harness({ ch: 'c1' });
  h.gen = null;
  const p = K.handIn({ t: 'pk_reg', id: 'k1' });
  await tick();
  h.ch = null; h.gen = { n: 2 };                      // generation back, channel not yet
  await tick();
  assert.equal(h.sent.length, 0);
  h.ch = 'c1';
  await p;
  assert.equal(h.sent.length, 1);
  assert.equal(h.failed, 0);
});

test('#363 bound to c1, then another computer: refused there once', async () => {
  const h = harness({ ch: null });
  const p = K.handIn({ t: 'pk_reg', id: 'k1' }, Date.now(), 'c1');   // offerPasskey passes the record's channel
  await tick();
  h.ch = 'c2'; h.gen = { n: 2 };
  await p;
  assert.equal(h.sent.length, 0);
  assert.equal(h.failed, 1);
});

// ---------------------------------------------------------------- #364: put() for every get() result
test('#364 put(): several answers at once, each on the first ready connection, each answered on its own', async () => {
  const h = harness();
  const out = [];
  const send = (k) => async (g) => { out.push([k, g.n]); };
  h.gen = null;
  const a = K.put('elev:a', { send: send('a'), until: Date.now() + 5000 });
  const b = K.put('sout:b', { send: send('b'), until: Date.now() + 5000 });
  await tick();
  h.gen = { n: 7 };
  assert.deepEqual(await Promise.all([a, b]), [true, true]);
  assert.deepEqual(out.sort(), [['a', 7], ['b', 7]]);
  assert.equal(K.busy('elev:a'), true, 'open until the host answers');
  assert.deepEqual(K.answered('elev:a'), { waited: true });
  h.gen = { n: 8 };
  K.again();
  await tick(20);
  assert.deepEqual(out.map((x) => x.join(':')).sort(), ['a:7', 'b:7', 'b:8'], 'only the unanswered one goes again');
});

test('#364 put(): waiting is said once (「正在补交…」); the card going away ends it with failed()', async () => {
  const h = harness();
  let live = true, waiting = 0, failed = 0;
  h.gen = null;
  const p = K.put('elev:x', { send: async () => {}, until: Date.now() + 5000, live: () => live,
    waiting: () => { waiting++; }, failed: () => { failed++; } });
  await tick(600);
  assert.equal(waiting, 1);
  live = false;
  assert.equal(await p, false);
  assert.equal(failed, 1);
  assert.equal(K.busy('elev:x'), false);
});

test('#364 put(): the window ends (the card expired) → false, nothing sent', async () => {
  const h = harness();
  h.gen = null;
  const sent = [];
  assert.equal(await K.put('ask:z', { send: async (g) => sent.push(g), until: Date.now() + 300 }), false);
  assert.equal(sent.length, 0);
});

test('#364 every get() path hands its answer in (static)', () => {
  const elev = read('../public/js/elevate.js'), sout = read('../public/js/secretout.js'), api = read('../public/js/api.js'),
    ctl = read('../public/js/controls.js'), app = read('../public/app.js');
  // key card: same arming = same card (the host re-sends it after the reconnect); signed for the card's computer
  assert.match(elev, /const same = !!old && old\.n === c\.n && !c\.running;/);
  assert.match(elev, /if \(old && shownId === m\.id && !same\) \{ shownId = null; busy = false; \}/);
  assert.match(elev, /const now = cards\.get\(c\.id\), same = !!now && now\.n === c\.n;/);
  assert.match(elev, /const ch = ch0, dev = await myDeviceId\(\);/);
  assert.match(elev, /await handIn\('elev:' \+ id, \{ send: \(g2\) => sendApp\(m, g2\), until: c\.deadline, ch,/);
  assert.doesNotMatch(elev, /if \(!fa \|\| cards\.get\(c\.id\) !== c\)/, 'the silent drop is gone');
  // pickup card: kept through the blip, handed in, a second copy of an opened value is ignored
  assert.match(sout, /if \(going && \(busy \|\| going\.sending\)\) \{ cards\.clear\(\); cards\.set\(going\.id, going\); return; \}/);
  assert.match(sout, /await handIn\('sout:' \+ id, \{ send: \(g2\) => sendApp\(\{ t: 'secret_out_open', id, n, fa \}, g2\), until: cur\.deadline, ch,/);
  assert.match(sout, /if \(opened && opened\.id === m\.id\) return;/);
  // approval card: signed for the card's computer, handed in until ask_done
  assert.match(api, /const ch = channel\(\);/);
  assert.match(api, /approveMessage\(ch, await myDeviceId\(\)/);
  assert.match(api, /H\.handed\?\.\(p\.id\);/, 'the held 「正在补交…」 goes when the hand-in ends');
  assert.match(app, /handed: \(\) => \{ if \(approveHanding\) \{ approveHanding = false; toastOff\(\); \} \}/);
  assert.match(app, /case 'ask_done': friends\.answerDone\(m\); snap\.askDone\(m\); pkhandin\.answered\('ask:' \+ m\.id\);/);
  // a Face ID confirmation (controls.signedWriteFa): resent on the reconnected session, same computer
  assert.match(ctl, /return fa \? commandFa\(\{ \.\.\.obj, fa \}, ch\) : \{ ok: false, why: 'cancel' \};/);
  assert.match(ctl, /if \(g2 !== g\) \{ if \(!fa\) throw new Error\('offline'\);/);
  // P555 self-check / 「测试一下」: answered on whichever socket (host keeps it per device) — both taps
  assert.equal((app.match(/if \(m\.t === 'offline' && await session\.whenReady\(30000\) && session\.channel\(\) === ch0\) m = await session\.ask1\(\{ t: 'pk_check'/g) || []).length, 2);
  // approving another remote's Face ID set-up (pk_peer_answer with this remote's assertion)
  assert.match(app, /o && o\.t === 'pk_peer_answer' && o\.ok \? pkhandin\.put\('peer:' \+ o\.id, \{ send: \(g\) => session\.sendApp\(o, g\), until: Date\.now\(\) \+ 120000, ch: readyCh \}\)/);
  assert.match(app, /pkhandin\.reset\(\);/, 'unpair / re-pair drops every open hand-in');
  assert.match(app, /if \(m\.t === 'pk_peer_answer_res'\) pkhandin\.answered\('peer:' \+ m\.id\);/);
});

// ---------------------------------------------------------------- #354 / #357: the key card
test('#354 every failure on the key card is said on the card and above it; a busy tap says so', () => {
  const elev = read('../public/js/elevate.js');
  assert.match(elev, /const err = node\('p', 'elev-err', 'elev-bad elev-err'\); err\.hidden = true; err\.setAttribute\('role', 'alert'\);/);
  assert.match(elev, /if \(busy \|\| c\.running \|\| c\.sending\) \{ toast\(t\(c\.sending \? 'p566\.handing' : 'p566\.busy'\), 3000\); return; \}/);
  for (const w of ['late', 'stale', 'replay', 'gone', 'bad_signature', 'no_key', 'shape', 'passkey']) assert.match(elev, new RegExp(`\\b${w}: '`), w);
  const ui = read('../public/js/ui.js'), css = read('../public/app.css');
  assert.match(ui, /document\.querySelector\('\.ajmodal\.dlgcard:not\(\[hidden\]\),#confirm:not\(\[hidden\]\)'\)/);
  assert.match(ui, /if \(dlg\) \{ document\.body\.appendChild\(box\); box\.classList\.add\('over-dlg'\); \}/);
  assert.match(css, /\.toast\.rect\.over-dlg\{position:fixed;[^}]*z-index:calc\(var\(--z-confirm\) \+ var\(--ontop-n,0\) \+ 60\)/);
});

test('#357 at 0 the key card goes by itself and says why (never left to be filled in)', () => {
  const elev = read('../public/js/elevate.js');
  assert.match(elev, /if \(c\.deadline <= Date\.now\(\) && !c\.sending\) \{ expire\(c\); return; \}/);
  assert.match(elev, /if \(Date\.now\(\) >= c\.deadline\) \{ expire\(c\); return; \}/);
  assert.match(elev, /function expire\(c\) \{[\s\S]{0,260}done\(\{ id: c\.id, result: 'timeout' \}\);\s*notify\(\{ kind: 'warn', text: t\('p566\.expired'\)/);
});

// ---------------------------------------------------------------- #350: no false 「找不到了」, strict seat keys
test('#350 WrongSeatKey reads as such (nothing marked)', async () => {
  const R = await import('../public/js/pkrules.js');
  assert.equal(R.failKey('WrongSeatKey', 'local'), 'p566.pk.wrongSeat');
  assert.equal(R.failKey({ name: 'WrongSeatKey' }, null), 'p566.pk.wrongSeat');
});

// ---------------------------------------------------------------- #346: Settings 「测试一下」 · 「重新设置」
test('#346 「已设好」 has 测试一下 + 重新设置 (no unpairing); 测试一下 only with a host check challenge', async () => {
  const html = read('../public/index.html'), app = read('../public/app.js');
  assert.match(html, /<span class="pkrow__acts" id="set-pk-acts" hidden><button type="button" id="set-pk-test" class="aj-btn aj-btn--sm" data-i18n="p566\.pk\.test">测试一下<\/button><button type="button" id="set-pk-reset" class="aj-btn aj-btn--sm" data-i18n="p561\.pk\.reset">重新设置<\/button><\/span>/);
  assert.match(app, /\$\('set-pk-test'\)\.addEventListener\('click', \(\) => testFaceId\(\)\);/);
  assert.match(app, /\$\('set-pk-reset'\)\.addEventListener\('click', \(\) => \{ setupFaceId\(\)\.catch\(\(\) => \{\}\); \}\);/);
  assert.match(app, /const chk = \(s === 'unknown' \|\| s === 'local'\) && typeof m\.fa === 'string'/);
  globalThis.window ??= globalThis;
  globalThis.addEventListener ??= () => {};
  globalThis.document ??= { getElementById: () => null, addEventListener() {}, body: null, createElement: () => ({}) };
  const A = await import('../public/js/pkassist.js');
  const box = () => ({ row: { hidden: true, dataset: {} }, label: {}, note: {}, setupBtn: {}, oldBtn: { dataset: {} }, acts: { hidden: true }, testBtn: { hidden: true } });
  const ok = box(); A.paintPkRow('local', { ...ok, canTest: true });
  assert.equal(ok.acts.hidden, false); assert.equal(ok.testBtn.hidden, false); assert.equal(ok.setupBtn.hidden, true);
  const old = box(); A.paintPkRow('local', { ...old, canTest: false });
  assert.equal(old.acts.hidden, false); assert.equal(old.testBtn.hidden, true, 'an older host sends no challenge: 重新设置 only');
  const bad = box(); A.paintPkRow('local', { ...bad, failed: true, canTest: true });
  assert.equal(bad.acts.hidden, true, '「找不到了」 keeps its one 重新设置 button');
  for (const s of ['none', 'borrowed', 'unknown']) { const x = box(); A.paintPkRow(s, { ...x, canTest: true }); assert.equal(x.acts.hidden, true, s); }
});

// ---------------------------------------------------------------- the 「保存」 button on a dialog card
test('a dialog card\'s primary button keeps Salt on hover / focus in both themes (was Noir = its label colour)', () => {
  const css = read('../public/app.css');
  assert.match(css, /\.ajmodal\.dlgcard \.aj-btn--primary:hover,\.ajmodal\.dlgcard \.aj-btn--primary:focus\{background:var\(--c-salt\);border-color:var\(--c-salt\);color:var\(--primary-fg\)\}/);
  const pal = read('../public/brand/palette.css');
  assert.match(pal, /--c-salt: #f0eee6;/, 'the palette itself is unchanged');
  assert.match(pal, /--c-noir: #2c2b26;/);
});

// ---------------------------------------------------------------- words
test('words: zh / en for every P566 key; 「Agent J 主机」, never a bare 主机 or 贾维斯; no 钥匙 / trial words', () => {
  const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));
  const keys = Object.keys(en).filter((k) => k.startsWith('p566.'));
  assert.ok(keys.length >= 16, keys.length);
  for (const k of keys) {
    assert.ok(zh[k] && en[k], k);
    assert.doesNotMatch(zh[k], /(?<!Agent J )主机/, k);
    assert.doesNotMatch(zh[k] + en[k], /贾维斯|Jarvis|试用|trial/i, k);
    assert.doesNotMatch(zh[k], /钥匙(?!串)/, k);
    assert.equal((zh[k].match(/\{bio\}/g) || []).length, (en[k].match(/\{bio\}/g) || []).length, k);
  }
  assert.equal(zh['p566.expired'], '这张卡已过期，请让 Agent 重发。');
  assert.equal(zh['p566.pk.test'], '测试一下');
});
