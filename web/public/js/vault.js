// P414 (PROTOCOL §37.9–§37.10, ADR-P414-bang): the phone half of the computer-password vault. The password itself lives on
// the computer only as ciphertext; this phone holds nothing but its passkey. What it gives the computer is the WebAuthn PRF
// output of that passkey over a salt the computer chose (`eval.first`) — and, when the computer also sent a next salt
// (`eval.second`, M3), the output over that one too, so the computer can re-wrap the key under a fresh salt on every use.
// The value is sealed to the card's (or the terminal's) one-time host key with the §11 seal and every buffer is zeroed.
// Nothing here is stored, logged or put into the page.
import { sealValue, b64u, unb64u } from '../proto/wire.js';
import { wantOf } from './faceid.js';   // P555 d: transports + hints for this remote's own key

/** A host salt (b64url, exactly 32 bytes) → bytes, else null. */
export function salt32(v) {
  try { const s = typeof v === 'string' && v ? unb64u(v) : null; return s && s.length === 32 ? s : null; } catch { return null; }
}
/** §37.9: `prf1 ‖ prf2` (64 B) when both came back, `prf1` (32 B) alone, else null. The inputs are zeroed. Pure. */
export function prfJoin(first, second) {
  const view = (x) => (x && x.byteLength ? (ArrayBuffer.isView(x) ? new Uint8Array(x.buffer, x.byteOffset, x.byteLength) : new Uint8Array(x)) : null);
  const a = view(first), b = view(second);
  try {
    if (!a || a.length !== 32) return null;
    if (!b || b.length !== 32) return a.slice();
    const out = new Uint8Array(64); out.set(a, 0); out.set(b, 32); return out;
  } finally { a?.fill(0); b?.fill(0); }
}
/** The PRF extension request: eval.first = salt, eval.second = the host's next salt only when it sent one (M3). Pure. */
export const prfEval = (first, second) => ({ prf: { eval: second ? { first, second } : { first } } });

const buf = (x) => new Uint8Array(x);
/** WebAuthn get() over the host's challenge (bytes) for this device's credential, with the prf extension when `first` is
 *  given. NOT async on purpose: get() starts synchronously inside the caller's tap (iOS gesture rule).
 *  → a promise of {fa: {id, cd, ad, sig}, prf: Uint8Array(32 | 64) | null}; rejects on cancel / failure. */
export function assertPrf(challenge, credId, first, second) {
  const publicKey = { challenge, rpId: location.hostname, userVerification: 'required', timeout: 60000, ...wantOf(credId) };
  if (first) publicKey.extensions = prfEval(first, second || null);
  return navigator.credentials.get({ publicKey }).then((cred) => {
    const r = cred.response;
    let prf = null;
    try {
      const res = cred.getClientExtensionResults?.()?.prf?.results;
      if (res) { prf = prfJoin(res.first, second ? res.second : null); if (!second && res.second) prfJoin(res.second, null)?.fill(0); }
    } catch { prf = null; }
    return { fa: { id: b64u(buf(cred.rawId)), cd: b64u(buf(r.clientDataJSON)), ad: b64u(buf(r.authenticatorData)), sig: b64u(buf(r.signature)) }, prf };
  });
}
/** §11 seal of a PRF value (kind `vault` · `vault_save` · `vshare` · `term_prf` · `vault_share`), then the value is zeroed. */
export async function sealPrf(hostEpk, prf, channel, device, id, kind, nonce, digest) {
  try { return await sealValue(hostEpk, prf, channel, device, id, kind, nonce, digest); } finally { prf.fill(0); }
}
let cap = null;
/** Does this browser say it can do PRF? true / false / null (it does not say — the assertion will tell). */
export async function prfSupport() {
  if (cap !== null) return cap;
  try {
    const c = await globalThis.PublicKeyCredential?.getClientCapabilities?.();
    cap = c && typeof c['extension:prf'] === 'boolean' ? c['extension:prf'] : null;
  } catch { cap = null; }
  return cap;
}
