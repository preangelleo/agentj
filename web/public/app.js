// agentjarvis web client (alpha). Spec: agentjarvis/protocol/PROTOCOL.md §2–§4. One host only (N=1).
// PR1: every human-typed byte leaves this page only inside a Noise transport message; message text is rendered with
// textContent only; the device's X25519 private key is a non-extractable CryptoKey kept in IndexedDB; chat history is
// kept in memory only (nothing plaintext at rest). L1 (PROTOCOL §8–§9): the Agent's replies, status and permission requests
// arrive inside the same Noise session; an approval is signed with a non-extractable Ed25519 key kept in IndexedDB; the
// push subscription goes to the host only (end to end), and pushes carry no content.
import { IK, IKPSK2, Handshake, generateKeypair } from './proto/noise.js';
import { KIND, MAX_TEXT, padJson, unpadJson, parsePairing as parseLink, pairPrologue, resumePrologue, safetyCode, frame,
  b64u, deviceId, approveMessage, generateSigningKeypair, controlMessage } from './proto/wire.js';
import VERSION from './version.js';

const $ = (id) => document.getElementById(id);
const enc = new TextEncoder();
const EMPTY = new Uint8Array(0);
const VIEWS = ['pair-view', 'sas-view', 'chat-view', 'mem-view', 'act-view', 'tasks-view', 'revoked-view', 'error-view'];
const PANELS = ['chat-view', 'mem-view', 'act-view', 'tasks-view'];   // views of a ready session
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
  $('send').disabled = s !== 'ready' || estopOn;
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
  if (m.t === 'status') return setAgentStatus(m.s, m.agent);
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
const AGENT_NAME = { claude: 'Claude Code', codex: 'Codex', opencode: 'OpenCode' };
const STATUS_TEXT = { idle: '空闲', working: '干活中…', compacting: '压缩中…', waiting: '等你批准', down: '没在运行', stopped: '已急停' };
function setAgentStatus(st, agent) {
  const el = $('agent-status');
  if (!agent || !STATUS_TEXT[st]) { el.hidden = true; return; }
  el.hidden = false;
  el.dataset.s = st;
  el.textContent = `${AGENT_NAME[agent] ?? 'Agent'} · ${STATUS_TEXT[st]}`;
}

