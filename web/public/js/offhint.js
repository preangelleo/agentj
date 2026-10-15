// P287: the line under 「电脑离线」. While the computer is offline it cannot tell the phone anything, so the words live in this
// page and the phone picks them from what the computer said while it was still up: `plat` / `boot` in approved / ready
// (PROTOCOL §10.0, kept in the IndexedDB host record for a cold start). Never while connected; never for a short drop (a
// service restart or an upgrade is back within seconds); an unknown computer gets the general line, not a guess.
export const HINT_AFTER_MS = 90_000;

const PLATS = ['macos', 'linux', 'windows', 'other'];
const BOOTS = ['login', 'auto'];

/** {plat, boot} from a host message or a stored record; unknown values become null. */
export function hostKind(m) {
  return { plat: PLATS.includes(m?.plat) ? m.plat : null, boot: BOOTS.includes(m?.boot) ? m.boot : null };
}

/** The i18n key to show after `downMs` of 「电脑离线」, or null (not yet / not offline). */
export function hintKey(kind, downMs) {
  if (!(downMs >= HINT_AFTER_MS)) return null;
  const { plat, boot } = hostKind(kind);
  if (plat === 'macos') return 'offhint.macos';
  if (plat === 'windows') return 'offhint.windows';
  if (plat === 'linux' && boot === 'login') return 'offhint.linuxLogin';
  if (plat === 'linux' && boot === 'auto') return 'offhint.linuxAuto';
  return 'offhint.generic';
}
