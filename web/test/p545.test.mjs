// P545 (FEEDBACK #309, PROTOCOL §40): m.agentj.app/<seat id> — the seat id (vs protocol/vectors/p545-seat-url.json, the
// same as host/agentj/seaturl.py), the Worker's seat routes (redirect, assets under the prefix, the seat's own manifest,
// renewal cookie and CSP) and js/seatscope.js (localStorage split by seat, root untouched). The browser walk is p545.mjs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { webcrypto } from 'node:crypto';
import { handle, seatRoute, seatManifest, cleanSeatName, connectSelf, rtSetCookie, RT_PATH, SPA_PATHS } from '../worker.ts';

const pub = (p) => new URL('../public/' + p, import.meta.url);
const V = JSON.parse(readFileSync(new URL('../../protocol/vectors/p545-seat-url.json', import.meta.url), 'utf8'));
const ENV = { WEB_HOST: 'm.agentj.app', LEGACY_WEB_HOST: 'alpha-web.agentjarvis.net', RELAY_URL: 'wss://relay.agentj.app', LEGACY_RELAY_URL: 'wss://alpha-relay.agentjarvis.net' };
const MANIFEST = JSON.parse(readFileSync(pub('manifest-dark.webmanifest'), 'utf8'));
const assets = (seen) => ({ fetch: async (req) => {
  const u = new URL(req.url); seen.push(u.pathname + u.search);
  if (/\.webmanifest$/.test(u.pathname)) return new Response(JSON.stringify(MANIFEST), { status: 200, headers: { 'content-type': 'application/manifest+json' } });
  return new Response('ok:' + u.pathname, { status: 200 });
} });

test('seat id: the vectors (JS = Python = dashboard)', async () => {
  globalThis.AJSeat = undefined;
  const { seatId, SEAT_RE } = await import('../public/js/seats.js');
  assert.equal(V.label, 'agentj/seat-url/v1\n');
  for (const c of V.cases) {
    assert.equal(await seatId(c.channel), c.seat, c.channel);
    assert.match(c.seat, SEAT_RE);
    assert.equal(c.url, 'https://m.agentj.app/' + c.seat);
  }
  await assert.rejects(() => seatId('short'));
});

test('no top-level file or page route of public/ looks like a seat id', () => {
  for (const n of readdirSync(pub(''))) assert.equal(seatRoute('/' + n + '/'), null, n);
  for (const p of SPA_PATHS) assert.equal(seatRoute(p), null, p);
  assert.deepEqual(seatRoute('/abcd2345'), { seat: 'abcd2345', rest: null });
  assert.deepEqual(seatRoute('/abcd2345/js/app.js'), { seat: 'abcd2345', rest: '/js/app.js' });
  for (const bad of ['/ABCD2345/', '/abcd2341/', '/abcd23456/', '/abcd234/']) assert.equal(seatRoute(bad), null, bad);
});

test('Worker: /<seat> → 308 /<seat>/ (query kept), assets served from under the prefix, SPA routes too', async () => {
  const seen = [];
  const env = { ...ENV, ASSETS: assets(seen) };
  let r = await handle(new Request('https://m.agentj.app/abcd2345?lang=en'), env);
  assert.equal(r.status, 308);
  assert.equal(r.headers.get('location'), '/abcd2345/?lang=en');
  r = await handle(new Request('https://m.agentj.app/abcd2345/js/app.js'), env);
  assert.equal(await r.text(), 'ok:/js/app.js');
  r = await handle(new Request('https://m.agentj.app/abcd2345/'), env);
  assert.equal(await r.text(), 'ok:/');
  r = await handle(new Request('https://m.agentj.app/abcd2345/friends'), env);
  assert.equal(await r.text(), 'ok:/');
  assert.match(r.headers.get('content-security-policy'), /connect-src wss:\/\/relay\.agentj\.app wss:\/\/alpha-relay\.agentjarvis\.net https:\/\/m\.agentj\.app\/version\.json https:\/\/m\.agentj\.app\/abcd2345\/\.aj\/rt;/);
  r = await handle(new Request('https://m.agentj.app/'), env);
  assert.match(r.headers.get('content-security-policy'), /https:\/\/m\.agentj\.app\/version\.json https:\/\/m\.agentj\.app\/\.aj\/rt;/, 'the root page: unchanged');
  r = await handle(new Request('https://evil.example/abcd2345/'), env);
  assert.equal(r.status, 404);
  r = await handle(new Request('https://m.agentj.app/abcd2345/', { method: 'POST' }), env);
  assert.equal(r.status, 404);
  assert.equal(connectSelf('https://m.agentj.app/abcd2345/friends'), 'https://m.agentj.app/version.json https://m.agentj.app/abcd2345/.aj/rt');
});

