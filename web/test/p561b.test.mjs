// P561 追加 11/12/14/17 (董事长 10-28 00:5x, Mac M1 Safari): a new remote whose id a ticket restore replaced (ga2pb8 → 4W2_)
// asked, the iPhone approved, 「保存」 → 「Touch ID 记录没存上」, and 「在本机设置」 then said 「这个页面用不了 Touch ID，请到…手机上
// 领取」 (the pickup card's words). Settings had two setup entries; a ③ card on a remote with no key showed 「用 Touch ID 确认」
// and only after the tap 「需要主人通行密钥；请先在手机菜单中设置…」.
// Here: (1) setupSupport — 「不支持」 only when this browser cannot make a key (Apple systems: the API is enough); (2) a failed
// create() keeps the other remote's approval: the same sheet comes back with 「再试一次」, the failure's name is kept; (3) a
// replaced remote id drops the old request; (4) one setup entry (Settings' top row) — the terminal row and the no-key card
// point there (scrollToPkRow); (5) the words. Browser legs: p561b.mjs (Chromium + WebKit).
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { readFileSync } from 'node:fs';

if (!globalThis.crypto) globalThis.crypto = webcrypto;
globalThis.window ??= globalThis;
globalThis.addEventListener ??= () => {};
const mem = new Map();
globalThis.localStorage = { getItem: (k) => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => mem.set(k, String(v)), removeItem: (k) => mem.delete(k) };
globalThis.location ??= { hostname: 'm.agentj.app' };
class El {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.className = ''; this.textContent = ''; this.dataset = {}; this.hidden = false; this.handlers = {}; }
  append(...k) { this.children.push(...k); }
  replaceChildren(...k) { this.children = k; }
  addEventListener(ev, fn) { this.handlers[ev] = fn; }
  setAttribute() {}
  get childElementCount() { return this.children.length; }
}
globalThis.document ??= { getElementById: () => null, addEventListener() {}, body: null, createElement: (t) => new El(t) };
const F = await import('../public/js/faceid.js');
const P = await import('../public/js/pkpeer.js');
const A = await import('../public/js/pkassist.js');
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const json = (p) => JSON.parse(read(p));
const b64u = (u) => Buffer.from(u).toString('base64url');
const ID = b64u(Buffer.alloc(16, 7)), ID2 = b64u(Buffer.alloc(16, 8));
const NONCE = b64u(Buffer.alloc(32, 1));
after(() => P._reset());                             // the request's expiry timer

function webauthn({ uvpaa = false, pkc = true, create = true } = {}) {
  globalThis.PublicKeyCredential = pkc ? { isUserVerifyingPlatformAuthenticatorAvailable: async () => uvpaa } : undefined;
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { userAgent: '', maxTouchPoints: 0,
    credentials: { get: async () => null, ...(create ? { create: async () => null } : {}) } } });
}

test('setupSupport: 「不支持」 only when this browser cannot make a key; Safari (mac / ios) is not held back by isUVPAA', async () => {
  webauthn({ uvpaa: false });
  assert.equal(await F.setupSupport('mac'), 'yes', 'Mac Safari answered isUVPAA false with Touch ID working (host.log 00:57)');
  assert.equal(await F.setupSupport('ios'), 'yes');
  assert.equal(await F.setupSupport('android'), 'noPlatform');
  assert.equal(await F.setupSupport('linux'), 'noPlatform');
  webauthn({ uvpaa: true });
  assert.equal(await F.setupSupport('linux'), 'yes');
  webauthn({ pkc: false });
  assert.equal(await F.setupSupport('mac'), 'no', 'no WebAuthn at all');
  webauthn({ uvpaa: true, create: false });
  assert.equal(await F.setupSupport('mac'), 'no');
  globalThis.isSecureContext = false;
  webauthn({ uvpaa: true });
  try { assert.equal(await F.setupSupport('mac'), 'no', 'not a secure page'); } finally { delete globalThis.isSecureContext; }
});