// ---------------------------------------------------------------- approvals (PROTOCOL §8)
const asks = new Map();                               // id → { li, tool, summary, deadline, timer, scope }
const ASK_RESULT = { allow: '已批准', deny: '已拒绝', timeout: '超时，已自动拒绝', gone: '已取消', stopped: '已作废（全部停下）' };
// the danger list (PROTOCOL §8, ADR-A47): fixed categories decided by the host; a dangerous request has no batch button
const CAT_LABEL = { spend: '花钱 · Spend', delete: '删除 · Delete', send: '对外发送 · Send', credentials: '改凭据 · Credentials', price: '改价 · Price' };
const LOW_LABEL = '低风险 · Low risk';
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
async function showAsk(s, m) {
  if (typeof m.id !== 'string' || !/^[0-9a-f]{32}$/.test(m.id) || typeof m.tool !== 'string' || typeof m.summary !== 'string') return;
  if (asks.has(m.id)) return;
  const ttl = Number.isInteger(m.ttl) && m.ttl >= 0 && m.ttl <= 600 ? m.ttl : 0;
  const cats = Array.isArray(m.cat) ? m.cat.filter((c) => CAT_LABEL[c]) : [];
  const danger = cats.length > 0;
  const scope = !danger && typeof m.batch === 'string' && m.batch.length > 0 && m.batch.length <= 400 ? m.batch : null;
  const li = el('li', danger ? 'ask ask-danger' : 'ask');
  li.dataset.dir = 'in'; li.dataset.from = 'ask'; li.dataset.risk = danger ? 'danger' : 'low';
  const tags = el('p', 'ask-tags');
  if (danger) for (const c of cats) { const t = el('span', 'tag tag-danger', CAT_LABEL[c]); t.dataset.cat = c; tags.append(t); }
  else tags.append(el('span', 'tag tag-low', LOW_LABEL));
  const h = el('p', 'ask-title', danger ? `⚠ 危险操作，必须逐条批准：${m.tool}` : `Agent 想要执行：${m.tool}`);
  if (typeof m.task === 'string' && m.task) tags.append(el('span', 'tag tag-task', `定时任务「${m.task.slice(0, 80)}」`));
  const why = typeof m.why === 'string' && m.why ? el('p', 'ask-why', `命中：${m.why.slice(0, 200)}`) : null;
  const pre = el('pre', 'ask-summary', m.summary);
  const meta = el('p', 'ask-meta');
  const row = el('div', 'ask-row');
  const yes = el('button', 'btn btn-strong ask-allow', danger ? '批准这一条' : '批准'); yes.type = 'button';
  const no = el('button', 'btn ask-deny', '拒绝'); no.type = 'button';
  row.append(no, yes);
  let batchBtn = null;
  if (scope) {
    const max = Number.isInteger(m.batch_max) ? m.batch_max : 20;
    const mins = Number.isInteger(m.batch_secs) ? Math.round(m.batch_secs / 60) : 10;
    batchBtn = el('button', 'btn ask-batch'); batchBtn.type = 'button';
    batchBtn.append(el('span', 'ask-batch-main', '批准，并在这一轮里自动批准同类低风险操作'),
      el('span', 'ask-batch-scope', `同类 = ${scope} · 最多 ${max} 次 / ${mins} 分钟 · 这一轮结束即失效 · 危险操作照样逐条问你`));
    row.append(batchBtn);
  }
  li.append(tags, h, ...(why ? [why] : []), pre, meta, row);
  const a = { li, tool: m.tool, summary: m.summary, deadline: Date.now() + ttl * 1000, timer: null, meta, row, done: false, scope };
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
    const p = el('p', 'small', '这个浏览器不支持签名（Ed25519），不能在这里批准。换最新版 Chrome / Safari / Firefox 后重新配对。');
    row.append(p);
  }
  yes.addEventListener('click', () => answer(m.id, true));
  no.addEventListener('click', () => answer(m.id, false));
  if (batchBtn) batchBtn.addEventListener('click', () => answer(m.id, true, true));
  tick();
  $('messages').append(li);
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
    a.meta.textContent = batch ? '已发送批准（含本轮同类授权），等电脑确认…' : ok ? '已发送批准，等电脑确认…' : '已发送拒绝，等电脑确认…';
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

