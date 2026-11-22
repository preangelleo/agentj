// Audit 2026-11-22 (N-W1 / N-W2 / N-W3): a stand-in IndexedDB whose failures are switched on and off while the page module
// runs — a slow or hung read, a hung / blocked / failing open, a lost connection, a store error on one key. One Map per
// database name (the X25519 probe database never touches the page's records). Every put and delete is logged.
export function fakeIDB() {
  const dbs = new Map();
  const ctl = {
    open: 'ok',            // ok | fail | hang | blocked | throw  (the page's database; the probe database follows `probe`)
    openDelay: 0,          // ms before an `ok` open answers
    probe: 'ok',           // ok | fail — fail = "this browser cannot keep an X25519 key" (WebKit): the wrapped record is used
    slow: new Map(),       // key → ms a request on that key takes
    hang: new Set(),       // keys whose request (and so its transaction) never completes
    failGet: new Map(),    // key → error name: the read's transaction fails with it
    failPut: new Map(),    // key → error name
  };
  const log = { opens: 0, puts: [], dels: [], live: new Set(), all: [] };
  const err = (name) => Object.assign(new Error(name), { name });
  class DB {
    constructor(name) { this.name = name; this.data = dbs.get(name) ?? dbs.set(name, new Map()).get(name); this.closed = false; this.lost = false; log.live.add(this); log.all.push(this); }
    createObjectStore() {}
    close() { this.closed = true; log.live.delete(this); }
    loseConnection() { this.lost = true; log.live.delete(this); this.onclose && this.onclose(); }
    transaction(_store, mode) {
      if (this.closed || this.lost) throw err('InvalidStateError');
      const tx = { error: null, done: false, abort() { this.done = true; } }, main = this.name.startsWith('agentjarvis');
      const req = (k, fnv, bad) => {
        const r = { result: undefined };
        if (main && ctl.hang.has(k)) return r;
        setTimeout(() => {
          if (tx.done) return;
          if (main && bad) { tx.done = true; tx.error = err(bad); tx.onerror && tx.onerror(); return; }
          r.result = fnv();
          setTimeout(() => { if (!tx.done) { tx.done = true; tx.oncomplete && tx.oncomplete(); } }, 0);
        }, main ? ctl.slow.get(k) ?? 0 : 0);
        return r;
      };
      tx.objectStore = () => ({
        get: (k) => req(k, () => this.data.get(k), ctl.failGet.get(k)),
        put: (v, k) => req(k, () => { if (mode !== 'readwrite') throw err('ReadOnlyError'); if (main) log.puts.push(k); this.data.set(k, v); return k; }, ctl.failPut.get(k)),
        delete: (k) => req(k, () => { if (main) log.dels.push(k); this.data.delete(k); }),
      });
      return tx;
    }
  }
  const indexedDB = {
    open(name) {
      const main = name.startsWith('agentjarvis'), how = main ? ctl.open : ctl.probe;
      if (main) log.opens++;
      if (how === 'throw') throw err('SecurityError');
      const r = {};
      if (how === 'hang') return r;
      setTimeout(() => {
        if (how === 'blocked') { r.onblocked && r.onblocked(); return; }
        if (how === 'fail') { r.error = err('UnknownError'); r.onerror && r.onerror(); return; }
        r.result = new DB(name); r.onupgradeneeded && r.onupgradeneeded(); r.onsuccess && r.onsuccess();
      }, main ? ctl.openDelay : 0);
      return r;
    },
    deleteDatabase(name) { dbs.delete(name); },
  };
  const data = (name = 'agentjarvis') => dbs.get(name) ?? dbs.set(name, new Map()).get(name);
  return { indexedDB, ctl, log, data };
}
/** localStorage stand-in (node has none): the paired marker "aj.paired" lives here. */
export function fakeLocalStorage(init = {}) {
  const m = new Map(Object.entries(init));
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => { m.set(k, String(v)); }, removeItem: (k) => { m.delete(k); }, map: m };
}
/** Install both for one test body; every `fresh(tag)` is a new copy of js/store.js = a new page load on the same storage. */
export async function withStorage(body, { paired = false, items = {}, blocked = false } = {}) {
  const old = { idb: globalThis.indexedDB, ls: Object.getOwnPropertyDescriptor(globalThis, 'localStorage') };
  const F = fakeIDB(), ls = fakeLocalStorage(paired ? { 'aj.paired': '1', ...items } : { ...items });
  globalThis.indexedDB = F.indexedDB;
  // blocked = the browser refuses localStorage itself (reading the property throws, as Safari does with site data blocked)
  if (blocked) Object.defineProperty(globalThis, 'localStorage', { configurable: true, get() { throw Object.assign(new Error('The operation is insecure.'), { name: 'SecurityError' }); } });
  else Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: ls });
  let n = 0;
  const fresh = (tag) => import('../public/js/store.js?audit1122-' + tag + '-' + (++n) + '-' + Math.random().toString(36).slice(2));
  try { await body({ F, ls, fresh }); }
  finally {
    globalThis.indexedDB = old.idb;
    if (old.ls) Object.defineProperty(globalThis, 'localStorage', old.ls); else delete globalThis.localStorage;
  }
}
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
/** p, or the string 'HUNG' when it has not settled within ms (a promise that never settles must fail a test, not stall it). */
export const settled = (p, ms) => { let t; return Promise.race([p.then((v) => ({ ok: true, v }), (e) => ({ ok: false, e })), new Promise((r) => { t = setTimeout(() => r('HUNG'), ms); })]).finally(() => clearTimeout(t)); };
export const hex = (u8) => Buffer.from(u8).toString('hex');