test('Worker: each seat its own manifest — id / start_url / scope, the name on the icon, the passkey hint', async () => {
  const env = { ...ENV, ASSETS: assets([]) };
  const get = async (u) => (await handle(new Request(u), env)).json();
  const a = await get('https://m.agentj.app/abcd2345/manifest-dark.webmanifest?n=Father%40TB');
  const b = await get('https://m.agentj.app/wxyz7777/manifest-dark.webmanifest');
  assert.equal(a.id, '/abcd2345/'); assert.equal(a.start_url, './'); assert.equal(a.scope, './');
  assert.equal(a.name, 'Agent J · Father@TB'); assert.equal(a.short_name, 'Father@TB');
  assert.equal(b.id, '/wxyz7777/'); assert.equal(b.name, 'Agent J');
  assert.deepEqual(a.icons, MANIFEST.icons, 'icons stay relative (served under the seat path)');
  assert.equal(a.share_target.action, './share-target');
  const c = await get('https://m.agentj.app/abcd2345/manifest.webmanifest?c=' + 'A'.repeat(43));
  assert.equal(c.start_url, './#c=' + 'A'.repeat(43), 'a fragment: the installed app\'s launches never send it');
  for (const bad of ['short', 'has space aaaaaaaaaaaaaaa', 'x'.repeat(600)]) {
    assert.equal((await get('https://m.agentj.app/abcd2345/manifest.webmanifest?c=' + encodeURIComponent(bad))).start_url, './', bad);
  }
  const root = await get('https://m.agentj.app/manifest-dark.webmanifest?n=x');
  assert.deepEqual(root, MANIFEST, 'the root manifest is the file as shipped');
  assert.equal(cleanSeatName('  a\u0000b​c   d  '), 'abc d');
  assert.equal(Array.from(cleanSeatName('名'.repeat(80))).length, 32);
  assert.equal(seatManifest(MANIFEST, 'abcd2345', new URLSearchParams('n=%E2%80%AE')).name, MANIFEST.name, 'an empty name after cleaning: Agent J');
});

test('renewal cookie: one per seat (Path=/<seat>/.aj/rt), the root one unchanged', async () => {
  assert.match(rtSetCookie('v', true), /Path=\/\.aj\/rt;/);
  assert.match(rtSetCookie('v', true, '/abcd2345/.aj/rt'), /Path=\/abcd2345\/\.aj\/rt;/);
  const env = { ...ENV, ASSETS: assets([]) };
  const v = '1.' + 'A'.repeat(67) + '.' + 'B'.repeat(22) + '.' + 'C'.repeat(43);
  const r = await handle(new Request('https://m.agentj.app/abcd2345' + RT_PATH, { method: 'POST', body: JSON.stringify({ op: 'set', v }),
    headers: { origin: 'https://m.agentj.app', 'sec-fetch-site': 'same-origin', 'content-type': 'application/json' } }), env);
  assert.equal(r.status, 204);
  assert.match(r.headers.get('set-cookie'), /^__Secure-aj_rt=1\.A+\.B+\.C+; Path=\/abcd2345\/\.aj\/rt; /);
  const renew = readFileSync(pub('js/renew.js'), 'utf8');
  assert.match(renew, /const ENDPOINT = \(globalThis\.AJSeat \? globalThis\.AJSeat\.base : '\/'\) \+ '\.aj\/rt';/);
});

function storage(init = {}) {
  const m = new Map(Object.entries(init));
  return { get length() { return m.size; }, key: (i) => [...m.keys()][i] ?? null, getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k), clear: () => m.clear(), _m: m };
}
function runScope(pathname, ls) {
  const win = { location: { pathname }, localStorage: ls };
  win.window = win;
  runInNewContext(readFileSync(pub('js/seatscope.js'), 'utf8'), win);
  return win;
}

