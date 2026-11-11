// Navigation circuit breaker. Only public reasons and timestamps are persisted, never page content.
const KEY = 'aj.reloadGuard.v1';
const WINDOW = 600000;
const DELAYS = [0, 5000, 30000];
const read = store => { try { return JSON.parse(store.getItem(KEY) || '{}') || {}; } catch { return {}; } };
const save = (store, data) => { try { store.setItem(KEY, JSON.stringify(data)); } catch {} };
export function createReloadGuard({session = sessionStorage, local = localStorage, now = () => Date.now(), wait = setTimeout, navigate = (mode, url) => mode === 'reload' ? location.reload() : location[mode](url), report = () => {}, notice = () => {}} = {}) {
  let pending = new Set();
  return function reloadGuard(reason, {mode = 'reload', url, manual = false} = {}) {
    if (!/^[a-z][a-z0-9_.-]{0,63}$/.test(reason) || !['reload', 'replace', 'assign'].includes(mode)) return false;
    const time = now(), a = read(session), b = read(local);
    const entries = [...(a[reason]?.times || []), ...(b[reason]?.times || [])].filter(n => Number.isFinite(n) && n <= time && n > time - WINDOW);
    const times = [...new Set(entries)].sort((x,y) => x-y);
    const stopped = times.length > 0 && !!(a[reason]?.stopped || b[reason]?.stopped);
    if (!manual && (times.length >= 3 || stopped)) {
      if (!stopped) {
        a[reason] = b[reason] = {times, stopped: true}; save(session,a); save(local,b);
        notice(reason); Promise.resolve().then(() => report(reason)).catch(() => {});
      }
      return false;
    }
    if (pending.has(reason)) return false;
    if (!manual) { times.push(time); a[reason] = b[reason] = {times}; save(session,a); save(local,b); }
    const delay = manual ? 0 : DELAYS[times.length - 1];
    pending.add(reason);
    wait(() => { pending.delete(reason); navigate(mode, url); }, delay);
    return true;
  };
}
