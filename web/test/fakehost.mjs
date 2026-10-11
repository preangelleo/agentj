// Test double: a local "relay + host" in one process, speaking the device side of PROTOCOL.md §2–§4, §8 and §10 with the
// real protocol/noise.js + wire.js (responder role). Lets screens.mjs drive the real client through pairing, approval,
// chat (§10.2 say), history pages (§10.5, frag), blobs and voice (§10.3 / §10.9), questions (§10.7), meters and models
// (§10.10 / §10.11), the menu (§10.12), host restart, resume and revoke — without the Python host. Not a relay
// implementation. Answer signatures ARE verified here against wire.js (approveMessage; questionMessage when wire.js has
// it) with the device's Ed25519 key from msg1 — the real host does the same (host/tests, tests/e2e_*).
import { createServer } from 'node:http';
import { createHash, randomBytes, webcrypto } from 'node:crypto';
import { Handshake, IK, IKPSK2, generateKeypair } from '../../protocol/noise.js';
import * as wire from '../../protocol/wire.js';

const { KIND, b64u, unb64u, frame, pairPrologue, resumePrologue, safetyCode, channelId, deviceId } = wire;
const EMPTY = new Uint8Array(0);
const enc = new TextEncoder();
const dec = new TextDecoder();
const P33_JSON = 61440;

function pad(obj, max) {
  const j = enc.encode(JSON.stringify(obj));
  if (j.length > max) throw new Error('message too large: ' + j.length);
  const total = Math.ceil((j.length + 2) / 256) * 256;
  const out = new Uint8Array(total);
  new DataView(out.buffer).setUint16(0, j.length, false);
  out.set(j, 2);
  return out;
}
function unpad(pt) {
  const n = new DataView(pt.buffer, pt.byteOffset, 2).getUint16(0, false);
  if (n > P33_JSON || n > pt.length - 2) throw new Error('bad length');
  return JSON.parse(dec.decode(pt.subarray(2, 2 + n)));
}
const sha = (b) => createHash('sha256').update(b).digest('hex');

