// F20 (0.15.2, PROTOCOL §12): a passkey (Face ID / Touch ID / the phone's screen lock) proves "the same already-approved
// phone" — a Home Screen app, a private tab or a browser that cleared its data reconnects without a second pairing. It
// never widens anything: the computer only lets the record that holds this passkey take the new key.
// Pure helpers + the two WebAuthn ceremonies; app.js / session.js do the wiring. Shared with F17 later (a passkey before
// 「同意」 on sudo / secret cards): register(), assert() and the codecs carry no Agent-J screen logic.
//
// User handle (≤ 64 bytes, stored in the passkey itself, so a context with NO stored data learns which computer to reach):
//   0x01 ‖ relay index u8 ‖ channel id raw (16) ‖ host X25519 public key (32)   = 50 bytes
// Relay index: 0 = relay.agentj.app, 1 = alpha-relay.agentjarvis.net (both wss, no port), 2 = a loopback test relay (only on
// a 127.0.0.1 / localhost page, whose URL then comes from the page's `?relay=` — tests only; never on a real origin).
import { sha256, concat } from '../proto/noise.js';
import { b64u, unb64u } from '../proto/wire.js';

// (built from host names: the shipped files carry no literal URLs except agentj.app page links — static.test.mjs)
export const RELAYS = ['relay.agentj.app', 'alpha-relay.agentjarvis.net'].map((h) => 'wss:' + '//' + h);
export const LOOPBACK = 2;
export const HANDLE_V = 1;
export const RESTORE_LABEL = 'agentjarvis/passkey/restore/v1\n';
const LOOP_HOSTS = ['127.0.0.1', 'localhost'];
const isLoop = (hostname) => LOOP_HOSTS.includes(hostname);

/** WebAuthn with a user-verifying platform authenticator (Face ID, Touch ID, Android screen lock)? Never throws. */
export async function supported() {
  try {
    const P = globalThis.PublicKeyCredential;
    if (!P || !navigator.credentials?.create || !navigator.credentials?.get) return false;
    return await P.isUserVerifyingPlatformAuthenticatorAvailable();
  } catch { return false; }
}

/** The relay URL → its index in the handle, or -1 (a relay we cannot name: no passkey is offered then). */
export function relayIndex(url, hostname = location.hostname) {
  const i = RELAYS.indexOf(String(url).replace(/\/+$/, ''));
  if (i >= 0) return i;
  try {
    const u = new URL(url);
    if (isLoop(hostname) && u.protocol === 'ws:' && isLoop(u.hostname)) return LOOPBACK;
  } catch { /* not a URL */ }
  return -1;
}

export function encodeHandle({ relayIndex: ri, channel, hostPub }) {
  const ch = unb64u(channel);
  const hk = hostPub instanceof Uint8Array ? hostPub : new Uint8Array(hostPub);
  if (!(ri >= 0 && ri <= 255) || ch.length !== 16 || hk.length !== 32) throw new Error('bad handle parts');
  return concat(Uint8Array.of(HANDLE_V, ri), ch, hk);
}

/** The handle from a passkey → {relay, channel, hostPub}. Refuses another version, an unknown relay index, and index 2
 *  on a non-loopback page. testRelay = the loopback relay URL a test page was given (`?relay=`). */
export function decodeHandle(bytes, { hostname = location.hostname, testRelay = null } = {}) {
  const b = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes || []);
  if (b.length !== 50 || b[0] !== HANDLE_V) throw new Error('bad handle');
  let relay = RELAYS[b[1]];
  if (b[1] === LOOPBACK) {
    if (!isLoop(hostname) || !testRelay) throw new Error('bad relay');
    const u = new URL(testRelay);
    if (u.protocol !== 'ws:' || !isLoop(u.hostname)) throw new Error('bad relay');
    relay = testRelay.replace(/\/+$/, '');
  }
  if (!relay) throw new Error('bad relay');
  return { relay, channel: b64u(b.slice(2, 18)), hostPub: b.slice(18, 50) };
}

/** SHA-256(label ‖ new device X25519 pub ‖ its Ed25519 approval pub ‖ ts u64 BE (ms) ‖ nonce(16)) — the host recomputes it
 *  from the session's own key, so the assertion is useless with any other key. */
export async function restoreChallenge(x25519Pub, ed25519Pub, ts, nonce) {
  if (x25519Pub.length !== 32 || ed25519Pub.length !== 32 || nonce.length !== 16) throw new Error('bad challenge parts');
  const t = new Uint8Array(8);
  new DataView(t.buffer).setBigUint64(0, BigInt(ts));
  return sha256(concat(new TextEncoder().encode(RESTORE_LABEL), x25519Pub, ed25519Pub, t, nonce));
}

/** Make everything get() needs BEFORE the tap (iOS wants the system sheet to follow the tap with no slow work between). */
export async function prepareRestore(x25519Pub, ed25519Pub, now = Date.now()) {
  const nonce = crypto.getRandomValues(new Uint8Array(16));
  return { ts: now, nonce, challenge: await restoreChallenge(x25519Pub, ed25519Pub, now, nonce) };
}

const buf = (x) => new Uint8Array(x);

