// P247: new hosts own the maximum watermark, scoped by host and epoch. Owner receipts
// are cached for reconnect retries; P207 bookkeeping below is only the old-host fallback.
// P207 (追加 2): 「未读」 = the Agent's replies that arrived after the last one this device saw while the page was in the
// foreground. Each remote keeps its own mark, here in localStorage (UNREAD_KEY = "aj.unread": {e, m, s} — the history
// epoch, the read-up-to turn id, and the ids seen above it; numbers only, never text). Silent turns are not pages, so they
// never count. A reply counts as seen when its page is on screen while the page is visible.
//
// P321: in owner mode the host's own value is `acked`; a local mark above it (a receipt lost on the way, or sent while the
// connection was down) is `pending()` and relay.js sends it again — a few times per id, so a refused id cannot loop.
// P333 (Leo 10-16): 「new on this remote」 is not 「unread」. A reply that arrives live while this remote is visible and
// not looking at it is `fresh` here until this remote's own screen shows it (`look`); another remote's receipt moves the
// shared mark only, so it can clear the count of what was read elsewhere but never this remote's own new-message alert
// (the page number's pulse and the header badge). In memory only: a reload / a return from the background aligns with
// the host as P247 / P321 did. `alerts(ids)` = shared unread ∪ fresh — what the badge counts and its tap jumps to.
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
  let st, owner = false, scope, acked = -1, tries = new Map();
  const RETRY_MAX = 3;
  let base = null, looked = 0;               // P333: the newest reply id already accounted for; the newest one shown here
  const fresh = new Set(), bg = new Set();   // bg: arrived live while this remote was in the background
  const forget = () => { base = null; looked = 0; fresh.clear(); bg.clear(); };
  const trim = (set) => { if (set.size > SEEN_MAX) for (const id of [...set].sort((a, b) => a - b).slice(0, set.size - SEEN_MAX)) set.delete(id); };
  const ownerKey = () => 'aj.ownerRead:' + scope;
  const saveOwner = () => { try { localStorage.setItem(ownerKey(), JSON.stringify({e:st.e,m:st.m})); } catch {} };
  const state = () => (st === undefined ? (st = readStore()) : st);
  return {
    /** Before anything else on a synced history: first run → everything already here counts as read; another epoch
     *  (a /clear on the computer) → every reply of the new one is unread until seen. */
    sync(epoch, ids) {
      const s = state();
      if (!s) { st = { e: epoch, m: ids.length ? ids[ids.length - 1] : 0, s: new Set() }; writeStore(st); return; }
      if (owner) return;
      if (s.e !== epoch) { s.e = epoch; s.m = 0; s.s.clear(); writeStore(s); }
    },
    bind(key) { if (scope !== key) { scope = key; owner = false; st = undefined; acked = -1; tries.clear(); forget(); } },
    owner(epoch, mark) {
      if (!Number.isSafeInteger(epoch) || !Number.isSafeInteger(mark) || mark < 0) return false;
      if (!owner || !st || epoch > st.e) {
        if (st && epoch > st.e) { acked = -1; tries.clear(); forget(); }
        let saved;
        try { saved = JSON.parse(localStorage.getItem(ownerKey()) || 'null'); } catch {}
        st = {e: epoch, m: saved?.e === epoch && Number.isSafeInteger(saved.m) && saved.m >= 0 ? Math.max(mark,saved.m) : mark, s: new Set()};
      }
      else if (epoch !== st.e) return false;
      else st.m = Math.max(st.m, mark);
      acked = Math.max(acked, mark);
      owner = true; saveOwner();
      return true;
    },
    isOwner: () => owner,
    /** P321: the owner mark the host has not confirmed yet, or null (none / not owner / tried RETRY_MAX times). */
    pending() {
      if (!owner || !st || st.m <= acked) return null;
      const n = tries.get(st.m) || 0;
      if (n >= RETRY_MAX) return null;
      tries.set(st.m, n + 1);
      return st.m;
    },
    ready: () => !!state(),
    /** The page showing reply `id` was on screen in the foreground. */
    see(id, ids, covered) {
      const s = state();
      if (!s || !Number.isInteger(id) || id <= s.m || s.s.has(id)) return false;
      if (owner) { s.m = id; saveOwner(); return true; }
      s.s.add(id);
      this.advance(ids, covered);
      writeStore(s);
      return true;
    },
    /** Move the mark over the seen replies that follow it without a gap (only when the loaded ids reach the mark). */
    advance(ids, covered) {
      const s = state();
      if (owner || !s || !covered) return;
      for (const id of ids) {
        if (id <= s.m) continue;
        if (!s.s.has(id)) break;
        s.m = id;
      }
      for (const id of [...s.s]) if (id <= s.m) s.s.delete(id);
    },
    /** P333: reply ids above the base came in since the last paint — `live` (this remote is visible) makes them fresh,
     *  otherwise (a background tab, a phone in the pocket) the shared mark decides (they can still relight the pulse). */
    arrivals(ids, live) {
      if (!ids.length) return;
      const top = ids[ids.length - 1];
      if (base === null) { base = top; return; }
      for (const id of ids) if (id > base && id > looked) (live ? fresh : bg).add(id);
      base = Math.max(base, top);
      trim(fresh); trim(bg);
    },
    /** P333: what a head fetch brought (a reload, a return to the foreground) is not a live arrival. */
    rebase(ids) { if (ids.length) base = Math.max(base ?? 0, ids[ids.length - 1]); },
    /** P333: this remote's screen showed reply `id` in the foreground: it and everything before it are no longer new here. */
    look(id) {
      if (!Number.isInteger(id)) return;
      looked = Math.max(looked, id);
      for (const set of [fresh, bg]) for (const x of [...set]) if (x <= id) set.delete(x);
    },
    fresh: () => [...fresh].sort((a, b) => a - b),
    /** P333: what came in live after reply `id` and is still news here — fresh, or arrived in the background and still
     *  unread on the shared mark. The page number pulses for these (not for older unread a first-unread jump steps over). */
    newer(ids, id) {
      const s = state();
      return ids.some((x) => x > id && (fresh.has(x) || (bg.has(x) && !!s && x > s.m && !s.s.has(x))));
    },
    /** P333: the badge — unread on the shared mark, plus what arrived here unseen even if another remote read it. */
    alerts(ids) {
      const s = state();
      return ids.filter((id) => fresh.has(id) || (!!s && id > s.m && !s.s.has(id)));
    },
    /** The unread ids among `ids`, oldest first. */
    list(ids) {
      const s = state();
      return s ? ids.filter((id) => id > s.m && !s.s.has(id)) : [];
    },
    mark: () => (state() ? state().m : null),
  };
}
