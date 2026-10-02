// agentjarvis web client (alpha). Spec: agentjarvis/protocol/PROTOCOL.md §2–§4. One host only (N=1).
// PR1: every human-typed byte leaves this page only inside a Noise transport message; message text is rendered with
// textContent only; the device's X25519 private key is a non-extractable CryptoKey kept in IndexedDB; chat history is
// kept in memory only (nothing plaintext at rest). L1 (PROTOCOL §8–§9): the Agent's replies, status and permission requests
// arrive inside the same Noise session; an approval is signed with a non-extractable Ed25519 key kept in IndexedDB; the
// push subscription goes to the host only (end to end), and pushes carry no content.
import { IK, IKPSK2, Handshake, generateKeypair } from './proto/noise.js';
import { KIND, MAX_TEXT, padJson, unpadJson, parsePairing as parseLink, pairPrologue, resumePrologue, safetyCode, frame,
  b64u, deviceId, approveMessage, generateSigningKeypair } from './proto/wire.js';
import VERSION from './version.js';

const $ = (id) => document.getElementById(id);
const enc = new TextEncoder();
const EMPTY = new Uint8Array(0);
const VIEWS = ['pair-view', 'sas-view', 'chat-view', 'revoked-view', 'error-view'];
const PAIR_FAIL = '未获批准或已过期，请在电脑上重新运行 jarvis pair';

// Read the pairing fragment first thing and strip it from the URL / history (it carries the one-time PSK).
let pendingLink = null;
if (location.hash.startsWith('#p=')) {
  pendingLink = location.hash;
  history.replaceState(null, '', location.pathname + location.search);
}

// ---------------------------------------------------------------- state / status
let state = 'idle';
Object.defineProperty(window, '__ajState', { get: () => state, enumerable: false, configurable: false });

function setStatus(s, text) {
  state = s;
  const el = $('status');
  el.dataset.state = s;
  el.textContent = text;
  $('send').disabled = s !== 'ready';
}
function show(view) { for (const v of VIEWS) $(v).hidden = v !== view; }

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
    : /Safari\//.test(ua) ? 'Safari' : '浏览器';
  return ('网页 · ' + [os, br].filter(Boolean).join(' ')).slice(0, 64);
}

// The client only ever talks to the relay next to it (alpha-web.X → alpha-relay.X): a pasted link cannot move this device
// to somebody else's relay + host (CSP connect-src enforces the same on this origin; this keeps it true in any shell).
// A page served from 127.0.0.1 exists only in tests and may also use a local relay.
function allowRelay(url) {
  const u = new URL(url);
  if (location.hostname === '127.0.0.1') {
    return (u.protocol === 'ws:' && u.hostname === '127.0.0.1') || (u.protocol === 'wss:' && u.hostname.startsWith('alpha-relay.'));
  }
  return u.protocol === 'wss:' && !u.port && u.hostname === location.hostname.replace(/^alpha-web\./, 'alpha-relay.');
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
  setStatus('connecting', '连接中…');
  ws.onopen = () => { if (sess === s && s.phase === 'wait-host') setStatus('waiting-host', '等电脑上线…'); };
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
      setStatus('awaiting-approval', '等电脑批准');
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
    s.mode = 'resume'; s.ctx = host;                  // drop the PSK; a later host restart resumes on this socket
    $('messages').replaceChildren(); asks.clear(); lastSeq = 0;
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
    const from = ['agent', 'host', 'you', 'device', 'notice'].includes(m.from) ? m.from : 'host';
    addMessage(from === 'you' ? 'out' : from === 'device' ? 'out' : 'in', m.text, from, typeof m.name === 'string' ? m.name : '');
    return;
  }
  if (m.t === 'status') return setAgentStatus(m.s, m.agent);
  if (m.t === 'ask') return showAsk(s, m);
  if (m.t === 'ask_done') return askDone(m.id, m.result);
  if (m.t === 'push_key') return gotPushKey(s, m.k);
  // unknown app message types are ignored (forward compatible)
}

function enterReady(s) {
  s.phase = 'ready';
  backoff = 1000;
  show('chat-view');
  setStatus('ready', '已连接');
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
    setStatus('pairing', '配对中…');
  } else {
    s.hs = await new Handshake({ protocol: IK, initiator: true, prologue: resumePrologue(s.ctx.channel), s: dev, rs: s.ctx.hostPub }).init();
    const sk = await signKey();                       // a device paired before L1 registers its approval key here (host keeps the first)
    msg1 = frame(KIND.RESUME_INIT, await s.hs.writeMessage(enc.encode(JSON.stringify(sk ? { v: 1, sk: b64u(sk.pub) } : { v: 1 }))));
    setStatus('connecting', '正在恢复连接…');
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
  setStatus('waiting-host', '电脑离线');
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
  if (e && e.name === 'NotSupportedError') return fatal('这个浏览器不支持所需的加密算法（X25519）。换最新版 Chrome、Safari 或 Firefox 再试。');
  setStatus('error', '连接异常，已断开');
  $('error-text').textContent = wasPair ? '配对数据没通过校验，已断开。请在电脑上重新运行 jarvis pair。'
    : '收到无法校验的数据，已断开以保护你的对话。';
  $('retry').textContent = wasPair ? '返回' : '重连';
  $('retry').dataset.action = wasPair ? 'idle' : 'resume';
  show('error-view');
}