// ---------------------------------------------------------------- batch approval (ADR-A48): auto lines + the grant bar
const grants = new Map();                             // grant id → { scope, left }
function showAuto(m) {
  if (typeof m.tool !== 'string' || typeof m.summary !== 'string') return;
  const li = el('li', 'auto', `已按你的授权自动批准：${m.tool} · ${m.summary.slice(0, 200)}`);
  li.dataset.dir = 'in'; li.dataset.from = 'auto';
  $('messages').append(li);
  li.scrollIntoView({ block: 'end' });
}
function renderGrants() {
  const bar = $('grant-bar');
  const live = [...grants.values()];
  bar.hidden = live.length === 0;
  $('grant-text').textContent = live.length === 0 ? '' :
    `本轮自动批准中：${live.map((g) => `${g.scope}（还剩 ${g.left} 次）`).join('；')}`;
}
function setGrant(m) {
  if (typeof m.id !== 'string' || !/^[0-9a-f]{32}$/.test(m.id) || typeof m.scope !== 'string') return;
  grants.set(m.id, { scope: m.scope.slice(0, 400), left: Number.isInteger(m.left) ? m.left : 0 });
  renderGrants();
}
const GRANT_END = { turn_end: '这一轮结束，批量授权已失效', revoked: '已收回批量授权', limit: '批量授权已用完', expired: '批量授权已过期', device_gone: '批量授权已失效（授权的设备已移除）', estop: '已急停，批量授权已收回' };
function endGrant(id, why) {
  if (!grants.delete(id)) return;
  renderGrants();
  if (why !== 'turn_end') addMessage('in', GRANT_END[why] ?? '批量授权已结束', 'notice');
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
const WHY = { changed: '文件刚被改过：已刷新，请再看一眼', unknown_item: '这一条已经不在了（已刷新）', bad_signature: '签名不对，电脑拒绝了',
  no_key: '这台设备没有批准密钥：重新配对后再试', stale: '手机时间和电脑差太多，电脑拒绝了', timeout: '电脑没有回应', offline: '没连上电脑',
  not_found: '回收站里没有这一条了', exists: '那个文件已经存在且内容不同，没有覆盖', symlink: '是软链接，不跟随',
  invalid: '任务文件无效，不能启用', unknown: '没有这个任务', io: '读写失败', shape: '请求格式不对', replay: '重复的请求' };

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

// ---- stop everything
function setEstop(m) {
  estopOn = m.on === true;
  $('estop-banner').hidden = !estopOn;
  $('estop').hidden = estopOn;
  $('estop-by').textContent = estopOn && typeof m.by === 'string' && m.by ? `（${m.by.slice(0, 64)}）` : '';
  $('send').disabled = state !== 'ready' || estopOn;
  $('msg-input').placeholder = estopOn ? '已急停：恢复后再发' : '说点什么，回车发送（/ 开头 = 命令）';
}
async function onEstop() {
  if (!await confirmSheet('全部停下？', '马上中断 Agent 正在做的这一轮，拒绝所有待批准的请求，收回批量授权，暂停全部定时任务。之后它不接新消息，直到你按「恢复」。', '确定，全部停下')) return;
  const res = await command(await signed('estop', {}, { t: 'estop' }).catch(() => ({ t: 'estop' })));
  if (!res.ok) toast('没有停下：' + (WHY[res.why] ?? res.why));
}
async function onResume() {
  if (!await confirmSheet('恢复？', 'Agent 重新接收消息，定时任务按各自的启用状态继续。', '恢复')) return;
  const res = await command(await signed('resume', {}, { t: 'resume' }).catch(() => ({ t: 'resume' })));
  if (!res.ok) toast('没有恢复：' + (WHY[res.why] ?? res.why));
}

// ---- 它记住了什么
const memItems = new Map();                           // key → item
function loadMemory() {
  $('mem-status').textContent = '正在读取…';
  const list = $('mem-list'); list.replaceChildren(); memItems.clear();
  const boxes = new Map();
  const r = request({ t: 'mem_list' }, (m) => {
    if (m.t === 'mem_sources') {
      const H = { claude: 'Claude Code', codex: 'Codex', opencode: 'OpenCode' };
      $('mem-status').textContent = m.harness ? `${H[m.harness] ?? m.harness} 的记忆来源（只列这些位置）：` : '还没接 Agent。';
      for (const src of Array.isArray(m.sources) ? m.sources : []) {
        const sec = el('section', 'mem-src');
        const head = el('h2', 'mem-src-h', String(src.label ?? ''));
        const path = el('p', 'small mem-path', String(src.path ?? ''));
        const PROB = { not_found: '（没有这个文件）', symlink: '（软链接，不跟随）', too_large: '（太大，不显示）', io: '（读不了）', too_many_files: '（文件太多，只显示前 200 个）' };
        const note = el('span', 'small mem-note', src.problem ? (PROB[src.problem] ?? `（${src.problem}）`) : `${Number.isInteger(src.n) ? src.n : 0} 条`);
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
        body.append(el('pre', 'mem-text', it.text + (it.cut ? `\n…（太长，只显示前 ${it.text.length} 字，共 ${it.cut} 字）` : '')));
        const del = el('button', 'btn mem-del', '删除'); del.type = 'button';
        del.setAttribute('aria-label', '删除这条记忆');
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
  if (!items.length) { ul.append(el('li', 'small', '没有。')); return; }
  for (const t of items) {
    const li = el('li', 'trash-item');
    li.append(el('span', 'trash-text', `${String(t.label ?? '')}：${String(t.text ?? '')}`));
    const b = el('button', 'btn', '恢复'); b.type = 'button';
    b.addEventListener('click', () => undoMemory(String(t.id)));
    li.append(b);
    ul.append(li);
  }
}
async function deleteMemory(key) {
  const it = memItems.get(key);
  if (!it) return;
  const preview = it.text.replace(/\s+/g, ' ').slice(0, 120);
  if (!await confirmSheet('删除这条记忆？', `「${preview}」\n原文先进电脑上的回收站，7 天内可以撤销。`, '删除')) return;
  const target = { src: it.src, file: it.file, fsha: it.fsha, iid: it.iid };
  let res;
  try { res = await command(await signed('mem_rm', target, { t: 'mem_rm', ...target })); } catch (e) { res = { ok: false, why: e.message }; }
  if (res.ok) {
    toast('已删除', '撤销', () => undoMemory(String(res.undo)));
  } else {
    toast('没有删除：' + (WHY[res.why] ?? res.why));
  }
  loadMemory();
}
async function undoMemory(id) {
  let res;
  try { res = await command(await signed('mem_undo', { id }, { t: 'mem_undo', id })); } catch (e) { res = { ok: false, why: e.message }; }
  toast(res.ok ? '已恢复' : '没有恢复：' + (WHY[res.why] ?? res.why));
  if (panel === 'mem-view') loadMemory();
}

// ---- 操作与审批记录
let actNext = null;
const ACT_RESULT = { allow: '批准', deny: '拒绝', allow_batch: '批准（含本轮同类）', timeout: '超时自动拒绝', no_device: '没有能批准的手机，拒绝',
  estop: '急停，拒绝', serve_stop: 'serve 停止，拒绝', agent_gone: 'Agent 撤回', too_many: '请求太多，拒绝',
  policy: '超出 Agent 自己的沙箱设置，自动拒绝' };
function actText(r) {
  const by = r.by ? `（${r.by}）` : '';
  const t = String(r.text ?? r.summary ?? '').replace(/\s+/g, ' ').slice(0, 200);
  const cats = Array.isArray(r.cats) && r.cats.length ? `[${r.cats.map((c) => (CAT_LABEL[c] ?? c).split(' · ')[0]).join('、')}] ` : '';
  const task = r.task ? `〔定时任务 ${r.task}〕` : '';
  switch (r.k) {
    case 'turn_start': return `对话开始${by}：${t}`;
    case 'turn_end': return `对话结束${r.result === 'stopped' ? '（被急停中断）' : ''}${r.secs !== undefined ? `，${r.secs} 秒` : ''}`;
    case 'ask': return `权限请求 ${task}${cats}${r.tool ?? ''}：${t}`;
    case 'decision': return `${ACT_RESULT[r.result] ?? r.result}${by}：${task}${r.tool ?? ''}${t ? ' ' + t : ''}`;
    case 'auto': return `按批量授权自动批准${by}：${r.tool ?? ''} ${t}`;
    case 'grant': return `批量授权${by}：${r.scope ?? ''}`;
    case 'grant_end': return `批量授权结束（${r.why ?? ''}）`;
    case 'estop': return `⛔ 全部停下${by}`;
    case 'resume': return `恢复${by}`;
    case 'message_refused': return `急停中拒收消息${by}：${t}`;
    case 'mem_rm': return `删除记忆${by} ${r.label ?? ''}：${t}`;
    case 'mem_undo': return `恢复记忆${by} ${r.label ?? ''}：${t}`;
    case 'task_on': return `启用定时任务${by}：${r.id ?? ''}`;
    case 'task_off': return `停用定时任务${by}：${r.id ?? ''}`;
    case 'task_run': return `定时任务开始：${r.title ?? r.id ?? ''}`;
    case 'task_done': return `定时任务结束：${r.title ?? r.id ?? ''} — ${r.verdict ?? ''}：${r.line ?? ''}${r.readonly ? '（只读运行）' : ''}`;
    case 'control_refused': return `拒绝了一条手机命令 ${r.action ?? ''}${by}：${r.why ?? ''}`;
    case 'slash': return `命令 /${r.cmd ?? ''}${by}：${CMD_RESULT[r.result] ?? r.result ?? ''}`;
    case 'truncated': return '（这一天的记录已达上限）';
    default: return String(r.k ?? '');
  }
}
function loadActivity(fresh) {
  if (fresh) { $('act-list').replaceChildren(); actNext = null; }
  $('act-status').textContent = '正在读取…';
  $('act-more').hidden = true;
  const r = request({ t: 'act_list', before: fresh ? null : actNext }, (m) => {
    if (m.t !== 'act_page') return;
    for (const it of Array.isArray(m.items) ? m.items : []) {
      const li = el('li', 'act-item');
      li.dataset.k = String(it.k ?? '');
      if (it.result) li.dataset.result = String(it.result);
      const when = Number.isInteger(it.ts) ? new Date(it.ts).toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
      li.append(el('span', 'act-time', when), el('span', 'act-text', actText(it)));
      $('act-list').append(li);
    }
    if (m.more === false) {
      pending.delete(r);
      actNext = typeof m.next === 'string' ? m.next : null;
      $('act-more').hidden = !actNext;
      const n = $('act-list').children.length;
      $('act-status').textContent = (m.on === false ? '（电脑上的操作记录已关闭：jarvis config activity on 打开）' : '') + (n ? `最近 ${n} 条，最新的在上面。` : '还没有记录。');
    }
  });
}

// ---- 定时任务
function loadTasks() {
  $('tasks-status').textContent = '正在读取…';
  const ul = $('tasks-list'); ul.replaceChildren();
  const r = request({ t: 'task_list' }, (m) => {
    if (m.t !== 'tasks') return;
    for (const t of Array.isArray(m.items) ? m.items : []) ul.append(taskCard(t, m.paused === true));
    if (m.more === false) {
      pending.delete(r);
      const n = ul.children.length;
      $('tasks-status').textContent = (m.paused ? '⛔ 已急停：全部定时任务暂停，恢复后按各自的启用状态继续。' : '') + (n ? '' : (m.agent ? '工作目录的 workflows/ 下还没有任务。' : '还没接 Agent。'));
    }
  });
}
function taskCard(t, paused) {
  const li = el('li', 'task-card');
  const title = t.title && typeof t.title.zh === 'string' ? t.title.zh : String(t.id);
  li.dataset.id = String(t.id); li.dataset.enabled = String(!!t.enabled);
  li.append(el('p', 'task-title', title));
  const tags = el('p', 'ask-tags');
  if (t.mode === 'research') tags.append(el('span', 'tag tag-low', '只读运行'));
  if (t.mode === 'normal') tags.append(el('span', 'tag', '可起草 · 危险动作问手机'));
  tags.append(el('span', 'tag ' + (t.enabled ? 'tag-on' : 'tag-off'), t.problems?.length ? '无效' : t.enabled ? (paused ? '已启用（急停中暂停）' : '已启用') : t.stale ? '已改动，需重新启用' : '未启用'));
  if (t.running) tags.append(el('span', 'tag tag-task', '正在运行'));
  li.append(tags);
  li.append(el('p', 'small', `时间：${cronText(t.schedule)}（${t.tz === 'local' ? '电脑本地时间' : t.tz ?? ''}）` + (Number.isInteger(t.next) ? ` · 下次 ${new Date(t.next * 1000).toLocaleString('zh-CN', { hour12: false, month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}` : '')));
  if (t.problems?.length) li.append(el('p', 'error small', `task.json 无效：${t.problems[0]}`));
  if (t.last && typeof t.last === 'object') li.append(el('p', 'small', `上次：${t.last.verdict ?? ''} — ${t.last.line ?? ''}`));
  if (!t.problems?.length || t.enabled) {
    const on = !t.enabled;
    const b = el('button', 'btn ' + (on ? 'btn-strong' : ''), on ? '启用' : '停用'); b.type = 'button';
    b.addEventListener('click', () => setTask(t, on, title));
    li.append(b);
  }
  return li;
}
const WEEK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六', '周日'];
function cronText(expr) {                             // the common shapes in words; anything else as the raw cron fields
  if (typeof expr !== 'string') return '?';
  const f = expr.split(' ');
  if (f.length !== 5) return expr;
  const [m, h, dom, mon, dow] = f;
  const hm = /^\d+$/.test(m) && /^\d+$/.test(h) ? `${h.padStart(2, '0')}:${m.padStart(2, '0')}` : null;
  if (expr === '* * * * *') return '每分钟';
  if (/^\*\/\d+$/.test(m) && h === '*' && dom === '*' && mon === '*' && dow === '*') return `每 ${m.slice(2)} 分钟`;
  if (/^\d+$/.test(m) && h === '*' && dom === '*' && mon === '*' && dow === '*') return `每小时第 ${m} 分`;
  if (hm && dom === '*' && mon === '*' && dow === '*') return `每天 ${hm}`;
  if (hm && dom === '*' && mon === '*' && /^[0-7](,[0-7])*$/.test(dow)) return `每${dow.split(',').map((d) => WEEK[+d]).join('、')} ${hm}`;
  if (hm && dom === '*' && mon === '*' && dow === '1-5') return `工作日 ${hm}`;
  if (hm && /^\d+$/.test(dom) && mon === '*' && dow === '*') return `每月 ${dom} 日 ${hm}`;
  return `cron ${expr}`;
}
async function setTask(t, on, title) {
  const text = on ? `「${title}」会按 ${cronText(t.schedule)}（${t.tz === 'local' ? '电脑本地时间' : t.tz}）自动运行${t.mode === 'research' ? '，只读运行' : ''}；运行中的危险动作照样逐条问你。改了任务文件就要重新启用。` : `「${title}」不再自动运行。`;
  if (!await confirmSheet(on ? '启用这个定时任务？' : '停用这个定时任务？', text, on ? '启用' : '停用')) return;
  const target = { id: String(t.id), tsha: typeof t.tsha === 'string' ? t.tsha : '' };
  let res;
  try { res = await command(await signed(on ? 'task_on' : 'task_off', target, { t: 'task_set', on, ...target })); } catch (e) { res = { ok: false, why: e.message }; }
  toast(res.ok ? (on ? '已启用' : '已停用') : '没有改：' + (WHY[res.why] ?? res.why));
  loadTasks();
}

// ---------------------------------------------------------------- slash commands (PROTOCOL §8 "slash commands", ADR-A70 – A72)
// The host interprets a short list of commands through each harness's own headless interface; anything else that looks like a
// command is refused by the host (or, for Claude Code, passed on when it is one of its skills). Results come back as chat
// entries `from: "cmd"` (cards). Only this page's paired session can send them; nothing is stored on the phone.
const CMDS = ['clear', 'compact', 'model', 'context', 'cost', 'usage', 'status', 'help', 'stop'];
const CMD_LABEL = { clear: '清空', compact: '压缩', model: '换模型', context: '上下文', cost: '花费', usage: '用量', status: '状态',
  help: '帮助', stop: '停止', undo_clear: '撤销清空', refused: '命令' };
const CMD_RESULT = { ok: '完成', info: '完成', error: '没做成', refused: '拒绝' };
const CMD_RE = /^\/([A-Za-z][\w:.-]{0,63})(?:[ \t]+([\s\S]*))?$/;
function sendCmd(cmd, arg = '', confirm = false) {
  const s = sess;
  if (!s || s.phase !== 'ready') return false;
  sendApp(s, { t: 'slash', cmd, ...(arg ? { arg: arg.slice(0, 200) } : {}), ...(confirm ? { confirm: true } : {}) }).catch(() => {});
  return true;
}
async function runCmd(cmd, arg = '') {
  if (cmd === 'clear') {
    if (!await confirmSheet('清空对话？', '清空后 Agent 不再记得这段对话（旧对话仍保存在电脑上，清空后可以「撤销清空」）。', '清空')) return false;
    return sendCmd('clear', '', true);
  }
  return sendCmd(cmd, arg);
}
function toggleCmdBar(open) {
  const bar = $('cmd-bar');
  const show = open ?? bar.hidden;
  bar.hidden = !show;
  $('cmd-toggle').setAttribute('aria-expanded', String(show));
}
function addCmdCard(m) {
  const name = typeof m.cmd === 'string' ? m.cmd : 'refused';
  const kind = ['ok', 'info', 'error', 'refused'].includes(m.kind) ? m.kind : (m.ok ? 'ok' : 'error');
  const li = el('li', m.sep ? 'cmd-card cmd-sep' : 'cmd-card');
  li.dataset.dir = 'in'; li.dataset.from = 'cmd'; li.dataset.cmd = name; li.dataset.kind = kind;
  if (Number.isInteger(m.seq)) lastSeq = m.seq;
  const head = el('p', 'cmd-head');
  head.append(el('span', '', name === 'refused' ? '命令' : `/${name === 'undo_clear' ? 'clear' : name} · ${CMD_LABEL[name] ?? name}`));
  if (typeof m.by === 'string' && m.by) head.append(el('span', 'cmd-by', m.by.slice(0, 64)));
  li.append(head, el('p', 'cmd-text', m.text));
  if (Array.isArray(m.models) && m.models.length) {
    const box = el('div', 'cmd-models');
    for (const x of m.models.slice(0, 40)) {
      if (!x || typeof x.id !== 'string') continue;
      const b = el('button', 'btn cmd-model'); b.type = 'button';
      b.dataset.model = x.id;
      if (x.cur) b.setAttribute('aria-current', 'true');
      b.append(el('span', '', (typeof x.name === 'string' && x.name ? x.name : x.id) + (x.cur ? '（当前）' : '')),
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
    const u = el('button', 'btn cmd-undo', '撤销清空'); u.type = 'button';
    u.addEventListener('click', () => { u.disabled = true; sendCmd('undo_clear'); });
    row.append(u);
    li.append(row);
  }
  $('messages').append(li);
  li.scrollIntoView({ block: 'end' });
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
