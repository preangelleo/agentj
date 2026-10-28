// P565 追加 A (华为 Pad 10-28 13:16): the system's fingerprint sheet sends the page to the background and the system drops the
// socket (dev_gone 16–20 s). create()'s result is handed in on the next ready connection — the host keeps the offer with this
// remote for 5 minutes (serve.py pk_offers) — and once more after a reconnect that swallowed its answer. 「正在补交…」 meanwhile,
// never an early 「没存上」. Only for the computer the key was made for (its channel); pk_reg_res ends it (answered()).
//   app.js: configure({gen, channel, send, waiting, failed}) · handIn(reg) · again() on every ready · answered() on pk_reg_res
export const HANDIN_MS = 4 * 60 * 1000;
const TICK_MS = 250;
let H = null;
let cur = null;                                       // {reg, ch, until, g: generation it went out on, bad: one that refused it, shown}

export function configure(h) { H = h; }
/** The result is waiting for the host (true while a hand-in is open). */
export const busy = () => !!cur;

export function handIn(reg, now = Date.now()) {
  const h = { reg, ch: H.channel(), until: now + HANDIN_MS, g: null, bad: null, shown: false };
  cur = h;
  return run(h);
}
async function run(h) {
  while (cur === h && Date.now() < h.until) {
    const g = H.gen();
    if (g && g === h.g) return;                       // on this connection already: its pk_reg_res comes, or the next ready resends
    if (!g || g === h.bad) {
      if (!h.shown) { h.shown = true; H.waiting(); }
      await new Promise((r) => setTimeout(r, TICK_MS));
      continue;
    }
    if (H.channel() !== h.ch) break;                 // another computer now: this key was not made for it
    try { h.g = g; await H.send(h.reg, g); return; } catch { h.g = null; h.bad = g; }
  }
  if (cur !== h) return;
  cur = null;
  H.failed();
}
/** app.js on ready: a result that went out on an earlier connection and got no answer goes again on this one. */
export function again() { const h = cur; if (h && h.g && h.g !== H.gen()) run(h).catch(() => {}); }
/** app.js pk_reg_res: → {waited} (it waited for a reconnect: 「已设好」 says it arrived), or null when none was open. */
export function answered() {
  const h = cur;
  cur = null;
  return h ? { waited: h.shown } : null;
}
export function _reset() { cur = null; }