test('seatscope.js: a seat page reads / writes its own keys; first visit starts from the root preferences; root untouched', () => {
  const ls = storage({ 'aj.lang': 'en', 'aj.theme': 'dark', 'aj.paired': '123', 'aj.iid': 'x'.repeat(22), 'aj.a2hs': '1' });
  const w = runScope('/abcd2345/friends', ls);
  assert.equal(w.AJSeat.id, 'abcd2345'); assert.equal(w.AJSeat.base, '/abcd2345/'); assert.equal(w.AJSeat.db('agentjarvis'), 'agentjarvis@abcd2345');
  assert.notEqual(w.localStorage, ls);
  assert.equal(w.localStorage.getItem('aj.lang'), 'en', 'display preferences copied on the first visit');
  assert.equal(w.localStorage.getItem('aj.paired'), null, 'never the root pairing marker');
  assert.equal(w.localStorage.getItem('aj.iid'), null, 'never the root install id');
  assert.equal(w.localStorage.getItem('aj.a2hs'), null, 'the Home Screen hint starts again for the seat');
  w.localStorage.setItem('aj.lang', 'zh'); w.localStorage.setItem('aj.fontScale', 1.2);
  assert.equal(ls.getItem('aj.lang'), 'en', 'the root key unchanged');
  assert.equal(ls.getItem('aj.lang@abcd2345'), 'zh'); assert.equal(ls.getItem('aj.fontScale@abcd2345'), '1.2');
  const other = runScope('/wxyz7777/', ls);
  assert.equal(other.localStorage.getItem('aj.fontScale'), null, 'another seat never sees it');
  ls.setItem('aj.lang', 'xx');
  const again = runScope('/abcd2345/', ls);
  assert.equal(again.localStorage.getItem('aj.lang'), 'zh', 'copied once only');
  assert.deepEqual([...Array(again.localStorage.length).keys()].map((i) => again.localStorage.key(i)).sort(), ['aj.fontScale', 'aj.lang', 'aj.seatInit', 'aj.theme']);
  again.localStorage.removeItem('aj.fontScale');
  assert.equal(ls.getItem('aj.fontScale@abcd2345'), null);
  ls.setItem('aj.seatName@abcd2345', 'Father@TB'); ls.setItem('aj.seatPk@abcd2345', 'A'.repeat(43));
  assert.equal(again.AJSeat.query(), '?n=Father%40TB&c=' + 'A'.repeat(43));
  assert.equal(again.AJSeat.name(), 'Father@TB');
  const root = runScope('/', ls);
  assert.equal(root.AJSeat.id, ''); assert.equal(root.localStorage, ls); assert.equal(root.AJSeat.db('agentjarvis'), 'agentjarvis'); assert.equal(root.AJSeat.query(), '');
  assert.equal(runScope('/friends', ls).AJSeat.id, '', '/friends is the root page');
});

test('the page: seatscope.js first in <head>; per-seat DB names; seat routes for /friends and /bots; passkey filter', () => {
  const html = readFileSync(pub('index.html'), 'utf8');
  assert.ok(html.indexOf('<script src="js/seatscope.js"></script>') < html.indexOf('<script src="brand/lang.js"></script>'));
  assert.ok(html.indexOf('<script src="js/seatscope.js"></script>') < html.indexOf('<script src="js/boot.js"></script>'));
  assert.match(readFileSync(pub('js/longrec.js'), 'utf8'), /const NAME = globalThis\.AJSeat \? globalThis\.AJSeat\.db\('agentj-rec'\) : 'agentj-rec';/);
  const app = readFileSync(pub('app.js'), 'utf8');
  assert.doesNotMatch(app, /location\.pathname ?=== ?'\/bots'|location\.pathname === FRIENDS_PATH/, 'routes go through seats.routeOf');
  assert.match(app, /passkey\.assert\(prep\.challenge, allow\)/);
  assert.match(app, /if \(other !== seats\.SEAT\) \{ pkRestoreOther\(other\); return renderPk\(\); \}/, 'another seat\'s passkey never restores on a seat page');
  assert.match(app, /if \(seats\.SEAT && \(await seats\.seatId\(to\.channel\)\) !== seats\.SEAT\) throw new Error\('seat'\);/, '…nor its renewal ticket');
  const fid = readFileSync(pub('js/faceid.js'), 'utf8');
  assert.match(fid, /export async function assert\(challenge, allow = \[\]\)/);
  assert.match(fid, /allowCredentials: allow\.map\(\(id\) => \(\{ type: 'public-key', id: unb64u\(id\), transports: TRANSPORTS\.unknown \}\)\)/);   // P555 d: + transports
  assert.match(readFileSync(pub('sw.js'), 'utf8'), /u\.pathname === new URL\('\.\/share-target', self\.location\.href\)\.pathname/, 'each worker takes only its own scope\'s shares (root ≠ seat)');
  const store = readFileSync(pub('js/store.js'), 'utf8');
  assert.match(store, /r\.onupgradeneeded = \(\) => r\.transaction\.abort\(\);/, 'reading the root database never creates it');
  assert.doesNotMatch(store, /exportKey\('(pkcs8|jwk)'/, 'the move copies CryptoKeys as structured clones; nothing exported');
});

test('a scanned seat pairing link is a pairing link (not chat text)', async () => {
  const w = await import('../../protocol/wire.js');
  assert.equal(w.classifyScan('https://m.agentj.app/abcd2345/#p=123'), 'pair');
  assert.equal(w.classifyScan('https://m.agentj.app/#p=123'), 'pair');
  assert.equal(w.classifyScan('http://localhost:8080/abcd2345/#p=123'), 'pair');
  assert.equal(w.classifyScan('https://m.agentj.app/ABCD2345/#p=123'), 'data');
  assert.equal(w.classifyScan('https://m.agentj.app/abcd2345/x/#p=123'), 'data');
  assert.ok(webcrypto.subtle);
});
