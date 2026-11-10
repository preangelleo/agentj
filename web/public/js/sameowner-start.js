// #0306 A5 (董事长 11-09 22:5x): 「同一个主人」 on a remote that is not on this host's signing roster yet. The host answers the
// start with {ok:false, why:'roster_binding_required', next:'bind', cont:<one-time ticket>} — a first step, not a failure. The
// page says 「这台遥控器第一次用，先用 Face ID 确认一次」 on the existing binding sheet (rosterbind.js, ONE UV), and once the host
// confirms the binding it sends the start again with the ticket — no second tap, no second scan. Then the usual six digits.
// THREAT-MODEL A5: the ticket saves a tap, never a UV — the host issues it bound to this device / session / friend, ≤ 120 s,
// single use, and only honours it when the binding really landed. Here: sent at most once, only after a successful binding
// receipt, only for the friend the owner chose, only before it expires; a failed / cancelled binding never continues.
// Pure: no DOM, no session — friends.js wires the real write / bind / toast (and node tests wire fakes).
export const CONT_MS = 120000;
const TICKET_RE = /^[A-Za-z0-9_-]{16,256}$/;

let installed = null;
/** The live flow (rosterbind.js asks it whether a binding sheet / receipt belongs to a start in progress). */
export const current = () => installed;
export function install(flow) { installed = flow; return flow; }

/** deps: write(target, extra) → ctl_res · bind() asks the host for this remote's binding offer (the sheet + UV follow) ·
 *  binding() → true while a binding sheet / hand-in is already open · say(key, vars) · sayRetry(key, retryLabelKey, fn) ·
 *  needPasskey(friend) (contract-a5 §DEVIATIONS 1: no passkey here yet → the 「在本机设置」 offer, never the binding sheet;
 *  0304 r2: the caller starts again by itself once that passkey is saved) ·
 *  fail(why) (an ordinary refusal, in plain words) · started() (the start went through: refresh the page) ·
 *  settle() (back to the normal button state) · now() */
export function createSameOwnerStart(deps) {
  const now = deps.now || (() => Date.now());
  let flow = null;          // { friend, cont, until, phase: 'starting' | 'binding' | 'continuing' | 'failed' }

  const finish = () => { flow = null; };
  const ticket = (res) => (typeof res?.cont === 'string' && TICKET_RE.test(res.cont) ? res.cont : null);
  async function begin(friend) {
    flow = { friend, cont: null, until: now() + CONT_MS, phase: 'starting' };
    const res = await deps.write({ friend, op: 'group', value: 'same-owner' });
    if (flow?.friend !== friend) return res;               // superseded meanwhile
    if (res?.ok) { finish(); return res; }
    if (res?.why !== 'roster_binding_required') { finish(); return res; }
    // contract-a5 DEVIATION 1: no passkey on this remote → nothing to bind, no ticket; say where to set one up
    if (res.next === 'passkey') { finish(); deps.needPasskey(friend); return { ok: false, why: 'passkey', quiet: true }; }
    // A first step: keep the ticket (if this host sends one) and open the binding sheet unless the host's offer already did
    const cancelled = flow.phase === 'failed';             // the host's own offer opened a sheet that was cancelled already
    flow.cont = ticket(res);
    flow.until = now() + CONT_MS;
    if (cancelled) return { ok: false, why: 'binding', quiet: true };
    flow.phase = 'binding';
    if (!deps.binding()) deps.bind();
    return { ok: false, why: 'binding', quiet: true };
  }
  /** Does a binding sheet that opens now belong to this start? (it then says the first-time line, not the upgrade one) */
  const active = () => !!flow && flow.phase !== 'failed';
  /** The binding sheet ended: ok = the host's roster_bind_res said yes; false = cancelled / failed / refused. → handled?
   *  At most ONE automatic continuation per binding (= per owner tap): a continuation the host turned down is answered like a
   *  normal start (contract-a5 DEVIATION 2) — ok → started; roster_binding_required again → the plain line + 「再试一次」 (the
   *  owner's next tap, with the fresh ticket), never a second automatic bind. */
  async function bound(ok) {
    const f = flow;
    if (!f || f.phase !== 'binding' && f.phase !== 'starting') return false;
    if (!ok) { failed(f); return true; }
    finish();
    if (!f.cont || now() > f.until) { deps.say('p0306.so.again'); deps.settle(); return true; }   // old host / expired / lost ticket
    // 'continuing': a binding sheet the host re-offers meanwhile still belongs to this start (its wording); a receipt is not taken
    const going = flow = { ...f, phase: 'continuing' };
    const res = await deps.write({ friend: f.friend, op: 'group', value: 'same-owner' }, { cont: f.cont });
    if (flow !== going) return true;                        // the owner started something else meanwhile
    finish();
    if (res?.ok) { deps.say('p417.so.started'); deps.started(); return true; }
    if (res?.why === 'roster_binding_required' && res.next === 'passkey') { deps.needPasskey(f.friend); deps.settle(); return true; }
    if (res?.why === 'roster_binding_required') {
      // The binding did not stick on the host: a fresh ticket waits for the owner's OWN next tap — never a second automatic bind.
      // The host re-offers the binding with this answer; when that sheet is already up, its tap is that next tap (it continues
      // once with the fresh ticket); otherwise the plain line + 「再试一次」.
      flow = { friend: f.friend, cont: ticket(res), until: now() + CONT_MS, phase: 'binding' };
      if (!deps.binding()) failed(flow);
      return true;
    }
    if (res?.why === 'offline') deps.say('p0306.so.again'); else deps.fail(res?.why);
    deps.settle();
    return true;
  }
  function failed(f) {
    f.phase = 'failed';
    deps.sayRetry('p0306.so.bindFailed', 'p0306.so.retry', retry);
    deps.settle();
  }
  /** The retry button after a failed / cancelled binding: the same sheet once more (the ticket keeps its own deadline). */
  function retry() {
    if (!flow || flow.phase !== 'failed') return;
    flow.phase = 'binding';
    deps.bind();
  }
  return { begin, bound, active, retry, get state() { return flow && { ...flow }; } };
}
