#!/usr/bin/env node
// P564 追加 A (董事长 10-28 10:5x: Oppo N6 ↔ 华为 Pad, Mac Safari ↔ iPhone — setting Touch ID on one remote drops the other's).
// A synced passkey manager (iCloud Keychain, Google Password Manager) keeps ONE passkey per rpId + user handle; a CTAP2
// resident-key authenticator does exactly the same, so one CDP virtual platform authenticator shared by two "remotes" (two
// install tags, same Agent J host) is that manager. Real faceid.js register() / assert() in headless Chromium:
//   v2 (P564)  remote A registers, remote B registers → two passkeys kept; A still signs with its own key; uh = A's 58-byte handle
//   v1 (≤ 0.25.16, the bug) the same two registrations without a tag → one passkey left; A's key is gone (the owner's report)
//   v1 restore an old 50-byte passkey still signs and decodeHandle reads it (host side: test_p564_handle.py)
// WebKit has no virtual authenticator (no CDP WebAuthn domain), so the shared-store model runs in Chromium only.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { randomBytes } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { launch, newPage, navigate, evaluate } from './browser.mjs';

const OUT = process.env.P564_OUT || null;
const checks = [];
const ok = (cond, msg) => { assert.ok(cond, msg); checks.push(msg); };
const web = await startWebServer();
web.url = web.url.replace('127.0.0.1', 'localhost');     // WebAuthn wants a host name, not an IP
const B = await launch();
const channel = randomBytes(16).toString('base64url'), hostPub = [...randomBytes(32)];
const tagA = [...randomBytes(8)], tagB = [...randomBytes(8)];

async function fresh() {
  const p = await newPage(B, 390, 844, 'light');
  await navigate(p, web.url);
  await p.send('WebAuthn.enable', { enableUI: false });
  const { authenticatorId } = await p.send('WebAuthn.addVirtualAuthenticator', { options: { protocol: 'ctap2', transport: 'internal',
    hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true } });
  return { p, auth: authenticatorId };
}
// register() on this page for one remote (tag = null → the ≤ 0.25.16 handle); → { id, uh length }
const reg = (p, tag) => evaluate(p, `import('./js/faceid.js').then(async (F) => {
  const r = await F.register(crypto.getRandomValues(new Uint8Array(32)), { relayIndex: 0, channel: ${JSON.stringify(channel)},
    hostPub: new Uint8Array(${JSON.stringify(hostPub)}), tag: ${tag ? `new Uint8Array(${JSON.stringify(tag)})` : 'null'} });
  return r.id; })`);
// assert() with only this remote's credential id; → { ok, uh, relay, ch } or { ok:false, err }
const sign = (p, id) => evaluate(p, `import('./js/faceid.js').then(async (F) => {
  try { const a = await F.assert(crypto.getRandomValues(new Uint8Array(32)), [${JSON.stringify(id)}]);
    const d = F.decodeHandle(a.uh, { hostname: 'm.agentj.app' });
    return { ok: true, id: a.id, uh: [...a.uh], relay: d.relay, ch: d.channel }; }
  catch (e) { return { ok: false, err: e.name || String(e) }; } })`);
const creds = async (s) => (await s.p.send('WebAuthn.getCredentials', { authenticatorId: s.auth })).credentials;

try {
  // v2: two remotes on one synced store
  const s = await fresh();
  const idA = await reg(s.p, tagA), idB = await reg(s.p, tagB);
  ok(idA && idB && idA !== idB, 'v2: remote A and remote B each made a passkey');
  ok((await creds(s)).length === 2, 'v2: the synced store keeps both (different user handles)');
  const a = await sign(s.p, idA);
  ok(a.ok && a.id === idA, 'v2: remote A still signs with its own key after remote B set up');
  ok(a.uh.length === 58 && a.uh[0] === 2 && a.uh.slice(50).join() === tagA.join(), 'v2: A\'s handle = 0x02 ‖ host ‖ A\'s tag (58 B)');
  ok(a.ch === channel && a.relay.endsWith('relay.agentj.app'), 'v2: decodeHandle reads the host back from A\'s handle');
  const b = await sign(s.p, idB);
  ok(b.ok && b.uh.slice(50).join() === tagB.join(), 'v2: remote B signs with its own key (its own tag)');
  const again = await reg(s.p, tagA);
  ok((await creds(s)).length === 2 && again !== idA, 'v2: the same remote setting up again replaces only its own key (#323 ③)');
  ok(!(await sign(s.p, idA)).ok && (await sign(s.p, again)).ok && (await sign(s.p, idB)).ok, 'v2: B untouched by A\'s re-setup');

  // v1: the bug, reproduced on the same model
  const o = await fresh();
  const oA = await reg(o.p, null), oB = await reg(o.p, null);
  ok((await creds(o)).length === 1, 'v1 (≤ 0.25.16): the second remote\'s setup overwrote the first (one passkey per handle)');
  const lost = await sign(o.p, oA);
  ok(!lost.ok, 'v1: remote A\'s key is gone — the owner\'s 「钥匙丢了」 (' + lost.err + ')');
  const old = await sign(o.p, oB);
  ok(old.ok && old.uh.length === 50 && old.uh[0] === 1 && old.ch === channel, 'v1 restore: an old 50-byte passkey still signs and decodes');
} finally {
  await B.close();
  await web.stop();
}
if (OUT) { mkdirSync(OUT, { recursive: true }); writeFileSync(OUT + '/p564-passkey.json', JSON.stringify({ checks }, null, 1)); }
for (const c of checks) console.log('ok -', c);
console.log(`p564-passkey: ${checks.length} checks passed`);
process.exit(0);
