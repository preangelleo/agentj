// P190 · 反向配对 (PROTOCOL §27): like paying with WeChat — scan someone's code, or be scanned.
//   showCode() — an UNPAIRED page draws a code (a throw-away rendezvous channel + x25519 key + nonce, 5 min) and waits on the
//                relay as that channel's "host" for a paired remote to hand it a pairing link, sealed to the code's key.
//   approve()  — a PAIRED remote scanned such a code: one card (untrusted name, both screens show the same 6 digits), the
//                computer makes the pairing (§24 dev_add with `rv`, signed), the link goes to that page sealed, and the human
//                confirms the codes match (§24 dev_confirm, signed). Cap, eviction and logs are §24's.
// Nothing of a code is secret (it is on the screen): the pairing link is, and only ever travels inside the remote's own Noise
// session or sealed to the code's key. A code is accepted once (per remote: nonce remembered until it expires; per page:
// the first offer that opens wins, the rest are told "taken").
import { reverseLink, parseReverse, rvOffer, rvOpenOffer, rvAnswer, rvOpenAnswer, relayAuthMessage, channelId,
  generateSigningKeypair, b64u, RV_TTL, RV_LINK_BASE, PAIR_DEFAULT_RELAY } from '../proto/wire.js';
import { t, fillText, onLang } from './t.js';
import { el, toast } from './ui.js';
import { qrSvg } from './qr.js';
import { ask1, isReady } from './session.js';
import { signedWrite } from './controls.js';
import { registerLayer } from './layers.js';

const OP_DATA = 0x01, OP_CLOSE = 0x02;
let H = { allowRelay: () => true, pair: () => {}, testRelay: () => null };

// ================================================================ the new device: show a code, wait to be scanned
let W = null;      // {gen, link, ch, x, nonce, sk, exp, ws, timer, tick, taken, bad, stopped}
let wgen = 0;
// A page served from 127.0.0.1 / localhost exists only in tests: it waits on the test relay named by ?relay=, or nowhere.
const loopback = () => ['127.0.0.1', 'localhost'].includes(location.hostname);
const relayFor = () => H.testRelay() || (loopback() ? null : PAIR_DEFAULT_RELAY);
const linkBase = () => (loopback() ? location.origin + '/#j=' : RV_LINK_BASE);

/** Show (or keep showing) the code on the pairing screen. */
export async function showCode() {
  if (W && !W.stopped && W.exp - 10 > Date.now() / 1000) return paintCode();
  stopCode();
  const g = ++wgen;
  let x, sk;
  try {
    x = await crypto.subtle.generateKey({ name: 'X25519' }, false, ['deriveBits']);
    sk = await generateSigningKeypair();
  } catch { el('rv-card').hidden = true; return; }                  // no X25519 / Ed25519 here: the other two ways still work
  if (g !== wgen) return;
  const xpub = new Uint8Array(await crypto.subtle.exportKey('raw', x.publicKey));
  const ch = await channelId(sk.pub);
  const nonce = crypto.getRandomValues(new Uint8Array(16));
  const exp = Math.floor(Date.now() / 1000) + RV_TTL;
  const relay = relayFor() || PAIR_DEFAULT_RELAY;
  W = { gen: g, ch, x, nonce, sk, exp, relay, link: reverseLink(ch, xpub, nonce, exp, relay, linkBase()), ws: null, taken: false,
    bad: 0, stopped: false, up: false, tick: setInterval(paintCode, 1000) };
  const box = el('rv-qr');
  const svg = qrSvg(W.link, t('rv.qrAria'));
  box.replaceChildren(...(svg ? [svg] : []));
  box.dataset.link = W.link;                                          // what the code says (public; tests read it)
  el('rv-card').hidden = false;
  paintCode();
  if (relayFor()) connect(W);
}
/** Leave the pairing screen / a pairing started: the code goes. A code that already took a link keeps its socket for
 *  LINGER_MS so a second scanner (the owner's phone after somebody else's) is told "taken" instead of "unreachable". */
