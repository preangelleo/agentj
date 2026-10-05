// P57 / F20 (PROTOCOL §12): the page's passkey helpers — the user handle codec and the restore challenge, which must match
// the host's (shared vectors with host/tests/test_p57_passkey.py).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';

if (!globalThis.crypto) globalThis.crypto = webcrypto;
const { encodeHandle, decodeHandle, restoreChallenge, relayIndex, RELAYS } = await import('../public/js/faceid.js');

const range = (a, b) => Uint8Array.from({ length: b - a }, (_, i) => a + i);
const hex = (u) => Buffer.from(u).toString('hex');
// the same values as test_p57_passkey.py (VEC_*)
const VEC_CHANNEL = 'AAECAwQFBgcICQoLDA0ODw';
const VEC_HOSTPUB = range(100, 132);
const VEC_HANDLE = '0102000102030405060708090a0b0c0d0e0f6465666768696a6b6c6d6e6f707172737475767778797a7b7c7d7e7f80818283';
const VEC_CHALLENGE = 'e6f34fdfcd8009b4fa0f382e8295bd9153b5f470f0d1143d7dc9f1460c5f640a';

test('§12 restore challenge = the host\'s (shared vector)', async () => {
  assert.equal(hex(await restoreChallenge(range(0, 32), range(32, 64), 1790000000000, range(64, 80))), VEC_CHALLENGE);
  await assert.rejects(restoreChallenge(range(0, 31), range(32, 64), 1, range(64, 80)));
});

test('§12 user handle: layout, round trip for every relay', () => {
  const h = encodeHandle({ relayIndex: 2, channel: VEC_CHANNEL, hostPub: VEC_HOSTPUB });
  assert.equal(hex(h), VEC_HANDLE);
  assert.equal(h.length, 50);
  for (const [i, url] of RELAYS.entries()) {
    const d = decodeHandle(encodeHandle({ relayIndex: i, channel: VEC_CHANNEL, hostPub: VEC_HOSTPUB }), { hostname: 'm.agentj.app' });
    assert.deepEqual({ ...d, hostPub: hex(d.hostPub) }, { relay: url, channel: VEC_CHANNEL, hostPub: hex(VEC_HOSTPUB) });
  }
  const loop = decodeHandle(h, { hostname: 'localhost', testRelay: 'ws://127.0.0.1:8787' });
  assert.equal(loop.relay, 'ws://127.0.0.1:8787');
});

test('§12 user handle: refuses another version, an unknown index, index 2 off loopback or without a loopback relay', () => {
  const h = encodeHandle({ relayIndex: 0, channel: VEC_CHANNEL, hostPub: VEC_HOSTPUB });
  assert.throws(() => decodeHandle(Uint8Array.of(2, ...h.slice(1)), { hostname: 'm.agentj.app' }), /bad handle/);
  assert.throws(() => decodeHandle(h.slice(0, 49), { hostname: 'm.agentj.app' }), /bad handle/);
  assert.throws(() => decodeHandle(Uint8Array.of(1, 7, ...h.slice(2)), { hostname: 'm.agentj.app' }), /bad relay/);
  const loop = Uint8Array.of(1, 2, ...h.slice(2));
  assert.throws(() => decodeHandle(loop, { hostname: 'm.agentj.app', testRelay: 'ws://127.0.0.1:1' }), /bad relay/);
  assert.throws(() => decodeHandle(loop, { hostname: 'localhost' }), /bad relay/);
  assert.throws(() => decodeHandle(loop, { hostname: 'localhost', testRelay: 'wss://evil.example' }), /bad relay/);
  assert.throws(() => encodeHandle({ relayIndex: 0, channel: 'short', hostPub: VEC_HOSTPUB }));
});

test('§12 relay index: our two relays; a loopback relay only from a loopback page; anything else → -1', () => {
  assert.equal(relayIndex('wss://relay.agentj.app', 'm.agentj.app'), 0);
  assert.equal(relayIndex('wss://alpha-relay.agentjarvis.net/', 'm.agentj.app'), 1);
  assert.equal(relayIndex('ws://127.0.0.1:8787', 'localhost'), 2);
  assert.equal(relayIndex('ws://127.0.0.1:8787', 'm.agentj.app'), -1);
  assert.equal(relayIndex('wss://relay.example', 'm.agentj.app'), -1);
});
