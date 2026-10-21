import { touch } from './snap.js';
// The adapter: relay's HTTP calls → Agent J's end-to-end app messages (agentjarvis/parity/DESIGN.md §a table). Every call
// resolves with the shape the ported page expects and never rejects; "offline" when there is no ready session.
//   POST /say · /say/cancel        → say → say_res / say_state · say_cancel → say_cancel_res        (§10.2)
//   POST /attach /upload /detach   → blob_open / blob_chunk / blob_end → blob_done · blob_drop      (§10.3, blobs.js)
//   POST /transcribe               → blob purpose "asr" → asr_res                                   (§10.9)
//   GET /history                   → hist_get → hist_page                                           (§10.5, snap.js)
//   POST /approve · POST /ask      → answer (signed) · q_answer (signed)                            (§8, §10.7)
//   POST /interrupt · /model · GET /menu → slash stop · model_set → model_res · menu_get → menu      (§8, §10.11, §10.12)
// Every write pins the session generation it started on (session.gen()): a reconnect or a new pairing in between refuses
// the send ("offline"), so nothing meant for one session — or one computer — is ever sent on another (P33-X01 / X02).
import { sendApp, isReady, peer, ask1, channel, textMax, gen } from './session.js';
import { Upload, newId } from './blobs.js';
import { approveMessage, mailAllowMessage, questionMessage, controlMessage, b64u, deviceId } from '../proto/wire.js';
import { deviceKey, signKey } from './store.js';
import * as snap from './snap.js';
import { approve as passkeyApprove, canAssert } from './faceid.js';
import { unb64u } from '../proto/wire.js';

let myId = null;
export async function myDeviceId() { return myId ?? (myId = await deviceId((await deviceKey()).pub)); }
export const myIdSync = () => myId;

