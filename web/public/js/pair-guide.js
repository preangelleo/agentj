// #435: the first-remote hint is the shared bottom notice for 10 s — only while exactly one remote is paired, and once per
// device (a refresh or the host's resend on every resume never brings it back). Display only: never a resident line.
import { t } from './t.js';
import { notifyOnce } from './ui.js';
export const PAIR_GUIDE_MS = 10000;
const SEEN = 'aj.pairGuide.seen';
let remotes = null, shownHere = false;
/** The `devs` list (the same one Settings → Remotes shows) says how many remotes are paired. */
export function noteRemotes(m) { if (Array.isArray(m?.items)) remotes = m.items.length; }
export function showPairGuide(kind) {
  if (kind !== 'phone' && kind !== 'computer') return;            // null = nothing to suggest; the notice ends by itself
  if (remotes !== 1 || shownHere) return;                         // 2+ remotes (or no list yet): never
  shownHere = true;                                               // storage blocked: still once per page
  notifyOnce({ kind: 'info', shape: 'rect', text: t('pairGuide.' + kind), ms: PAIR_GUIDE_MS }, SEEN);
}
