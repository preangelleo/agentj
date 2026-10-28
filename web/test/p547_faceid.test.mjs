// P547 (#317): faceid.register({peer:true}) never verifies the record's old key (no approve() → no browser cross-device QR
// sheet), and navigator.credentials.create starts in the same tick as the call (the tap). Without `peer` the P198 replace
// path is unchanged: get() on the old credential first, then create(), and replace_fa travels with pk_reg.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';

if (!globalThis.crypto) globalThis.crypto = webcrypto;
const calls = [];
const buf = (n, v = 1) => new Uint8Array(n).fill(v).buffer;
const fakeCreate = async (o) => {
  calls.push(['create', o.publicKey.challenge.length]);
  return { rawId: buf(16, 2), response: { clientDataJSON: buf(8, 3), attestationObject: buf(8, 4), getPublicKey: () => buf(91, 5), getPublicKeyAlgorithm: () => -7 } };
};
const fakeGet = async (o) => {
  calls.push(['get', o.publicKey.allowCredentials?.length ?? 0]);
  // a browser answers with a credential it was asked for (P566: another seat's key is refused by approve())
  const want = o.publicKey.allowCredentials?.[0]?.id;
  return { rawId: want ? new Uint8Array(want).buffer : buf(16, 6), response: { clientDataJSON: buf(8, 7), authenticatorData: buf(8, 8), signature: buf(8, 9) } };
};
Object.defineProperty(globalThis, 'navigator', { value: { credentials: { create: fakeCreate, get: fakeGet } }, configurable: true });
globalThis.location = { hostname: 'm.agentj.app' };
const { register } = await import('../public/js/faceid.js');
const opts = { relayIndex: 0, channel: 'AAECAwQFBgcICQoLDA0ODw', hostPub: new Uint8Array(32).fill(9), name: 'Agent J' };
const nonce = new Uint8Array(32).fill(1);

test('peer: create() starts synchronously, approve() never runs, no replace_fa — even when the record names an old key', async () => {
  calls.length = 0;
  const p = register(nonce, { ...opts, replace: 'b2xkLWtleQ', peer: true });
  assert.deepEqual(calls, [['create', 32]], 'create() is called before register() yields — still inside the tap');
  const reg = await p;
  assert.equal(reg.t, 'pk_reg');
  assert.equal(reg.replace_fa, undefined);
  assert.deepEqual(calls.map((c) => c[0]), ['create']);
});

test('no peer + replace: the P198 path is unchanged (get() on the old key, then create(), replace_fa sent)', async () => {
  calls.length = 0;
  const reg = await register(nonce, { ...opts, replace: 'b2xkLWtleQ' });
  assert.deepEqual(calls.map((c) => c[0]), ['get', 'create']);
  assert.equal(calls[0][1], 1, 'allowCredentials = the old key only');
  assert.equal(typeof reg.replace_fa, 'object');
});

test('no peer, no replace: create() only (first device, as before)', async () => {
  calls.length = 0;
  const reg = await register(nonce, opts);
  assert.deepEqual(calls.map((c) => c[0]), ['create']);
  assert.equal(reg.replace_fa, undefined);
});
