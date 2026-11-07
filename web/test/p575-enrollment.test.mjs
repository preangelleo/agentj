import { test } from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
if (!globalThis.crypto?.subtle) Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});
globalThis.window = globalThis;
globalThis.addEventListener = () => {};
globalThis.document = { getElementById: () => null, addEventListener() {}, body: null };
const P = await import('../public/js/pkpeer.js');
const { b64u, unb64u } = await import('../public/proto/wire.js');
const id = b64u(new Uint8Array(16).fill(8));
function harness() {
  P._reset();
  const sent = [], shown = [];
  P.configure({view: {show: s => shown.push(s), hide() {}}, now: () => 1000000,
    canAssert: () => true, channel: () => 'synthetic-host-channel', device: async () => 'synthetic-phone',
    sendBootstrap: async m => sent.push(m), send: async m => sent.push(m),
    approve() { throw Error('no UV key may be fabricated'); }});
  return { sent, shown };
}
async function ask(extra = {}) {
  const kp = await crypto.subtle.generateKey('X25519', false, ['deriveBits']);
  const epk = b64u(new Uint8Array(await crypto.subtle.exportKey('raw', kp.publicKey)));
  const msg = { id, code: '123456', name: 'Mac', at: 900, exp: 1300, needs_setup: true,
    bootstrap: true, fa: null, fa_ch: b64u(new Uint8Array(32)), epk, n: 'synthetic-nonce', digest: 'synthetic-digest', ...extra };
  assert.equal(P.onAsk(msg), true);
  return {kp, msg};
}
test('no PK phone sees controlled bootstrap; never a UV approve button', async () => {
  const h = harness(); await ask();
  assert.equal(h.shown.at(-1).bootstrap, true);
  assert.equal(h.shown.at(-1).can, false);
  assert.equal(P.approveTap(), null);
});
test('controlled input seals to the host; input is cleared before the first await', async () => {
  const h = harness(), {kp, msg} = await ask();
  const input = {value: 'synthetic-phone-only-password'};
  const p = P.bootstrapTap(input);
  assert.equal(input.value, '');
  await p;
  assert.equal(h.sent.length, 1);
  const reply = h.sent[0];
  assert.equal(reply.bootstrap, true);
  assert.equal(JSON.stringify(reply).includes('synthetic-phone-only-password'), false);
  const peer = await crypto.subtle.importKey('raw', unb64u(reply.epk), 'X25519', false, []);
  const shared = await crypto.subtle.deriveBits({name: 'X25519', public: peer}, kp.privateKey, 256);
  const hkdf = await crypto.subtle.importKey('raw', shared, 'HKDF', false, ['deriveKey']);
  const hostEpk = unb64u(msg.epk), phoneEpk = unb64u(reply.epk);
  const salt = new Uint8Array(64); salt.set(hostEpk); salt.set(phoneEpk, 32);
  const enc = new TextEncoder();
  const key = await crypto.subtle.deriveKey({name:'HKDF', hash:'SHA-256', salt, info:enc.encode('agentjarvis-seal-v1')}, hkdf,
    {name:'AES-GCM', length:256}, false, ['decrypt']);
  const aad = enc.encode(['agentjarvis-seal-v1','synthetic-host-channel','synthetic-phone',id,'pk_bootstrap',msg.n,msg.digest].join('\n'));
  const ct = unb64u(reply.ct);
  const plaintext = await crypto.subtle.decrypt({name:'AES-GCM', iv:ct.slice(0,12), additionalData:aad}, key, ct.slice(12));
  assert.equal(new TextDecoder().decode(plaintext), 'synthetic-phone-only-password');
  await assert.rejects(crypto.subtle.decrypt({name:'AES-GCM', iv:ct.slice(0,12), additionalData:enc.encode('other-device-or-rid')}, key, ct.slice(12)));
});
test('no-PK information card does not turn into fake approval', async () => {
  const h = harness(); await ask({bootstrap: false});
  assert.equal(h.shown.at(-1).needsSetup, true);
  const input = {value:'synthetic-password'};
  await P.bootstrapTap(input);
  assert.equal(input.value, ''); assert.equal(h.sent.length, 0);
});
test('pending card can reopen and closed request cannot be submitted', async () => {
  const h = harness(); await ask();
  P.closeResult(); P.reopenPending();
  assert.equal(h.shown.at(-1).mode, 'ask');
  P.onClose({id});
  await P.bootstrapTap({value:'synthetic-password'});
  assert.equal(h.sent.length, 0);
});
test('request closed during asynchronous seal sends no ciphertext', async () => {
  const h = harness(); await ask();
  const p = P.bootstrapTap({value:'synthetic-password'});
  P.onClose({id}); await p;
  assert.equal(h.sent.length, 0);
});

test('roster requests and malformed setup flags cannot use the bootstrap fallback',async()=>{
 const h=harness();await ask({roster:true});const input={value:'synthetic-password'};
 await P.bootstrapTap(input);assert.equal(input.value,'');assert.equal(h.sent.length,0);
 P._reset();assert.equal(P.onAsk({id,code:'123456',fa_ch:b64u(new Uint8Array(32)),needs_setup:1}),false);
});
