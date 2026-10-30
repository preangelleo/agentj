// P574: device-bound roster registrations and locally reconstructed grant / introduction challenges.
// No host-supplied challenge is signed for these cross-host authorities.
import { b64u, unb64u, shownDigest, sha256hex, controlObject, canonicalJson } from '../proto/wire.js';
import { concat, sha256 } from '../proto/noise.js';
import { elevateChallenge } from './faceid.js';

export const rosterCore = (reg, dev = reg.dev, rp = reg.rp) => ({ alg: reg.alg, dev, id: reg.id, rp, spki: reg.spki });
export function rosterMessage(reg, dev = reg.dev, rp = reg.rp) {
  const core = rosterCore(reg, dev, rp);
  return new TextEncoder().encode('agentj-owner-roster-v1\n' + JSON.stringify(core));
}
export async function bindRegistration(reg, dev, key, rp) {
  return { ...reg, roster_sign_pub: b64u(key.pub),
    roster_sig: b64u(new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, key.priv, rosterMessage(reg, dev, rp)))) };
}
export function checkGrantTime(otg, now = Date.now() / 1000) {
  if (!otg || !Number.isSafeInteger(otg.iat) || !Number.isSafeInteger(otg.exp) || otg.iat <= 0 || !Number.isFinite(now)
      || otg.iat > now + 600 || otg.exp - otg.iat !== 86400 || otg.exp <= now) throw new Error('grant lifetime');
}
export async function verifyPublicGrant(m, now = Date.now() / 1000) {
  const otg = m.owner_grant;
  checkGrantTime(otg, now);
  if (otg.v !== 1 || !['zh','en'].includes(otg.lang) || !/^[0-9a-f]{32}$/.test(otg.tid || '') || typeof m.summary !== 'string') throw new Error('grant metadata');
  const core = Object.fromEntries(Object.entries(otg).filter(([k]) => k !== 'tid'));
  const tid = (await sha256hex(new TextEncoder().encode('agentj-owner-grant-v1\n' + canonicalJson(core)))).slice(0,32);
  if (tid !== otg.tid) throw new Error('grant id');
  const lines = m.summary.split('\n'), zh = otg.lang === 'zh';
  if (lines.at(-2) !== (zh ? '编号 ' : 'Grant ') + tid || lines.at(-1) !== (zh
      ? '不是你发起的？点拒绝，并到好友页删除该好友。' : 'Not you? Deny, then remove this friend on the friends page.')) throw new Error('grant shown id');
  return otg;
}
export async function grantChallenge(m, channel, dev, now = Date.now() / 1000) {
  await verifyPublicGrant(m, now);
  if (m.tool !== 'agentj secret grant' || !['sandbox', 'perm'].includes(m.pk_kind)) throw new Error('grant kind');
  return elevateChallenge(channel, dev, m.id, m.pk_kind, m.id, await shownDigest(m.tool, m.summary));
}
export async function introductionChallenge(m, channel, dev) {
  if (!/^[A-Za-z0-9_-]{43}$/.test(m.sign_pub || '') || typeof m.device !== 'string' || typeof m.name !== 'string') throw new Error('introduction');
  const digest = await sha256hex(new TextEncoder().encode(['agentj-pk-peer-v1', m.device, m.name, String(m.code), m.sign_pub, ...(m.roster === true ? ['roster'] : [])].join('\n')));
  return elevateChallenge(channel, dev, m.id, 'pk_peer', m.id, digest);
}

/** Both hosts' exact ceremony bytes, with the local approving device independently present. */
export async function pairingScope(pq, signingPublicKey, device, localEntry) {
  if (typeof pq.pairing_scope !== 'string' || pq.pairing_scope.length > 48000) throw new Error('pairing scope');
  const ends = JSON.parse(pq.pairing_scope);
  if (!Array.isArray(ends) || ends.length !== 2 || !ends.every(e => Array.isArray(e) && e.length === 5 && Array.isArray(e[4]))) throw new Error('pairing scope');
  if (!ends.some(e => e[0] === pq.friend)) throw new Error('pairing friend');
  const own = ends.find(e => e[0] !== pq.friend);
  if (!own || own[2] !== signingPublicKey || !device || !localEntry) throw new Error('pairing signer');
  for (const end of ends) for (const entry of end[4]) if (!(await verifyBinding(entry))) throw new Error('pairing binding');
  const mine = own[4].filter(e => e.sign_pub === signingPublicKey && e.dev === device);
  if (mine.length !== 1 || JSON.stringify(rosterCore(mine[0])) !== JSON.stringify(rosterCore(localEntry))
      || localEntry.sign_pub !== signingPublicKey || localEntry.dev !== device || !(await verifyBinding(localEntry))) throw new Error('pairing local key');
  if ((await sha256hex(new TextEncoder().encode(pq.pairing_scope))).slice(0,32) !== pq.challenge) throw new Error('pairing digest');
  const code = pairingCode(pq.challenge);
  if (pq.code !== undefined && pq.code !== code) throw new Error('pairing code');
  const result = ends.map(e => {
    const trusted = e[4].filter(r => r.sign_pub === e[2]);
    if (trusted.length !== 1) throw new Error('pairing root');
    const label = r => {
    if (typeof r.name !== 'string' || typeof r.dev !== 'string' || !r.name || r.name.length > 64 || r.dev.length > 64) throw new Error('pairing entry');
      return r.name + ' · ' + r.dev;
    };
    const pending = e[4].filter(r => r !== trusted[0]);
    return { here: e === own, trusted, pending, remotes: trusted.map(label), pendingRemotes: pending.map(label) };
  });
  result.code = code;
  return result;
}

