// P555 (董事长 10-27): after the 0.25.13 upgrade every remote said 「本机 Face ID：未确认」 and asked another remote to approve a
// key it already had. Page side: the Settings row (已设好 · 「用 Face ID 确认」 for a key the Agent J host cannot place), the
// one-tap self-check in setupFaceId (get() inside the tap, no pk_peer request when it passes), the waiting sheet's Agent J host
// terminal way, wording (「Agent J 主机」, never a bare 「主机」) and the seat-transfer list (left-aligned, hanging items).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { readFileSync } from 'node:fs';

if (!globalThis.crypto) globalThis.crypto = webcrypto;
globalThis.window ??= globalThis;
globalThis.addEventListener ??= () => {};
class El {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.className = ''; this.textContent = ''; this.dataset = {}; this.hidden = false; }
  append(...k) { this.children.push(...k); }
  replaceChildren(...k) { this.children = k; }
  addEventListener() {}
  get childElementCount() { return this.children.length; }
}
globalThis.document ??= { getElementById: () => null, addEventListener() {}, body: null, createElement: (t) => new El(t) };
const P = await import('../public/js/pkpeer.js');
const A = await import('../public/js/pkassist.js');
const { listify } = await import('../public/js/seatxfer.js');
const { t } = await import('../public/js/t.js');
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const D = Object.fromEntries(['web.zh.src', 'web.zh.override', 'web.zh', 'web.en'].map((n) => [n, JSON.parse(read(`../i18n/${n}.json`))]));
const ID = Buffer.alloc(16, 7).toString('base64url');

function harness(res) {
  P._reset();
  const h = { shown: [], sent: [] };
  P.configure({
    view: { show: (s) => h.shown.push(s), hide() {} },
    ask: async () => res, send: async (o) => { h.sent.push(o); }, supported: async () => true, canAssert: () => true,
    regOpts: async () => ({ relayIndex: 0 }), now: () => 1_800_000_000_000,
    ua: () => 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Safari/605', touch: () => 5,
  });
  return h;
}

test('waiting sheet: nobody online and the host offers its terminal → host mode; approved by the terminal → its own name', async () => {
  const h = harness({ t: 'pk_peer_res', ok: true, mode: 'peer', id: ID, code: '123456', exp: 1_800_000_300, online: 0, host: 1 });
  assert.equal(await P.startPeerSetup(), 'peer');
  assert.deepEqual(h.shown.at(-1), { mode: 'wait', code: '123456', online: 0, host: true, where: 'ios' });
  P.onDone({ id: ID, ok: true, by: 'host' });
  assert.equal(await P.onOffer({ n: Buffer.alloc(32, 1).toString('base64url'), peer: ID }), true);
  assert.deepEqual(h.shown.at(-1), { mode: 'approved', by: t('pkp.byHost') });
  const src = read('../public/js/pkpeer.js');
  assert.match(src, /hostOnly \? 'pkp\.waitHostTitle' : 'pkp\.waitTitle'/);
  assert.match(src, /fillText\(cmd, t\('pkp\.hostWay', \{ code: s\.code \}\)\)/, 'the command is a copyable `code`');
});

test('Settings row: 已设好 hides the button; a key the Agent J host cannot place offers 「用 Face ID 确认」, not a new key', () => {
  const box = () => ({ row: { hidden: true, dataset: {} }, label: {}, note: {}, setupBtn: {}, oldBtn: { dataset: {} } });
  const b = box();
  A.paintPkRow('local', b);
  assert.equal(b.setupBtn.hidden, true);
  assert.match(b.label.textContent, /：已设好$/);
  const u = box();
  A.paintPkRow('unknown', u);
  assert.equal(u.setupBtn.hidden, false);
  assert.equal(u.setupBtn.textContent, A.withBio('p555.pk.check'));
  assert.equal(u.note.textContent, A.withBio('p555.pk.unknownNote'));
  assert.match(u.note.textContent, /不用另一台批准/);
  const n = box();
  A.paintPkRow('none', n);
  assert.equal(n.setupBtn.textContent, t('p539.pk.setup'));
});

