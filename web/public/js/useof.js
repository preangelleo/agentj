// P204 (PROTOCOL §29): a history page's `use` → plain numbers (no DOM, no i18n — snap.js and node tests import it).
const NUM = (x, max = 1e12) => (typeof x === 'number' && Number.isFinite(x) && x >= 0 && x <= max ? x : null);
/** P204 (§29): a page's `use` → numbers only (absent = the harness did not report it), or null. */
export function useOf(u) {
  if (!u || typeof u !== 'object') return null;
  const o = { s: NUM(u.s, 1e7), i: NUM(u.i), o: NUM(u.o), cr: NUM(u.cr), cw: NUM(u.cw), usd: NUM(u.usd, 1e6), est: u.est === true,
    b: u.b === 'sub' ? 'sub' : 'api', q: NUM(u.q, 100), tps: NUM(u.tps, 1e6), tool_s: NUM(u.tool_s, 1e7),
    m: typeof u.m === 'string' ? u.m.replace(/[^\w.:/@[\]-]/g, '').slice(0, 100) : null,
    h: ['claude', 'codex', 'opencode'].includes(u.h) ? u.h : null };
  return o.s === null && o.i === null && o.o === null ? null : o;
}
