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
import { approveMessage, questionMessage, controlMessage, b64u, deviceId } from '../proto/wire.js';
import { deviceKey, signKey } from './store.js';
import * as snap from './snap.js';

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

export async function say({ sid, text, att = [], reply_to = null, excerpt = null }) {
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
  }
  if ((m.text.length + (m.excerpt ? m.excerpt.length : 0)) > textMax()) return { ok: false, why: 'too_long' };
  const res = new Promise((resolve) => {
    sayWait.set(sid, resolve);
    setTimeout(() => { if (sayWait.delete(sid)) resolve({ ok: false, why: 'timeout' }); }, 30000);
  });
  try { await sendApp(m, g); } catch { sayWait.delete(sid); return { ok: false, why: 'offline' }; }
  const r = await res;
  return r.ok === true ? { ok: true, state: r.state === 'queued' ? 'queued' : 'delivered', turn: r.turn } : { ok: false, why: typeof r.why === 'string' ? r.why : 'other', att: r.att };
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

/** action: allow | deny | batch. Signed over exactly what the sheet shows (§8). */
export async function approve(p, action) {
  const g = gen();
  if (!g) return { ok: false, why: 'offline' };
  const sk = await signKey();
  if (!sk) return { ok: false, why: 'no_key' };
  try {
    const batch = action === 'batch';
    if (batch && !p.scope) return { ok: false, why: 'no_batch' };
    const decision = batch ? 'allow_batch' : action === 'allow' ? 'allow' : 'deny';
    const msg = await approveMessage(channel(), await myDeviceId(), p.id, decision, p.tool, p.summary, batch ? p.scope : undefined);
    const sig = new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, sk.priv, msg));
    await sendApp(batch ? { t: 'answer', id: p.id, ok: true, batch: true, sig: b64u(sig) } : { t: 'answer', id: p.id, ok: decision === 'allow', sig: b64u(sig) }, g);
    return { ok: true };
  } catch { return { ok: false, why: 'offline' }; }
}

/** picks = [[1,3],[2]] (1-based) or null = 取消问题. Signed over the questions as shown (§10.7). */
export async function answerQ(q, picks) {
  const g = gen();
  if (!g) return { ok: false, why: 'offline' };
  const sk = await signKey();
  if (!sk) return { ok: false, why: 'no_key' };
  try {
    const clean = picks ? picks.map((p) => [...new Set(p)].sort((a, b) => a - b)) : null;
    const msg = await questionMessage(channel(), await myDeviceId(), q.id, clean ? 'answer' : 'cancel', q.raw, clean);
    const sig = b64u(new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, sk.priv, msg)));
    await sendApp(clean ? { t: 'q_answer', id: q.id, pick: clean, sig } : { t: 'q_answer', id: q.id, cancel: true, sig }, g);
    return { ok: true };
  } catch { return { ok: false, why: 'offline' }; }
}

export function slash(cmd, arg = '', confirm = false) {
  if (!isReady()) return false;
  if (cmd === 'update') return signedUpgrade();
  sendApp({ t: 'slash', cmd, ...(arg ? { arg: String(arg).slice(0, 200) } : {}), ...(confirm ? { confirm: true } : {}) }).catch(() => {});
  return true;
}

export async function model(want) {
  if (!isReady()) return { ok: false, why: 'offline' };
  if (!peer.p33) return { ok: false, why: 'unsupported' };
  const m = await ask1(want.default ? { t: 'model_set', default: true } : { t: 'model_set', model: want.model ?? null, effort: want.effort ?? null }, 'model_res', 15000);
  if (m.t === 'model_res') return m.ok === true ? { ok: true } : { ok: false, why: m.why || 'other' };
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
  const fail=()=>{clearTimeout(w.timer);speechWait.delete(m.r);w.reject(new Error('speech failed'));};
  if(w.g!==gen())return fail();
  if(m.t==='tts_chunk'){
    if(m.i!==w.parts.length || typeof m.data!=='string' || m.data.length>32768)return fail();
    try{const bytes=Uint8Array.from(atob(m.data),c=>c.charCodeAt(0));w.size+=bytes.length;if(w.size>8*1024*1024)return fail();w.parts.push(bytes);}catch{return fail();}
  }else{
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
export async function asrInstall() {
  if (!isReady()) return {ok:false,why:'offline'};
  const m = await ask1({t:'asr_install',enable:true}, 'asr_install_res', 600000);
  return m.t === 'asr_install_res' ? m : {ok:false,why:m.t};
}

export async function models() {
  if (!isReady()) return null;
  const m = await ask1({t:'models_get'}, 'models', 15000);
  return m.t === 'models' ? m : null;
}

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