// ---------------------------------------------------------------- minimal RFC 6455 server (no fragmentation needed)
function wsFrame(op, payload) {
  const n = payload.length;
  const head = n < 126 ? Buffer.from([0x80 | op, n]) : n < 65536 ? Buffer.from([0x80 | op, 126, n >> 8, n & 255])
    : (() => { const b = Buffer.alloc(10); b[0] = 0x80 | op; b[1] = 127; b.writeBigUInt64BE(BigInt(n), 2); return b; })();
  return Buffer.concat([head, Buffer.from(payload)]);
}
function accept(req, sock, onConn) {
  const key = req.headers['sec-websocket-key'];
  const acc = createHash('sha1').update(key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
  sock.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${acc}\r\n\r\n`);
  let buf = Buffer.alloc(0), closed = false;
  const c = {
    path: req.url,
    frames: 0,
    sendText: (s) => { if (!closed) sock.write(wsFrame(1, Buffer.from(s))); },
    sendBin: (u8) => { if (!closed) sock.write(wsFrame(2, u8)); },
    close: (code = 1000) => { if (closed) return; closed = true; const p = Buffer.alloc(2); p.writeUInt16BE(code); sock.write(wsFrame(8, p)); sock.end(); },
    onText: () => {}, onBin: () => {}, onClose: () => {},
    get closed() { return closed; },
  };
  sock.on('data', (d) => {
    buf = Buffer.concat([buf, d]);
    for (;;) {
      if (buf.length < 2) return;
      const op = buf[0] & 15; let len = buf[1] & 127; let o = 2;
      if (len === 126) { if (buf.length < 4) return; len = buf.readUInt16BE(2); o = 4; }
      else if (len === 127) { if (buf.length < 10) return; len = Number(buf.readBigUInt64BE(2)); o = 10; }
      const masked = buf[1] & 128;
      if (buf.length < o + (masked ? 4 : 0) + len) return;
      const mask = masked ? buf.subarray(o, o + 4) : null; o += masked ? 4 : 0;
      const pl = Buffer.from(buf.subarray(o, o + len)); buf = buf.subarray(o + len);
      if (mask) for (let i = 0; i < pl.length; i++) pl[i] ^= mask[i & 3];
      if (op === 1) c.onText(pl.toString('utf8'));
      else if (op === 2) { c.frames++; if (pl.length > 65536) { c.close(1009); return; } c.onBin(new Uint8Array(pl)); }
      else if (op === 8) { if (!closed) { closed = true; sock.write(wsFrame(8, pl.subarray(0, 2))); sock.end(); } }
      else if (op === 9) sock.write(wsFrame(10, pl));
    }
  });
  const gone = () => { if (!c._gone) { c._gone = true; closed = true; c.onClose(); } };
  sock.on('close', gone); sock.on('error', gone);
  onConn(c);
}

/** A strict §10.9 WAV check (the real host's): RIFF/WAVE, PCM 1 ch 16 kHz 16 bit, data length = file. */
export function wavOk(b) {
  if (b.length < 44 || b.toString('ascii', 0, 4) !== 'RIFF' || b.toString('ascii', 8, 12) !== 'WAVE') return false;
  if (b.toString('ascii', 12, 16) !== 'fmt ' || b.readUInt16LE(20) !== 1 || b.readUInt16LE(22) !== 1 || b.readUInt32LE(24) !== 16000 || b.readUInt16LE(34) !== 16) return false;
  return b.toString('ascii', 36, 40) === 'data' && b.readUInt32LE(40) === b.length - 44;
}

// ---------------------------------------------------------------- fake relay + host
export async function startFakeHost() {
  const hostKp = await generateKeypair(false);
  const channel = await channelId(randomBytes(32));
  const pairings = new Map();       // b64u(id) → { psk }
  const allow = new Map();          // b64u(device pub) → { sk (b64u Ed25519 pub) | null }
  const conns = new Set();
  const log = [];                   // app messages received from devices
  let up = true;
  let lastSas = null, lastLabel = null;
  const fixtures = {};              // fixtures[t] = (req) => answer | [answers] (memory / activity / tasks / estop)
  const st = {
    p33: true, asr: 'ready', asrText: '这是转写出来的文字', asrWhy: null, sayMode: 'delivered', autoReply: true, replyFor: null,
    asrTexts: [], asrDelays: [], sayDelay: 0, stallAfter: 0, blobErr: null, slashes: [],
    epoch: 1, nextId: 1, turns: [], blobs: new Map(), staged: new Set(), queued: new Map(), opens: [], cancels: [], answers: [],
    qAnswers: [], chunks: 0, wavs: [], menu: null, models: null, meter: null, sigOk: [], says: [], modelSets: [], rate: true,
    firsts: [], errors: [],
    prefs: null, prefSet: true, prefSets: [], version: null,
  };

  // one ordered send queue per connection: CipherState nonces must hit the socket in the order they were taken
  function sendApp(c, obj) {
    const p = (c.outq || Promise.resolve()).then(() => sendNow(c, obj));
    c.outq = p.catch(() => {});
    return p;
  }
  async function sendNow(c, obj) {
    const max = c.p33 ? P33_JSON : 16384;
    const j = JSON.stringify(obj);
    if (c.p33 && enc.encode(j).length > max) {                 // §10.1 frag
      const f = randomBytes(8).toString('hex');
      const cps = Array.from(j), parts = [];
      let cur = '';
      for (const ch of cps) { if (enc.encode(cur + ch).length > 40000) { parts.push(cur); cur = ''; } cur += ch; }
      if (cur) parts.push(cur);
      for (let i = 0; i < parts.length; i++) c.sendBin(frame(KIND.DATA, await c.send.encrypt(EMPTY, pad({ t: 'frag', f, i, n: parts.length, d: parts[i] }, max))));
      return;
    }
    c.sendBin(frame(KIND.DATA, await c.send.encrypt(EMPTY, pad(obj, max))));
  }
  const ready = () => [...conns].filter((c) => c.send && c.mode === 'resume' && c.isReady);
  const broadcast = async (obj) => { for (const c of ready()) await sendApp(c, obj); };
  const meta = () => ({ t: 'hist_meta', epoch: st.epoch, first: st.turns.length ? st.turns[0].id : 0, last: st.turns.length ? st.turns[st.turns.length - 1].id : 0, count: st.turns.length });
  async function pushTurn(turn) { for (const c of ready()) if (c.p33) await sendApp(c, { t: 'hist_turn', epoch: st.epoch, turn }); }
  async function addTurn(src, reply = '', end = 'done', card = null, extra = {}) {
    const turn = { id: st.nextId++, ts: Date.now(), src, reply: { text: reply, ...(extra.part ? { part: extra.part } : {}) }, end, ...(card ? { card } : {}) };
    st.turns.push(turn);
    await pushTurn(turn);
    return turn;
  }
  async function updateTurn(id, patch) {
    const turn = st.turns.find((x) => x.id === id);
    if (!turn) return;
    Object.assign(turn, patch);
    await pushTurn(turn);
  }

  async function verifyAnswer(c, m) {
    const sk = allow.get(b64u(c.devPub))?.sk;
    if (!sk) return false;
    const a = st.asksOpen?.get(m.id);
    if (!a) return false;
    const decision = m.batch ? 'allow_batch' : m.ok ? 'allow' : 'deny';
    // §17.7: a friend_request accepted into a group signs one more line (friendAnswerMessage)
    const msg = a.tool === 'friend_request' && m.ok && (m.group != null || m.ctx != null)
      ? await wire.friendAnswerMessage(channel, c.devId, m.id, 'allow', a.tool, a.summary, m.group, m.ctx)
      : await wire.approveMessage(channel, c.devId, m.id, decision, a.tool, a.summary, m.batch ? a.batch : undefined);
    const key = await webcrypto.subtle.importKey('raw', unb64u(sk), { name: 'Ed25519' }, false, ['verify']);
    return webcrypto.subtle.verify({ name: 'Ed25519' }, key, unb64u(m.sig), msg);
  }

  async function onData(c, m) {
    log.push(m);
    if (m.t === 'hello' && c.mode === 'pair') { c.awaiting = true; c.helloCaps = m.caps; lastSas = c.sas; return; }
    if (m.t === 'hello' && c.mode === 'resume') {
      if(c.removed){await sendApp(c,{t:'removed',why:'revoked'});return c.close(4010);}
      c.p33 = st.p33 && Array.isArray(m.caps) && m.caps.includes('p33');
      c.hello = m;
      await sendApp(c, st.p33 ? { t: 'ready', caps: ['p33', ...(st.heartbeat ? ['heartbeat'] : []), ...(st.tgfwd ? ['tgfwd'] : [])], asr: st.asr, hist: 'on' } : { t: 'ready' });
      c.isReady = true;
      return afterReady(c);
    }
    if (!c.isReady) return;
    if (st.onApp && await st.onApp(c, m, (o) => sendApp(c, o))) return;   // a test's own handler (P57 media: media_get)
    if (st.friends && /^(fr_|pg_)/.test(m.t)) return onFriends(c, m);     // §17.7 (0.16): the friends page
    if (m.t === 'msg') { st.says.push(m); if (st.autoReply) await sendApp(c, { t: 'msg', id: randomBytes(8).toString('hex'), text: 'echo: ' + m.text, ts: Date.now(), from: 'agent', seq: ++st.seq }); return; }
    if (m.t === 'say') return onSay(c, m);
    if (m.t === 'say_cancel') {
      st.cancels.push(m);
      const q = st.queued.get(m.sid);
      if (q) {
        // like serve's say_cancel: files staged again, the page ends stopped + card.withdrawn, say_state cancelled, then the answer
        st.queued.delete(m.sid); for (const b of q.att || []) st.staged.add(b);
        if (q.turn) await updateTurn(q.turn, { end: 'stopped', card: { withdrawn: true } });
        await sendApp(c, { t: 'say_state', sid: m.sid, s: 'cancelled' });
        return sendApp(c, { t: 'say_cancel_res', sid: m.sid, r: 'cancelled' });
      }
      return sendApp(c, { t: 'say_cancel_res', sid: m.sid, r: st.says.some((x) => x.sid === m.sid) ? 'already_delivered' : 'not_found' });
    }
    if (m.t === 'blob_open' || m.t === 'blob_chunk' || m.t === 'blob_end' || m.t === 'blob_drop') return onBlob(c, m);
    if (m.t === 'hist_get') {
      let ts = st.turns.slice();
      if (Number.isInteger(m.before)) ts = ts.filter((x) => x.id < m.before);
      if (Number.isInteger(m.after)) ts = ts.filter((x) => x.id > m.after);
      const lim = Math.min(50, m.limit || 50);
      const more = ts.length > lim;
      ts = Number.isInteger(m.after) ? ts.slice(0, lim) : ts.slice(-lim);
      const mm = meta();
      return sendApp(c, { t: 'hist_page', r: m.r, epoch: st.epoch, first: mm.first, last: mm.last, count: mm.count, turns: ts, more });
    }
    if (m.t === 'tg_fwd') {          // P118 (B10): the owner's one-tap Telegram forward; st.tgfwdWhy = a one-shot refusal
      (st.tgFwds = st.tgFwds || []).push(m);
      const why = st.tgfwdWhy; st.tgfwdWhy = null;
      return sendApp(c, why ? { t: 'tg_fwd_res', r: m.r, ok: false, why } : { t: 'tg_fwd_res', r: m.r, ok: true, parts: 1 });
    }
    if (m.t === 'menu_get') return sendApp(c, { t: 'menu', r: m.r, ...(st.menu || { source: 'default', items: [], skills: [], cmds: ['clear', 'compact', 'model', 'context', 'cost', 'usage', 'status', 'help', 'stop'] }) });
    if (m.t === 'model_set') {
      st.modelSets.push(m);
      await sendApp(c, { t: 'model_res', r: m.r, ok: true });
      const mod = st.models?.models.find((x) => x.id === (m.default ? st.models.default.model : m.model));
      if (st.meter && mod) { st.meter = { ...st.meter, model: mod.id, model_name: mod.name, effort: m.default ? st.models.default.effort : (m.effort ?? st.meter.effort), at: Math.floor(Date.now() / 1000) }; await broadcast({ t: 'meter', ...st.meter }); }
      return;
    }
    if (m.t === 'answer') {
      st.answers.push(m);
      const ok = await verifyAnswer(c, m);
      st.sigOk.push(ok);
      if (ok) { st.asksOpen.delete(m.id); await broadcast({ t: 'ask_done', id: m.id, result: m.ok ? 'allow' : 'deny' }); }
      return;
    }
    if (m.t === 'q_answer') {
      st.qAnswers.push(m);
      return broadcast({ t: 'question_done', id: m.id, result: m.cancel ? 'cancelled' : 'answered' });
    }
    if (m.t === 'pref_set') {                       // F13 · C6 / Amendment A1: user-tier whitelist only; older hosts (prefSet:false) ignore it
      st.prefSets.push(m);
      if (!st.prefSet) return;
      const allowed = PREF_ENUM[m.key];
      if (!allowed) return sendApp(c, { t: 'pref_res', r: m.r, ok: false, key: m.key, problem: 'not_allowed' });
      if (!allowed.includes(m.value)) return sendApp(c, { t: 'pref_res', r: m.r, ok: false, key: m.key, problem: 'bad_value' });
      st.prefs = st.prefs || { appearance: { language: 'zh', theme: 'system' }, voice: { speak_replies: false, wake_enabled: false }, agent: { high_risk_warnings: true, session_mode: 'shared' } };
      const [a, b] = m.key.split('.');
      st.prefs[a] = { ...(st.prefs[a] || {}), [b]: m.value };
      await sendApp(c, { t: 'pref_res', r: m.r, ok: true, key: m.key });
      await broadcast(prefsMsg());
      return;
    }
    if (m.t === 'slash') {
      st.slashes = (st.slashes || []).concat([m]);
      await addTurn({ k: 'cmd', text: '/' + m.cmd + (m.arg ? ' ' + m.arg : '') }, m.cmd === 'stop' ? '已中断这一轮。' : `/${m.cmd} 完成。`, 'done',
        { cmd: m.cmd, ok: true, kind: 'ok', ...(m.cmd === 'clear' ? { undo: true } : {}), ...(m.cmd === 'model' && st.models ? { models: st.models.models.map((x) => ({ id: x.id, name: x.name, cur: false })) } : {}) });
      return;
    }
    if (m.r && fixtures[m.t]) for (const x of [].concat(fixtures[m.t](m))) await sendApp(c, { ...x, r: m.r });
  }

  // ---- §17.7 agent friends: reads answered from st.friends, the six signed writes verified like the real host (§8 control
  // signature with the device's approval key), applied, answered ctl_res and announced with fr_changed. Anything else that
  // starts with fr_ (fr_send, fr_msg, …) is refused and logged — there is no device → friend message.
  async function verifyControl(c, action, m, target) {
    const sk = allow.get(b64u(c.devPub))?.sk;
    if (!sk || typeof m.sig !== 'string' || typeof m.n !== 'string' || !Number.isInteger(m.ts)) return false;
    try {
      const msg = await wire.controlMessage(channel, c.devId, action, m.n, m.ts, target);
      const key = await webcrypto.subtle.importKey('raw', unb64u(sk), { name: 'Ed25519' }, false, ['verify']);
      return await webcrypto.subtle.verify({ name: 'Ed25519' }, key, unb64u(m.sig), msg);
    } catch { return false; }
  }
  async function onFriends(c, m) {
    const F = st.friends;
    const reply = (o) => sendApp(c, { ...o, r: m.r });
    if (m.t === 'fr_list') return reply({ t: 'fr_list_res', me: F.me, friends: F.friends, pending: F.pending, groups: F.groups, global: F.global });
    if (m.t === 'fr_hist') {
      let items = (F.hist[m.friend] || []).slice();                       // newest first
      if (Number.isFinite(m.before)) items = items.filter((x) => x.ts < m.before);
      return reply({ t: 'fr_hist_res', friend: m.friend, items: items.slice(0, F.page || 50), more: items.length > (F.page || 50) });
    }
    if (m.t === 'fr_ctx_get') return reply({ t: 'fr_ctx_res', friend: m.friend, text: (F.ctx || {})[m.friend] || '', max: 4000 });   // P73
    if (m.t === 'fr_usage') return reply({ t: 'fr_usage_res', friend: m.friend, ...(F.usage[m.friend] || { group: 'default', used: { msg: { min: 0, hour: 0, day: 0, month: 0 }, tok: { min: null, hour: null, day: null, month: null } }, limits: {}, tok_source: null, blocked: 0 }) });
    const TARGET = {
      fr_set: () => ({ friend: m.friend, op: m.op, value: m.value }), pg_set: () => ({ group: m.group }), pg_del: () => ({ id: m.id }),
      fr_add: () => ({ id: m.id, note: m.note }), fr_discoverable: () => ({ on: m.on }), fr_card: () => ({ owner: m.owner, intro: m.intro }),
      fr_ctx: () => ({ friend: m.friend, text: m.text }),                                                   // P73
    };
    if (!TARGET[m.t]) { F.refused.push(m.t); return; }
    const ok = await verifyControl(c, m.t, m, TARGET[m.t]());
    F.writes.push({ ...m, sigOk: ok });
    if (!ok) return reply({ t: 'ctl_res', ok: false, why: 'bad_signature' });
    const f = F.friends.find((x) => x.id === m.friend);
    if (m.t === 'fr_set') {
      if (!f) return reply({ t: 'ctl_res', ok: false, why: 'not_friend' });
      if (m.op === 'group') f.group = m.value;
      if (m.op === 'block' || m.op === 'unblock') f.blocked = m.op === 'block';
      if (m.op === 'delete') F.friends = F.friends.filter((x) => x !== f);
    }
    if (m.t === 'pg_set') { const i = F.groups.findIndex((g) => g.id === m.group.id); if (i >= 0) F.groups[i] = m.group; else F.groups.push(m.group); }
    if (m.t === 'pg_del') {
      const g = F.groups.find((x) => x.id === m.id);
      if (!g || g.builtin) return reply({ t: 'ctl_res', ok: false, why: 'builtin' });
      F.groups = F.groups.filter((x) => x !== g);
      for (const x of F.friends) if (x.group === m.id) x.group = 'default';
    }
    if (m.t === 'fr_add') F.pending.push({ id: m.id, state: 'pending', ts: Date.now(), dir: 'out' });
    if (m.t === 'fr_discoverable') F.me.discoverable = m.on === true;
    if (m.t === 'fr_card') F.me.card = { ...F.me.card, owner: m.owner, intro: m.intro };
    if (m.t === 'fr_ctx') {
      if (!f) return reply({ t: 'ctl_res', ok: false, why: 'not_friend' });
      if ([...String(m.text)].length > 4000) return reply({ t: 'ctl_res', ok: false, why: 'too_long' });
      (F.ctx ||= {})[m.friend] = m.text;
    }
    await reply({ t: 'ctl_res', ok: true });
    await broadcast({ t: 'fr_changed', friend: m.friend ?? null });
  }

  // the `preferences` message; a 0.15 host adds its `host` block (version, phone-settable keys, paired phones — metadata)
  function prefsMsg(value = st.prefs) {
    const m = { t: 'preferences', value, problem: null };
    if (st.prefSet) m.host = { version: st.version || '0.15.0a1', language_at: 0, settable: Object.keys(PREF_ENUM),
      devices: [...conns].filter((c) => c.devId).map((c) => ({ id: c.devId, name: lastLabel || '', online: !!c.isReady, paired_at: 0 })) };
    return m;
  }
  async function afterReady(c) {
    if (!c.p33) return;
    if (st.rate) c.sendText(JSON.stringify({ t: 'rate', n: 240, w: 10 }));
    await sendApp(c, meta());
    const h = c.hello && c.hello.hist;
    const since = h && h.epoch === st.epoch ? st.turns.filter((x) => x.id > h.last) : st.turns.slice(-50);
    for (const turn of since.slice(0, 50)) await sendApp(c, { t: 'hist_turn', epoch: st.epoch, turn });
    if (st.meter) await sendApp(c, { t: 'meter', ...st.meter });
    if (st.models) await sendApp(c, { t: 'models', ...st.models });
    if (st.prefs) await sendApp(c, prefsMsg());
  }

  async function onSay(c, m) {
    st.says.push(m);
    if (typeof m.sid !== 'string' || typeof m.text !== 'string') return sendApp(c, { t: 'say_res', sid: m.sid, ok: false, why: 'shape' });
    if (st.estop) return sendApp(c, { t: 'say_res', sid: m.sid, ok: false, why: 'stopped' });
    if (st.sayWhy) { const why = st.sayWhy; st.sayWhy = null; return sendApp(c, { t: 'say_res', sid: m.sid, ok: false, why }); }   // one-shot refusal (e.g. too_many)
    const att = Array.isArray(m.att) ? m.att : [];
    const gone = att.filter((b) => !st.staged.has(b));
    if (gone.length) return sendApp(c, { t: 'say_res', sid: m.sid, ok: false, why: 'att_gone', att: gone });
    for (const b of att) st.staged.delete(b);
    const src = { k: 'phone', dev: c.devId, text: m.text, ...(att.length ? { att: att.map((b) => ({ name: st.blobs.get(b)?.name || b, mime: st.blobs.get(b)?.mime || '', bytes: st.blobs.get(b)?.size || 0, kind: st.blobs.get(b)?.kind || 'file' })) } : {}) };
    if (Number.isInteger(m.reply_to)) {
      const q = st.turns.find((x) => x.id === m.reply_to);
      if (!q) return sendApp(c, { t: 'say_res', sid: m.sid, ok: false, why: 'reply_unknown' });
      src.quote = { id: q.id, who: 'Agent', ts: q.ts, text: m.excerpt || q.reply.text.slice(0, 300), ex: !!m.excerpt };
    }
    if (st.sayMode === 'queued') {
      // like serve.on_say: the page first (open, no reply), then say_res queued naming it
      const turn = await addTurn(src, '', 'open');
      st.queued.set(m.sid, { m, att, src, c, turn: turn.id });
      return sendApp(c, { t: 'say_res', sid: m.sid, ok: true, state: 'queued', turn: turn.id });
    }
    if (st.sayDelay) await new Promise((r) => setTimeout(r, st.sayDelay));
    return deliver(c, m, src);
  }
  async function deliver(c, m, src) {
    const turn = await addTurn(src, '', 'open');
    await sendApp(c, { t: 'say_res', sid: m.sid, ok: true, state: 'delivered', turn: turn.id });
    if (st.autoReply) setTimeout(() => updateTurn(turn.id, { reply: { text: st.replyFor ? st.replyFor(m) : 'echo: ' + m.text }, end: 'done' }), 60);
  }

  async function onBlob(c, m) {
    st.opens.push(m.t === 'blob_open' ? m : null);
    if (m.t === 'blob_drop') { st.blobs.delete(m.bid); st.staged.delete(m.bid); return sendApp(c, { t: 'blob_err', bid: m.bid, why: 'dropped' }); }
    if (m.t === 'blob_open') {
      const old = st.blobs.get(m.bid);
      if (!old || old.size !== m.size || old.sha !== m.sha256 || old.mime !== m.mime) {
        if (!TYPES.includes(m.mime)) return sendApp(c, { t: 'blob_err', bid: m.bid, why: 'type' });
        if (st.blobErr) { const why = st.blobErr; st.blobErr = null; return sendApp(c, { t: 'blob_err', bid: m.bid, why }); }
        if (m.size > 26214400) return sendApp(c, { t: 'blob_err', bid: m.bid, why: 'too_big' });
        st.blobs.set(m.bid, { size: m.size, sha: m.sha256, mime: m.mime, name: m.name, purpose: m.purpose, origin: m.origin, secs: m.secs, parts: [], got: 0, n: 0,
          kind: /^image\//.test(m.mime) ? 'image' : /^audio\//.test(m.mime) ? 'audio' : 'file' });
      }
      return sendApp(c, { t: 'blob_ack', bid: m.bid, next: st.blobs.get(m.bid).got });
    }
    const b = st.blobs.get(m.bid);
    if (!b) return sendApp(c, { t: 'blob_err', bid: m.bid, why: 'expired' });
    if (m.t === 'blob_chunk') {
      st.chunks++;
      if (m.o !== b.got) return sendApp(c, { t: 'blob_ack', bid: m.bid, next: b.got });
      const d = Buffer.from(unb64u(m.d));
      if (d.length > 45056) return sendApp(c, { t: 'blob_err', bid: m.bid, why: 'shape' });
      b.parts.push(d); b.got += d.length; b.n++;
      if (st.stallAfter && b.n >= st.stallAfter) return;          // a test can freeze an upload mid-way
      if (b.n % 4 === 0) await sendApp(c, { t: 'blob_ack', bid: m.bid, next: b.got });
      return;
    }
    if (m.t === 'blob_end') {
      const all = Buffer.concat(b.parts);
      if (all.length !== b.size) return sendApp(c, { t: 'blob_done', bid: m.bid, ok: false, why: 'size_mismatch' });
      if (sha(all) !== b.sha) { st.blobs.delete(m.bid); return sendApp(c, { t: 'blob_done', bid: m.bid, ok: false, why: 'sha_mismatch' }); }
      b.bytes = all;
      if (b.purpose === 'asr') {
        await sendApp(c, { t: 'blob_done', bid: m.bid, ok: true, kind: 'audio', bytes: all.length });
        const ok = wavOk(all);
        st.wavs.push({ ok, bytes: all.length, secs: b.secs });
        st.blobs.delete(m.bid);
        if (!ok) return sendApp(c, { t: 'asr_res', bid: m.bid, ok: false, why: 'bad_audio' });
        if (st.asrWhy) return sendApp(c, { t: 'asr_res', bid: m.bid, ok: false, why: st.asrWhy });
        const text = st.asrTexts.length ? st.asrTexts.shift() : st.asrText;
        const delay = st.asrDelays.length ? st.asrDelays.shift() : 0;
        setTimeout(() => sendApp(c, { t: 'asr_res', bid: m.bid, ok: true, text, engine: 'sherpa', ms: 40 }).catch(() => {}), delay);
        return;
      }
      st.staged.add(m.bid);
      return sendApp(c, { t: 'blob_done', bid: m.bid, ok: true, kind: b.kind, bytes: all.length });
    }
  }

  async function onBin(c, b) {
    const kind = b[0];
    if (!up) return;
    if (kind === KIND.PAIR_INIT) {
      const id = b.slice(1, 17), p = pairings.get(b64u(id));
      if (!p) return c.close(4010);
      pairings.delete(b64u(id));
      c.hs = await new Handshake({ protocol: IKPSK2, initiator: false, prologue: pairPrologue(channel, id), s: hostKp, psk: p.psk }).init();
      const info = JSON.parse(dec.decode(await c.hs.readMessage(b.subarray(17))));
      lastLabel = info.name; c.sk = info.sk || null;
      c.mode = 'pair';
    } else if (kind === KIND.RESUME_INIT) {
      c.hs = await new Handshake({ protocol: IK, initiator: false, prologue: Uint8Array.from(resumePrologue(channel)), s: hostKp }).init();
      const info = JSON.parse(dec.decode(await c.hs.readMessage(b.subarray(1))));
      if (st.refuseResumes > 0) { st.refuseResumes--; return c.close(4010); }
      c.removed = !allow.has(b64u(c.hs.rs));   // modern host explicitly authenticates removal
      const a = allow.get(b64u(c.hs.rs)); if (a && !a.sk && info.sk) a.sk = info.sk;
      c.mode = 'resume';
    } else if (kind === KIND.DATA && c.recv) {
      const m = unpad(await c.recv.decrypt(EMPTY, b.subarray(1)));
      if (c.nData++ === 0) st.firsts.push(m.t);                // P33-X01: the first app message of every handshake
      return onData(c, m);
    } else return c.close(4010);
    const msg2 = await c.hs.writeMessage();
    const { send, recv, h } = await c.hs.split();
    c.devPub = c.hs.rs; c.devId = await deviceId(c.hs.rs); c.hs = null; c.send = send; c.recv = recv; c.sas = await safetyCode(h); c.nData = 0;
    c.sendBin(frame(KIND.HS_RESP, msg2));
  }

  const server = createServer((req, res) => { res.writeHead(404).end(); });
  server.on('upgrade', (req, sock) => {
    if (req.url !== `/v1/dev/${channel}`) { sock.end('HTTP/1.1 404 Not Found\r\n\r\n'); return; }
    accept(req, sock, (c) => {
      conns.add(c);
      c.chain = Promise.resolve();
      c.onBin = (b) => { c.chain = c.chain.then(() => onBin(c, b)).catch((e) => { st.lastError = String(e); st.errors.push(String(e)); c.close(4010); }); };
      c.onClose = () => conns.delete(c);
      c.sendText(JSON.stringify({ t: 'host', up }));
    });
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const relay = `ws://127.0.0.1:${server.address().port}`;
  st.seq = 0;
  st.asksOpen = new Map();

  return {
    relay, channel, log, st,
    get sas() { return lastSas; },
    get label() { return lastLabel; },
    get frames() { return [...conns].reduce((n, c) => n + c.frames, 0); },
    /** F17: ready devices with their approval key (b64u Ed25519 public) — to check a signed elev_answer. */
    get devs() { return [...conns].filter((c) => c.isReady && c.devId).map((c) => ({ id: c.devId, sk: allow.get(b64u(c.devPub))?.sk || null })); },
    newPairing(base, ttl = 300, { compact = false } = {}) {
      const id = randomBytes(16), psk = randomBytes(32);
      pairings.set(b64u(id), { psk });
      const x = Math.floor(Date.now() / 1000) + ttl;
      if (compact) {   // ADR-A177: the digits link today's host prints (host wire.pairing_link)
        const r = enc.encode(relay), b = new Uint8Array(101 + r.length);
        b[0] = 2; b.set(wire.unb64u(channel), 1); b.set(hostKp.pub, 17); b.set(id, 49); b.set(psk, 65); new DataView(b.buffer).setUint32(97, x); b.set(r, 101);
        return `${base}#p=` + BigInt('0x' + Buffer.from(b).toString('hex')).toString();
      }
      const p = { v: 1, r: relay, c: channel, k: b64u(hostKp.pub), i: b64u(id), p: b64u(psk), x };
      return `${base}#p=` + b64u(enc.encode(JSON.stringify(p)));
    },
    async approve() {
      for (const c of conns) if (c.awaiting) {
        c.awaiting = false; c.mode = 'resume'; allow.set(b64u(c.devPub), { sk: c.sk });
        c.p33 = st.p33 && Array.isArray(c.helloCaps) && c.helloCaps.includes('p33');
        await sendApp(c, st.p33 ? { t: 'approved', caps: ['p33', ...(st.heartbeat ? ['heartbeat'] : []), ...(st.tgfwd ? ['tgfwd'] : [])], asr: st.asr, hist: 'on' } : { t: 'approved' });
        c.isReady = true; c.hello = null;
        await afterReady(c);
      }
    },
    /** host → device chat (§8 msg) — an old host, or `agentj send` before p33 */
    async say(text, from = 'host') {
      if (ready().some((c) => c.p33)) await addTurn(from === 'agent' ? { k: 'agent' } : { k: 'host', text }, from === 'agent' ? text : '');
      for (const c of ready()) if (!c.p33) await sendApp(c, { t: 'msg', id: randomBytes(8).toString('hex'), text, ts: Date.now(), from, seq: ++st.seq });
    },
    addTurn, updateTurn,
    async deliverQueued(reply = true) {
      for (const [sid, q] of st.queued) {
        st.queued.delete(sid); st.says.push({ sid });
        await sendApp(q.c, { t: 'say_state', sid, s: 'delivered' });
        if (reply && q.turn) setTimeout(() => updateTurn(q.turn, { reply: { text: 'echo: ' + q.m.text }, end: 'done' }), 60);
      }
    },
    async clearHistory() { st.epoch++; st.turns = []; await broadcast(meta()); },
    async ask(m) { st.asksOpen.set(m.id, m); await broadcast({ t: 'ask', ...m }); },
    /** §17.7: turn the friends responder on with a state (sampleFriends()) — st.friends.writes / .refused record what came in */
    friends(state) { st.friends = state; return state; },
    async askDone(id, result) { st.asksOpen.delete(id); await broadcast({ t: 'ask_done', id, result }); },
    setUp(v) {
      up = v;
      for (const c of conns) { if (!v) { c.hs = c.send = c.recv = null; c.mode = null; c.awaiting = false; c.isReady = false; } c.sendText(JSON.stringify({ t: 'host', up: v })); }
    },
    /** app message → every ready session (status, ask, push_key, grant, estop_state, meter, models, question …) */
    async send(obj) {
      if (obj.t === 'meter') st.meter = { ...obj }; if (obj.t === 'models') st.models = { ...obj };
      if (obj.t === 'estop_state') st.estop = obj.on;
      await broadcast(obj);
    },
    prefsMsg,
    answer(t, fn) { fixtures[t] = fn; },
    async revokeAll() { allow.clear(); for (const c of conns) { if(c.isReady)await sendApp(c,{t:'removed',why:'revoked'}); c.close(4010); } },
    /** drop every device socket like a network failure (not a revoke) */
    refuseNextResumes(n) { st.refuseResumes = n; },
    kick(code = 1001) { for (const c of conns) c.close(code); },
    /** back to an empty host between test blocks (pairings and allowlist stay) */
    reset() {
      Object.assign(st, { p33: true, asr: 'ready', asrText: '这是转写出来的文字', asrWhy: null, asrTexts: [], asrDelays: [], sayMode: 'delivered', sayDelay: 0,
        autoReply: true, replyFor: null, epoch: st.epoch + 1, turns: [], blobs: new Map(), staged: new Set(), queued: new Map(), opens: [], cancels: [],
        answers: [], qAnswers: [], chunks: 0, wavs: [], menu: null, models: null, meter: null, sigOk: [], says: [], modelSets: [], slashes: [],
        stallAfter: 0, blobErr: null, sayWhy: null, estop: false, rate: true, firsts: [], errors: [], lastError: undefined,
        prefs: null, prefSet: true, prefSets: [], version: null, friends: null, tgfwd: false, tgfwdWhy: null, tgFwds: [] });
      st.asksOpen = new Map();
      log.length = 0;
    },
    get conns() { return conns.size; },
    sendRaw(u8) { for (const c of conns) c.sendBin(u8); },
    stop: () => new Promise((r) => { for (const c of conns) c.close(1001); server.closeAllConnections?.(); server.close(() => r()); }),
  };
}
// the phone-settable keys (contract C6 / Amendment A1) and their values; everything else → not_allowed
const PREF_ENUM = { 'updates.mode': ['auto', 'ask'], 'appearance.language': ['zh', 'en'], 'appearance.theme': ['system', 'light', 'dark'], 'voice.speak_replies': [true, false], 'voice.wake_enabled': [true, false], 'agent.high_risk_warnings': [true, false], 'agent.session_mode': ['shared', 'independent'], 'agent.isolation': [true, false], 'agent.allow_docker': [true, false] };
const TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/heic', 'image/heif', 'application/pdf', 'text/plain', 'text/markdown', 'text/csv',
  'application/json', 'audio/webm', 'audio/ogg', 'audio/mpeg', 'audio/mp4', 'audio/aac', 'audio/wav', 'audio/x-wav'];

