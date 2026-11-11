// 0.30.7 (preview-frset): on iPhone / iPad, Safari and the Home Screen app keep separate storage, so one phone holds two remote
// identities. Each one's Face ID / renewal takeover makes the host drop the other as `removed {why:'replaced'}`, and the two
// keep pushing each other out. The side that was pushed out says why ONCE (per storage): use one of them, the Home Screen icon.
// The host's `replaced` also covers a full computer dropping its stalest remote; only `moved: true` (the same phone's other
// identity took the record over by passkey / renewal-ticket restore) shows this hint.
const KEY = 'aj.twoIdHint';
const iosNow = () => typeof document !== 'undefined' && document.documentElement.dataset.ios === '1';
const seen = () => { try { return localStorage.getItem(KEY) === '1'; } catch { return false; } };
const mark = () => { try { localStorage.setItem(KEY, '1'); } catch { /* private mode: shown again next time, harmless */ } };
/** pure rule (tested): iOS, pushed out by its sibling, never shown in this storage before */
export const shouldShow = ({ ios, why, moved, already }) => ios === true && why === 'replaced' && moved === true && already !== true;
/** revoked view: show / hide the box; once shown it is remembered (the page stays until the owner leaves it) */
export function paint(box, why, moved = false) {
  if (!box) return false;
  const show = box.hidden === false && why === 'replaced' && moved === true || shouldShow({ ios: iosNow(), why, moved, already: seen() });
  box.hidden = !show;
  if (show) mark();
  return show;
}