test('register(): create() still starts in the tap\'s tick; a failure keeps its NAME (never the message) for the sheet / support', async () => {
  mem.clear();
  let called = 0;
  const reg = { relayIndex: 0, channel: b64u(Buffer.alloc(16, 2)), hostPub: new Uint8Array(32), peer: true };
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { userAgent: '', credentials: {
    create: () => { called++; const e = new Error('secret detail https://x'); e.name = 'NotAllowedError'; return Promise.reject(e); } } } });
  const p = F.register(new Uint8Array(32), reg);
  assert.equal(called, 1, 'create() ran before register() returned (no await before it)');
  await assert.rejects(p, { name: 'NotAllowedError' });
  const kept = F.lastRegError();
  assert.equal(kept.name, 'NotAllowedError');
  assert.ok(!JSON.stringify(kept).includes('secret'), 'the message is not kept');
  await assert.rejects(F.register(new Uint8Array(32), { ...reg, hostPub: new Uint8Array(3) }));   // a bad host record: our own Error
  assert.equal(F.lastRegError().name, 'Error');
});

function harness({ reg } = {}) {
  P._reset();
  const h = { sent: [], shown: [], hidden: 0, asked: 0, registered: 0 };
  let n = 0;
  P.configure({
    view: { show: (s) => h.shown.push(s), hide: () => { h.hidden++; } },
    ask: async () => { h.asked++; return { t: 'pk_peer_res', ok: true, mode: 'peer', id: [ID, ID2][n++] || ID2, code: '123456', exp: 1_800_000_300, online: 1 }; },
    send: async (o) => { h.sent.push(o); },
    supported: async () => true,
    regOpts: async () => ({ relayIndex: 0, channel: 'C'.repeat(22), hostPub: new Uint8Array(32), name: 'Agent J', displaySuffix: ' · x' }),
    register: () => { h.registered++; return reg ? reg(h.registered) : Promise.resolve({ t: 'pk_reg', id: 'new' }); },
    notSaved: () => 'pk.notSaved.mac',
    now: () => 1_800_000_000_000,
  });
  return h;
}
const last = (h) => h.shown[h.shown.length - 1];

test('a failed 「保存」 keeps the approval: the sheet comes back with the reason and 「再试一次」 — no second approval, no toast', async () => {
  const h = harness({ reg: (i) => (i === 1 ? Promise.reject(Object.assign(new Error('x'), { name: 'NotAllowedError' })) : Promise.resolve({ t: 'pk_reg', id: 'k2' })) });
  assert.equal(await P.startPeerSetup(), 'peer');
  P.handle({ t: 'pk_peer_done', id: ID, ok: true, by: 'iPhone' });
  assert.equal(await P.onOffer({ t: 'pk_offer', n: NONCE, peer: ID }), true);
  await P.saveTap();
  assert.deepEqual(last(h), { mode: 'approved', by: 'iPhone', err: 'NotAllowedError', notSaved: 'pk.notSaved.mac' });
  assert.equal(P._state().shown, 'approved');
  assert.equal(h.sent.length, 0, 'nothing went to the host');
  await P.saveTap();                                  // 「再试一次」 = the same saveTap, straight from the tap
  assert.equal(h.registered, 2);
  assert.equal(h.asked, 1, 'the iPhone was not asked again');
  assert.deepEqual(h.sent, [{ t: 'pk_reg', id: 'k2' }]);
});

