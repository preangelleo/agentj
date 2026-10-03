// Minimal Noise (rev 34) for agentj: Noise_IK / Noise_IKpsk2 over 25519 / AESGCM / SHA256.
// Runtime crypto is WebCrypto only (browser + Node + Workers) — no third-party code. Pinned by protocol/vectors/ and the
// JS↔Python interop test. Keys are { priv: CryptoKey (X25519, may be non-extractable), pub: Uint8Array(32) }.
const subtle = globalThis.crypto.subtle;
const enc = new TextEncoder();

export const IK = 'Noise_IK_25519_AESGCM_SHA256';
export const IKPSK2 = 'Noise_IKpsk2_25519_AESGCM_SHA256';
const PATTERNS = {
  [IK]: [['e', 'es', 's', 'ss'], ['e', 'ee', 'se']],
  [IKPSK2]: [['e', 'es', 's', 'ss'], ['e', 'ee', 'se', 'psk']],
};
const MAX_NONCE = 2 ** 53; // Number-safe counter; stops earlier than Python's 2^64-1 (Noise reserves it): both fail closed

export function concat(...parts) {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}

export async function sha256(data) { return new Uint8Array(await subtle.digest('SHA-256', data)); }

export async function hmac(key, data) {
  const k = await subtle.importKey('raw', key, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return new Uint8Array(await subtle.sign('HMAC', k, data));
}

async function hkdf(ck, ikm, n) {
  const temp = await hmac(ck, ikm);
  const o1 = await hmac(temp, Uint8Array.of(1));
  const o2 = await hmac(temp, concat(o1, Uint8Array.of(2)));
  if (n === 2) return [o1, o2];
  return [o1, o2, await hmac(temp, concat(o2, Uint8Array.of(3)))];
}

// ---------------------------------------------------------------- X25519
export async function generateKeypair(extractable = false) {
  const kp = await subtle.generateKey({ name: 'X25519' }, extractable, ['deriveBits']);
  return { priv: kp.privateKey, pub: new Uint8Array(await subtle.exportKey('raw', kp.publicKey)) };
}

const PKCS8_X25519 = Uint8Array.from([0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x6e, 0x04, 0x22, 0x04, 0x20]);

/** Import a raw 32-byte X25519 private key (tests / Node host tools only; the browser never has raw keys). */
export async function keypairFromPrivate(raw, extractable = false) {
  const priv = await subtle.importKey('pkcs8', concat(PKCS8_X25519, raw), { name: 'X25519' }, true, ['deriveBits']);
  const jwk = await subtle.exportKey('jwk', priv);
  const pub = Uint8Array.from(atob(jwk.x.replace(/-/g, '+').replace(/_/g, '/')), (c) => c.charCodeAt(0));
  const p2 = extractable ? priv : await subtle.importKey('pkcs8', concat(PKCS8_X25519, raw), { name: 'X25519' }, false, ['deriveBits']);
  return { priv: p2, pub };
}

async function dh(kp, pub) {
  const k = await subtle.importKey('raw', pub, { name: 'X25519' }, false, []);
  return new Uint8Array(await subtle.deriveBits({ name: 'X25519', public: k }, kp.priv, 256));
}

// ---------------------------------------------------------------- CipherState
function nonce(n) {
  const iv = new Uint8Array(12);
  new DataView(iv.buffer).setBigUint64(4, BigInt(n), false);
  return iv;
}

export class CipherState {
  constructor(k = null) { this.k = k; this.n = 0; this._key = null; }
  hasKey() { return this.k !== null; }
  async _aes() {
    if (!this._key) this._key = await subtle.importKey('raw', this.k, 'AES-GCM', false, ['encrypt', 'decrypt']);
    return this._key;
  }
  async encrypt(ad, pt) {
    if (!this.k) return pt;
    if (this.n >= MAX_NONCE) throw new Error('nonce exhausted');
    const ct = new Uint8Array(await subtle.encrypt({ name: 'AES-GCM', iv: nonce(this.n), additionalData: ad, tagLength: 128 }, await this._aes(), pt));
    this.n += 1;
    return ct;
  }
  async decrypt(ad, ct) {
    if (!this.k) return ct;
    if (this.n >= MAX_NONCE) throw new Error('nonce exhausted');
    const pt = new Uint8Array(await subtle.decrypt({ name: 'AES-GCM', iv: nonce(this.n), additionalData: ad, tagLength: 128 }, await this._aes(), ct));
    this.n += 1; // only after a successful decrypt
    return pt;
  }
}

// ---------------------------------------------------------------- HandshakeState
export class Handshake {
  /**
   * @param {object} o protocol (IK | IKPSK2), initiator (bool), prologue (Uint8Array), s (keypair), rs (Uint8Array, initiator only),
   *                   psk (Uint8Array(32), IKpsk2 only), e (keypair, tests only)
   */
  constructor(o) { Object.assign(this, { e: null, re: null, rs: null, psk: null }, o); this.msgs = PATTERNS[o.protocol]; this.i = 0; }

  async init() {
    if (!this.msgs) throw new Error('unsupported protocol');
    const isPsk = this.protocol === IKPSK2;
    if (isPsk && !(this.psk instanceof Uint8Array && this.psk.length === 32)) throw new Error('psk required');
    const name = enc.encode(this.protocol);
    this.h = name.length <= 32 ? concat(name, new Uint8Array(32 - name.length)) : await sha256(name);
    this.ck = this.h;
    this.cs = new CipherState();
    await this.mixHash(this.prologue ?? new Uint8Array(0));
    // pre-message: <- s
    await this.mixHash(this.initiator ? this.rs : this.s.pub);
    return this;
  }

  async mixHash(d) { this.h = await sha256(concat(this.h, d)); }
  async mixKey(ikm) { const [ck, k] = await hkdf(this.ck, ikm, 2); this.ck = ck; this.cs = new CipherState(k); }
  async mixKeyAndHash(ikm) { const [ck, th, k] = await hkdf(this.ck, ikm, 3); this.ck = ck; await this.mixHash(th); this.cs = new CipherState(k); }
  async encryptAndHash(pt) { const ct = await this.cs.encrypt(this.h, pt); await this.mixHash(ct); return ct; }
  async decryptAndHash(ct) { const pt = await this.cs.decrypt(this.h, ct); await this.mixHash(ct); return pt; }

  async _dh(tok) {
    const I = this.initiator;
    if (tok === 'ee') return dh(this.e, this.re);
    if (tok === 'ss') return dh(this.s, this.rs);
    if (tok === 'es') return I ? dh(this.e, this.rs) : dh(this.s, this.re);
    /* se */ return I ? dh(this.s, this.re) : dh(this.e, this.rs);
  }

  myTurn() { return (this.i % 2 === 0) === this.initiator; }
  done() { return this.i >= this.msgs.length; }

  async writeMessage(payload = new Uint8Array(0)) {
    if (this.done() || !this.myTurn()) throw new Error('not my turn');
    const isPsk = this.protocol === IKPSK2;
    const out = [];
    for (const tok of this.msgs[this.i]) {
      if (tok === 'e') {
        if (!this.e) this.e = await generateKeypair(false);
        out.push(this.e.pub); await this.mixHash(this.e.pub);
        if (isPsk) await this.mixKey(this.e.pub);
      } else if (tok === 's') {
        out.push(await this.encryptAndHash(this.s.pub));
      } else if (tok === 'psk') {
        await this.mixKeyAndHash(this.psk);
      } else {
        await this.mixKey(await this._dh(tok));
      }
    }
    out.push(await this.encryptAndHash(payload));
    this.i += 1;
    return concat(...out);
  }

  async readMessage(msg) {
    if (this.done() || this.myTurn()) throw new Error('not their turn');
    const isPsk = this.protocol === IKPSK2;
    let o = 0;
    const take = (n) => { if (o + n > msg.length) throw new Error('short message'); const b = msg.slice(o, o + n); o += n; return b; };
    for (const tok of this.msgs[this.i]) {
      if (tok === 'e') {
        this.re = take(32); await this.mixHash(this.re);
        if (isPsk) await this.mixKey(this.re);
      } else if (tok === 's') {
        this.rs = await this.decryptAndHash(take(this.cs.hasKey() ? 48 : 32));
      } else if (tok === 'psk') {
        await this.mixKeyAndHash(this.psk);
      } else {
        await this.mixKey(await this._dh(tok));
      }
    }
    const payload = await this.decryptAndHash(msg.slice(o));
    this.i += 1;
    return payload;
  }

  /** After the last message: { send, recv, h } — CipherStates for this side and the final handshake hash. */
  async split() {
    if (!this.done()) throw new Error('handshake not finished');
    const [k1, k2] = await hkdf(this.ck, new Uint8Array(0), 2);
    const c1 = new CipherState(k1), c2 = new CipherState(k2);
    return this.initiator ? { send: c1, recv: c2, h: this.h } : { send: c2, recv: c1, h: this.h };
  }
}