export function pairingCode(challenge) {
  if (!/^[0-9a-f]{32}$/.test(challenge || '')) throw new Error('pairing hash');
  return String(BigInt('0x' + challenge) % 1000000n).padStart(6, '0');
}

function validCore(e) {
  return !!e && [-7,-8,-257].includes(e.alg) && ['id','dev','rp','spki'].every(k => typeof e[k] === 'string' && e[k].length > 0)
    && e.id.length <= 1400 && e.dev.length <= 64 && e.rp.length <= 253 && e.spki.length <= 8192;
}
function keyAlgorithm(alg) {
  if (alg === -7) return { name: 'ECDSA', namedCurve: 'P-256' };
  if (alg === -8) return { name: 'Ed25519' };
  if (alg === -257) return { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' };
  throw new Error('roster algorithm');
}
export async function verifyBinding(entry) {
  try {
    if (!validCore(entry) || typeof entry.sign_pub !== 'string' || typeof entry.bind !== 'string') return false;
    await crypto.subtle.importKey('spki', unb64u(entry.spki), keyAlgorithm(entry.alg), false, ['verify']);
    const ed = await crypto.subtle.importKey('raw', unb64u(entry.sign_pub), 'Ed25519', false, ['verify']);
    return await crypto.subtle.verify('Ed25519', ed, unb64u(entry.bind), rosterMessage(entry));
  } catch { return false; }
}
export async function bindChallenge(entry) {
  if (!validCore(entry)) throw new Error('binding core');
  return sha256(new TextEncoder().encode('agentj-owner-roster-bind-v1\n' + JSON.stringify(rosterCore(entry))));
}
// WebAuthn ES256 signatures are DER, while WebCrypto ECDSA verify expects fixed-size r || s.
function es256Raw(der) {
  const d = new Uint8Array(der); let at = 0;
  if (d[at++] !== 0x30 || d[at++] !== d.length - 2) throw new Error('ECDSA sequence');
  const parts = [];
  for (let i = 0; i < 2; i++) {
    if (d[at++] !== 0x02) throw new Error('ECDSA integer');
    const n = d[at++]; if (!n || n > 33 || at + n > d.length) throw new Error('ECDSA size');
    let v = d.slice(at, at + n); at += n;
    if (v[0] & 0x80 || (v.length > 1 && v[0] === 0 && !(v[1] & 0x80))) throw new Error('ECDSA encoding');
    if (v.length === 33) { if (v[0] !== 0) throw new Error('ECDSA size'); v = v.slice(1); }
    const fixed = new Uint8Array(32); fixed.set(v, 32 - v.length); parts.push(fixed);
  }
  if (at !== d.length) throw new Error('ECDSA trailing');
  return concat(...parts);
}
/** A legacy key may be bound only after UV with that exact actual key, locally verified without trusting the host. */
export async function verifyBindingAssertion(entry, fa, challenge, { rp, origin }) {
  try {
    if (entry.rp !== rp || fa.id !== entry.id) return false;
    const cd = unb64u(fa.cd), ad = unb64u(fa.ad), data = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(cd));
    if (data.type !== 'webauthn.get' || data.challenge !== b64u(challenge) || data.origin !== origin || data.crossOrigin === true
        || ad.length < 37 || (ad[32] & 5) !== 5) return false;
    const rpHash = await sha256(new TextEncoder().encode(rp));
    if (!rpHash.every((b, i) => ad[i] === b)) return false;
    const key = await crypto.subtle.importKey('spki', unb64u(entry.spki), keyAlgorithm(entry.alg), false, ['verify']);
    const sig = entry.alg === -7 ? es256Raw(unb64u(fa.sig)) : unb64u(fa.sig);
    return await crypto.subtle.verify(entry.alg === -7 ? { name: 'ECDSA', hash: 'SHA-256' } : keyAlgorithm(entry.alg), key, sig, concat(ad, await sha256(cd)));
  } catch { return false; }
}

export async function ownerConfirmChallenge(target, nonce, ch, dev) {
  if (target.op !== 'owner-confirm-v3' || !/^[0-9a-f]{32}$/.test(target.value || '')) throw new Error('pairing confirmation');
  const digest = await sha256hex(new TextEncoder().encode(controlObject('fr_set', target)));
  return elevateChallenge(ch, dev, nonce, 'fr_set', nonce, digest);
}