/** §10.0 text rules: \r\n / \r → \n, no C0 control character but \t and \n, no lone surrogate. */
export function cleanText(s) {
  return String(s ?? '').replace(/\r\n?/g, '\n').replace(/[\u0000-\u0008\u000b-\u001f\u007f]/g, '')
    .replace(/[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/g, '�');
}

// say_res / say_state / say_cancel_res carry the send id (sid), not r.
const sayWait = new Map();       // sid → resolve(say_res)
const cancelWait = new Map();    // sid → resolve(say_cancel_res)
let onSayState = () => {};
export function onState(fn) { onSayState = fn; }
/** → true when m was one of ours. */
export function route(m) {
  if (m.t === "tts_chunk" || m.t === "tts_end") { routeSpeech(m); return true; }
  if (m.t === 'say_res' && typeof m.sid === 'string') { const f = sayWait.get(m.sid); if (f) { sayWait.delete(m.sid); f(m); } return true; }
  if (m.t === 'say_cancel_res' && typeof m.sid === 'string') { const f = cancelWait.get(m.sid); if (f) { cancelWait.delete(m.sid); f(m.r); } return true; }
  if (m.t === 'say_state' && typeof m.sid === 'string') { onSayState(m); return true; }
  return false;
}

export const newSid = () => newId();

/** P286: this computer takes several quotes in one say (`quote_more`, §10.6). */
export const multiQuote = () => !!(peer.p33 && peer.mq);
export async function say({ sid, text, att = [], reply_to = null, excerpt = null, more = null }) {
  const g = gen();
  if (!g) return { ok: false, why: 'offline' };
  text = cleanText(text);
  if (!peer.p33) {                                   // an older host: chat as §8 msg, no attachments / quotes
    if (att.length) return { ok: false, why: 'shape' };
    if (text.length > textMax()) return { ok: false, why: 'too_long' };
    try { await sendApp({ t: 'msg', id: newId().slice(0, 16), text, ts: Date.now() }, g); } catch { return { ok: false, why: 'offline' }; }
    return { ok: true, state: 'delivered' };
  }
  const m = { t: 'say', sid, text, ts: Date.now() };
  if (att.length) m.att = att.slice(0, 10);
  if (Number.isInteger(reply_to)) {
    m.reply_to = reply_to;
    if (typeof excerpt === 'string' && excerpt) m.excerpt = cleanText(excerpt).slice(0, 2000);
    const mq = Array.isArray(more) ? more.filter((q) => q && Number.isInteger(q.id)).slice(0, 4) : [];
    if (mq.length) m.quote_more = mq.map((q) => typeof q.excerpt === 'string' && q.excerpt ? { id: q.id, ex: cleanText(q.excerpt).slice(0, 2000) } : { id: q.id });
  }
  const exn = (m.excerpt ? m.excerpt.length : 0) + (m.quote_more || []).reduce((n, q) => n + (q.ex ? q.ex.length : 0), 0);
  if ((m.text.length + exn) > textMax()) return { ok: false, why: 'too_long' };
  const res = new Promise((resolve) => {
    sayWait.set(sid, resolve);
    setTimeout(() => { if (sayWait.delete(sid)) resolve({ ok: false, why: 'timeout' }); }, 30000);
  });
  try { await sendApp(m, g); } catch { sayWait.delete(sid); return { ok: false, why: 'offline' }; }
  const r = await res;
  return r.ok === true ? { ok: true, state: r.state === 'queued' ? 'queued' : 'delivered', turn: r.turn } : { ok: false, why: typeof r.why === 'string' ? r.why : 'other', att: r.att };
}

/** P398 (§10.2): after a reconnect, where this device's still-queued sends are — the answers come back as say_state. */
export function saySync(sids) {
  const g = gen();
  if (!g || !peer.p33 || !peer.ss || !Array.isArray(sids) || !sids.length) return false;
  sendApp({ t: 'say_sync', sids: sids.slice(0, 32) }, g).catch(() => {});
  return true;
}
export async function sayCancel(sid) {
  const g = gen();
  if (!g || !peer.p33) return 'offline';
  const res = new Promise((resolve) => {
    cancelWait.set(sid, resolve);
    setTimeout(() => { if (cancelWait.delete(sid)) resolve('timeout'); }, 15000);
  });
  try { await sendApp({ t: 'say_cancel', sid }, g); } catch { cancelWait.delete(sid); return 'offline'; }
  return res;
}

export async function transcribe(blob, secs) {
  if (!isReady()) return { ok: false, why: 'net' };
  const u = new Upload(blob, { purpose: 'asr', origin: 'recording', name: `voice-${Math.round(secs)}s.wav`, mime: 'audio/wav', secs });
  const r = await u.start();
  return r.ok ? { ok: true, text: r.text } : { ok: false, why: r.why };
}

export const history = (q) => snap.history(q);
export const findHistory = (q, sort) => snap.findHistory(q, sort);   // P188 (§28)
export const canFind = () => peer.p33 && peer.hfind;

/** P296 / P339: what an allow may add, in the card's order — a mail card's offered addresses (allow_offer), a goal `hold`
 *  card's one extra goal grant (goal_add). Only what the card offered; the host checks the same line in the signature. */
export function allowOffer(p) {
  if (!p) return [];
  if (p.goal === 'hold' && typeof p.goal_add === 'string' && p.goal_add) return [p.goal_add];
  return Array.isArray(p.allow_offer) ? p.allow_offer : [];
}
export function allowPicks(p, allowAdd) {
  return Array.isArray(allowAdd) ? allowOffer(p).filter((a) => allowAdd.includes(a)) : [];
}

/** action: allow | deny | batch. Signed over exactly what the sheet shows (§8). allowAdd (P296, a mail card): the ticked
 *  「以后发给 X 不用再确认」 addresses, in the card's order — signed with the allow (mailAllowMessage). */
export async function approve(p, action, allowAdd) {
  touch(p.id);
  const g = gen();
  if (!g) return { ok: false, why: 'offline' };
  let fa = null;
  if (action !== 'deny' && p.passkey_required) {
    if (action === 'batch' || !p.fa || !p.fa_ch || !canAssert()) return { ok: false, why: 'passkey_required' };
    try { fa = await passkeyApprove(unb64u(p.fa_ch), p.fa); } catch { return { ok: false, why: 'passkey_required' }; }
    if (!fa) return { ok: false, why: 'passkey_required' };
  }
  const sk = await signKey();
  if (!sk) return { ok: false, why: 'no_key' };
  try {
    const batch = action === 'batch';
    if (batch && !p.scope) return { ok: false, why: 'no_batch' };
    const decision = batch ? 'allow_batch' : action === 'allow' ? 'allow' : 'deny';
    const picks = decision === 'allow' ? allowPicks(p, allowAdd) : [];
    const msg = picks.length ? await mailAllowMessage(channel(), await myDeviceId(), p.id, p.tool, p.summary, picks)
      : await approveMessage(channel(), await myDeviceId(), p.id, decision, p.tool, p.summary, batch ? p.scope : undefined);
    const sig = new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, sk.priv, msg));
    await sendApp(batch ? { t: 'answer', id: p.id, ok: true, batch: true, sig: b64u(sig) } : { t: 'answer', id: p.id, ok: decision === 'allow', sig: b64u(sig), ...(picks.length ? { allow_add: picks } : {}), ...(fa ? { fa } : {}) }, g);
    return { ok: true };
  } catch { return { ok: false, why: 'offline' }; }
}

