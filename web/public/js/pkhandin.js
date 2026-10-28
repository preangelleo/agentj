// P565 追加 A (华为 Pad 10-28 13:16): the system's fingerprint sheet sends the page to the background and the system drops the
// socket (dev_gone 16–20 s). create()'s result is handed in on the next ready connection — the host keeps the offer with this
// remote for 5 minutes (serve.py pk_offers) — and once more after a reconnect that swallowed its answer. 「正在补交…」 meanwhile,
// never an early 「没存上」. Only for the computer the key was made for (its channel); pk_reg_res ends it (answered()).
// P566 (#364, 华为 Pad 10-28 21:02): the same for every get() result — the key card, the pickup card, the approval card and a
// Face ID confirmation (put(key, …)). Their challenges are bound to the card / request nonce and this remote, never to the
// socket, and the Agent J host checks them unchanged (signature, nonce once, counter, device); this only carries the answer
// over the gap. P566 (#363): a hand-in started inside the drop-reconnect gap (channel() = null) binds to the first channel it
// sees, never to the gap's null — that was read as 「another computer」 after the reconnect.
//   app.js: configure({gen, channel, send, waiting, failed}) · handIn(reg) · again() on every ready · answered() on pk_reg_res
export const HANDIN_MS = 4 * 60 * 1000;
const TICK_MS = 250;
let H = null;
const open = new Map();   // key → {send, ch, until, live, waiting, failed, g: generation it went out on, bad: one that refused it, shown}

export function configure(h) { H = h; }
/** A result is waiting for the host (true while that hand-in is open). */
export const busy = (key = 'reg') => open.has(key);

/** The made passkey (pk_reg). ch = the computer it was made for (null: the one this page is talking to). */
export function handIn(reg, now = Date.now(), ch = null) {
  return put('reg', { send: (g) => H.send(reg, g), until: now + HANDIN_MS, ch, waiting: () => H.waiting(), failed: () => H.failed() });
}
/** P566: one answer that carries a Face ID result → sent on the first ready connection to the same computer before `until`,
 *  again after a reconnect that swallowed its answer, while live() (its card is still open). → true once it went out;
 *  false (failed() said so) when the window ended, the card went or another computer answers now. */
export function put(key, { send, until, ch = null, live = () => true, waiting = null, failed = null }) {
  const e = { send, until, ch: ch || H.channel() || null, live, waiting, failed, g: null, bad: null, shown: false };
  open.set(key, e);
  return run(key, e);
}
async function run(key, e) {
  while (open.get(key) === e && Date.now() < e.until && e.live()) {
    const g = H.gen(), c = H.channel();
    if (g && g === e.g) return true;                  // on this connection already: its answer comes, or the next ready resends
    if (!g || g === e.bad || !c) {
      if (!e.shown) { e.shown = true; e.waiting?.(); }
      await new Promise((r) => setTimeout(r, TICK_MS));
      continue;
    }
    if (!e.ch) e.ch = c;                              // #363: started in the gap — the first computer it sees is the one
    else if (c !== e.ch) break;                       // another computer now: this answer was not made for it
    try { e.g = g; await e.send(g); return true; } catch { e.g = null; e.bad = g; }
  }
  if (open.get(key) !== e) return false;
  open.delete(key);
  e.failed?.();
  return false;
}
/** app.js on ready: a result that went out on an earlier connection and got no answer goes again on this one. */
export function again() {
  const g = H.gen();
  for (const [k, e] of open) if (e.g && e.g !== g) run(k, e).catch(() => {});
}
/** The host answered it (pk_reg_res · elev_progress / elev_done / elev_refused · secret_out_val / _err / _done · ask_done ·
 *  pk_peer_answer_res): → {waited} (it waited for a reconnect: say it arrived), or null when none was open. */
export function answered(key = 'reg') {
  const e = open.get(key);
  open.delete(key);
  return e ? { waited: e.shown } : null;
}
/** app.js forgetLocal (unpair / re-pair, review N3): nothing made for the old pairing goes out any more. */
export function reset() { open.clear(); }
export const _reset = reset;
