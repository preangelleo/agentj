// agentj web client (alpha). Spec: agentjarvis/protocol/PROTOCOL.md §2–§4. One host only (N=1).
// PR1: every human-typed byte leaves this page only inside a Noise transport message; message text is rendered with
// textContent only; the device's X25519 private key is a non-extractable CryptoKey kept in IndexedDB; chat history is
// kept in memory only (nothing plaintext at rest). L1 (PROTOCOL §8–§9): the Agent's replies, status and permission requests
// arrive inside the same Noise session; an approval is signed with a non-extractable Ed25519 key kept in IndexedDB; the
// push subscription goes to the host only (end to end), and pushes carry no content.
import { IK, IKPSK2, Handshake, generateKeypair } from './proto/noise.js';
import { KIND, MAX_TEXT, padJson, unpadJson, parsePairing as parseLink, pairPrologue, resumePrologue, safetyCode, frame,
  b64u, deviceId, approveMessage, generateSigningKeypair, controlMessage } from './proto/wire.js';
import VERSION from './version.js';
import DICT from './i18n.js';

const $ = (id) => document.getElementById(id);
const enc = new TextEncoder();
const EMPTY = new Uint8Array(0);
const VIEWS = ['pair-view', 'sas-view', 'chat-view', 'mem-view', 'act-view', 'tasks-view', 'revoked-view', 'error-view'];
const PANELS = ['chat-view', 'mem-view', 'act-view', 'tasks-view'];   // views of a ready session