const LINGER_MS = 120_000;
export function stopCode() {
  ++wgen;
  if (!W) return;
  W.stopped = true;
  clearInterval(W.tick);
  const ws = W.ws;
  if (W.taken && ws) setTimeout(() => { try { ws.close(1000); } catch { /* gone */ } }, LINGER_MS);
  else try { ws?.close(1000); } catch { /* gone */ }
  W = null;
  el('rv-card').hidden = true;
  el('rv-qr').replaceChildren();
  delete el('rv-qr').dataset.link;
}
function paintCode() {
  if (!W) return;
  const left = W.exp - Math.floor(Date.now() / 1000);
  if (left <= 0 && !W.taken) { W.stopped = true; showCode(); return; }
  const mmss = Math.floor(Math.max(0, left) / 60) + ':' + String(Math.max(0, left) % 60).padStart(2, '0');
  const st = el('rv-status');
  st.dataset.k = W.taken ? 'got' : W.up ? 'wait' : 'conn';
  fillText(st, W.taken ? t('rv.got') : W.up ? t('rv.wait', { t: mmss }) : t('rv.connecting'));
}
function connect(w) {
  if (w.stopped || w.taken) return;
  let ws;
  try { ws = new WebSocket(w.relay + '/v1/host/' + w.ch); } catch { return retry(w); }
  ws.binaryType = 'arraybuffer';
  w.ws = ws;
  ws.onmessage = (e) => onFrame(w, ws, e.data).catch(() => {});
  ws.onclose = () => { if (w.ws === ws && !w.stopped) { w.up = false; paintCode(); retry(w); } };
}
function retry(w) { if (!w.stopped && !w.taken) setTimeout(() => { if (W === w && !w.stopped && !w.taken) connect(w); }, 2000); }
async function onFrame(w, ws, data) {
  if ((W !== w || w.stopped) && !w.taken) return;              // a code that took a link still answers "taken" (LINGER_MS)
  if (typeof data === 'string') {                                     // §2 host auth for the rendezvous channel
    let m; try { m = JSON.parse(data); } catch { return; }
    if (m.t === 'challenge' && typeof m.n === 'string' && /^[A-Za-z0-9_-]{43}$/.test(m.n)) {
      const sig = new Uint8Array(await crypto.subtle.sign('Ed25519', w.sk.priv, relayAuthMessage(w.ch, m.n)));
      ws.send(JSON.stringify({ t: 'auth', pk: b64u(w.sk.pub), sig: b64u(sig) }));
    } else if (m.t === 'ok') { w.up = true; paintCode(); }
    return;
  }
  const b = new Uint8Array(data);
  if (b.length < 5 || b[0] !== OP_DATA) return;                        // device up / gone: nothing to do
  if (w.stopped && !w.taken) return;
  const cid = b.slice(1, 5), payload = b.slice(5);
  const send = (op, body = new Uint8Array(0)) => { try { ws.send(Uint8Array.of(op, ...cid, ...body)); } catch { /* gone */ } };
  let offer;
  try { offer = await rvOpenOffer(payload, w.x.privateKey, w.ch, w.nonce); } catch {
    send(OP_CLOSE);                                                    // not sealed to this code: nothing is answered
    if (++w.bad >= 8 && !w.taken) { w.stopped = true; showCode(); }   // somebody is guessing: a new code
    return;
  }
  if (w.taken || W !== w) { send(OP_DATA, await rvAnswer(offer.key, w.ch, { ok: false, why: 'taken' })); return; }
  w.taken = true;
  send(OP_DATA, await rvAnswer(offer.key, w.ch, { ok: true }));
  paintCode();
  H.pair(offer.link);
}

// ================================================================ the paired remote: scan → confirm
const USED_KEY = 'aj.rvUsed';
let A = null;      // {code, ws, phase, added, pending, timer}
function usedOnce(code) {
  let m = {};
  try { m = JSON.parse(localStorage.getItem(USED_KEY) || '{}') || {}; } catch { m = {}; }
  const now = Math.floor(Date.now() / 1000), id = b64u(code.nonce);
  for (const k of Object.keys(m)) if (!(m[k] >= now)) delete m[k];
  const seen = id in m;
  m[id] = code.expires;
  try { localStorage.setItem(USED_KEY, JSON.stringify(m)); } catch { /* private mode: this page's memory only */ }
  return seen;
}
export const active = () => !!A;