/** picks = [[1,3],[2]] (1-based) or null = 取消问题. Signed over the questions as shown (§10.7). */
export async function answerQ(q, picks) {
  touch(q.id);
  const g = gen();
  if (!g) return { ok: false, why: 'offline' };
  const sk = await signKey();
  if (!sk) return { ok: false, why: 'no_key' };
  try {
    const clean = picks ? picks.map((p) => typeof p === "string" ? p : [...new Set(p)].sort((a, b) => a - b)) : null;
    const msg = await questionMessage(channel(), await myDeviceId(), q.id, clean ? 'answer' : 'cancel', q.raw, clean);
    const sig = b64u(new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, sk.priv, msg)));
    await sendApp(clean ? { t: 'q_answer', id: q.id, pick: clean, sig } : { t: 'q_answer', id: q.id, cancel: true, sig }, g);
    return { ok: true };
  } catch { return { ok: false, why: 'offline' }; }
}

export function slash(cmd, arg = '', confirm = false) {
  if (!isReady()) return false;
  if (cmd === 'update') return upgradeFlow();        // P203: upgcard.start — asks first; nothing in the way → signedUpgrade
  sendApp({ t: 'slash', cmd, ...(arg ? { arg: String(arg).slice(0, 200) } : {}), ...(confirm ? { confirm: true } : {}) }).catch(() => {});
  return true;
}

export async function model(want) {
  if (!isReady()) return { ok: false, why: 'offline' };
  if (!peer.p33) return { ok: false, why: 'unsupported' };
  const m = await ask1(want.default ? { t: 'model_set', default: true } : { t: 'model_set', model: want.model ?? null, effort: want.effort ?? null }, 'model_res', 15000);
  if (m.t === 'model_res') return m.ok === true ? { ok: true, queued: m.why === 'queued', superseded: m.why === 'superseded' } : { ok: false, why: m.why || 'other' };
  return { ok: false, why: m.t };
}

/** P118 (B10): this reply → the owner's own Telegram private chat (host cap `tgfwd`). → {ok, why?} */
export async function tgForward(id) {
  if (!isReady() || !peer.p33 || !peer.tgfwd) return { ok: false, why: 'not_configured' };
  const m = await ask1({ t: 'tg_fwd', id }, 'tg_fwd_res', 30000);
  if (m.t === 'tg_fwd_res') return m.ok === true ? { ok: true } : { ok: false, why: m.why || 'send_failed' };
  return { ok: false, why: m.t };
}

/** P183: one file a reply shows → the owner's own Telegram private chat (host cap `tgfile`; the computer re-checks and
 *  re-reads the file itself — no bytes go up from the phone). → {ok, why?} (why: not_configured | not_found | too_big | …) */
export async function tgForwardFile(mid) {
  if (!isReady() || !peer.p33 || !peer.tgfile) return { ok: false, why: 'unsupported' };
  if (!peer.tgfwd) return { ok: false, why: 'not_configured' };
  const m = await ask1({ t: 'tg_fwd', mid }, 'tg_fwd_res', 120000);
  if (m.t === 'tg_fwd_res') return m.ok === true ? { ok: true } : { ok: false, why: m.why || 'send_failed' };
  return { ok: false, why: m.t };
}

