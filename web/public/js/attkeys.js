// P398 (#156): the same file twice in the tray — 截图自动进附件 put the screenshot in and a ⌘V of the same clipboard put it
// in again — is taken once. A file's keys: the SHA-256 of its bytes and, for an image, of its decoded pixels (Chromium
// re-encodes what it reads from / writes to the clipboard, so the same picture can arrive as different PNG bytes). Keys
// live only in memory, per File object; nothing is stored or sent. No crypto.subtle (an insecure page): no keys, no dedupe.
const BYTES_MAX = 64 * 1024 * 1024;     // larger files are not read twice just to compare them
const PIXELS_MAX = 16e6;                 // ~ a 5K screenshot; larger images are compared by bytes only
const cache = new WeakMap();             // File → Promise<string[]>

const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
async function digest(data) { return hex(await crypto.subtle.digest('SHA-256', data)); }

async function pixelKey(f) {
  if (!/^image\//.test(f.type || '') || typeof createImageBitmap !== 'function' || typeof OffscreenCanvas !== 'function') return null;
  const bm = await createImageBitmap(f);
  try {
    if (!bm.width || !bm.height || bm.width * bm.height > PIXELS_MAX) return null;
    const c = new OffscreenCanvas(bm.width, bm.height), g = c.getContext('2d');
    g.drawImage(bm, 0, 0);
    return `p:${bm.width}x${bm.height}:${await digest(g.getImageData(0, 0, bm.width, bm.height).data)}`;
  } finally { if (bm.close) bm.close(); }
}
async function compute(f) {
  const out = [];
  if (!(globalThis.crypto && crypto.subtle) || !f || typeof f.arrayBuffer !== 'function') return out;
  try { if (f.size <= BYTES_MAX) out.push('b:' + await digest(await f.arrayBuffer())); } catch { /* unreadable: no key */ }
  try { const k = await pixelKey(f); if (k) out.push(k); } catch { /* not decodable: bytes only */ }
  return out;
}
/** The keys of one file (computed once per File object). */
export function keysOf(f) {
  if (!f || typeof f !== 'object') return Promise.resolve([]);
  let p = cache.get(f);
  if (!p) { p = compute(f); cache.set(f, p); }
  return p;
}
/** `files` minus those whose content is already in `have` (Files already in the tray) or earlier in `files`. */
export async function unique(files, have) {
  const seen = new Set();
  for (const ks of await Promise.all(have.map(keysOf))) for (const k of ks) seen.add(k);
  const out = [];
  const all = await Promise.all(files.map(keysOf));
  files.forEach((f, i) => {
    const ks = all[i];
    if (ks.some((k) => seen.has(k))) return;
    for (const k of ks) seen.add(k);
    out.push(f);
  });
  return out;
}