test('a failed Face ID on a key thought to be here (#317) still offers a new key; a passing one says nothing', () => {
  const mkBox = () => ({ dataset: {}, hidden: true, replaceChildren(...k) { this.kids = k; }, });
  const fail = mkBox();
  A.paintPk(fail, { fa: 'local', err: 'NotAllowedError', retry: () => {} });
  const keys = fail.kids[1].children.map((x) => x.dataset.k);
  assert.deepEqual(keys, ['p539.fail.retry', 'p539.fail.setup']);
  const quiet = mkBox();
  A.paintPk(quiet, { fa: 'local' });
  assert.equal(quiet.hidden, true);
});

test('app.js: the self-check calls get() inside the tap before any await, and a pass never sends pk_peer_req', () => {
  const app = read('../public/app.js');
  const fn = app.slice(app.indexOf('function selfCheck()'), app.indexOf('async function setupFaceId()'));
  assert.ok(fn.indexOf('passkey.approve(') > 0 && !fn.slice(0, fn.indexOf('passkey.approve(')).includes('await'), 'get() first');
  assert.match(fn, /t: 'pk_check', n: c\.n/);
  const setup = app.slice(app.indexOf('async function setupFaceId()'), app.indexOf('/** A host from before P547'));
  assert.ok(setup.indexOf('selfCheck()') < setup.indexOf('await'), 'the check starts before anything is awaited');
  assert.ok(setup.indexOf('if (checked && (await checked)) return;') < setup.indexOf('pkpeer.startPeerSetup()'), 'passed → no peer request');
  assert.match(app, /pkNow = \{ ch: session\.channel\(\), s, chk \}/);
  assert.match(read('../public/js/faceid.js'), /const a = where \? attachmentOf\(cred\) : null;/);
});

test('wording: new keys in all four dictionaries; zh says 「Agent J 主机」; the screenshot lines are fixed', () => {
  for (const k of ['p555.pk.check', 'p555.pk.unknownNote', 'p555.pk.checked', 'pkp.waitHostTitle', 'pkp.waitHostText', 'pkp.hostWay', 'pkp.byHost']) {
    for (const n of Object.keys(D)) assert.ok(D[n][k], `${n}: ${k}`);
    assert.equal(D['web.zh'][k], D['web.zh.override'][k]);
  }
  const zh = D['web.zh'];
  assert.equal(zh['p539.pk.st.local'], '本机 {bio}：已设好');
  // the chairman's final seat-transfer wording (10-27), verbatim; 「你」 as everywhere else on the phone
  assert.equal(zh['sxf.rowNote'], '换新的 Agent J 主机时用：席位和遥控器都跟过去。');
  assert.equal(zh['sxf.explainTitle'], '把席位转到另一台 Agent J 主机');
  assert.match(zh['sxf.explainText'], /^换新的 Agent J 主机时用：你的席位（也就是 Agent J 的订阅）和手机遥控器都会跟过去，不用重新付费，也不用重新配对。\n步骤：\n1\. 点「继续」/);
  assert.match(zh['sxf.explainText'], /\n要知道的：\n· 新的 Agent J 主机装好之前，这台 Agent J 主机照常能用。\n· 转移码 24 小时内有效，只能用一次。\n· 新的 Agent J 主机用码之前，随时可以回到这里点「撤销」。$/);
  assert.ok(zh['sxf.explainText'].includes(`「${zh['sxf.explainYes']}」`) && zh['sxf.explainText'].includes(`「${zh['sxf.cancelBtn']}」`), 'quoted labels are the real buttons');
  assert.doesNotMatch(zh['sxf.explainText'], /电脑|(?<!Agent J )主机/);
  assert.match(zh['pkp.hostWay'], /`agentj faceid approve \{code\}`/);
  assert.match(D['web.en']['pkp.hostWay'], /`agentj faceid approve \{code\}`/);
});

