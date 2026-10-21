// P182: long recordings survive a reload, a crash or a dropped connection. Every few seconds the recorder's newest chunk is
// sealed (store.js sealBytes: the same per-profile AES-GCM key as the drafts) and written to its own IndexedDB database
// "agentj-rec"; a 20-minute segment is a run of chunks, a take is up to 10 segments. Nothing here talks to the network:
// relay.js turns a closed segment into a tray attachment (uploaded at once), and drops it from here when the message that
// carried it was accepted, or when the human removes it. Unpair / revoke wipe the whole database (wipe()).
import { sealBytes, unsealBytes } from './store.js';

const NAME = 'agentj-rec';
// A conservative container floor, not voice activity detection: silence with real frames is valid.
export const MIN_REC_BYTES = 256, MIN_REC_SECS = 0.5;
export const usable = (blob, secs) => !!blob && blob.size >= MIN_REC_BYTES && Number.isFinite(secs) && secs >= MIN_REC_SECS;
const firstChunks = new Map(); // before the first usable slice, nothing is persisted
let dbp = null;
function db() {
  if (!dbp) dbp = new Promise((res, rej) => {
    const r = indexedDB.open(NAME, 1);
    r.onupgradeneeded = () => {
      r.result.createObjectStore('takes', { keyPath: 'id' });
      r.result.createObjectStore('chunks', { keyPath: ['take', 'seg', 'i'] });
    };
    r.onsuccess = () => { const d = r.result; d.onversionchange = () => { d.close(); dbp = null; }; res(d); };
    r.onerror = () => { dbp = null; rej(r.error); };
  });
  return dbp;
}
async function tx(stores, mode, fn) {
  const d = await db();
  return new Promise((res, rej) => {
    const t = d.transaction(stores, mode);
    let out;
    Promise.resolve(fn(t, (v) => { out = v; })).catch(rej);
    t.oncomplete = () => res(out);
    t.onerror = t.onabort = () => rej(t.error);
  });
}
const req = (r) => new Promise((res, rej) => { r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
const range = (take, seg) => seg == null
  ? IDBKeyRange.bound([take, -Infinity, -Infinity], [take, Infinity, Infinity])
  : IDBKeyRange.bound([take, seg, -Infinity], [take, seg, Infinity]);

// one write queue: a segment is closed only after its last chunk landed
let q = Promise.resolve();
const later = (fn) => (q = q.then(fn, fn).catch(() => {}));

export async function startTake(mime) {
  const id = Date.now().toString(36) + '-' + Array.from(crypto.getRandomValues(new Uint8Array(4)), (x) => x.toString(16).padStart(2, '0')).join('');
  await later(() => tx(['takes'], 'readwrite', (t) => t.objectStore('takes').put({ id, t0: Date.now(), mime, segs: {} })));
  return id;
}
/** One recorder chunk (Blob) of segment `seg`, `ms` long; sealed before it is stored. */
export function addChunk(take, seg, i, blob, ms) {
  return later(async () => {
    const key = take + ':' + seg;
    let batch = firstChunks.get(key) || [];
    if (i === 0 || batch.length) {
      batch.push({i, blob, ms}); firstChunks.set(key, batch);
      if (!usable(new Blob(batch.map(x => x.blob)), batch.reduce((n, x) => n + x.ms, 0) / 1000)) return;
      firstChunks.delete(key);
    } else batch = [{i, blob, ms}];
    for (const x of batch) {
      const rec = await sealBytes(new Uint8Array(await x.blob.arrayBuffer()));
      await tx(['takes', 'chunks'], 'readwrite', async (t) => {
        t.objectStore('chunks').put({ take, seg, i: x.i, ms: x.ms, rec });
        const takes = t.objectStore('takes'), v = await req(takes.get(take));
        if (v && !v.mime && x.blob.type){ v.mime = x.blob.type; takes.put(v); }
      });
    }
  });
}
function patchSeg(take, seg, patch) {
  return later(() => tx(['takes'], 'readwrite', async (t) => {
    const s = t.objectStore('takes');
    const v = await req(s.get(take));
    if (!v) return;
    v.segs[seg] = { ...(v.segs[seg] || {}), ...patch };
    s.put(v);
  }));
}
export const closeSeg = (take, seg, ms) => patchSeg(take, seg, { ms, done: true });
export const setBid = (take, seg, bid) => patchSeg(take, seg, { bid });

/** The segment as one Blob (its chunks in order, unsealed); null when nothing readable is left. */
export async function segBlob(take, seg, mime) {
  await q;
  const rows = await tx(['chunks'], 'readonly', async (t, out) => out(await req(t.objectStore('chunks').getAll(range(take, seg)))));
  const parts = [];
  for (const r of rows) { const b = await unsealBytes(r.rec); if (b) parts.push(b); }
  return parts.length ? new Blob(parts, { type: mime || 'audio/webm' }) : null;
}
export function dropSeg(take, seg) {
  return later(() => { firstChunks.delete(take + ':' + seg); return tx(['takes', 'chunks'], 'readwrite', async (t) => {
    t.objectStore('chunks').delete(range(take, seg));
    const s = t.objectStore('takes');
    const v = await req(s.get(take));
    if (!v) return;
    delete v.segs[seg];
    const left = await req(t.objectStore('chunks').count(range(take)));
    if (!Object.keys(v.segs).length && !left) s.delete(take); else s.put(v);
  }); });
}
export function dropTake(take) {
  return later(() => {
    for (const key of firstChunks.keys()) if (key.startsWith(take + ':')) firstChunks.delete(key);
    return tx(['takes', 'chunks'], 'readwrite', (t) => { t.objectStore('chunks').delete(range(take)); t.objectStore('takes').delete(take); });
  });
}
/** Takes still on this phone (not yet accepted by the computer): [{id, t0, mime, segs:[{n, ms, bid}]}], oldest first.
 *  A segment whose recorder never closed (reload / crash mid-take) is counted from its chunks. `except`: take ids in use. */
export async function pending(except = []) {
  await q;
  const [takes, keys] = await tx(['takes', 'chunks'], 'readonly', async (t, out) => {
    const a = await req(t.objectStore('takes').getAll());
    const rows = await req(t.objectStore('chunks').getAll());
    out([a, rows.map((r) => [r.take, r.seg, r.ms])]);
  });
  const out = [];
  for (const v of takes.sort((a, b) => a.t0 - b.t0)) {
    if (except.includes(v.id)) continue;
    const per = {};
    for (const [tk, sg, ms] of keys) if (tk === v.id) per[sg] = (per[sg] || 0) + (ms || 0);
    const segs = Object.keys(per).map(Number).sort((a, b) => a - b)
      .map((n) => ({ n, ms: (v.segs[n]?.done ? v.segs[n].ms : per[n]), bid: v.segs[n] && v.segs[n].bid }));
    const valid = [];
    for (const s of segs) {
      // Historical 0:00 entries are purged before offering a dialog; done:0 must not fall back to chunk wall time.
      const blob = await segBlob(v.id, s.n, v.mime);
      if (usable(blob, s.ms / 1000)) valid.push(s);
      else await dropSeg(v.id, s.n);
    }
    if (valid.length) out.push({ id: v.id, t0: v.t0, mime: v.mime, segs: valid });
    else await dropTake(v.id);
  }
  return out;
}
/** Unpair / revoke / remove: every stored recording goes (and its key went with store.wipeLocal). */
export async function wipe() {
  firstChunks.clear();
  try { if (dbp) (await dbp).close(); } catch { /* not open */ }
  dbp = null;
  await new Promise((res) => { try { const r = indexedDB.deleteDatabase(NAME); r.onsuccess = r.onerror = r.onblocked = () => res(); } catch { res(); } });
}
