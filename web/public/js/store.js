// IndexedDB (db "agentjarvis", store "kv" — the name and version stay, so paired phones keep their keys) and the
// phone-local encrypted store (PROTOCOL §10.14). Records: device (X25519, non-extractable), sign (Ed25519,
// non-extractable), host (relay, channel, host key, cached Agent name), local (AES-256-GCM key, non-extractable) and two
// sealed records — draft, ihist — that hold only {iv, ct}: drafts and sent-input history are not stored as readable JSON.
// What that buys (P33-C05, PROTOCOL §10.14): the key sits in the same origin's IndexedDB as the ciphertext, so it stops a
// casual read of the stored files (a backup, a copied store) — not code running in this origin, and not someone who copies
// the whole browser profile and runs it. The same holds for the device keys.
import { generateKeypair, sha256, concat } from '../proto/noise.js';
import { generateSigningKeypair, b64u } from '../proto/wire.js';

function idb() {
  return new Promise((res, rej) => {
    const r = indexedDB.open('agentjarvis', 1);
    r.onupgradeneeded = () => r.result.createObjectStore('kv');
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error);
  });
}
async function kv(mode, fn) {
  const db = await idb();
  return new Promise((res, rej) => {
    const tx = db.transaction('kv', mode);
    const req = fn(tx.objectStore('kv'));
    tx.oncomplete = () => { db.close(); res(req.result); };
    tx.onerror = tx.onabort = () => { db.close(); rej(tx.error); };
  });
}
export const dbGet = (k) => kv('readonly', (s) => s.get(k));
export const dbPut = (k, v) => kv('readwrite', (s) => s.put(v, k));
export const dbDel = (k) => kv('readwrite', (s) => s.delete(k));