test('seat transfer sheet: the list is left-aligned, a wrapped line starts under its own words', () => {
  const node = new El('p');
  listify(node, '导语一句。\n步骤：\n1. 第一步很长很长\n2. 第二步\n要知道的：\n· 第一条很长很长\n· 第二条');
  assert.deepEqual(node.children.map((c) => c.tagName), ['P', 'P', 'OL', 'P', 'UL']);
  assert.deepEqual(node.children.map((c) => c.className), ['sxf-lead', 'sxf-lead sxf-head', 'sxf-list', 'sxf-lead sxf-head', 'sxf-list']);
  assert.deepEqual(node.children[2].children.map((li) => li.textContent), ['第一步很长很长', '第二步'], 'the number is drawn by CSS');
  assert.deepEqual(node.children[4].children.map((li) => li.textContent), ['第一条很长很长', '第二条'], 'the dot is drawn by CSS');
  for (const n of ['web.zh', 'web.en']) {
    const real = new El('p'); listify(real, D[n]['sxf.explainText']);
    assert.deepEqual(real.children.map((c) => c.tagName), ['P', 'P', 'OL', 'P', 'UL'], n);
    assert.deepEqual(real.children.filter((c) => c.tagName !== 'P').map((l) => l.children.length), [3, 3], n);
  }
  const css = read('../public/menu.css');
  assert.match(css, /#confirm\[data-kind="sxf"\] #confirm-text\{white-space:normal;text-align:left\}/);
  assert.match(css, /#confirm\[data-kind="sxf"\] \.sxf-list li\{position:relative;padding-left:1em/);
  assert.match(css, /#confirm\[data-kind="sxf"\] \.sxf-list li::before\{content:"·";position:absolute;left:\.25em\}/);
  assert.match(css, /#confirm\[data-kind="sxf"\] ol\.sxf-list li::before\{content:counter\(sxf\) "\.";left:0\}/);
  assert.match(read('../public/js/seatxfer.js'), /if \(!await explain\(\)\) return;/);
});

// 追加 d (董事长 10-27 13:16, Mac M1 Chrome): 「已设好」 but the card's Touch ID opened Chrome's phone-QR sheet.
test('own-key get(): transports + hints by where the key lives (never a bare allowCredentials entry)', async () => {
  const F = await import('../public/js/faceid.js');
  const id = Buffer.alloc(16, 3).toString('base64url');
  const w = (st) => { const o = F.wantOf(id, st); return { tr: o.allowCredentials.map((x) => x.transports), hints: o.hints, n: o.allowCredentials[0].id.length }; };
  assert.deepEqual(w('local'), { tr: [['internal']], hints: ['client-device'], n: 16 });
  assert.deepEqual(w('borrowed'), { tr: [['hybrid']], hints: ['hybrid'], n: 16 });
  assert.deepEqual(w('unknown'), { tr: [['internal', 'hybrid']], hints: ['client-device'], n: 16 });
  assert.deepEqual(w('nonsense'), w('unknown'));
  F.setOwnState('local');
  assert.deepEqual(F.wantOf(id).allowCredentials[0].transports, ['internal'], 'pk_state sets the default');
  F.setOwnState('bogus');
  assert.deepEqual(F.wantOf(id).allowCredentials[0].transports, ['internal', 'hybrid']);
  // every WebAuthn get() in the page names transports (static lock)
  for (const f of ['faceid.js', 'vault.js']) {
    const src = read('../public/js/' + f);
    for (const m of src.matchAll(/allowCredentials:\s*([^\n]+)/g)) assert.match(m[1], /transports/, f + ': ' + m[1]);
  }
  assert.match(read('../public/js/vault.js'), /\.\.\.wantOf\(credId\)/);
  assert.match(read('../public/app.js'), /passkey\.setOwnState\(s\)/);
  assert.match(read('../public/app.js'), /passkey\.approve\(unb64u\(c\.fa_ch\), c\.fa, true, 'local'\)/, 'the self-check asks HERE only');
});