function pairFailed() {
  setStatus('error', PAIR_FAIL);
  $('error-text').textContent = PAIR_FAIL;
  $('retry').textContent = '返回';
  $('retry').dataset.action = 'idle';
  show('error-view');
}

function revoked() {
  closeSession();
  setStatus('revoked', '此设备已被主机移除');
  show('revoked-view');
}

function fatal(text) {
  closeSession();
  setStatus('error', '无法使用');
  $('error-text').textContent = text;
  $('retry').textContent = '重试';
  $('retry').dataset.action = 'reload';
  show('error-view');
}

function scheduleReconnect() {
  clearTimeout(reconnectTimer);
  const wait = backoff;
  backoff = Math.min(backoff * 2, 30000);
  setStatus('connecting', `连接断开，${Math.round(wait / 1000)} 秒后重连…`);
  reconnectTimer = setTimeout(() => { reconnectTimer = null; resume(); }, wait);
}

async function resume() {
  const host = await dbGet('host');
  if (!host || !host.approved) return showIdle();
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
    err.textContent = '链接无效或已过期，请在电脑上重新运行 jarvis pair';
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
  show('pair-view');
  setStatus('idle', '未配对');
}

// ---------------------------------------------------------------- scanning (BarcodeDetector + camera, when available)
let scanStream = null;
async function startScan() {
  const hint = $('scan-hint');
  hint.textContent = '用系统相机扫描电脑上的二维码';
  let formats = [];
  if ('BarcodeDetector' in window && navigator.mediaDevices?.getUserMedia) {
    try { formats = await window.BarcodeDetector.getSupportedFormats(); } catch { formats = []; }
  }
  if (!formats.includes('qr_code')) { hint.hidden = false; return; }
  try {
    scanStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false });
  } catch {
    hint.textContent = '没拿到相机权限。用系统相机扫描电脑上的二维码';
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
  if (scanStream) for (const t of scanStream.getTracks()) t.stop();
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
  li.scrollIntoView({ block: 'end' });
}

// ---------------------------------------------------------------- Agent status (PROTOCOL §8)
const AGENT_NAME = { claude: 'Claude Code', codex: 'Codex' };
const STATUS_TEXT = { idle: '空闲', working: '干活中…', waiting: '等你批准', down: '没在运行' };
function setAgentStatus(st, agent) {
  const el = $('agent-status');
  if (!agent || !STATUS_TEXT[st]) { el.hidden = true; return; }
  el.hidden = false;
  el.dataset.s = st;
  el.textContent = `${AGENT_NAME[agent] ?? 'Agent'} · ${STATUS_TEXT[st]}`;
}

// ---------------------------------------------------------------- approvals (PROTOCOL §8)
const asks = new Map();                               // id → { li, tool, summary, deadline, timer }
const ASK_RESULT = { allow: '已批准', deny: '已拒绝', timeout: '超时，已自动拒绝', gone: '已取消' };
async function showAsk(s, m) {
  if (typeof m.id !== 'string' || !/^[0-9a-f]{32}$/.test(m.id) || typeof m.tool !== 'string' || typeof m.summary !== 'string') return;
  if (asks.has(m.id)) return;
  const ttl = Number.isInteger(m.ttl) && m.ttl >= 0 && m.ttl <= 600 ? m.ttl : 0;
  const li = document.createElement('li');
  li.dataset.dir = 'in'; li.dataset.from = 'ask'; li.className = 'ask';
  const h = document.createElement('p'); h.className = 'ask-title'; h.textContent = `Agent 想要执行：${m.tool}`;
  const pre = document.createElement('pre'); pre.className = 'ask-summary'; pre.textContent = m.summary;
  const meta = document.createElement('p'); meta.className = 'ask-meta';
  const row = document.createElement('div'); row.className = 'ask-row';
  const yes = document.createElement('button'); yes.type = 'button'; yes.className = 'btn btn-strong ask-allow'; yes.textContent = '批准';
  const no = document.createElement('button'); no.type = 'button'; no.className = 'btn ask-deny'; no.textContent = '拒绝';
  row.append(no, yes);
  li.append(h, pre, meta, row);
  const a = { li, tool: m.tool, summary: m.summary, deadline: Date.now() + ttl * 1000, timer: null, meta, row, done: false };
  asks.set(m.id, a);
  const tick = () => {
    if (a.done) return;
    const left = Math.max(0, Math.ceil((a.deadline - Date.now()) / 1000));
    a.meta.textContent = left > 0 ? `还剩 ${left} 秒，不按就自动拒绝` : '时间到，等电脑确认…';
    if (left > 0) a.timer = setTimeout(tick, 1000);
  };
  const sk = await signKey();
  if (!sk) {
    row.replaceChildren();
    const p = document.createElement('p'); p.className = 'small';
    p.textContent = '这个浏览器不支持签名（Ed25519），不能在这里批准。换最新版 Chrome / Safari / Firefox 后重新配对。';
    row.append(p);
  }
  yes.addEventListener('click', () => answer(m.id, true));
  no.addEventListener('click', () => answer(m.id, false));
  tick();
  $('messages').append(li);
  li.scrollIntoView({ block: 'end' });
}
async function answer(id, ok) {
  const a = asks.get(id);
  const s = sess;
  if (!a || a.done || !s || s.phase !== 'ready') return;
  const sk = await signKey();
  if (!sk) return;
  for (const b of a.row.querySelectorAll('button')) b.disabled = true;
  try {
    const msg = await approveMessage(s.ctx.channel, await myDeviceId(), id, ok ? 'allow' : 'deny', a.tool, a.summary);
    const sig = new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, sk.priv, msg));
    await sendApp(s, { t: 'answer', id, ok, sig: b64u(sig) });
    a.meta.textContent = ok ? '已发送批准，等电脑确认…' : '已发送拒绝，等电脑确认…';
  } catch {
    for (const b of a.row.querySelectorAll('button')) b.disabled = false;
  }
}
function askDone(id, result) {
  const a = asks.get(id);
  if (!a || !ASK_RESULT[result]) return;
  a.done = true; clearTimeout(a.timer);
  a.row.replaceChildren();
  a.meta.textContent = ASK_RESULT[result];
  a.li.dataset.result = result;
}