/** Create the passkey for this phone ↔ computer (call straight from a tap). → the pk_reg message. Throws on refusal. */
export async function register(offerNonce, { relayIndex: ri, channel, hostPub, name = 'Agent J', rpName = 'Agent J', displaySuffix = ' · 电脑' }) {
  const cred = await navigator.credentials.create({ publicKey: {
    rp: { id: location.hostname, name: rpName },
    user: { id: encodeHandle({ relayIndex: ri, channel, hostPub }), name, displayName: name + displaySuffix },
    challenge: offerNonce,
    pubKeyCredParams: [{ type: 'public-key', alg: -7 }, { type: 'public-key', alg: -8 }, { type: 'public-key', alg: -257 }],
    authenticatorSelection: { residentKey: 'required', requireResidentKey: true, userVerification: 'required', authenticatorAttachment: 'platform' },
    attestation: 'none', timeout: 60000, excludeCredentials: [],
  } });
  const r = cred.response;
  const spki = typeof r.getPublicKey === 'function' ? r.getPublicKey() : null;
  const alg = typeof r.getPublicKeyAlgorithm === 'function' ? r.getPublicKeyAlgorithm() : null;
  if (!spki || ![-7, -8, -257].includes(alg)) throw new Error('no public key');
  return { t: 'pk_reg', id: b64u(buf(cred.rawId)), alg, spki: b64u(buf(spki)), cd: b64u(buf(r.clientDataJSON)) };
}

/** Ask for any passkey of this site (discoverable; call straight from a tap). → {id, cd, ad, sig, uh (bytes)}. */
export async function assert(challenge) {
  const cred = await navigator.credentials.get({ publicKey: {
    challenge, rpId: location.hostname, userVerification: 'required', allowCredentials: [], timeout: 60000,
  } });
  const r = cred.response;
  if (!r.userHandle) throw new Error('no user handle');
  return { id: b64u(buf(cred.rawId)), cd: b64u(buf(r.clientDataJSON)), ad: b64u(buf(r.authenticatorData)),
    sig: b64u(buf(r.signature)), uh: buf(r.userHandle) };
}

/** The phone holds a passkey the computer no longer knows (revoked / never saved there): let the system drop it. */
export function forgetDead(credId) {
  try { globalThis.PublicKeyCredential?.signalUnknownCredential?.({ rpId: location.hostname, credentialId: credId })?.catch?.(() => {}); } catch { /* optional API */ }
}

// ---------------------------------------------------------------- F17 收尾 (P59, ADR-A163, PROTOCOL §16.1): Face ID before 「同意」
export const ELEVATE_LABEL = 'agentjarvis/passkey/elevate/v1';
/** SHA-256(UTF-8(label \n channel \n device \n card id \n kind \n "approve" \n card nonce \n shown digest)) — same as
 *  host/agentj/passkey.py elevate_challenge. Computed when the card arrives (no timestamp), so the tap goes straight to get(). */
export async function elevateChallenge(channel, device, id, kind, nonce, digest) {
  const parts = [ELEVATE_LABEL, channel, device, id, kind, 'approve', nonce, digest];
  if (!parts.every((x) => typeof x === 'string' && x && !x.includes('\n'))) throw new Error('bad challenge parts');
  return sha256(new TextEncoder().encode(parts.join('\n')));
}

/** WebAuthn get() exists at all (no platform check: the record already holds a passkey made on this phone). */
export const canAssert = () => !!(globalThis.PublicKeyCredential && navigator.credentials?.get);

/** Face ID for one card. NOT async on purpose: get() starts synchronously inside the tap (iOS user-gesture rule).
 *  → a promise of the `fa` object {id, cd, ad, sig}; rejects on cancel / failure. */
export function approve(challenge, credId) {
  return navigator.credentials.get({ publicKey: {
    challenge, rpId: location.hostname, userVerification: 'required', timeout: 60000,
    allowCredentials: [{ type: 'public-key', id: unb64u(credId) }],
  } }).then((cred) => {
    const r = cred.response;
    return { id: b64u(buf(cred.rawId)), cd: b64u(buf(r.clientDataJSON)), ad: b64u(buf(r.authenticatorData)), sig: b64u(buf(r.signature)) };
  });
}

// ---------------------------------------------------------------- F32 (P73, ADR-A180, PROTOCOL §18): the secret pickup card
// Face ID opens it with the same assertion as above, kind "secret_out", over this digest of what the card shows (pure, so
// node tests can check it against host/agentj/secret_out.py).
export const SECRET_OUT_CTX = 'agentjarvis-secret-out-v1';
export const SECRET_OUT_KIND = 'secret_out';
/** What the card shows before Face ID, in the host's order: name, purpose, text|file, file name, size (bytes). */
export const secretOutFields = (c) => [c.name, c.purpose || '', c.kind, c.filename || '', String(c.size)];
const hexOf = (u) => Array.from(u, (b) => b.toString(16).padStart(2, '0')).join('');
/** hex SHA-256(ctx \n kind \n one hex sha256(field) line per field). */
export async function secretOutDigest(fields) {
  const te = new TextEncoder();
  const lines = [SECRET_OUT_CTX, SECRET_OUT_KIND];
  for (const f of fields) lines.push(hexOf(await sha256(te.encode(f))));
  return hexOf(await sha256(te.encode(lines.join('\n'))));
}