// §17.7 a friends state for the fake host: my card, three friends in the three built-in groups (ADR §6.3 numbers), one request
// waiting, a conversation with auto replies and an owner-confirmed one, usage with and without a token source.
export function sampleFriends({ myId = 'AJ-FRGG-6PG6-V1WK-AAQA', now = Date.now() } = {}) {
  const win = (min, hour, day, month) => ({ min, hour, day, month });
  const group = (id, name, msg, tok, iv, len, mode, allow, rounds) => ({ id, name, builtin: true, limits: { msg, tok, min_interval_s: iv, max_len: len },
    auto: { mode, allow, ask: ['报价、付款、合作条款', '约时间', '任何需要承诺的事'], max_auto_rounds: rounds } });
  const m = 60000;
  return {
    me: { id: myId, link: 'https://m.agentj.app/friends#add=' + myId, discoverable: true, on: true,
      card: { v: 1, id: myId, name: '助理一号', owner: 'Leo', intro: '跨境电商选品和客服' } },
    friends: [
      { id: 'AJ-PH8E-AJT4-26GJ-ACQ9', name: '小鹿采购', owner: '王姐', intro: '深圳灯具工厂，负责报价和打样', group: 'default', state: 'friend', blocked: false, last: { ts: now - 5 * m, text: '好的，打样排期发你了' }, unread: 2 },
      { id: 'AJ-QNRR-7DZ1-DTBE-79B7', name: 'Kai 的研发助手', owner: 'Kai', intro: '', group: 'colleague', state: 'friend', blocked: false, last: { ts: now - 90 * m, text: '接口文档我更新了' }, unread: 0 },
      { id: 'AJ-RQCZ-QGZK-REZD-0S3C', name: 'Mina', owner: '', intro: '写稿', group: 'friend', state: 'friend', blocked: true, last: now - 3 * 86400000, unread: 0 },
    ],
    pending: [{ id: 'AJ-Q3TM-0000-0000-8XKN', state: 'pending', ts: now - 3600000, dir: 'out' }],
    groups: [
      group('default', '默认', win(3, 20, 50, 500), win(20000, 60000, 150000, 1500000), 10, 2000, 'scoped', ['寒暄', '名片和公开资料里的信息'], 6),
      group('friend', '好友', win(10, 100, 300, 3000), win(100000, 300000, 600000, 6000000), 3, 8000, 'scoped', ['寒暄', '名片和公开资料里的信息', '日常协作、技术问题'], 12),
      group('colleague', '同事', win(30, 500, 2000, null), win(500000, 2000000, 4000000, 40000000), 1, 20000, 'all', [], 30),
      { id: 'g-vip', name: '老客户', builtin: false, limits: { msg: win(5, 50, 100, 1000), tok: win(null, null, 200000, 2000000), min_interval_s: 5, max_len: 4000 },
        auto: { mode: 'off', allow: [], ask: ['报价'], max_auto_rounds: 6 } },
    ],
    global: { used: 48000, limit: 300000 },
    hist: {
      'AJ-PH8E-AJT4-26GJ-ACQ9': [
        { mid: 'a4', dir: 'out', text: '主人说这批先按 3.5，量到 2000 可以谈 3.2。', ts: now - 4 * m, s: 'got', auto: false },
        { mid: 'a3', dir: 'in', text: '500 个打样单价能不能降到 3.2 美元？', ts: now - 10 * m, s: 'queued_for_owner', auto: false },
        { mid: 'a2', dir: 'out', text: '可以，主人这周已经确认了 SKU，15 号前没问题。', ts: now - 13 * m, s: 'replied', auto: true },
        { mid: 'a1', dir: 'in', text: '10 月打样能排在 15 号前吗？', ts: now - 14 * m, s: '', auto: false },
        { mid: 'a0', dir: 'out', text: '你好，我是助理一号。', ts: now - 86400000, s: 'undelivered', auto: true },
      ],
    },
    usage: {
      'AJ-PH8E-AJT4-26GJ-ACQ9': { group: 'default', used: { msg: win(1, 4, 9, 31), tok: win(1200, 6000, 12000, 48000) },
        limits: { msg: win(3, 20, 50, 500), tok: win(20000, 60000, 150000, 1500000) }, tok_source: 'harness', blocked: 2 },
      'AJ-QNRR-7DZ1-DTBE-79B7': { group: 'colleague', used: { msg: win(0, 2, 12, 140), tok: win(null, null, null, null) },
        limits: { msg: win(30, 500, 2000, null), tok: win(500000, 2000000, 4000000, 40000000) }, tok_source: null, blocked: 0 },
    },
    page: 50, writes: [], refused: [],
  };
}