/** P160 (§22 busy_get): one background task's read-only detail → {ok, item|null} | {ok:false, why}. */
export async function busyDetail(id) {
  if (!isReady() || !peer.p33 || !peer.busyget) return { ok: false, why: 'unsupported' };
  const m = await ask1({ t: 'busy_get', id: String(id).slice(0, 40) }, 'busy_info', 8000);
  if (m.t === 'busy_info') return { ok: true, item: m.item && typeof m.item === 'object' ? m.item : null };
  return { ok: false, why: m.t };
}

export async function menu() {
  if (!isReady() || !peer.p33) return null;
  const m = await ask1({ t: 'menu_get' }, 'menu', 15000);
  return m.t === 'menu' ? m : null;
}

let signReady = false;
export const canSign = () => signReady;
/** Called by main() after the legacy-host check: makes / loads the approval key once. */
export async function initSign() { signReady = !!(await signKey()); return signReady; }


const speechWait = new Map();
export function speechAudio(id) {
  const r=newId(), g=gen();
  if (!g) return Promise.reject(new Error('offline'));
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{speechWait.delete(r);reject(new Error('timeout'));},90000);
    speechWait.set(r,{parts:[],size:0,g,resolve,reject,timer});
    sendApp({t:'tts_get',r,id},g).catch(()=>{clearTimeout(timer);speechWait.delete(r);reject(new Error('offline'));});
  });
}
function routeSpeech(m) {
  const w=speechWait.get(m.r); if(!w)return;
  const fail=(why='engine')=>{clearTimeout(w.timer);speechWait.delete(m.r);const err=new Error('speech failed');err.why=why;w.reject(err);};
  if(w.g!==gen())return fail();
  if(m.t==='tts_chunk'){
    if(m.i!==w.parts.length || typeof m.data!=='string' || m.data.length>32768)return fail();
    try{const bytes=Uint8Array.from(atob(m.data),c=>c.charCodeAt(0));w.size+=bytes.length;if(w.size>8*1024*1024)return fail();w.parts.push(bytes);}catch{return fail();}
  }else{
    if(m.ok===false)return fail(['missing_key','provider_rejected','provider_busy','provider_unavailable','network','busy','not_ready','missing'].includes(m.why)?m.why:'engine');
    if(!m.ok || m.bytes!==w.size || m.mime!=='audio/wav')return fail();
    clearTimeout(w.timer);speechWait.delete(m.r);w.resolve(new Blob(w.parts,{type:'audio/wav'}));
  }
}

// P67: authenticated paired-phone recovery, outside the broken model's execution path.
export async function providers() {
  if (!isReady()) return null;
  const m = await ask1({t:'provider_get'}, 'providers', 15000);
  return m.t === 'providers' ? m : null;
}
export async function providerKey(provider) {
  if (!isReady()) return {ok:false,why:'offline'};
  const m = await ask1({t:'provider_key',provider}, 'provider_res', 15000);
  return m.t === 'provider_res' ? m : {ok:false,why:m.t};
}
// P352 「模型和 API Key」 (js/keys.js): the host key file's names and states — never a value, except the one copy reply
export async function keysGet() {
  if (!isReady()) return null;
  const m = await ask1({t:'keys_get'}, 'keys', 15000);
  return m.t === 'keys' ? m : null;
}
/** op copy | delete, after Face ID (fa = the assertion over keysDigest). → keys_res (copy: {ok, value, n}) or {ok:false, why}. */
export async function keysOp(op, name, id, n, fa, more) {   // P362: more = {src, keep, fp} for op import
  if (!isReady()) return {ok:false,why:'offline'};
  const m = await ask1({t:'keys_op',op,name,id,n,fa,...(op === 'import' && more ? {src:more.src,keep:more.keep === true,fp:more.fp} : {})}, 'keys_res', 30000);
  return m.t === 'keys_res' ? m : {ok:false,why:m.t};
}
/** Edit / Add: the host opens the existing encrypted key card for `name` (its 同意 asks for Face ID itself). */
export async function keysSet(name) {
  if (!isReady()) return {ok:false,why:'offline'};
  const m = await ask1({t:'keys_set',name}, 'keys_res', 15000);
  return m.t === 'keys_res' ? m : {ok:false,why:m.t};
}
export async function asrInstall(onProgress) {
  if (!isReady()) return {ok:false,why:'offline'};
  const m = await ask1({t:'asr_install',enable:true}, 'asr_install_res', 600000, m => { if (m.t === 'asr_install_progress') onProgress?.(m.stage); });
  return m.t === 'asr_install_res' ? m : {ok:false,why:m.t};
}