/** A scanned (or opened) reverse code. */
export async function approve(text) {
  let code;
  try { code = parseReverse(text, Math.floor(Date.now() / 1000), H.allowRelay); }
  catch (e) { toast(t(/expired/.test(e?.message) ? 'rv.expired' : 'rv.bad'), 5000); return false; }
  if (!isReady()) { toast(t('rv.notReady'), 5000); return false; }
  if (A) return false;
  if (usedOnce(code)) { toast(t('rv.used'), 6000); return false; }
  A = { code, ws: null, phase: 'conn', added: false, pending: null, name: '' };
  const a = A;
  openDlg();
  setPhase('conn');
  try {
    const ws = await openDev(a, code);
    if (A !== a) { ws.close(); return false; }
    setPhase('ask');
    const res = await signedWrite('dev_add', { rv: code.channel }, { t: 'dev_add', rv: code.channel });
    if (A !== a) return false;
    if (!res.ok) return fail(a, res.why === 'bad_signature' ? 'rv.upgrade' : ({ busy: 'rm.busy', disabled: 'rm.disabled', offline: 'rm.offline' }[res.why] || 'rv.fail'));
    if (typeof res.link !== 'string' || res.rv !== true) return fail(a, 'rv.upgrade');
    a.added = true;
    a.full = res.full === true;
    const o = await rvOffer(code, res.link);
    const answer = waitBinary(ws, 10000);
    ws.send(o.frame);
    const ans = await rvOpenAnswer(await answer, o.key, code.channel);
    try { ws.close(1000); } catch { /* gone */ }
    if (A !== a) return false;
    if (!ans.ok) return fail(a, 'rv.taken');
    if (!a.pending) setPhase('join');
    return true;
  } catch (e) {
    if (A === a) fail(a, e?.message === 'nodev' ? 'rv.noDevice' : 'rv.noAnswer');
    return false;
  }
}
function openDev(a, code) {
  return new Promise((resolve, reject) => {
    let ws;
    try { ws = new WebSocket(code.relay + '/v1/dev/' + code.channel); } catch { reject(new Error('nodev')); return; }
    ws.binaryType = 'arraybuffer';
    a.ws = ws;
    const to = setTimeout(() => { try { ws.close(); } catch { /* */ } reject(new Error('nodev')); }, 8000);
    ws.onmessage = (e) => {
      if (typeof e.data !== 'string') return;
      let m; try { m = JSON.parse(e.data); } catch { return; }
      if (m.t !== 'host') return;
      clearTimeout(to);
      if (m.up === true) resolve(ws); else { try { ws.close(); } catch { /* */ } reject(new Error('nodev')); }
    };
    ws.onclose = () => { clearTimeout(to); reject(new Error('nodev')); };
  });
}
function waitBinary(ws, ms) {
  return new Promise((resolve, reject) => {
    const to = setTimeout(() => reject(new Error('timeout')), ms);
    ws.onmessage = (e) => { if (typeof e.data !== 'string') { clearTimeout(to); resolve(new Uint8Array(e.data)); } };
    ws.onclose = () => { clearTimeout(to); reject(new Error('closed')); };
  });
}
/** app.js: `dev_pending` / `dev_pair_end` while a reverse card is open → true (handled here). */
export function onRemotes(m) {
  if (!A || !A.added || m.t === 'devs') return false;
  if (m.rv !== undefined && m.rv !== A.code.channel) return true;   // a late event of an earlier code: not this card's
  if (m.t === 'dev_pending') {
    if (m.rv !== A.code.channel || !/^[0-9]{6}$/.test(String(m.sas || ''))) { fail(A, 'rv.upgrade'); return true; }
    A.pending = { device: String(m.device || ''), name: clean(m.name), sas: String(m.sas), evict: m.evict || null, replaces: m.replaces || null };
    setPhase('confirm');
    return true;
  }
  if (m.t === 'dev_pair_end') {
    if (m.ev === 'approved') return true;
    if (A.phase !== 'done') fail(A, m.ev === 'expired' || m.reason === 'timeout' ? 'rv.timeout' : 'rv.ended', false);
    return true;
  }
  return false;
}
const clean = (s) => String(s ?? '').replace(/[\u0000-\u001f\u007f]/g, '').slice(0, 64);
async function confirm() {
  const a = A;
  if (!a || a.phase !== 'confirm' || !a.pending) return;
  el('rvYes').disabled = true;
  const p = a.pending;
  const res = await signedWrite('dev_confirm', { device: p.device, code: p.sas }, { t: 'dev_confirm', device: p.device, code: p.sas });
  if (A !== a) return;
  if (!res.ok) return fail(a, res.why === 'timeout' || res.why === 'gone' ? 'rv.timeout' : 'rv.fail', false);
  a.phase = 'done';
  a.done = { name: clean(res.name || p.name), evicted: res.evicted ? clean(res.evicted.name) : '' };
  setPhase('done');
  setTimeout(() => { if (A === a) closeDlg(false); }, 2500);
}
function fail(a, key, tellHost = true) {
  if (A !== a) return false;
  if (tellHost && a.added && isReady()) ask1({ t: 'dev_cancel' }, 'ctl_res', 4000).catch(() => {});
  a.added = a.added && !tellHost;
  try { a.ws?.close(); } catch { /* gone */ }
  a.err = key;
  setPhase('error');
  return false;
}
function closeDlg(tellHost = true) {
  const a = A;
  if (!a) return;
  if (tellHost && a.added && a.phase !== 'done' && a.phase !== 'error' && isReady()) ask1({ t: 'dev_cancel' }, 'ctl_res', 4000).catch(() => {});
  try { a.ws?.close(); } catch { /* gone */ }
  A = null;
  el('rvDlg').hidden = true;
}

