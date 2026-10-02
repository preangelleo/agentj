// Test double: a local "relay + host" in one process, speaking the device side of PROTOCOL.md §2–§4 with the real
// protocol/noise.js + wire.js (responder role). Lets screens.mjs drive the real client through pairing, approval, chat,
// host restart, resume and revoke without the Python host. Not a relay implementation — just enough for the client.
import { createServer } from 'node:http';
import { createHash, randomBytes } from 'node:crypto';
import { Handshake, IK, IKPSK2, generateKeypair } from '../../protocol/noise.js';
import { KIND, b64u, frame, padJson, unpadJson, pairPrologue, resumePrologue, safetyCode, channelId } from '../../protocol/wire.js';

const EMPTY = new Uint8Array(0);

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
      else if (op === 2) c.onBin(new Uint8Array(pl));
      else if (op === 8) { if (!closed) { closed = true; sock.write(wsFrame(8, pl.subarray(0, 2))); sock.end(); } }
      else if (op === 9) sock.write(wsFrame(10, pl));
    }
  });
  const gone = () => { if (!c._gone) { c._gone = true; closed = true; c.onClose(); } };
  sock.on('close', gone); sock.on('error', gone);
  onConn(c);
}

// ---------------------------------------------------------------- fake relay + host
export async function startFakeHost() {
  const hostKp = await generateKeypair(false);
  const channel = await channelId(randomBytes(32));
  const pairings = new Map();       // b64u(id) → { psk }
  const allow = new Set();          // b64u(device pub)
  const conns = new Set();
  const log = [];                   // app messages received from devices
  let up = true;
  let lastSas = null, lastLabel = null;
  // Canned answers to the phone's read requests and signed writes (screens.mjs gallery). The fake does NOT verify the
  // signature — the real host does (host/tests, tests/e2e_controls.mjs); this only lets the page reach every screen.
  const fixtures = {};

  async function sendApp(c, obj) { c.sendBin(frame(KIND.DATA, await c.send.encrypt(EMPTY, padJson(obj)))); }

  async function onBin(c, b) {
    const kind = b[0];
    if (!up) return;
    if (kind === KIND.PAIR_INIT) {
      const id = b.slice(1, 17), p = pairings.get(b64u(id));
      if (!p) return c.close(4010);
      pairings.delete(b64u(id));
      c.hs = await new Handshake({ protocol: IKPSK2, initiator: false, prologue: pairPrologue(channel, id), s: hostKp, psk: p.psk }).init();
      lastLabel = JSON.parse(new TextDecoder().decode(await c.hs.readMessage(b.subarray(17)))).name;
      c.mode = 'pair';
    } else if (kind === KIND.RESUME_INIT) {
      c.hs = await new Handshake({ protocol: IK, initiator: false, prologue: Uint8Array.from(resumePrologue(channel)), s: hostKp }).init();
      await c.hs.readMessage(b.subarray(1));
      if (!allow.has(b64u(c.hs.rs))) return c.close(4010);   // unknown device: silently closed
      c.mode = 'resume';
    } else if (kind === KIND.DATA && c.recv) {
      const m = unpadJson(await c.recv.decrypt(EMPTY, b.subarray(1)));
      log.push(m);
      if (m.t === 'hello' && c.mode === 'pair') { c.awaiting = true; lastSas = c.sas; }
      else if (m.t === 'hello' && c.mode === 'resume') await sendApp(c, { t: 'ready' });
      else if (m.t === 'msg') await sendApp(c, { t: 'msg', id: randomBytes(8).toString('hex'), text: 'echo: ' + m.text, ts: Date.now() });
      else if (m.r && fixtures[m.t]) for (const x of [].concat(fixtures[m.t](m))) await sendApp(c, { ...x, r: m.r });
      return;
    } else return c.close(4010);
    const msg2 = await c.hs.writeMessage();
    const { send, recv, h } = await c.hs.split();
    c.devPub = c.hs.rs; c.hs = null; c.send = send; c.recv = recv; c.sas = await safetyCode(h);
    c.sendBin(frame(KIND.HS_RESP, msg2));
  }

  const server = createServer((req, res) => { res.writeHead(404).end(); });
  server.on('upgrade', (req, sock) => {
    if (req.url !== `/v1/dev/${channel}`) { sock.end('HTTP/1.1 404 Not Found\r\n\r\n'); return; }
    accept(req, sock, (c) => {
      conns.add(c);
      c.chain = Promise.resolve();
      c.onBin = (b) => { c.chain = c.chain.then(() => onBin(c, b)).catch(() => c.close(4010)); };
      c.onClose = () => conns.delete(c);
      c.sendText(JSON.stringify({ t: 'host', up }));
    });
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const relay = `ws://127.0.0.1:${server.address().port}`;

  return {
    relay, channel, log,
    get sas() { return lastSas; },
    get label() { return lastLabel; },
    newPairing(base, ttl = 300) {
      const id = randomBytes(16), psk = randomBytes(32);
      pairings.set(b64u(id), { psk });
      const p = { v: 1, r: relay, c: channel, k: b64u(hostKp.pub), i: b64u(id), p: b64u(psk), x: Math.floor(Date.now() / 1000) + ttl };
      return `${base}#p=` + b64u(new TextEncoder().encode(JSON.stringify(p)));
    },
    async approve() {
      for (const c of conns) if (c.awaiting) { c.awaiting = false; c.mode = 'resume'; allow.add(b64u(c.devPub)); await sendApp(c, { t: 'approved' }); }
    },
    async say(text) { for (const c of conns) if (c.send && c.mode === 'resume') await sendApp(c, { t: 'msg', id: randomBytes(8).toString('hex'), text, ts: Date.now() }); },
    setUp(v) {
      up = v;
      for (const c of conns) { if (!v) { c.hs = c.send = c.recv = null; c.mode = null; c.awaiting = false; } c.sendText(JSON.stringify({ t: 'host', up: v })); }
    },
    /** app message → every ready session (status, ask, push_key, grant, estop_state, cmd cards …) */
    async send(obj) { for (const c of conns) if (c.send && c.mode === 'resume') await sendApp(c, obj); },
    /** fixtures[t] = (request) => answer | [answers]; each answer gets the request's r */
    answer(t, fn) { fixtures[t] = fn; },
    revokeAll() { allow.clear(); for (const c of conns) c.close(4010); },
    sendRaw(u8) { for (const c of conns) c.sendBin(u8); },
    stop: () => new Promise((r) => { for (const c of conns) c.close(1001); server.closeAllConnections?.(); server.close(() => r()); }),
  };
}