export async function models() {
  if (!isReady()) return null;
  const m = await ask1({t:'models_get'}, 'models', 15000);
  return m.t === 'models' ? m : null;
}

/** P153 (§3.3): a QR + link that adds another remote, signed like every phone control. The host answers this session only.
 *  → {ok:true, link, qr (PNG data URI), ttl} | {ok:false, why}. */
export async function pairNew() {
  if (!peer.pair1) return { ok: false, why: 'old_host' };
  const g = gen(), sk = await signKey();
  if (!g || !sk) return { ok: false, why: sk ? 'offline' : 'no_key' };
  const n = [...crypto.getRandomValues(new Uint8Array(16))].map(x => x.toString(16).padStart(2, '0')).join(''), ts = Date.now();
  const msg = await controlMessage(channel(), await myDeviceId(), 'pair_new', n, ts, {});
  const sig = b64u(new Uint8Array(await crypto.subtle.sign({name:'Ed25519'}, sk.priv, msg)));
  if (g !== gen()) return { ok: false, why: 'offline' };
  const m = await ask1({ t: 'pair_new', n, ts, sig }, 'pair_link', 15000);
  if (m.t !== 'pair_link') return { ok: false, why: m.t === 'timeout' ? 'timeout' : 'offline' };
  if (!m.ok) return { ok: false, why: typeof m.why === 'string' ? m.why.slice(0, 32) : 'failed' };
  let u = null;
  try { u = new URL(m.link); } catch { /* checked below */ }
  const loop = u && ['127.0.0.1', 'localhost'].includes(u.hostname);       // test pages only
  if (typeof m.qr !== 'string' || !m.qr.startsWith('data:image/png;base64,') || m.qr.length > 40000 || !u
      || !(u.protocol === 'https:' || loop)) return { ok: false, why: 'failed' };
  return { ok: true, link: m.link, qr: m.qr, ttl: Number.isInteger(m.ttl) && m.ttl > 0 && m.ttl <= 600 ? m.ttl : 300 };
}
/** The QR sheet closed before a device was added: the host ends that pairing. */
export function pairStop() { if (isReady()) sendApp({ t: 'pair_stop' }).catch(() => {}); }

// P203 (§29.2): app.js plugs in upgcard.start (impact → card → this); without it (node tests) /update is the old one step
let upgradeFlow = () => signedUpgrade();
export function setUpgradeFlow(fn) { upgradeFlow = fn; }
export const upgradeNow = () => signedUpgrade();
async function signedUpgrade() {
  const g = gen(), sk = await signKey();
  if (!g || !sk) return false;
  const n = [...crypto.getRandomValues(new Uint8Array(16))].map(x => x.toString(16).padStart(2, '0')).join(''), ts = Date.now();
  const msg = await controlMessage(channel(), await myDeviceId(), 'update', n, ts, {});
  const sig = b64u(new Uint8Array(await crypto.subtle.sign({name:'Ed25519'}, sk.priv, msg)));
  if (g !== gen()) return false;
  await sendApp({t:'slash',cmd:'update',n,ts,sig});
  return true;
}

// A visual receipt, not an approval; sent only on this paired Noise generation.
export async function readHistory(epoch, id) {
  const g = gen();
  if (!g) return;
  try { await sendApp({t: 'hist_read', epoch, id}, g); } catch {}
}
