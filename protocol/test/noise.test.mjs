// Noise state machine (protocol/noise.js) against the official vectors + negative cases + wire helpers.
// Run: node --test protocol/test/
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { Handshake, IK, IKPSK2, keypairFromPrivate, generateKeypair } from '../noise.js';
import { padJson, unpadJson, safetyCode, parsePairing, b64u } from '../wire.js';

const hex = (s) => Uint8Array.from(s.match(/../g) ?? [], (h) => parseInt(h, 16));
const tohex = (b) => Buffer.from(b).toString('hex');
const { vectors } = JSON.parse(readFileSync(new URL('../vectors/noise-ik.json', import.meta.url)));

async function party(v, side) {
  const p = side === 'init' ? 'init' : 'resp';
  return new Handshake({
    protocol: v.protocol_name, initiator: side === 'init', prologue: hex(v[`${p}_prologue`]),
    s: await keypairFromPrivate(hex(v[`${p}_static`])), e: await keypairFromPrivate(hex(v[`${p}_ephemeral`])),
    rs: side === 'init' ? hex(v.init_remote_static) : undefined,
    psk: v[`${p}_psks`]?.length ? hex(v[`${p}_psks`][0]) : undefined,
  }).init();
}

for (const v of vectors) {
  test(`vector ${v._source} ${v.protocol_name}`, async () => {
    const I = await party(v, 'init'), R = await party(v, 'resp');
    let iT = null, rT = null;
    for (const [n, m] of v.messages.entries()) {
      const fromI = n % 2 === 0;
      let ct, pt;
      if (n < 2) {
        ct = await (fromI ? I : R).writeMessage(hex(m.payload));
        assert.equal(tohex(ct), m.ciphertext, `msg ${n} ciphertext`);
        pt = await (fromI ? R : I).readMessage(ct);
        if (n === 1) { iT = await I.split(); rT = await R.split(); if (v.handshake_hash) { assert.equal(tohex(iT.h), v.handshake_hash); assert.equal(tohex(rT.h), v.handshake_hash); } }
      } else {
        ct = await (fromI ? iT : rT).send.encrypt(new Uint8Array(0), hex(m.payload));
        assert.equal(tohex(ct), m.ciphertext, `msg ${n} ciphertext`);
        pt = await (fromI ? rT : iT).recv.decrypt(new Uint8Array(0), ct);
      }
      assert.equal(tohex(pt), m.payload, `msg ${n} payload`);
    }
  });
}

async function pairUp({ pskI, pskR, rs } = {}) {
  const host = await generateKeypair(), dev = await generateKeypair();
  const psk = crypto.getRandomValues(new Uint8Array(32));
  const pro = new Uint8Array([1, 2, 3]);
  const I = await new Handshake({ protocol: IKPSK2, initiator: true, prologue: pro, s: dev, rs: rs ?? host.pub, psk: pskI ?? psk }).init();
  const R = await new Handshake({ protocol: IKPSK2, initiator: false, prologue: pro, s: host, psk: pskR ?? psk }).init();
  // a second responder with the SAME identity and PSK: a tamper test must fail only because of the tampering
  const Rsame = await new Handshake({ protocol: IKPSK2, initiator: false, prologue: pro, s: host, psk: pskR ?? psk }).init();
  return { I, R, Rsame, host, dev };
}

test('IKpsk2 wrong psk: host cannot tell from msg1, device rejects msg2', async () => {
  const { I, R } = await pairUp({ pskR: new Uint8Array(32) });
  await R.readMessage(await I.writeMessage(new Uint8Array([7])));
  await assert.rejects(I.readMessage(await R.writeMessage()));
});

test('IKpsk2 device with wrong host key fails at msg1', async () => {
  const other = await generateKeypair();
  const { I, R } = await pairUp({ rs: other.pub });
  await assert.rejects(R.readMessage(await I.writeMessage()));
});

test('tampered msg1 / transport frame rejected; nonce not advanced on failure', async () => {
  const { I, R, Rsame } = await pairUp();
  const m1 = await I.writeMessage(new Uint8Array([1, 2]));
  const bad = m1.slice(); bad[40] ^= 1;
  await assert.rejects(Rsame.readMessage(bad));            // same host key + PSK: rejected only because of the flipped bit
  await R.readMessage(m1);
  await I.readMessage(await R.writeMessage());
  const a = await I.split(), b = await R.split();
  const ct = await a.send.encrypt(new Uint8Array(0), new Uint8Array([9, 9]));
  const t = ct.slice(); t[0] ^= 1;
  await assert.rejects(b.recv.decrypt(new Uint8Array(0), t));
  assert.deepEqual([...await b.recv.decrypt(new Uint8Array(0), ct)], [9, 9]);
  await assert.rejects(b.recv.decrypt(new Uint8Array(0), ct), 'replay of the same frame must fail');
  assert.equal(await safetyCode(a.h), await safetyCode(b.h));
});

test('IK resume: device static learned by host', async () => {
  const host = await generateKeypair(), dev = await generateKeypair();
  const I = await new Handshake({ protocol: IK, initiator: true, prologue: new Uint8Array(0), s: dev, rs: host.pub }).init();
  const R = await new Handshake({ protocol: IK, initiator: false, prologue: new Uint8Array(0), s: host }).init();
  await R.readMessage(await I.writeMessage());
  assert.equal(tohex(R.rs), tohex(dev.pub));
});

test('padding: multiples of 256, strict unpad', () => {
  for (const s of ['', 'x', 'x'.repeat(253), 'x'.repeat(254), '中'.repeat(500)]) {
    const p = padJson({ t: 'msg', text: s });
    assert.equal(p.length % 256, 0);
    assert.equal(unpadJson(p).text, s);
  }
  const p = padJson({ t: 'msg' }); p[p.length - 1] = 1;
  assert.throws(() => unpadJson(p));
  // receive side enforces the 16 KiB JSON limit too (A2 review A2-06), not only the sender
  const j = new TextEncoder().encode(JSON.stringify({ t: 'msg', text: 'x'.repeat(16 * 1024) }));
  const big = new Uint8Array(Math.ceil((j.length + 2) / 256) * 256);
  new DataView(big.buffer).setUint16(0, j.length, false); big.set(j, 2);
  assert.throws(() => unpadJson(big), /bad length/);
});

test('pairing link parsing', () => {
  const now = 1_800_000_000;
  const good = { v: 1, r: 'wss://relay.agentj.app', c: 'A'.repeat(22), k: b64u(new Uint8Array(32)), i: b64u(new Uint8Array(16)), p: b64u(new Uint8Array(32)), x: now + 60 };
  const link = (o) => 'https://m.agentj.app/#p=' + b64u(new TextEncoder().encode(JSON.stringify(o)));
  assert.equal(parsePairing(link(good), now).channel, 'A'.repeat(22));
  assert.throws(() => parsePairing(link({ ...good, x: now - 1 }), now), /expired/);
  assert.throws(() => parsePairing(link({ ...good, r: 'https://evil.example' }), now));
  assert.throws(() => parsePairing(link({ ...good, k: b64u(new Uint8Array(31)) }), now));
  // a client can pin its relay: a link pointing at someone else's relay is refused (A2 review L-2)
  const pin = (u) => u === 'wss://relay.agentj.app';
  assert.equal(parsePairing(link(good), now, pin).relay, good.r);
  assert.throws(() => parsePairing(link({ ...good, r: 'wss://relay.evil.example' }), now, pin), /not allowed/);
});