// ---------------------------------------------------------------- Web Push (PROTOCOL §9): no content, subscription → host only
let pushKey = null;
const pushSupported = () => 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window && window.isSecureContext;
const isIOS = () => /iPhone|iPad|iPod/.test(navigator.userAgent);
const standalone = () => matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
let swReg = null;
async function registration() {
  if (swReg) return swReg;
  swReg = await navigator.serviceWorker.register('sw.js', { scope: './' });
  return swReg;
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
function pushUi(stateName) {
  const row = $('push-row'), btn = $('push-on'), txt = $('push-text');
  row.hidden = stateName === 'none';
  btn.hidden = stateName !== 'offer';
  txt.textContent = { offer: '锁屏后也想知道 Agent 回话了？', on: '锁屏提醒已开启（提醒里不含内容）', ios: 'iPhone 要先「分享 → 添加到主屏幕」，从主屏幕打开后才能开锁屏提醒', denied: '通知被浏览器禁止了：去浏览器设置里允许本站通知', none: '' }[stateName] ?? '';
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
    $('push-text').textContent = '没开成：这个浏览器不支持网页推送，或推送服务连不上。';
  }
}

async function onSend() {
  const input = $('msg-input');
  const text = input.value;
  if (!text.trim() || !sess || sess.phase !== 'ready') return;
  if (text.length > MAX_TEXT) return;
  try {
    await sendApp(sess, { t: 'msg', id: randHex(8), text, ts: Date.now() });
  } catch { return; }                                 // the status line already reflects the broken connection
  input.value = '';
  addMessage('out', text);
}

// ---------------------------------------------------------------- wiring
function wire() {
  $('version-hash').textContent = VERSION.combined;
  $('badge').addEventListener('click', () => {
    const panel = $('badge-panel');
    panel.hidden = !panel.hidden;
    $('badge').setAttribute('aria-expanded', String(!panel.hidden));
  });
  $('badge-close').addEventListener('click', () => { $('badge-panel').hidden = true; $('badge').setAttribute('aria-expanded', 'false'); });
  $('pair-go').addEventListener('click', () => startPairing($('pair-link').value));
  $('scan').addEventListener('click', () => { startScan(); });
  $('scan-stop').addEventListener('click', stopScan);
  $('send').addEventListener('click', onSend);
  $('push-on').addEventListener('click', enablePush);
  $('msg-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); onSend(); }
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
}

async function main() {
  wire();
  if (!globalThis.crypto?.subtle || !globalThis.indexedDB || !globalThis.WebSocket) return fatal('这个浏览器缺少必要功能（WebCrypto / IndexedDB / WebSocket）。');
  if (pushSupported()) registration().catch(() => {});
  try { await deviceKey(); } catch (e) {
    return fatal(e && e.name === 'NotSupportedError' ? '这个浏览器不支持所需的加密算法（X25519）。换最新版 Chrome、Safari 或 Firefox 再试。'
      : '无法在本机保存设备密钥（可能是隐私模式）。换普通窗口再试。');
  }
  if (pendingLink) { const l = pendingLink; pendingLink = null; return startPairing(l); }
  const host = await dbGet('host');
  if (host && host.approved) return resume();
  showIdle();
}

main();
