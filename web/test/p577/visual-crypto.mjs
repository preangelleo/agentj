/** Test-context-only public vectors. Never imported by served application assets. */
export function publicVisualCrypto() {
  if (!['localhost', '127.0.0.1', '[::1]'].includes(location.hostname)) return;
  const subtle = crypto.subtle;
  const nativeGenerate = subtle.generateKey.bind(subtle);
  const nativeImport = subtle.importKey.bind(subtle);
  const nativeExport = subtle.exportKey.bind(subtle);
  const nativeDigest = subtle.digest.bind(subtle);
  const counters = new Map();
  // Per-origin counters survive same-context reloads; unrelated browser contexts remain isolated.
  const next = name => {
    let old;
    try { old = Number(sessionStorage.getItem('p577-public-vector/' + name) || 0); sessionStorage.setItem('p577-public-vector/' + name, String(old + 1)); }
    catch { old = counters.get(name) || 0; counters.set(name, old + 1); }
    return old;
  };
  subtle.generateKey = async (algorithm, extractable, usages) => {
    const name = typeof algorithm === 'string' ? algorithm : algorithm.name;
    if (!['X25519', 'Ed25519'].includes(name)) return nativeGenerate(algorithm, extractable, usages);
    const raw = new Uint8Array(await nativeDigest('SHA-256', new TextEncoder().encode(`P577 public browser vector/v1/${name}/${next(name)}`)));
    const pkcs8 = new Uint8Array(48);
    pkcs8.set([0x30,0x2e,2,1,0,0x30,5,6,3,0x2b,0x65,name === 'X25519' ? 0x6e : 0x70,4,0x22,4,0x20]);
    pkcs8.set(raw, 16);
    const privateUsages = name === 'X25519' ? usages : usages.filter(x => x === 'sign');
    const temporary = await nativeImport('pkcs8', pkcs8, algorithm, true, privateUsages);
    const jwk = await nativeExport('jwk', temporary);
    const pub = Uint8Array.from(atob(jwk.x.replace(/-/g, '+').replace(/_/g, '/')), c => c.charCodeAt(0));
    return {
      privateKey: await nativeImport('pkcs8', pkcs8, algorithm, extractable, privateUsages),
      publicKey: await nativeImport('raw', pub, algorithm, true, name === 'X25519' ? [] : usages.filter(x => x === 'verify'))
    };
  };
  crypto.getRandomValues = array => {
    if (!ArrayBuffer.isView(array) || array instanceof Float32Array || array instanceof Float64Array || array instanceof DataView) throw new DOMException('Expected integer typed array', 'TypeMismatchError');
    if (array.byteLength > 65536) throw new DOMException('Too many bytes', 'QuotaExceededError');
    let state = (0x5772026 ^ next('random')) >>> 0;
    const bytes = new Uint8Array(array.buffer, array.byteOffset, array.byteLength);
    for (let i = 0; i < bytes.length; i++) { state ^= state << 13; state ^= state >>> 17; state ^= state << 5; bytes[i] = state & 255; }
    return array;
  };
}