// P122 (root cause of "every open asks for the QR again" on iPhone): WebKit — Safari and every iOS browser — cannot read a
// stored X25519 CryptoKey back from IndexedDB (WebKit bug 312279: get() returns null or never completes; Ed25519, P-256 and
// AES keys are fine). So the device key is stored directly only where a probe in a throw-away database proves the round trip;
// elsewhere it is kept WRAPPED ('device2'): AES-GCM(pkcs8) under a non-extractable AES key in the same record, unwrapped on
// load straight into a non-extractable X25519 key — the private bytes never reach page script. Trade-off (stated in
// PROTOCOL §12.1): same-origin script could unwrap it as extractable, which it could not do with a directly stored key; the
// same bound as the sealed records below (a casual read of the stored files learns nothing; code in this origin is trusted).
const READ_MS = 2500;
const within = (p, ms) => Promise.race([p, new Promise((r) => setTimeout(() => r(undefined), ms))]);
async function x25519Persists() {
  const name = 'aj-x25519-probe';
  try {
    const kp = await generateKeypair(false);
    const db = await new Promise((res, rej) => { const r = indexedDB.open(name, 1); r.onupgradeneeded = () => r.result.createObjectStore('kv'); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
    const io = (mode, fn) => new Promise((res, rej) => { const tx = db.transaction('kv', mode); const q = fn(tx.objectStore('kv')); tx.oncomplete = () => res(q.result); tx.onerror = tx.onabort = () => rej(tx.error); });
    await io('readwrite', (st) => st.put({ priv: kp.priv }, 'k'));
    const back = await within(io('readonly', (st) => st.get('k')), READ_MS);
    db.close();
    return !!(back && back.priv && back.priv.algorithm?.name === 'X25519');
  } catch { return false; } finally { try { indexedDB.deleteDatabase(name); } catch { /* best effort */ } }
}
const WRAP = (iv) => ({ name: 'AES-GCM', iv });
async function wrappedKeypair() {
  const kek = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['wrapKey', 'unwrapKey']);
  const kp = await crypto.subtle.generateKey({ name: 'X25519' }, true, ['deriveBits']);   // extractable only to be wrapped
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(await crypto.subtle.wrapKey('pkcs8', kp.privateKey, kek, WRAP(iv)));
  const pub = new Uint8Array(await crypto.subtle.exportKey('raw', kp.publicKey));
  const rec = { v: 2, pub, iv, ct, kek };
  return { rec, kp: { priv: await unwrapDevice(rec), pub } };        // the extractable original is dropped here
}
const unwrapDevice = (d) => crypto.subtle.unwrapKey('pkcs8', d.ct, d.kek, WRAP(d.iv), { name: 'X25519' }, false, ['deriveBits']);

let devKey = null;
export async function deviceKey() {
  if (devKey) return devKey;
  const w = await dbGet('device2');
  if (w && w.v === 2 && w.kek && w.ct && w.iv && w.pub) {
    try { return (devKey = { priv: await unwrapDevice(w), pub: new Uint8Array(w.pub) }); } catch { /* unusable: a new key below */ }
  }
  const d = await within(dbGet('device'), READ_MS).catch(() => undefined);
  if (d && d.priv && d.pub) return (devKey = { priv: d.priv, pub: new Uint8Array(d.pub) });
  if (await x25519Persists()) {
    const kp = await generateKeypair(false);          // extractable: false — the private key never leaves WebCrypto
    await dbPut('device', { priv: kp.priv, pub: kp.pub });
    return (devKey = kp);
  }
  const { rec, kp } = await wrappedKeypair();
  await dbPut('device2', rec);
  return (devKey = kp);
}

// ---------------------------------------------------------------- 0.15.1 (P55): surviving a lost key, and saying so
// Install id: 16 random bytes kept in BOTH localStorage ("aj.iid") and IndexedDB ("iid"), so either copy restores the
// other. The pairing msg1 carries only a per-computer hash of it (PROTOCOL §3 `iid`): when this browser lost its device
// key but kept one copy, the computer replaces the old record instead of counting a second remote; two computers cannot
// match their hashes. It is not a fingerprint: nothing about the phone goes into it, and unpairing does not reset it.
const IID_KEY = 'aj.iid';
let iidRaw = null;
async function installRaw() {
  if (iidRaw) return iidRaw;
  let v = null;
  try { v = localStorage.getItem(IID_KEY); } catch { /* blocked storage */ }
  if (!/^[A-Za-z0-9_-]{22}$/.test(v || '')) { try { v = await dbGet('iid'); } catch { v = null; } }
  if (!/^[A-Za-z0-9_-]{22}$/.test(v || '')) v = b64u(crypto.getRandomValues(new Uint8Array(16)));
  try { localStorage.setItem(IID_KEY, v); } catch { /* blocked storage */ }
  try { await dbPut('iid', v); } catch { /* the other copy still holds it */ }
  return (iidRaw = v);
}
/** The install id as this computer (channel) sees it: b64url(SHA-256("agentjarvis/iid/v1\n" + channel + "\n" + id)[0:16]). */
export async function installId(channel) {
  const raw = await installRaw();
  return b64u((await sha256(concat(new TextEncoder().encode('agentjarvis/iid/v1\n' + channel + '\n'), new TextEncoder().encode(raw)))).slice(0, 16));
}
// Paired marker (localStorage "aj.paired"): set on approval, cleared on unpair. When the page opens with the marker but
// without its pairing in IndexedDB, the browser deleted the site's data — the page says exactly that, not "removed".
const PAIRED_KEY = 'aj.paired';
export function markPaired(on) {
  try { if (on) localStorage.setItem(PAIRED_KEY, String(Math.floor(Date.now() / 1000))); else localStorage.removeItem(PAIRED_KEY); } catch { /* blocked */ }
}
export function wasPaired() { try { return !!localStorage.getItem(PAIRED_KEY); } catch { return false; } }
/** Ask the browser not to evict this origin's storage (no prompt in Safari / Chrome; a refusal changes nothing). */
export async function askPersist() { try { return await navigator.storage?.persist?.(); } catch { return false; } }

// Ed25519 approval key (PROTOCOL §8). null = this browser cannot sign → it can chat but not approve.
let signKp;
export async function signKey() {
  if (signKp !== undefined) return signKp;
  try {
    const d = await dbGet('sign');
    if (d && d.priv && d.pub) return (signKp = { priv: d.priv, pub: new Uint8Array(d.pub) });
    const kp = await generateSigningKeypair();       // private key not extractable
    await dbPut('sign', { priv: kp.priv, pub: kp.pub });
    return (signKp = kp);
  } catch { return (signKp = null); }
}

// ---------------------------------------------------------------- sealed records (§10.14)
// One AES-256-GCM key per browser profile, generated non-extractable and kept as a CryptoKey in IndexedDB (structured
// clone keeps it opaque). A sealed value = {v:1, iv, ct}; the plaintext JSON exists only in memory.
const enc = new TextEncoder();
const dec = new TextDecoder();
let localKey = null;
let wipes = 0;                                       // bumped by wipeLocal: a write started before a wipe never lands after it
async function sealKey() {
  if (localKey) return localKey;
  const w = wipes;
  const k = await dbGet('local');
  if (k && k.key) return w === wipes ? (localKey = k.key) : sealKey();
  const key = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
  if (w !== wipes) return sealKey();
  await dbPut('local', { key });
  return (localKey = key);
}
export async function seal(obj) {
  const key = await sealKey();
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, enc.encode(JSON.stringify(obj))));
  return { v: 1, iv, ct };
}
export async function unseal(rec) {
  if (!rec || rec.v !== 1 || !rec.iv || !rec.ct) return null;
  try {
    const key = await sealKey();
    return JSON.parse(dec.decode(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: rec.iv }, key, rec.ct)));
  } catch { return null; }
}
export async function putSealed(name, obj) {
  const w = wipes;
  const rec = await seal(obj);
  if (w === wipes) await dbPut(name, rec);
}
export async function getSealed(name) { try { return await unseal(await dbGet(name)); } catch { return null; } }
/** Unpair / revoke: the drafts, the input history, the offline queue (0.15.2, outbox.js) and the key that sealed them go. */
export async function wipeLocal() {
  localKey = null; wipes++;
  for (const k of ['draft', 'ihist', 'outbox', 'local']) { try { await dbDel(k); } catch { /* nothing stored */ } }
}