test('a remote id replaced under a ticket restore (ga2pb8 → 4W2_): the old id\'s request never blocks the new id\'s key', async () => {
  const h = harness();
  P.onReady('ga2pb8BTfF91FD2s');
  await P.startPeerSetup();                           // a request of the old id, still in memory
  assert.equal(P._state().req.id, ID);
  assert.equal(await P.onOffer({ t: 'pk_offer', n: NONCE, peer: ID2 }), false, 'P547: an offer for another request is not this one');
  P.onReady('4W2_qyEyGkOK9hfy');                      // the same page now speaks for the new record
  assert.equal(P._state().req, null, 'the old request is gone');
  assert.equal(await P.startPeerSetup(), 'peer');
  assert.equal(P._state().req.id, ID2);
  P.handle({ t: 'pk_peer_done', id: ID2, ok: true, by: 'iPhone' });
  assert.equal(await P.onOffer({ t: 'pk_offer', n: NONCE, peer: ID2 }), true);
  await P.saveTap();
  assert.deepEqual(h.sent, [{ t: 'pk_reg', id: 'new' }]);
  P.onReady('4W2_qyEyGkOK9hfy');                      // a plain reconnect of the same id keeps a request
  await P.startPeerSetup();
  P.onReady('4W2_qyEyGkOK9hfy');
  assert.ok(P._state().req, 'same id: the request stays');
});

test('the no-key card: words on top (line + note), 「去设置」 · 「拒绝」 one row under them — no 「用 Face ID 确认」 path', () => {
  const box = new El('div');
  A.paintPk(box, { noKey: true });
  assert.equal(box.hidden, false);
  assert.equal(box.dataset.state, 'nokey');
  // P563 追加 7 (说明在上、按钮在下): the block is only words; the button lives in the card's own .apprbtns row
  assert.equal(box.children.length, 2);
  const [line, note] = box.children;
  assert.equal(line.dataset.k, 'p561.card.noKey');
  assert.equal(note.dataset.k, 'p561.card.elsewhere');
  const html = read('../public/index.html');
  const row = html.slice(html.indexOf('<div class="apprbtns" id="apprBtns"'), html.indexOf('</div>', html.indexOf('<div class="apprbtns" id="apprBtns"')));
  assert.match(row, /<button class="apprallow apprgo" id="apprGo" type="button" hidden><span class="lbl" data-i18n="p561\.pk\.go">去设置<\/span><\/button>\s*<button class="apprdeny" id="apprDeny"/, '去设置 in 批准\'s slot, before 拒绝');
  const rl = read('../public/js/relay.js');
  assert.match(rl, /if \(goShown && !apprBusy\) el\("askState"\)\.before\(el\("apprPk"\)\); else el\("apprBtns"\)\.after\(el\("apprPk"\)\);/, 'the words move above the button row');
  assert.match(rl, /el\("apprGo"\)\.hidden = !goShown;/);
  const relay = read('../public/js/relay.js');
  assert.match(relay, /el\("apprAllow"\)\.hidden = !p \|\| p\.local_only \|\| noKey;/);
  assert.match(relay, /if\(action!=="deny"&&cardNoKey\(p\)\)return;/);
  assert.match(relay, /const cardNoKey = \(p\) => !!\(p && p\.passkey_required && !p\.fa && !p\.local_only && !isTV\(\)\);/);
  assert.match(relay, /el\("sheetClose"\)\.click\(\); scrollToPkRow\(\);/, '去设置 folds the card first (it stays pending), then Settings');
});

test('one setup entry: the terminal row points up (scrollToPkRow), no button of its own; Settings opens and the row lights up', () => {
  const html = read('../public/index.html');
  const term = html.slice(html.indexOf('<div id="set-term-pk"'), html.indexOf('</div>', html.indexOf('<div id="set-term-pk"')) + 6);
  assert.match(term, /id="set-term-faceid" class="setlink pkgo" data-i18n="p561\.pk\.goUp"/);
  assert.doesNotMatch(term, /aj-btn|phoneEnv\.setup|p417\.nopk/);
  assert.match(read('../public/js/termhook.js'), /el\('set-term-faceid'\)\?\.addEventListener\('click', \(\) => scrollToPkRow\(\)\)/);
  const pk = read('../public/js/pkassist.js');
  assert.match(pk, /export function scrollToPkRow\(\)/);
  assert.match(pk, /row\.classList\.add\('set-found'\)/, 'the Settings search\'s own highlight');
  assert.match(pk, /return H\.setupFaceId\(\);/, 'an older host without the row: the setup itself');
  assert.match(read('../public/app.js'), /pkassist\.configure\(\{ setupFaceId, openSettings: \(\) => \{ if \(!settings\.isOpen\(\)\) settings\.open\(\); \} \}\)/);
});

