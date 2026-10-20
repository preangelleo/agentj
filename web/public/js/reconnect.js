// The five-second rule for 「连不上 Agent J 主机」. A phone that comes back from the lock screen, a frozen tab or a
// network switch usually reconnects within a second; telling the owner 「断开了」 for that blink makes Agent J look
// unstable. So the link has three states:
//   up     — connected (or nothing to say: not paired, an error page owns the screen)
//   quiet  — the link dropped less than DOWN_DEBOUNCE_MS ago, or the page has just come back to the foreground: the page
//            reconnects silently; only the small status dots show it (header dot hollow, Settings dot amber)
//   down   — unreachable for DOWN_DEBOUNCE_MS continuously while the page was in front: the disconnected card, the
//            Settings red line + notice and Emergency repair's 「现在连不上」 may speak
// Only foreground time counts. A hidden page never turns `down` (its timers are throttled or frozen and the browser may
// have parked its socket on purpose); coming back to the foreground (visibilitychange / pageshow / resume / online /
// network change) starts the window again. A timer that fires much later than it was set for means the page was asleep in
// between — that time is not evidence either, so the window starts again from the wake-up.
export const DOWN_DEBOUNCE_MS = 5000;
export const LATE_MS = 1000;            // a timer this late = the page was frozen / throttled in between

/** A gate with an injectable clock (tests drive it with a fake one). */
export function createGate({ ms = DOWN_DEBOUNCE_MS, now = () => Date.now(), setT = (f, d) => setTimeout(f, d), clearT = (h) => clearTimeout(h),
  hidden = () => typeof document !== 'undefined' && document.hidden === true, onChange = () => {} } = {}) {
  let st = 'up', start = 0, due = 0, timer = 0, parked = false;
  const set = (v) => { if (v === st) return; st = v; onChange(v); };
  const disarm = () => { if (timer) clearT(timer); timer = 0; };
  const arm = (left) => { disarm(); due = now() + left; timer = setT(fire, left); };
  function fire() {
    timer = 0;
    if (st !== 'quiet') return;
    const t = now();
    if (hidden()) { parked = true; return; }                     // wait for the foreground; restart() opens a new window
    if (t - due > LATE_MS) { start = t; arm(ms); return; }        // asleep in between: count from the wake-up
    if (t - start < ms) { arm(ms - (t - start)); return; }
    set('down');
  }
  return {
    /** The link dropped (or a paired page starts without one). No-op while already quiet / down. */
    drop() { if (st !== 'up') return false; start = now(); parked = false; set('quiet'); arm(ms); return true; },
    /** Connected again, or nothing to report: back to up, the window forgotten. */
    up() { disarm(); parked = false; start = 0; set('up'); },
    /** Foreground / resume / network change: a drop that is not over yet gets a fresh, silent window. */
    restart() {
      if (st === 'up') return false;
      start = now(); parked = false;
      if (st === 'down') set('quiet');
      arm(ms);
      return true;
    },
    state: () => st,
    /** ms of the current window (0 when up). */
    elapsed: () => (st === 'up' ? 0 : now() - start),
    _debug: () => ({ st, start, due, timer: !!timer, parked }),
  };
}
