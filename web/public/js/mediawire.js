// The wire side of media out (PROTOCOL §13, F21): the limits, the sanitiser of a page's `media` / `media_skip`, the
// slot ↔ item match and the HTML preview frame's attributes. Pure (no DOM, no session, no strings), so snap.js can use it
// and Node can test it; media.js does the fetching and the rendering.
export const CHUNK = 45056;
export const WINDOW = 8;
export const EAGER = 1024 * 1024;            // pictures up to this size load with the page
export const LRU_MAX = 64 * 1024 * 1024;
export const MAX_ITEMS = 8;
const MIB = 1024 * 1024;
export const CAPS = { image: 10 * MIB, audio: 25 * MIB, video: 50 * MIB, pdf: 25 * MIB, html: 2 * MIB, file: 25 * MIB };
export const WHYS = ['too_big', 'outside', 'secret', 'type', 'gone'];
export const HTML_CSP = "default-src 'none'; img-src data:; style-src 'unsafe-inline'";   // no network at all

const ID22 = /^[A-Za-z0-9_-]{22}$/;
const MIME = /^[a-z0-9.+-]{1,40}\/[a-z0-9.+-]{1,80}$/i;
const base = (s) => String(s).split(/[/\\]/).filter(Boolean).pop() || '';

/** The wire's media / media_skip of a turn → safe plain objects (unknown kinds, bad ids, over-cap sizes dropped). */
export function sanitize(t) {
  const media = (Array.isArray(t && t.media) ? t.media : []).filter((m) => m && typeof m === 'object'
    && ID22.test(m.mid) && typeof m.name === 'string' && typeof m.mime === 'string' && MIME.test(m.mime)
    && Object.hasOwn(CAPS, m.kind) && Number.isInteger(m.bytes) && m.bytes > 0 && m.bytes <= CAPS[m.kind]
    && typeof m.sha256 === 'string' && /^[0-9a-f]{64}$/.test(m.sha256)).slice(0, MAX_ITEMS)
    .map((m) => ({ mid: m.mid, name: base(m.name).slice(0, 128) || m.kind, mime: m.mime.toLowerCase(), kind: m.kind, bytes: m.bytes,
      sha256: m.sha256, ref: typeof m.ref === 'string' ? m.ref.slice(0, 512) : '' }));
  const skip = (Array.isArray(t && t.media_skip) ? t.media_skip : []).filter((s) => s && typeof s.name === 'string' && WHYS.includes(s.why))
    .slice(0, 16).map((s) => ({ name: base(s.name).slice(0, 128), why: s.why, bytes: Number.isInteger(s.bytes) && s.bytes > 0 ? s.bytes : 0 }));
  return { media, skip };
}

/** The attributes of the HTML preview frame — the whole policy in one place (unit-tested). */
export function sandboxAttrs() {
  return { sandbox: '', referrerpolicy: 'no-referrer', csp: HTML_CSP, allow: '', loading: 'eager' };
}
/** A reference as md.js / the computer wrote it → the comparable form (no <>, no backslash escapes, no ./). */
export function normRef(r) {
  let s = String(r || '').trim();
  if (s.startsWith('<') && s.endsWith('>')) s = s.slice(1, -1).trim();
  s = s.replace(/\\([!-/:-@[-`{-~])/g, '$1');
  if (/^file:\/\//i.test(s)) { try { s = decodeURIComponent(new URL(s).pathname); } catch { /* as it is */ } }
  return s.replace(/^\.\//, '');
}
/** Which item an inline slot (md.js `![](ref)`) shows: the same reference, else the only item with that file name. */
export function matchSlot(ref, items, used = new Set()) {
  const r = normRef(ref);
  const free = items.filter((m) => !used.has(m.mid));
  const same = free.find((m) => normRef(m.ref) === r);
  if (same) return same;
  const byName = free.filter((m) => m.name === base(r));
  return byName.length === 1 ? byName[0] : null;
}
/** P59 (ADR-A164): where each item and skip note of a page goes. refs = the data-ref of every slot in reading order (md.js
 *  `![](local)` and `[label](local)`). → { at: per slot {mid} | {skip: <index into skips>} | null (keep the source text),
 *  rest: the items no slot shows (the strip under the words), restSkips: the skip notes no slot shows }.
 *  Order of preference: the same reference, then the only item with that file name (two items of one name: no guess), then an item another slot already
 *  shows (the reply wrote it twice: shown twice, where it was written), then a refusal of that file name (its one-line note
 *  in place of the Markdown). Nothing is guessed when two items share a name. */
export function placeSlots(refs, items, skips = []) {
  const at = refs.map(() => null), used = new Set(), usedSkip = new Set();
  const rs = refs.map(normRef);
  rs.forEach((r, i) => {
    const m = items.find((x) => !used.has(x.mid) && normRef(x.ref) === r);
    if (m) { at[i] = { mid: m.mid }; used.add(m.mid); }
  });
  const byName = (r) => { const n = base(r), all = n ? items.filter((x) => x.name === n) : []; return all.length === 1 ? all[0] : null; };
  rs.forEach((r, i) => {
    if (at[i]) return;
    const m = byName(r);
    if (m && !used.has(m.mid)) { at[i] = { mid: m.mid }; used.add(m.mid); }
  });
  rs.forEach((r, i) => {
    if (at[i]) return;
    const m = items.find((x) => normRef(x.ref) === r) || byName(r);
    if (m) { at[i] = { mid: m.mid }; return; }
    const n = base(r);
    if (!n) return;
    let k = skips.findIndex((s, j) => !usedSkip.has(j) && s.name === n);
    if (k < 0) k = skips.findIndex((s) => s.name === n);
    if (k >= 0) { at[i] = { skip: k }; usedSkip.add(k); }
  });
  return { at, rest: items.filter((m) => !used.has(m.mid)), restSkips: skips.filter((_, j) => !usedSkip.has(j)) };
}
/** Blob type: QuickTime H.264 plays as MP4 on both engines; everything else as the computer typed it. */
export const blobType = (m) => (m.mime === 'video/quicktime' ? 'video/mp4' : m.mime);
