// P203 (PROTOCOL §29.1): rebuild the cards from the computer's snapshot. The relay delivers a card at most once, so a card
// sent while this page was frozen in the background (or a 「已处理」 that never came) would otherwise be missed until it timed
// out on the computer. When this remote becomes ready, comes back to the front or reopens, it asks once — a read signed with
// its approval key — listing the cards it already shows; the computer sends the missing ones and every open id, and the
// cards it no longer lists close here: 「已过期」 when their time ran out, else quietly (answered on another remote).
import { ask1, isReady, peer } from './session.js';
import { signed } from './controls.js';
import * as snap from './snap.js';
import * as controls from './controls.js';
import * as elevate from './elevate.js';
import * as secretout from './secretout.js';
import * as contacts from './contacts.js';
import * as longtask from './longtask.js';
import * as logincard from './logincard.js';
import * as upgcard from './upgcard.js';

const MODS = [
  [snap.cardIds, snap.prune], [controls.grantIds, controls.pruneGrants], [elevate.ids, elevate.prune],
  [secretout.ids, secretout.prune], [contacts.ids, contacts.prune], [longtask.ids, longtask.prune],
  [logincard.ids, logincard.prune], [upgcard.ids, upgcard.prune],
];
const GAP_MS = 1500;          // ready and 「回到前台」 often fire together: one request
const HAVE_MAX = 256;         // the host reads at most this many (CARDS_HAVE_MAX)
let inflight = null, lastAt = 0, stats = { asked: 0, answered: 0, pruned: 0 };

export function openIds() { return MODS.flatMap(([ids]) => ids()); }

/** → a promise that settles when this sync is over (never rejects). `force` skips the short gap (tests, the ≡ menu). */
export function sync(force = false) {
  if (!isReady() || !peer.cards1) return Promise.resolve(false);
  if (inflight) return inflight;
  if (!force && Date.now() - lastAt < GAP_MS) return Promise.resolve(false);
  lastAt = Date.now();
  const have = openIds().slice(0, HAVE_MAX);
  inflight = (async () => {
    stats.asked++;
    let obj;
    try { obj = await signed('cards_get', {}, { t: 'cards_get', have }); } catch { return false; }   // no approval key: nothing to show
    const m = await ask1(obj, 'cards', 15000);
    if (m.t !== 'cards' || m.ok !== true || !Array.isArray(m.open)) return false;
    stats.answered++;
    const open = new Set(m.open.filter((x) => typeof x === 'string'));
    const asked = new Set(have);              // only cards shown when we asked: one that arrived meanwhile is newer than the list
    const gone = (id) => asked.has(id) && !open.has(id);
    for (const [ids, prune] of MODS) { const n = ids().filter(gone).length; if (n) { stats.pruned += n; prune(gone); } }
    return true;
  })().catch(() => false).finally(() => { inflight = null; });
  return inflight;
}
export const _debug = () => ({ ...stats, open: openIds() });
