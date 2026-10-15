// P207 (追加 2): 「未读」 = the Agent's replies that arrived after the last one this device saw while the page was in the
// foreground. Each remote keeps its own mark, here in localStorage (UNREAD_KEY = "aj.unread": {e, m, s} — the history
// epoch, the read-up-to turn id, and the ids seen above it; numbers only, never text). Silent turns are not pages, so they
// never count. A reply counts as seen when its page is on screen while the page is visible.
//
// Pure bookkeeping: relay.js passes the loaded reply ids (ascending) and whether they reach back to the mark (`covered`).
export const UNREAD_KEY = 'aj.unread';
const SEEN_MAX = 400;

function readStore() {
  try {
    const v = JSON.parse(localStorage.getItem(UNREAD_KEY) || 'null');
    if (v && Number.isInteger(v.e) && Number.isInteger(v.m) && Array.isArray(v.s)) return { e: v.e, m: v.m, s: new Set(v.s.filter(Number.isInteger)) };
  } catch (_) { /* a broken record starts over */ }
  return null;
}
function writeStore(st) {
  try { localStorage.setItem(UNREAD_KEY, JSON.stringify({ e: st.e, m: st.m, s: [...st.s].sort((a, b) => a - b).slice(-SEEN_MAX) })); } catch (_) { /* private mode: this visit only */ }
}

export function createUnread() {
  let st;
  const state = () => (st === undefined ? (st = readStore()) : st);
  return {
    /** Before anything else on a synced history: first run → everything already here counts as read; another epoch
     *  (a /clear on the computer) → every reply of the new one is unread until seen. */
    sync(epoch, ids) {
      const s = state();
      if (!s) { st = { e: epoch, m: ids.length ? ids[ids.length - 1] : 0, s: new Set() }; writeStore(st); return; }
      if (s.e !== epoch) { s.e = epoch; s.m = 0; s.s.clear(); writeStore(s); }
    },
    ready: () => !!state(),
    /** The page showing reply `id` was on screen in the foreground. */
    see(id, ids, covered) {
      const s = state();
      if (!s || !Number.isInteger(id) || id <= s.m || s.s.has(id)) return false;
      s.s.add(id);
      this.advance(ids, covered);
      writeStore(s);
      return true;
    },
    /** Move the mark over the seen replies that follow it without a gap (only when the loaded ids reach the mark). */
    advance(ids, covered) {
      const s = state();
      if (!s || !covered) return;
      for (const id of ids) {
        if (id <= s.m) continue;
        if (!s.s.has(id)) break;
        s.m = id;
      }
      for (const id of [...s.s]) if (id <= s.m) s.s.delete(id);
    },
    /** The unread ids among `ids`, oldest first. */
    list(ids) {
      const s = state();
      return s ? ids.filter((id) => id > s.m && !s.s.has(id)) : [];
    },
    mark: () => (state() ? state().m : null),
  };
}