// ---------------------------------------------------------------- language (window.AJLang from brand/lang.js; zh default, en)
// Every user-visible string comes from i18n.js (built from web/i18n/web.{zh,en}.json). Dictionary text is ours, so `code`
// spans in it become <code> elements (createElement + textContent; never HTML). Host / Agent text is shown as plain text.
const fmtVars = (str, vars) => (vars ? String(str).replace(/\{(\w+)\}/g, (m, k) => (Object.prototype.hasOwnProperty.call(vars, k) ? String(vars[k]) : m)) : str);
function t(key, vars) { return window.AJLang ? window.AJLang.t(DICT, key, vars) : fmtVars(DICT.zh[key] ?? key, vars); }
const lang = () => (window.AJLang ? window.AJLang.get() : 'zh');
const locale = () => (lang() === 'en' ? 'en-GB' : 'zh-CN');
function fillText(node, str) {                       // `x` → <code>x</code>; everything else text nodes
  const parts = String(str).split('`');
  node.replaceChildren(...parts.map((p, i) => { if (i % 2 === 0) return document.createTextNode(p); const c = document.createElement('code'); c.textContent = p; return c; }));
}
const plain = (str) => String(str).replace(/`/g, '');
function applyStatic() {
  for (const n of document.querySelectorAll('[data-i18n]')) fillText(n, t(n.dataset.i18n));
  for (const n of document.querySelectorAll('[data-i18n-attr]')) {
    for (const pair of n.dataset.i18nAttr.split(',')) { const [a, k] = pair.split('='); n.setAttribute(a.trim(), plain(t(k.trim()))); }
  }
  renderName();
}

// ---------------------------------------------------------------- Agent display name (host → device {"t":"status",…,"name"})
// The name the owner gave this Agent, shown top-left (#brand-name) and as the tab title; "Agent J" when the host sends
// none (null or absent — hosts from before the rename never send it). It comes from the host, so it is untrusted display
// text: textContent only; format / surrogate / private-use / unassigned characters dropped, any whitespace (tabs, line
// breaks, line / paragraph separators) folded to one space, other control characters dropped, capped at 32 code points (the Agent-name maximum). The last name is cached in the
// host record (IndexedDB "host"), so a cold start shows it before the connection is up; re-pairing drops it with the record.
const DEFAULT_NAME = 'Agent J';
const NAME_MAX = 32;
let agentName = null;                                // null = the default
function cleanName(v) {
  if (typeof v !== 'string') return null;
  const s = v.slice(0, 512).replace(/[\p{Cf}\p{Cs}\p{Co}\p{Cn}]/gu, '').replace(/\s+/gu, ' ').replace(/\p{Cc}/gu, '').replace(/ {2,}/g, ' ').trim();
  const cps = Array.from(s);
  return (cps.length > NAME_MAX ? cps.slice(0, NAME_MAX).join('').trimEnd() : s) || null;
}
function renderName() {
  $('brand-name').textContent = agentName ?? DEFAULT_NAME;
  document.title = agentName ?? t('meta.title');
}
function setAgentName(v, save = true) {
  const n = cleanName(v);
  const changed = n !== agentName;
  agentName = n;
  renderName();
  if (save && changed && sess) saveName(sess.ctx.channel, n).catch(() => { /* only a cache */ });
}
async function saveName(channel, name) {
  const h = await dbGet('host');
  if (!h || !h.approved || h.channel !== channel || (h.name ?? null) === name) return;
  await dbPut('host', { ...h, name });
}

// Read the pairing fragment first thing and strip it from the URL / history (it carries the one-time PSK).
let pendingLink = null;
if (location.hash.startsWith('#p=')) {
  pendingLink = location.hash;
  history.replaceState(null, '', location.pathname + location.search);
}

// ---------------------------------------------------------------- state / status
let state = 'idle';
Object.defineProperty(window, '__ajState', { get: () => state, enumerable: false, configurable: false });

let statusKey = ['st.idle'];
function setStatus(s, key, vars) {
  state = s;
  statusKey = [key, vars];
  const el = $('status');
  el.dataset.state = s;
  el.textContent = plain(t(key, vars));
  document.body.dataset.conn = s;
  $('send').disabled = s !== 'ready' || estopOn;
  if (agentLast && agentLast[1]) setAgentStatus(agentLast[0], agentLast[1]);
  renderLogo();
}
let view = null;
function show(v) { view = v; for (const x of VIEWS) $(x).hidden = x !== v; renderA2hs(); }

// The header shield follows the Agent / connection state (brand/img/status/*): working = blue, waiting for you = orange,
// pairing = purple, offline / stopped = grey; idle = the green shield.
const LOGO = { mark: 'brand/img/logo-mark.png', working: 'brand/img/status/logo-working.png', waiting: 'brand/img/status/logo-waiting.png',
  question: 'brand/img/status/logo-question.png', offline: 'brand/img/status/logo-offline.png' };
let agentSt = 'none';
function renderLogo() {
  let v = 'mark';
  if (state === 'ready') v = estopOn ? 'offline' : ({ working: 'working', compacting: 'working', waiting: 'waiting', down: 'offline', stopped: 'offline' })[agentSt] ?? 'mark';
  else if (state === 'pairing' || state === 'awaiting-approval') v = 'question';
  else if (state === 'waiting-host' || state === 'error' || state === 'revoked') v = 'offline';
  const img = $('logo');
  if (img.getAttribute('src') !== LOGO[v]) img.setAttribute('src', LOGO[v]);
  img.dataset.v = v;
}

// ---------------------------------------------------------------- IndexedDB (db "agentjarvis", store "kv")
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
const dbGet = (k) => kv('readonly', (s) => s.get(k));
const dbPut = (k, v) => kv('readwrite', (s) => s.put(v, k));
const dbDel = (k) => kv('readwrite', (s) => s.delete(k));

let devKey = null;
async function deviceKey() {
  if (devKey) return devKey;
  const d = await dbGet('device');
  if (d && d.priv && d.pub) return (devKey = { priv: d.priv, pub: new Uint8Array(d.pub) });
  const kp = await generateKeypair(false);            // extractable: false — the private key never leaves WebCrypto
  await dbPut('device', { priv: kp.priv, pub: kp.pub });
  return (devKey = kp);
}

// Ed25519 approval key (PROTOCOL §8). null = this browser cannot sign → it can chat but not approve.
let signKp;
async function signKey() {
  if (signKp !== undefined) return signKp;
  try {
    const d = await dbGet('sign');
    if (d && d.priv && d.pub) return (signKp = { priv: d.priv, pub: new Uint8Array(d.pub) });
    const kp = await generateSigningKeypair();       // private key not extractable
    await dbPut('sign', { priv: kp.priv, pub: kp.pub });
    return (signKp = kp);
  } catch { return (signKp = null); }
}
let myId = null;
async function myDeviceId() { return myId ?? (myId = await deviceId((await deviceKey()).pub)); }

function deviceLabel() {
  const ua = navigator.userAgent;
  const os = /Android/.test(ua) ? 'Android' : /iPhone|iPad|iPod/.test(ua) ? 'iOS' : /Mac OS X/.test(ua) ? 'macOS'
    : /Windows/.test(ua) ? 'Windows' : /Linux|CrOS/.test(ua) ? 'Linux' : '';
  const br = /Edg\//.test(ua) ? 'Edge' : /Firefox\/|FxiOS/.test(ua) ? 'Firefox' : /CriOS|Chrome\//.test(ua) ? 'Chrome'
    : /Safari\//.test(ua) ? 'Safari' : '浏览器';   // the device label is data for the host's device list, not UI text
  return ('网页 · ' + [os, br].filter(Boolean).join(' ')).slice(0, 64);
}

// The client only ever talks to our relay: relay.agentj.app, or the legacy alpha-relay.agentjarvis.net that pairing links
// from not-yet-updated hosts still carry (one version cycle; the same relay Worker answers on both hosts and both reach the
// same channel). A pasted link cannot move this device to somebody else's relay + host (CSP connect-src enforces the same
// on this origin; this keeps it true in any shell). A page served from 127.0.0.1 exists only in tests and may also use a
// local relay.
const RELAY_HOSTS = ['relay.agentj.app', 'alpha-relay.agentjarvis.net'];
function allowRelay(url) {
  const u = new URL(url);
  const ours = u.protocol === 'wss:' && !u.port && RELAY_HOSTS.includes(u.hostname);
  if (location.hostname === '127.0.0.1') return ours || (u.protocol === 'ws:' && u.hostname === '127.0.0.1');
  return ours;
}

// The legacy web host (alpha-web.agentjarvis.net) still serves this page for one version cycle: a phone paired there keeps
// its keys in that origin's IndexedDB and keeps working there. A visitor with no pairing there goes to the new origin with
// path + query + fragment intact (a #p= pairing link arrives whole; it was only stripped from this page's history).
const LEGACY_WEB_HOST = 'alpha-web.agentjarvis.net';
const WEB_ORIGIN = 'https://m.agentj.app';
async function leaveLegacyHost() {
  if (location.hostname !== LEGACY_WEB_HOST) return false;
  let paired = false;
  try { const h = await dbGet('host'); paired = !!(h && h.approved); } catch { /* no storage → nothing to keep here */ }
  if (paired) return false;
  location.replace(WEB_ORIGIN + location.pathname + location.search + (pendingLink ?? location.hash));
  return true;
}
const parsePairing = (input) => parseLink(input, undefined, allowRelay);

function randHex(bytes) { return Array.from(crypto.getRandomValues(new Uint8Array(bytes)), (b) => b.toString(16).padStart(2, '0')).join(''); }

// ---------------------------------------------------------------- session
// One live session at a time. phase: wait-host → hs → (pair) approval | (resume) ready-wait → ready
let sess = null;
let reconnectTimer = null;
let backoff = 1000;

class ProtocolError extends Error {}

function closeSession() {
  clearTimeout(reconnectTimer); reconnectTimer = null;
  const s = sess; sess = null;
  if (s) { s.closedByUs = true; try { s.ws.close(1000); } catch { /* already closed */ } }
}

function openSession(mode, ctx) {
  closeSession();
  const ws = new WebSocket(ctx.relay + '/v1/dev/' + ctx.channel);
  ws.binaryType = 'arraybuffer';
  const s = { ws, mode, ctx, phase: 'wait-host', hs: null, send: null, recv: null, chain: Promise.resolve(), sendChain: Promise.resolve(), closedByUs: false };
  sess = s;
  setStatus('connecting', 'st.connecting');
  ws.onopen = () => { if (sess === s && s.phase === 'wait-host') setStatus('waiting-host', 'st.waitingHost'); };
  // Frames are handled strictly in order (async crypto must not interleave).
  ws.onmessage = (ev) => { s.chain = s.chain.then(() => onFrame(s, ev.data)).catch((e) => protocolFail(s, e)); };
  ws.onclose = (ev) => { s.chain = s.chain.then(() => onClose(s, ev)); };
  ws.onerror = () => { /* a close event always follows */ };
  return s;
}

function resetCrypto(s) { s.hs = null; s.send = null; s.recv = null; s.sendChain = Promise.resolve(); }

async function onFrame(s, data) {
  if (s !== sess) return;
  if (typeof data === 'string') {                     // relay status; anything else from the relay is ignored
    let m; try { m = JSON.parse(data); } catch { return; }
    if (m && m.t === 'host' && typeof m.up === 'boolean') return m.up ? hostUp(s) : hostDown(s);
    return;
  }
  const b = new Uint8Array(data);
  if (b.length < 1) throw new ProtocolError('empty frame');
  const kind = b[0], body = b.subarray(1);
  if (kind === KIND.HS_RESP) {
    if (s.phase !== 'hs') throw new ProtocolError('unexpected HS_RESP');
    const payload = await s.hs.readMessage(body);
    if (payload.length !== 0) throw new ProtocolError('msg2 payload must be empty');
    const { send, recv, h } = await s.hs.split();
    s.hs = null; s.send = send; s.recv = recv;
    if (s.mode === 'pair') {
      s.phase = 'approval';
      await sendApp(s, { t: 'hello' });
      $('sas').textContent = await safetyCode(h);
      show('sas-view');
      setStatus('awaiting-approval', 'st.awaiting');
    } else {
      s.phase = 'ready-wait';
      await sendApp(s, { t: 'hello', since: lastSeq });   // the host replays the chat this page has not seen (memory only)
    }
    return;
  }
  if (kind === KIND.DATA) {
    if (!s.recv) throw new ProtocolError('DATA before handshake');
    return onApp(s, unpadJson(await s.recv.decrypt(EMPTY, body)));
  }
  throw new ProtocolError('bad kind');
}

async function onApp(s, m) {
  if (m.t === 'approved') {
    if (s.mode !== 'pair' || s.phase !== 'approval') throw new ProtocolError('unexpected approved');
    const host = { relay: s.ctx.relay, channel: s.ctx.channel, hostPub: s.ctx.hostPub, approved: true };
    await dbPut('host', host);
    setAgentName(null, false);                        // a new pairing: its name comes with the host's first status
    s.mode = 'resume'; s.ctx = host;                  // drop the PSK; a later host restart resumes on this socket
    $('messages').replaceChildren(); asks.clear(); grants.clear(); renderGrants(); lastSeq = 0; panel = 'chat-view';
    return enterReady(s);
  }
  if (m.t === 'ready') {
    if (s.mode !== 'resume' || s.phase !== 'ready-wait') throw new ProtocolError('unexpected ready');
    return enterReady(s);
  }
  if (s.phase !== 'ready') {
    if (m.t === 'msg') throw new ProtocolError('msg before ready');
    return;
  }
  if (m.t === 'msg') {
    if (typeof m.text !== 'string' || m.text.length > MAX_TEXT) throw new ProtocolError('bad msg');
    if (Number.isInteger(m.seq)) lastSeq = m.seq;
    if (m.from === 'cmd') return addCmdCard(m);
    const from = ['agent', 'host', 'you', 'device', 'notice'].includes(m.from) ? m.from : 'host';
    addMessage(from === 'you' ? 'out' : from === 'device' ? 'out' : 'in', m.text, from, typeof m.name === 'string' ? m.name : '');
    return;
  }
  if (m.t === 'status') { setAgentName(m.name); return setAgentStatus(m.s, m.agent); }
  if (m.t === 'ask') return showAsk(s, m);
  if (m.t === 'ask_done') return askDone(m.id, m.result);
  if (m.t === 'auto') return showAuto(m);
  if (m.t === 'grant') return setGrant(m);
  if (m.t === 'grant_end') return endGrant(m.id, m.why);
  if (m.t === 'push_key') return gotPushKey(s, m.k);
  if (m.t === 'estop_state') return setEstop(m);
  if (typeof m.r === 'string' && pending.has(m.r)) return pending.get(m.r)(m);
  // unknown app message types are ignored (forward compatible)
}

function enterReady(s) {
  s.phase = 'ready';
  grants.clear(); renderGrants();                     // the host re-sends the batch approvals still live (PROTOCOL §8)
  backoff = 1000;
  show(panel);
  if (panel !== 'chat-view') openPanel(panel);       // reconnected while a page was open: load it again
  setStatus('ready', 'st.ready');
  if (document.visibilityState !== 'visible') sendApp(s, { t: 'vis', fg: false }).catch(() => {});
}

async function hostUp(s) {
  if (s.phase !== 'wait-host') return;                // duplicate "up"
  const dev = await deviceKey();
  let msg1;
  if (s.mode === 'pair') {
    const p = s.ctx;
    if (p.expires < Math.floor(Date.now() / 1000)) { closeSession(); return pairFailed(); }
    s.hs = await new Handshake({ protocol: IKPSK2, initiator: true, prologue: pairPrologue(p.channel, p.pairingId), s: dev, rs: p.hostPub, psk: p.psk }).init();
    const sk = await signKey();
    const info = sk ? { v: 1, name: deviceLabel(), sk: b64u(sk.pub) } : { v: 1, name: deviceLabel() };
    msg1 = frame(KIND.PAIR_INIT, p.pairingId, await s.hs.writeMessage(enc.encode(JSON.stringify(info))));
    setStatus('pairing', 'st.pairing');
  } else {
    s.hs = await new Handshake({ protocol: IK, initiator: true, prologue: resumePrologue(s.ctx.channel), s: dev, rs: s.ctx.hostPub }).init();
    const sk = await signKey();                       // a device paired before L1 registers its approval key here (host keeps the first)
    msg1 = frame(KIND.RESUME_INIT, await s.hs.writeMessage(enc.encode(JSON.stringify(sk ? { v: 1, sk: b64u(sk.pub) } : { v: 1 }))));
    setStatus('connecting', 'st.resuming');
  }
  if (s !== sess || s.ws.readyState !== WebSocket.OPEN) return;
  s.phase = 'hs';
  s.ws.send(msg1);
}

function hostDown(s) {
  if (s.mode === 'pair' && s.phase !== 'wait-host') {  // host restarted mid-pairing: the pairing is gone
    closeSession();
    return pairFailed();
  }
  resetCrypto(s);                                     // the host lost all session state; redo RESUME on its next "up"
  s.phase = 'wait-host';
  setStatus('waiting-host', 'st.hostDown');
}

function onClose(s, ev) {
  if (s !== sess) return;                             // superseded or closed by us
  sess = null;
  if (ev.code === 4010) return revoked();
  if (s.mode === 'pair') return pairFailed();
  // An approved device whose RESUME is answered by a close (not a network drop) is no longer on the allowlist.
  if ((s.phase === 'hs' || s.phase === 'ready-wait') && ev.code !== 1006) return revoked();
  scheduleReconnect();
}

function protocolFail(s, e) {
  if (s !== sess) return;
  const wasPair = s.mode === 'pair';
  closeSession();
  if (e && e.name === 'NotSupportedError') return fatal('error.noCrypto');
  setStatus('error', 'st.error');
  showError(wasPair ? 'error.pairBad' : 'error.dataBad', wasPair ? 'error.back' : 'error.reconnect', wasPair ? 'idle' : 'resume');
}

// error view: texts are keys so a language switch re-renders them
let errorKeys = null;
function renderError() {
  if (!errorKeys) return;
  fillText($('error-text'), t(errorKeys[0]));
  $('retry').textContent = t(errorKeys[1]);
}
function showError(textKey, btnKey, action) {
  errorKeys = [textKey, btnKey];
  renderError();
  $('retry').dataset.action = action;
  show('error-view');
}

function pairFailed() {
  setStatus('error', 'st.pairFail');
  showError('st.pairFail', 'error.back', 'idle');
}

function revoked() {
  closeSession();
  setStatus('revoked', 'st.revoked');
  show('revoked-view');
}

function fatal(key) {
  closeSession();
  setStatus('error', 'st.unusable');
  showError(key, 'error.retry', 'reload');
}

function scheduleReconnect() {
  clearTimeout(reconnectTimer);
  const wait = backoff;
  backoff = Math.min(backoff * 2, 30000);
  setStatus('connecting', 'st.retryIn', { n: Math.round(wait / 1000) });
  reconnectTimer = setTimeout(() => { reconnectTimer = null; resume(); }, wait);
}

async function resume() {
  const host = await dbGet('host');
  if (!host || !host.approved) return showIdle();
  setAgentName(host.name, false);                     // the cached name, shown before the connection is up
  show('chat-view');
  openSession('resume', { relay: host.relay, channel: host.channel, hostPub: new Uint8Array(host.hostPub) });
}

function reconnectNow() {
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; resume(); }
}

// Transport sends are serialized: CipherState nonces must match the order frames hit the socket.
function sendApp(s, obj) {
  const p = s.sendChain.then(async () => {
    if (!s.send) throw new Error('no session');
    const ct = await s.send.encrypt(EMPTY, padJson(obj));
    if (s.ws.readyState !== WebSocket.OPEN) throw new Error('socket closed');
    s.ws.send(frame(KIND.DATA, ct));
  });
  s.sendChain = p.catch(() => {});
  return p;
}

// ---------------------------------------------------------------- pairing input
function startPairing(input) {
  let p;
  try { p = parsePairing(input); } catch {
    showIdle();
    const err = $('pair-error');
    err.textContent = t('pair.badLink');
    err.hidden = false;
    return;
  }
  $('pair-link').value = '';                          // the link carries the one-time PSK
  $('pair-error').hidden = true;
  show('pair-view');
  openSession('pair', p);
}

function showIdle() {
  closeSession();
  setAgentName(null, false);
  show('pair-view');
  setStatus('idle', 'st.idle');
}

// ---------------------------------------------------------------- scanning (BarcodeDetector + camera, when available)
let scanStream = null;
async function startScan() {
  const hint = $('scan-hint');
  fillText(hint, t('pair.scanHint'));
  let formats = [];
  if ('BarcodeDetector' in window && navigator.mediaDevices?.getUserMedia) {
    try { formats = await window.BarcodeDetector.getSupportedFormats(); } catch { formats = []; }
  }
  if (!formats.includes('qr_code')) { hint.hidden = false; return; }
  try {
    scanStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false });
  } catch {
    fillText(hint, t('pair.scanNoCam'));
    hint.hidden = false;
    return;
  }
  const det = new window.BarcodeDetector({ formats: ['qr_code'] });
  const video = $('scan-video');
  video.srcObject = scanStream;
  $('scan-box').hidden = false;
  try { await video.play(); } catch { /* autoplay of a muted stream is allowed */ }
  const stream = scanStream;
  const tick = async () => {
    if (scanStream !== stream) return;
    try {
      const hit = (await det.detect(video)).find((c) => typeof c.rawValue === 'string' && c.rawValue.includes('#p='));
      if (hit) { stopScan(); startPairing(hit.rawValue); return; }
    } catch { /* frame not ready */ }
    setTimeout(tick, 250);
  };
  tick();
}
function stopScan() {
  if (scanStream) for (const tr of scanStream.getTracks()) tr.stop();
  scanStream = null;
  $('scan-video').srcObject = null;
  $('scan-box').hidden = true;
}

// ---------------------------------------------------------------- chat
let lastSeq = 0;
function addMessage(dir, text, from = '', name = '') {
  const li = document.createElement('li');
  li.dataset.dir = dir;
  if (from) li.dataset.from = from;
  if (from === 'device' && name) {
    const who = document.createElement('span');
    who.className = 'who';
    who.textContent = name;
    li.append(who, document.createTextNode(text));
  } else {
    li.textContent = text;
  }
  const list = $('messages');
  list.append(li);
  chatEmpty();
  li.scrollIntoView({ block: 'end' });
}
function chatEmpty() { $('chat-empty').hidden = $('messages').childElementCount > 0; }

// ---------------------------------------------------------------- Agent status (PROTOCOL §8)
const AGENT_NAME = { claude: 'Claude Code', codex: 'Codex', opencode: 'OpenCode' };
const STATUS_KEYS = ['idle', 'working', 'compacting', 'waiting', 'down', 'stopped'];
let agentLast = null;
function setAgentStatus(st, agent) {
  agentLast = [st, agent];
  const el = $('agent-status');
  if (!agent || !STATUS_KEYS.includes(st)) { el.hidden = true; agentSt = 'none'; document.body.dataset.agent = 'none'; renderLogo(); return; }
  // the harness · state pill after the connection line; the state is only true while connected, so it shows only then
  // (the Agent's own display name is line 1, #brand-name)
  el.hidden = state !== 'ready';
  el.dataset.s = st;
  el.textContent = `${AGENT_NAME[agent] ?? 'Agent'} · ${t('ag.' + st)}`;
  agentSt = st;
  document.body.dataset.agent = st;
  renderLogo();
}

// ---------------------------------------------------------------- approvals (PROTOCOL §8)
const asks = new Map();                               // id → { li, tool, summary, deadline, timer, scope }
const ASK_RESULTS = ['allow', 'deny', 'timeout', 'gone', 'stopped'];
// the danger list (PROTOCOL §8, ADR-A47): fixed categories decided by the host; a dangerous request has no batch button
const CATS = ['spend', 'delete', 'send', 'credentials', 'price'];
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
async function showAsk(s, m) {
  if (typeof m.id !== 'string' || !/^[0-9a-f]{32}$/.test(m.id) || typeof m.tool !== 'string' || typeof m.summary !== 'string') return;
  if (asks.has(m.id)) return;
  const ttl = Number.isInteger(m.ttl) && m.ttl >= 0 && m.ttl <= 600 ? m.ttl : 0;
  const cats = Array.isArray(m.cat) ? m.cat.filter((c) => CATS.includes(c)) : [];
  const danger = cats.length > 0;
  const scope = !danger && typeof m.batch === 'string' && m.batch.length > 0 && m.batch.length <= 400 ? m.batch : null;
  const li = el('li', danger ? 'ask ask-danger' : 'ask');
  li.dataset.dir = 'in'; li.dataset.from = 'ask'; li.dataset.risk = danger ? 'danger' : 'low';
  const tags = el('p', 'ask-tags');
  if (danger) for (const c of cats) { const tg = el('span', 'tag tag-danger', t('ask.cat.' + c)); tg.dataset.cat = c; tags.append(tg); }
  else tags.append(el('span', 'tag tag-low', t('ask.low')));
  const h = el('p', 'ask-title', t(danger ? 'ask.titleDanger' : 'ask.title', { tool: m.tool }));
  if (typeof m.task === 'string' && m.task) tags.append(el('span', 'tag tag-task', t('ask.task', { task: m.task.slice(0, 80) })));
  const whyEl = typeof m.why === 'string' && m.why ? el('p', 'ask-why', t('ask.why', { why: m.why.slice(0, 200) })) : null;
  const pre = el('pre', 'ask-summary', m.summary);
  const meta = el('p', 'ask-meta');
  const row = el('div', 'ask-row');
  const yes = el('button', 'aj-btn aj-btn--primary ask-allow', t(danger ? 'ask.allowOne' : 'ask.allow')); yes.type = 'button';
  const no = el('button', 'aj-btn ask-deny', t('ask.deny')); no.type = 'button';
  row.append(no, yes);
  let batchBtn = null;
  if (scope) {
    const max = Number.isInteger(m.batch_max) ? m.batch_max : 20;
    const mins = Number.isInteger(m.batch_secs) ? Math.round(m.batch_secs / 60) : 10;
    batchBtn = el('button', 'aj-btn ask-batch'); batchBtn.type = 'button';
    batchBtn.append(el('span', 'ask-batch-main', t('ask.batch')),
      el('span', 'ask-batch-scope', t('ask.batchScope', { scope, max, mins })));
    row.append(batchBtn);
  }
  li.append(tags, h, ...(whyEl ? [whyEl] : []), pre, meta, row);
  const a = { li, tool: m.tool, summary: m.summary, deadline: Date.now() + ttl * 1000, timer: null, meta, row, done: false, scope };
  asks.set(m.id, a);
  const tick = () => {
    if (a.done) return;
    const left = Math.max(0, Math.ceil((a.deadline - Date.now()) / 1000));
    a.meta.textContent = left > 0 ? t('ask.left', { n: left }) : t('ask.timeUp');
    if (left > 0) a.timer = setTimeout(tick, 1000);
  };
  const sk = await signKey();
  if (!sk) {
    row.replaceChildren();
    const p = el('p', 'small', t('ask.noSign'));
    row.append(p);
  }
  yes.addEventListener('click', () => answer(m.id, true));
  no.addEventListener('click', () => answer(m.id, false));
  if (batchBtn) batchBtn.addEventListener('click', () => answer(m.id, true, true));
  tick();
  $('messages').append(li);
  chatEmpty();
  li.scrollIntoView({ block: 'end' });
}
async function answer(id, ok, batch = false) {
  const a = asks.get(id);
  const s = sess;
  if (!a || a.done || !s || s.phase !== 'ready') return;
  if (batch && (!ok || !a.scope)) return;
  const sk = await signKey();
  if (!sk) return;
  for (const b of a.row.querySelectorAll('button')) b.disabled = true;
  try {
    const decision = batch ? 'allow_batch' : ok ? 'allow' : 'deny';
    const msg = await approveMessage(s.ctx.channel, await myDeviceId(), id, decision, a.tool, a.summary, batch ? a.scope : undefined);
    const sig = new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, sk.priv, msg));
    await sendApp(s, batch ? { t: 'answer', id, ok: true, batch: true, sig: b64u(sig) } : { t: 'answer', id, ok, sig: b64u(sig) });
    a.meta.textContent = t(batch ? 'ask.sentBatch' : ok ? 'ask.sentAllow' : 'ask.sentDeny');
  } catch {
    for (const b of a.row.querySelectorAll('button')) b.disabled = false;
  }
}
function askDone(id, result) {
  const a = asks.get(id);
  if (!a || !ASK_RESULTS.includes(result)) return;
  a.done = true; clearTimeout(a.timer);
  a.row.replaceChildren();
  a.meta.textContent = t('ask.result.' + result);
  a.li.dataset.result = result;
}

// ---------------------------------------------------------------- batch approval (ADR-A48): auto lines + the grant bar
const grants = new Map();                             // grant id → { scope, left }
function showAuto(m) {
  if (typeof m.tool !== 'string' || typeof m.summary !== 'string') return;
  const li = el('li', 'auto', t('ask.auto', { tool: m.tool, summary: m.summary.slice(0, 200) }));
  li.dataset.dir = 'in'; li.dataset.from = 'auto';
  $('messages').append(li);
  chatEmpty();
  li.scrollIntoView({ block: 'end' });
}
function renderGrants() {
  const bar = $('grant-bar');
  const live = [...grants.values()];
  bar.hidden = live.length === 0;
  $('grant-text').textContent = live.length === 0 ? '' :
    t('grant.live', { list: live.map((g) => t('grant.item', { scope: g.scope, n: g.left })).join(lang() === 'en' ? '; ' : '；') });
}
function setGrant(m) {
  if (typeof m.id !== 'string' || !/^[0-9a-f]{32}$/.test(m.id) || typeof m.scope !== 'string') return;
  grants.set(m.id, { scope: m.scope.slice(0, 400), left: Number.isInteger(m.left) ? m.left : 0 });
  renderGrants();
}
const GRANT_END = ['turn_end', 'revoked', 'limit', 'expired', 'device_gone', 'estop'];
function endGrant(id, code) {
  if (!grants.delete(id)) return;
  renderGrants();
  if (code !== 'turn_end') addMessage('in', t('grant.end.' + (GRANT_END.includes(code) ? code : 'other')), 'notice');
}
async function revokeGrants() {
  const s = sess;
  if (!s || s.phase !== 'ready' || grants.size === 0) return;
  await sendApp(s, { t: 'grant_off', id: null }).catch(() => {});
}

// ---------------------------------------------------------------- phone controls (PROTOCOL §8): memory, activity, tasks, stop
// Reads are plain requests; every write is signed with the approval key over (action, nonce, time, SHA-256 of the target as
// shown) — the host does nothing without a valid signature. Nothing here is stored on the phone.
let panel = 'chat-view';
let estopOn = false;
const pending = new Map();                            // request id → handler for the host's answers (same r)
function request(obj, onMsg) {
  const s = sess;
  if (!s || s.phase !== 'ready') return null;
  const r = randHex(8);
  pending.set(r, onMsg);
  sendApp(s, { ...obj, r }).catch(() => pending.delete(r));
  return r;
}
async function signed(action, target, extra) {
  const s = sess;
  const sk = await signKey();
  if (!s || s.phase !== 'ready' || !sk) throw new Error(sk ? 'offline' : 'no_key');
  const n = randHex(16), ts = Date.now();
  const msg = await controlMessage(s.ctx.channel, await myDeviceId(), action, n, ts, target);
  const sig = new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, sk.priv, msg));
  return { ...extra, n, ts, sig: b64u(sig) };
}
function command(obj) {                               // one signed write → the host's ctl_res
  return new Promise((resolve) => {
    const r = request(obj, (m) => { if (m.t === 'ctl_res') { pending.delete(r); resolve(m); } });
    if (!r) resolve({ ok: false, why: 'offline' });
    else setTimeout(() => { if (pending.delete(r)) resolve({ ok: false, why: 'timeout' }); }, 20000);
  });
}
const WHY_CODES = ['changed', 'unknown_item', 'bad_signature', 'no_key', 'stale', 'timeout', 'offline', 'not_found', 'exists', 'symlink',
  'invalid', 'unknown', 'io', 'shape', 'replay'];
const why = (code) => (WHY_CODES.includes(code) ? t('why.' + code) : String(code ?? ''));   // error code from the host → plain words

// the confirm sheet (two-step for every write)
function confirmSheet(title, text, yes) {
  return new Promise((resolve) => {
    $('sheet-title').textContent = title; $('sheet-text').textContent = text; $('sheet-yes').textContent = yes;
    $('sheet').hidden = false;
    $('sheet-yes').focus();
    const done = (v) => { $('sheet').hidden = true; $('sheet-yes').onclick = null; $('sheet-no').onclick = null; resolve(v); };
    $('sheet-yes').onclick = () => done(true);
    $('sheet-no').onclick = () => done(false);
  });
}
let toastTimer = null;
function toast(text, btnText, onBtn) {
  $('toast-text').textContent = text;
  const b = $('toast-btn');
  b.hidden = !btnText; b.textContent = btnText || ''; b.onclick = onBtn ? () => { $('toast').hidden = true; onBtn(); } : null;
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, btnText ? 12000 : 5000);
}

function openPanel(v) {
  if (!PANELS.includes(v)) return;
  panel = v;
  show(v);
  if (v === 'mem-view') loadMemory();
  if (v === 'act-view') loadActivity(true);
  if (v === 'tasks-view') loadTasks();
  window.scrollTo(0, 0);
}
function reloadPanel() { if (state === 'ready' && panel !== 'chat-view') openPanel(panel); }

// ---- stop everything
function setEstop(m) {
  estopOn = m.on === true;
  $('estop-banner').hidden = !estopOn;
  $('estop').hidden = estopOn;
  estopBy = estopOn && typeof m.by === 'string' && m.by ? m.by.slice(0, 64) : '';
  $('send').disabled = state !== 'ready' || estopOn;
  renderEstop();
  renderLogo();
}
let estopBy = '';
function renderEstop() {
  $('estop-by').textContent = estopBy ? t('estop.by', { by: estopBy }) : '';
  $('msg-input').placeholder = t(estopOn ? 'chat.placeholderStopped' : 'chat.placeholder');
}
async function onEstop() {
  if (!await confirmSheet(t('estop.confirmTitle'), t('estop.confirmText'), t('estop.confirmYes'))) return;
  const res = await command(await signed('estop', {}, { t: 'estop' }).catch(() => ({ t: 'estop' })));
  if (!res.ok) toast(t('estop.fail', { why: why(res.why) }));
}
async function onResume() {
  if (!await confirmSheet(t('estop.resumeTitle'), t('estop.resumeText'), t('estop.resumeYes'))) return;
  const res = await command(await signed('resume', {}, { t: 'resume' }).catch(() => ({ t: 'resume' })));
  if (!res.ok) toast(t('estop.resumeFail', { why: why(res.why) }));
}

// ---- 它记住了什么
const memItems = new Map();                           // key → item
function loadMemory() {
  $('mem-status').textContent = t('mem.loading');
  const list = $('mem-list'); list.replaceChildren(); memItems.clear();
  const boxes = new Map();
  const r = request({ t: 'mem_list' }, (m) => {
    if (m.t === 'mem_sources') {
      const H = { claude: 'Claude Code', codex: 'Codex', opencode: 'OpenCode' };
      $('mem-status').textContent = m.harness ? t('mem.sources', { agent: H[m.harness] ?? m.harness }) : t('mem.noAgent');
      for (const src of Array.isArray(m.sources) ? m.sources : []) {
        const sec = el('section', 'mem-src');
        const head = el('h2', 'mem-src-h', String(src.label ?? ''));
        const path = el('p', 'small mem-path', String(src.path ?? ''));
        const PROB = ['not_found', 'symlink', 'too_large', 'io', 'too_many_files'];
        const note = el('span', 'small mem-note', src.problem ? (PROB.includes(src.problem) ? t('mem.prob.' + src.problem) : `(${src.problem})`) : t('mem.count', { n: Number.isInteger(src.n) ? src.n : 0 }));
        head.append(' ', note);
        const ul = el('ul', 'mem-items');
        sec.append(head, path, ul);
        sec.dataset.src = String(src.id ?? '');
        if (src.problem === 'not_found') sec.classList.add('mem-empty');
        list.append(sec);
        boxes.set(src.id, ul);
      }
      renderTrash(Array.isArray(m.trash) ? m.trash : []);
      return;
    }
    if (m.t === 'mem_items') {
      for (const it of Array.isArray(m.items) ? m.items : []) {
        const ul = boxes.get(it.src);
        if (!ul || typeof it.text !== 'string') continue;
        const key = `${it.src}\n${it.file}\n${it.iid}`;
        memItems.set(key, it);
        const li = el('li', 'mem-item');
        li.dataset.kind = String(it.kind ?? '');
        const body = el('div', 'mem-body');
        if (it.title) body.append(el('p', 'mem-title', String(it.title) + (it.file ? `  ·  ${it.file}` : '')));
        if (it.desc) body.append(el('p', 'small mem-desc', String(it.desc)));
        body.append(el('pre', 'mem-text', it.text + (it.cut ? '\n' + t('mem.cut', { shown: it.text.length, total: it.cut }) : '')));
        const del = el('button', 'aj-btn aj-btn--sm mem-del', t('mem.del')); del.type = 'button';
        del.setAttribute('aria-label', t('mem.delAria'));
        del.addEventListener('click', () => deleteMemory(key));
        li.append(body, del);
        ul.append(li);
      }
      if (m.more === false) { pending.delete(r); if (!$('mem-status').textContent.endsWith('。')) $('mem-status').textContent += ''; }
    }
  });
}
function renderTrash(items) {
  const ul = $('mem-trash'); ul.replaceChildren();
  if (!items.length) { ul.append(el('li', 'small', t('mem.trashEmpty'))); return; }
  for (const x of items) {
    const li = el('li', 'trash-item');
    li.append(el('span', 'trash-text', `${String(x.label ?? '')}${lang() === 'en' ? ': ' : '：'}${String(x.text ?? '')}`));
    const b = el('button', 'aj-btn aj-btn--sm', t('mem.restore')); b.type = 'button';
    b.addEventListener('click', () => undoMemory(String(x.id)));
    li.append(b);
    ul.append(li);
  }
}
async function deleteMemory(key) {
  const it = memItems.get(key);
  if (!it) return;
  const preview = it.text.replace(/\s+/g, ' ').slice(0, 120);
  if (!await confirmSheet(t('mem.delTitle'), (lang() === 'en' ? `"${preview}"` : `「${preview}」`) + '\n' + plain(t('mem.delText')), t('mem.delYes'))) return;
  const target = { src: it.src, file: it.file, fsha: it.fsha, iid: it.iid };
  let res;
  try { res = await command(await signed('mem_rm', target, { t: 'mem_rm', ...target })); } catch (e) { res = { ok: false, why: e.message }; }
  if (res.ok) {
    toast(t('mem.deleted'), t('mem.undo'), () => undoMemory(String(res.undo)));
  } else {
    toast(t('mem.delFail', { why: why(res.why) }));
  }
  loadMemory();
}
async function undoMemory(id) {
  let res;
  try { res = await command(await signed('mem_undo', { id }, { t: 'mem_undo', id })); } catch (e) { res = { ok: false, why: e.message }; }
  toast(res.ok ? t('mem.restored') : t('mem.restoreFail', { why: why(res.why) }));
  if (panel === 'mem-view') loadMemory();
}

// ---- 操作与审批记录 (Activity)
let actNext = null;
const ACT_RESULTS = ['allow', 'deny', 'allow_batch', 'timeout', 'no_device', 'estop', 'serve_stop', 'agent_gone', 'too_many', 'policy'];
const ACT_KINDS = ['turn_start', 'turn_end', 'ask', 'decision', 'auto', 'grant', 'grant_end', 'estop', 'resume', 'message_refused', 'mem_rm',
  'mem_undo', 'task_on', 'task_off', 'task_run', 'task_done', 'control_refused', 'slash', 'truncated'];
function actText(r) {
  const by = r.by ? t('act.by', { by: r.by }) : '';
  const tx = String(r.text ?? r.summary ?? '').replace(/\s+/g, ' ').slice(0, 200);
  const sep = lang() === 'en' ? ', ' : '、';
  const cats = Array.isArray(r.cats) && r.cats.length ? `[${r.cats.map((c) => (CATS.includes(c) ? t('ask.cat.' + c) : c)).join(sep)}] ` : '';
  const task = r.task ? t('act.task', { task: r.task }) : '';
  if (!ACT_KINDS.includes(r.k)) return String(r.k ?? '');
  const v = {
    by, t: tx, task, cats, tool: r.tool ?? '', scope: r.scope ?? '', why: r.why ?? '', label: r.label ?? '', id: r.id ?? '',
    title: r.title ?? r.id ?? '', verdict: r.verdict ?? '', line: r.line ?? '', action: r.action ?? '', cmd: r.cmd ?? '',
    ro: r.readonly ? t('act.k.readonly') : '',
    stopped: r.result === 'stopped' ? t('act.k.turn_end_stopped') : '',
    secs: r.secs !== undefined ? t('act.k.secs', { n: r.secs }) : '',
    result: r.k === 'slash' ? (['ok', 'info', 'error', 'refused'].includes(r.result) ? t('cmd.result.' + r.result) : r.result ?? '')
      : ACT_RESULTS.includes(r.result) ? t('act.result.' + r.result) : r.result ?? '',
  };
  if (r.k === 'decision') v.t = tx ? ' ' + tx : '';
  return t('act.k.' + r.k, v);
}
function loadActivity(fresh) {
  if (fresh) { $('act-list').replaceChildren(); actNext = null; }
  $('act-status').textContent = t('act.loading');
  $('act-more').hidden = true;
  const r = request({ t: 'act_list', before: fresh ? null : actNext }, (m) => {
    if (m.t !== 'act_page') return;
    for (const it of Array.isArray(m.items) ? m.items : []) {
      const li = el('li', 'act-item');
      li.dataset.k = String(it.k ?? '');
      if (it.result) li.dataset.result = String(it.result);
      const when = Number.isInteger(it.ts) ? new Date(it.ts).toLocaleString(locale(), { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
      li.append(el('span', 'act-time', when), el('span', 'act-text', actText(it)));
      $('act-list').append(li);
    }
    if (m.more === false) {
      pending.delete(r);
      actNext = typeof m.next === 'string' ? m.next : null;
      $('act-more').hidden = !actNext;
      const n = $('act-list').children.length;
      $('act-status').textContent = (m.on === false ? t('act.off') + ' ' : '') + (n ? t('act.recent', { n }) : t('act.none'));
    }
  });
}

// ---- 定时任务 (scheduled tasks)
function loadTasks() {
  $('tasks-status').textContent = t('tasks.loading');
  const ul = $('tasks-list'); ul.replaceChildren();
  const r = request({ t: 'task_list' }, (m) => {
    if (m.t !== 'tasks') return;
    for (const x of Array.isArray(m.items) ? m.items : []) ul.append(taskCard(x, m.paused === true));
    if (m.more === false) {
      pending.delete(r);
      const n = ul.children.length;
      $('tasks-status').textContent = (m.paused ? t('tasks.paused') : '') + (n ? '' : (m.agent ? t('tasks.none') : t('tasks.noAgent')));
    }
  });
}
const tzText = (tk) => (tk.tz === 'local' ? t('tasks.localTz') : tk.tz ?? '');
function taskCard(tk, paused) {
  const li = el('li', 'task-card');
  const title = tk.title && typeof tk.title[lang()] === 'string' ? tk.title[lang()] : tk.title && typeof tk.title.zh === 'string' ? tk.title.zh : String(tk.id);
  li.dataset.id = String(tk.id); li.dataset.enabled = String(!!tk.enabled);
  li.append(el('p', 'task-title', title));
  const tags = el('p', 'ask-tags');
  if (tk.mode === 'research') tags.append(el('span', 'tag tag-low', t('tasks.readonly')));
  if (tk.mode === 'normal') tags.append(el('span', 'tag', t('tasks.normal')));
  tags.append(el('span', 'tag ' + (tk.enabled ? 'tag-on' : 'tag-off'), t(tk.problems?.length ? 'tasks.invalid' : tk.enabled ? (paused ? 'tasks.onPaused' : 'tasks.on') : tk.stale ? 'tasks.stale' : 'tasks.off')));
  if (tk.running) tags.append(el('span', 'tag tag-task', t('tasks.running')));
  li.append(tags);
  li.append(el('p', 'small', t('tasks.when', { when: cronText(tk.schedule), tz: tzText(tk) }) + (Number.isInteger(tk.next) ? t('tasks.next', { at: new Date(tk.next * 1000).toLocaleString(locale(), { hour12: false, month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) }) : '')));
  if (tk.problems?.length) li.append(el('p', 'error small', t('tasks.bad', { p: tk.problems[0] })));
  if (tk.last && typeof tk.last === 'object') li.append(el('p', 'small', t('tasks.last', { verdict: tk.last.verdict ?? '', line: tk.last.line ?? '' })));
  if (!tk.problems?.length || tk.enabled) {
    const on = !tk.enabled;
    const b = el('button', on ? 'aj-btn aj-btn--primary aj-btn--sm' : 'aj-btn aj-btn--sm', t(on ? 'tasks.enable' : 'tasks.disable')); b.type = 'button';
    b.addEventListener('click', () => setTask(tk, on, title));
    li.append(b);
  }
  return li;
}
function cronText(expr) {                             // the common shapes in words; anything else as the raw cron fields
  if (typeof expr !== 'string') return '?';
  const f = expr.split(' ');
  if (f.length !== 5) return expr;
  const [m, h, dom, mon, dow] = f;
  const week = t('cron.days').split('|');
  const hm = /^\d+$/.test(m) && /^\d+$/.test(h) ? `${h.padStart(2, '0')}:${m.padStart(2, '0')}` : null;
  if (expr === '* * * * *') return t('cron.everyMin');
  if (/^\*\/\d+$/.test(m) && h === '*' && dom === '*' && mon === '*' && dow === '*') return t('cron.everyN', { n: m.slice(2) });
  if (/^\d+$/.test(m) && h === '*' && dom === '*' && mon === '*' && dow === '*') return t('cron.hourly', { m });
  if (hm && dom === '*' && mon === '*' && dow === '*') return t('cron.daily', { hm });
  if (hm && dom === '*' && mon === '*' && /^[0-7](,[0-7])*$/.test(dow)) return t('cron.weekly', { days: dow.split(',').map((d) => week[+d]).join(t('cron.daySep')), hm });
  if (hm && dom === '*' && mon === '*' && dow === '1-5') return t('cron.weekdays', { hm });
  if (hm && /^\d+$/.test(dom) && mon === '*' && dow === '*') return t('cron.monthly', { d: dom, hm });
  return t('cron.raw', { expr });
}
async function setTask(tk, on, title) {
  const text = on ? t('tasks.textOn', { title, when: cronText(tk.schedule), tz: tzText(tk), ro: tk.mode === 'research' ? t('tasks.textOnRo') : '' }) : t('tasks.textOff', { title });
  if (!await confirmSheet(t(on ? 'tasks.confirmOn' : 'tasks.confirmOff'), text, t(on ? 'tasks.enable' : 'tasks.disable'))) return;
  const target = { id: String(tk.id), tsha: typeof tk.tsha === 'string' ? tk.tsha : '' };
  let res;
  try { res = await command(await signed(on ? 'task_on' : 'task_off', target, { t: 'task_set', on, ...target })); } catch (e) { res = { ok: false, why: e.message }; }
  toast(res.ok ? t(on ? 'tasks.enabled' : 'tasks.disabled') : t('tasks.fail', { why: why(res.why) }));
  loadTasks();
}

// ---------------------------------------------------------------- slash commands (PROTOCOL §8 "slash commands", ADR-A70 – A72)
// The host interprets a short list of commands through each harness's own headless interface; anything else that looks like a
// command is refused by the host (or, for Claude Code, passed on when it is one of its skills). Results come back as chat
// entries `from: "cmd"` (cards). Only this page's paired session can send them; nothing is stored on the phone.
const CMDS = ['clear', 'compact', 'model', 'context', 'cost', 'usage', 'status', 'help', 'stop'];
const CMD_LABELS = [...CMDS, 'undo_clear', 'refused'];
const CMD_RE = /^\/([A-Za-z][\w:.-]{0,63})(?:[ \t]+([\s\S]*))?$/;
function sendCmd(cmd, arg = '', confirm = false) {
  const s = sess;
  if (!s || s.phase !== 'ready') return false;
  sendApp(s, { t: 'slash', cmd, ...(arg ? { arg: arg.slice(0, 200) } : {}), ...(confirm ? { confirm: true } : {}) }).catch(() => {});
  return true;
}
async function runCmd(cmd, arg = '') {
  if (cmd === 'clear') {
    if (!await confirmSheet(t('cmd.clearTitle'), t('cmd.clearText'), t('cmd.clearYes'))) return false;
    return sendCmd('clear', '', true);
  }
  return sendCmd(cmd, arg);
}
function toggleCmdBar(open) {
  const bar = $('cmd-bar');
  const showIt = open ?? bar.hidden;
  bar.hidden = !showIt;
  $('cmd-toggle').setAttribute('aria-expanded', String(showIt));
}
function addCmdCard(m) {
  const name = typeof m.cmd === 'string' ? m.cmd : 'refused';
  const kind = ['ok', 'info', 'error', 'refused'].includes(m.kind) ? m.kind : (m.ok ? 'ok' : 'error');
  const li = el('li', m.sep ? 'cmd-card cmd-sep' : 'cmd-card');
  li.dataset.dir = 'in'; li.dataset.from = 'cmd'; li.dataset.cmd = name; li.dataset.kind = kind;
  if (Number.isInteger(m.seq)) lastSeq = m.seq;
  const head = el('p', 'cmd-head');
  head.append(el('span', '', name === 'refused' ? t('cmd.refused') : `/${name === 'undo_clear' ? 'clear' : name} · ${CMD_LABELS.includes(name) ? t('cmd.' + name) : name}`));
  if (typeof m.by === 'string' && m.by) head.append(el('span', 'cmd-by', m.by.slice(0, 64)));
  li.append(head, el('p', 'cmd-text', m.text));
  if (Array.isArray(m.models) && m.models.length) {
    const box = el('div', 'cmd-models');
    for (const x of m.models.slice(0, 40)) {
      if (!x || typeof x.id !== 'string') continue;
      const b = el('button', 'aj-btn cmd-model'); b.type = 'button';
      b.dataset.model = x.id;
      if (x.cur) b.setAttribute('aria-current', 'true');
      b.append(el('span', '', (typeof x.name === 'string' && x.name ? x.name : x.id) + (x.cur ? t('cmd.current') : '')),
        el('span', 'cmd-model-desc', [x.id, typeof x.desc === 'string' ? x.desc : ''].filter(Boolean).join(' · ')));
      b.addEventListener('click', () => { for (const o of box.querySelectorAll('button')) o.disabled = true; sendCmd('model', x.id); });
      box.append(b);
    }
    li.append(box);
  }
  if (name === 'clear' || name === 'undo_clear') {   // only the newest clear can be undone
    for (const old of $('messages').querySelectorAll('li.cmd-card .cmd-undo')) old.remove();
  }
  if (m.undo === true) {
    const row = el('div', 'cmd-row');
    const u = el('button', 'aj-btn aj-btn--sm cmd-undo', t('cmd.undo')); u.type = 'button';
    u.addEventListener('click', () => { u.disabled = true; sendCmd('undo_clear'); });
    row.append(u);
    li.append(row);
  }
  $('messages').append(li);
  chatEmpty();
  li.scrollIntoView({ block: 'end' });
}

// ---------------------------------------------------------------- Web Push (PROTOCOL §9): no content, subscription → host only
let pushKey = null;
const pushSupported = () => 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window && window.isSecureContext;
const isIOS = () => /iPhone|iPad|iPod/.test(navigator.userAgent);
const isAndroid = () => /Android/.test(navigator.userAgent);
const standalone = () => matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
let swReg = null;
async function registration() {
  if (swReg) return swReg;
  swReg = await navigator.serviceWorker.register('sw.js' + (lang() === 'en' ? '?lang=en' : ''), { scope: './' });
  return swReg;
}
function reregister() {                               // the worker's two notification sentences follow the language
  if (!pushSupported()) return;
  swReg = null;
  registration().catch(() => {});
}
const sameKey = (sub, key) => {
  const k = sub?.options?.applicationServerKey;
  if (!k) return false;
  const a = new Uint8Array(k);
  return a.length === key.length && a.every((x, i) => x === key[i]);
};
function subMsg(sub) {
  const j = sub.toJSON();
  return { t: 'push_sub', endpoint: j.endpoint, p256dh: j.keys?.p256dh, auth: j.keys?.auth };
}
let pushState = 'none';
function pushUi(stateName) {
  pushState = stateName;
  const row = $('push-row'), btn = $('push-on'), txt = $('push-text');
  row.hidden = stateName === 'none';
  btn.hidden = stateName !== 'offer';
  txt.textContent = ['offer', 'on', 'ios', 'denied', 'failed'].includes(stateName) ? t('push.' + stateName) : '';
}
async function gotPushKey(s, k) {
  let key;
  try { key = Uint8Array.from(atob(k.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((k.length + 3) % 4)), (c) => c.charCodeAt(0)); } catch { return; }
  if (key.length !== 65) return;
  pushKey = key;
  if (!pushSupported()) return pushUi(isIOS() && !standalone() ? 'ios' : 'none');
  if (Notification.permission === 'denied') return pushUi('denied');
  try {
    const reg = await registration();
    const sub = await reg.pushManager.getSubscription();
    if (sub && sameKey(sub, key) && Notification.permission === 'granted') {
      await sendApp(s, subMsg(sub));                  // re-sent on every connect; the host keeps one per device
      return pushUi('on');
    }
  } catch { /* fall through to the offer */ }
  pushUi('offer');
}
async function enablePush() {
  if (!pushKey || !sess || sess.phase !== 'ready') return;
  try {
    if ((await Notification.requestPermission()) !== 'granted') return pushUi(Notification.permission === 'denied' ? 'denied' : 'offer');
    const reg = await registration();
    let sub = await reg.pushManager.getSubscription();
    if (sub && !sameKey(sub, pushKey)) { await sub.unsubscribe(); sub = null; }
    sub = sub ?? await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: pushKey });
    await sendApp(sess, subMsg(sub));
    pushUi('on');
  } catch {
    pushUi('failed');
    $('push-on').hidden = false;
  }
}

// ---------------------------------------------------------------- Add to Home Screen hint (phones only, not when installed)
// The one thing this page keeps in local storage besides brand/lang.js's language + theme: that the hint was dismissed.
const A2HS_KEY = 'aj.a2hs';
let a2hsDismissed = false;
try { a2hsDismissed = localStorage.getItem(A2HS_KEY) === '1'; } catch { /* blocked storage: show it again next time */ }
function renderA2hs() {
  const box = $('a2hs');
  if (!box) return;
  const kind = isIOS() ? 'ios' : isAndroid() ? 'android' : null;
  box.hidden = a2hsDismissed || standalone() || !kind || !['pair-view', 'chat-view'].includes(view);
  if (!box.hidden) fillText($('a2hs-text'), t('a2hs.' + kind));
  $('pair-iphone').hidden = !(isIOS() && !standalone());
  fillText($('pair-step1'), t(isIOS() ? 'pair.step1ios' : 'pair.step1'));   // iPhone Safari cannot scan inside the page:
  fillText($('pair-step2'), t(isIOS() ? 'pair.step2ios' : 'pair.step2'));   // pair with the link, from the Home Screen app
}
function dismissA2hs() {
  a2hsDismissed = true;
  try { localStorage.setItem(A2HS_KEY, '1'); } catch { /* blocked storage */ }
  renderA2hs();
}

async function onSend() {
  const input = $('msg-input');
  const text = input.value;
  if (!text.trim() || !sess || sess.phase !== 'ready' || estopOn) return;
  if (text.length > MAX_TEXT) return;
  const c = CMD_RE.exec(text.trim());
  if (c && CMDS.includes(c[1].toLowerCase())) {     // typed like a button: the host runs it; /clear asks first
    const name = c[1].toLowerCase();
    if (!await runCmd(name, (c[2] || '').trim())) return;
    input.value = '';
    addMessage('out', text.trim());
    return;
  }
  try {
    await sendApp(sess, { t: 'msg', id: randHex(8), text, ts: Date.now() });
  } catch { return; }                                 // the status line already reflects the broken connection
  input.value = '';
  input.style.height = '';
  addMessage('out', text);
}

// ---------------------------------------------------------------- language switch: re-render everything that is ours
function relang() {
  applyStatic();
  setStatus(state, statusKey[0], statusKey[1]);
  if (agentLast) setAgentStatus(agentLast[0], agentLast[1]);
  pushUi(pushState);
  renderGrants();
  renderEstop();
  renderError();
  renderA2hs();
  if (!$('scan-hint').hidden) fillText($('scan-hint'), t('pair.scanHint'));
  if (!$('pair-error').hidden) $('pair-error').textContent = t('pair.badLink');
  reloadPanel();
  reregister();
}

// ---------------------------------------------------------------- wiring
function closeMenu() { $('menu').open = false; }
function toggleBadge(open) {
  const p = $('badge-panel');
  p.hidden = !(open ?? p.hidden);
  $('badge').setAttribute('aria-expanded', String(!p.hidden));
  if (!p.hidden) p.scrollIntoView({ block: 'nearest' });
}
function wire() {
  $('version-hash').textContent = VERSION.combined;
  $('badge').addEventListener('click', () => toggleBadge());
  $('badge-close').addEventListener('click', () => toggleBadge(false));
  $('menu-about').addEventListener('click', () => { closeMenu(); toggleBadge(true); });
  document.addEventListener('click', (e) => { if ($('menu').open && !e.target.closest('#menu')) closeMenu(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && $('menu').open) { closeMenu(); $('menu').querySelector('summary').focus(); } });
  for (const a of $('menu').querySelectorAll('a')) a.addEventListener('click', closeMenu);
  $('a2hs-ok').addEventListener('click', dismissA2hs);
  $('scan').hidden = !('BarcodeDetector' in window);          // iPhone Safari has no in-page scanner: paste the link instead
  $('pair-go').addEventListener('click', () => startPairing($('pair-link').value));
  $('scan').addEventListener('click', () => { startScan(); });
  $('scan-stop').addEventListener('click', stopScan);
  $('send').addEventListener('click', onSend);
  $('push-on').addEventListener('click', enablePush);
  $('grant-off').addEventListener('click', revokeGrants);
  $('open-mem').addEventListener('click', () => openPanel('mem-view'));
  $('open-act').addEventListener('click', () => openPanel('act-view'));
  $('open-tasks').addEventListener('click', () => openPanel('tasks-view'));
  for (const b of document.querySelectorAll('[data-back]')) b.addEventListener('click', () => openPanel('chat-view'));
  $('act-more').addEventListener('click', () => loadActivity(false));
  $('estop').addEventListener('click', onEstop);
  $('cmd-toggle').addEventListener('click', () => toggleCmdBar());
  for (const b of document.querySelectorAll('#cmd-bar [data-cmd]')) {
    b.addEventListener('click', async () => { if (await runCmd(b.dataset.cmd)) toggleCmdBar(false); });
  }
  $('resume').addEventListener('click', onResume);
  $('msg-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); onSend(); }
  });
  $('msg-input').addEventListener('input', () => {             // grow with the text, up to the CSS max-height
    const i = $('msg-input'); i.style.height = 'auto'; i.style.height = Math.min(i.scrollHeight + 2, 160) + 'px';
  });
  $('repair').addEventListener('click', async () => { await dbDel('host'); showIdle(); });   // keeps the device key
  $('retry').addEventListener('click', () => {
    const a = $('retry').dataset.action;
    if (a === 'resume') resume(); else if (a === 'reload') location.reload(); else showIdle();
  });
  addEventListener('hashchange', () => {             // a QR opened while this page is already open
    if (!location.hash.startsWith('#p=')) return;
    const l = location.hash;
    history.replaceState(null, '', location.pathname + location.search);
    startPairing(l);
  });
  addEventListener('online', reconnectNow);
  document.addEventListener('visibilitychange', () => {
    const fg = document.visibilityState === 'visible';
    if (sess && sess.phase === 'ready') sendApp(sess, { t: 'vis', fg }).catch(() => {});   // the host pushes only while hidden
    if (fg) reconnectNow();
  });
  if (window.AJLang) window.AJLang.onChange(relang);
}

async function main() {
  if (await leaveLegacyHost()) return;
  applyStatic();
  renderEstop();
  wire();
  if (!globalThis.crypto?.subtle || !globalThis.indexedDB || !globalThis.WebSocket) return fatal('error.missing');
  if (pushSupported()) registration().catch(() => {});
  try { await deviceKey(); } catch (e) {
    return fatal(e && e.name === 'NotSupportedError' ? 'error.noCrypto' : 'error.noStore');
  }
  if (pendingLink) { const l = pendingLink; pendingLink = null; return startPairing(l); }
  const host = await dbGet('host');
  if (host && host.approved) return resume();
  showIdle();
}

main();