test('words: setting up says 「这个浏览器不支持…」 (not the pickup line); pickup keeps sout.noFaceId; nothing says the owner refused', () => {
  const app = read('../public/app.js');
  const setup = app.slice(app.indexOf('async function setupFaceId()'), app.indexOf('/** A host from before P547'));
  assert.doesNotMatch(setup, /t\('sout\.noFaceId'\)/);
  assert.match(setup, /passkey\.setupSupport\(\)\) !== 'yes'\) \{[^\n]*toast\(t\('p561\.pk\.unsupported'\), 5000\)/);
  assert.match(app, /unsupported: \(\) => toast\(t\('p561\.pk\.unsupported'\), 5000\)/);
  assert.match(app, /if \(ri < 0 \|\| \(await passkey\.setupSupport\(\)\) !== 'yes'\) return null;/, 'regOpts: the same test');
  assert.doesNotMatch(app, /t\('sout\.noFaceId'\)/);
  assert.match(read('../public/js/secretout.js'), /sout\.noFaceId/, 'the pickup card keeps its own line');
  const zs = json('../i18n/web.zh.src.json'), zo = json('../i18n/web.zh.override.json'), zj = json('../i18n/web.zh.json'), en = json('../i18n/web.en.json');
  const keys = ['p561.pk.unsupported', 'p561.save.failTitle', 'p561.save.fail', 'p561.card.noKey', 'p561.card.elsewhere', 'p561.pk.go', 'p561.pk.goUp'];
  for (const k of keys) {
    assert.ok(zs[k] && zs[k] === zo[k] && zs[k] === zj[k], k + ': the three Chinese files agree');
    assert.ok(en[k] && !/[一-鿿]/.test(en[k]), k + ': English');
    for (const s of [zs[k], en[k]]) assert.doesNotMatch(s, /拒绝了|取消了|手机菜单|通行密钥|refused|declined|cancel|passkey|phone menu/i, k);
  }
  assert.equal(zs['p561.pk.unsupported'], '这个浏览器不支持 Face ID。');   // t() says Touch ID on a Mac (P559 bioWords)
  for (const [d, k] of [[zs.ask, 'passkeyRequired'], [zj.ask, 'passkeyRequired'], [en.ask, 'passkeyRequired'], [zo, 'ask.passkeyRequired']]) {
    assert.doesNotMatch(d[k], /手机菜单|通行密钥|phone menu|passkey/i, 'the old toast is gone: ' + d[k]);
  }
  assert.match(zj.sout.noFaceId, /领取/, 'sout.noFaceId unchanged: it is the pickup line');
  assert.doesNotMatch(zs['pk.notSaved.mac'], /没存上/, 'the Mac line says it was not set up, not that a record was dropped');
});

test('P561 追加 17: a remote without its own key shows no 「用 Face ID 确认」 title or tag on the card', () => {
  const relay = read('../public/js/relay.js');
  assert.match(relay, /p && p\.passkey_required && \(p\.tool === "SandboxOnce" \|\| !cardNoKey\(p\)\) \? t\(p\.tool === "SandboxOnce" \? "ask\.sandboxTitle" : "p336\.faTitle"\)/, 'sheet title (the sandbox card keeps 「沙箱不可用」)');
  assert.match(relay, /else if \(item\.passkey_required && \(item\.tool === "SandboxOnce" \|\| !cardNoKey\(item\)\)\) \{/, 'tag: the category chips instead');
  assert.match(relay, /\n    else if \(item\.passkey_required\) \{\}\n    else if \(item\.goal !== "grant"/, 'never the low-risk tag');
});