// ---------------------------------------------------------------- the card
function openDlg() {
  el('rvDlg').hidden = false;
  el('rvNo').focus({ preventScroll: true });
}
function setPhase(ph) {
  if (!A) return;
  A.phase = ph;
  paintDlg();
}
function paintDlg() {
  const a = A;
  if (!a) return;
  const ph = a.phase, p = a.pending;
  el('rvDlg').dataset.phase = ph;
  const st = el('rvState');
  const say = { conn: 'rv.sConn', ask: 'rv.sAsk', join: 'rv.sJoin', confirm: '', done: '', error: a.err || 'rv.fail' }[ph];
  fillText(st, say ? t(say) : '');
  st.hidden = !say;
  el('rvPend').hidden = !(ph === 'confirm' && p);
  if (p) {
    el('rvName').textContent = p.name || '—';
    el('rvCode').textContent = p.sas.slice(0, 3) + ' ' + p.sas.slice(3);
    const ev = p.replaces ? t('rm.replaces', { name: clean(p.replaces.name) }) : p.evict ? t('rm.evict', { name: clean(p.evict.name), max: 10 }) : '';
    el('rvEvict').hidden = !ev;
    el('rvEvict').textContent = ev;
  }
  el('rvWarn').hidden = ph === 'done';
  el('rvDone').hidden = ph !== 'done';
  if (ph === 'done') el('rvDone').textContent = t('rv.done', { name: a.done.name || '—' }) + (a.done.evicted ? '\n' + t('rv.evicted', { name: a.done.evicted }) : '');
  el('rvYes').hidden = ph !== 'confirm';
  el('rvYes').disabled = false;
  el('rvNo').textContent = t(ph === 'confirm' ? 'rv.mismatch' : ph === 'done' || ph === 'error' ? 'rv.close' : 'rv.cancel');
  if (ph === 'confirm') el('rvYes').focus({ preventScroll: true });
}

/** hooks: { allowRelay(url), pair(link) (the new device got its link), testRelay() → a loopback test relay or null } */
export function configure(hooks) {
  H = { ...H, ...hooks };
  el('rv-refresh').addEventListener('click', () => { if (W) W.stopped = true; showCode(); });
  el('rvYes').addEventListener('click', () => confirm().catch(() => {}));
  el('rvNo').addEventListener('click', () => closeDlg(true));
  registerLayer({ element: () => el('rvDlg'), open: () => !el('rvDlg').hidden, close: () => closeDlg(true), fallback: () => el('scanBtn'), rank: 55 });
  onLang(() => { paintCode(); paintDlg(); if (W) el('rv-qr').firstChild?.setAttribute?.('aria-label', t('rv.qrAria')); });
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && W) paintCode(); });
}
// for tests
export const _debug = () => ({ code: W && { ch: W.ch, up: W.up, taken: W.taken, exp: W.exp }, card: A && { phase: A.phase, err: A.err || null } });
