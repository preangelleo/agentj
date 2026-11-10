// P574: one-time UV binding for an existing passkey. Never creates/replaces a key or changes a vault wrap.
import { b64u } from '../proto/wire.js';
import { bindChallenge, bindRegistration, verifyBindingAssertion, rosterCore } from './ownergrant.js';
import { dbGet, dbPut, signKey, deviceKey } from './store.js';
import { deviceId } from '../proto/wire.js';
import { channel, gen, sendApp } from './session.js';
import { approve, canAssert } from './faceid.js';
import { confirmSheet, toast, toastAction } from './ui.js';
import { startRosterIntro } from './pkpeer.js';
import { t } from './t.js';
import { put, answered } from './pkhandin.js';
import { current as sameOwnerStart } from './sameowner-start.js';
import { offerSetupHere, withBio } from './pkassist.js';
let busy = false, offeredAt = 0, grantCard = null;
/** 0304 R-F5 (contract-a5 DEVIATION 4): 「批准」 on a same-owner grant card from a remote not on the roster yet. The host sends this
 *  remote its roster_bind_offer (only when it has a passkey) and then answer_refused roster_binding_required; the card stays open.
 *  → the line the card shows instead of 「确认没生效」. With an offer: the binding sheet is already up (no popup over it); after the
 *  binding the owner taps 「批准」 again. No offer = no passkey here: the 「在本机设置」 offer. Never a raw code. */
export function grantRefused(m) {
  if (busy || Date.now() - offeredAt < 5000) { grantCard = m.id; return { text: t('p0306.grant.bind'), quiet: true }; }
  offerSetupHere('p0306.grant.needPasskey');
  return { text: withBio('p0306.grant.needPasskey'), quiet: true };
}
/** The grant card this binding is for (null once it is answered / gone). */
export const grantWaiting = () => grantCard;
export function grantEnded(id) { if (grantCard === id) grantCard = null; }
/** A binding sheet or its hand-in is open (a start in progress need not ask the host for another offer). */
export const isBinding = () => busy;
/** #0306 A5: ask the host for this remote's binding offer (its roster_bind_offer opens the sheet below). */
export function requestOffer() { Promise.resolve().then(() => sendApp({ t: 'roster_bind_get' })).catch(() => {}); }
export const descriptorKey = ch => 'owner-roster:' + ch;
export const descriptor = ch => dbGet(descriptorKey(ch));
export async function remember(reg, dev, ch, rp) {
  if (!ch) throw new Error('binding host');
  await dbPut(descriptorKey(ch), { ...rosterCore(reg, dev, rp), sign_pub: reg.roster_sign_pub, bind: reg.roster_sig });
}
export async function offer(m) {
  offeredAt = Date.now();
  if (busy) return false;
  // #0306 A5: a 「同一个主人」 start in progress owns this sheet's wording and its outcome (continue / retry). Read when the sheet
  // opens and when it ends — the start's answer (or a continuation's) may land while the keys are being read.
  const flowNow = () => { const f = sameOwnerStart(); return f?.active() ? f : null; };
  const ended = async () => { const f = flowNow(); if (f) { busy = false; await f.bound(false); return true; } return false; };
  busy = true;
  let ours = false;
  try {
    const e = m.entry, ch = channel(), g = gen(), key = await signKey(), dev = await deviceId((await deviceKey()).pub);
    if (!ch || !g || !e || e.dev !== dev || e.sign_pub !== b64u(key.pub) || e.rp !== location.hostname) return false;   // another remote's offer
    if (!canAssert()) { await ended(); return false; }
    ours = true;
    const challenge = await bindChallenge(e);
    const starting = !!flowNow();
    if (!await confirmSheet(t('p336.faTitle'), t(starting ? 'p0306.so.firstBind' : 'p574.bindWhy'), t('p336.faYes'), false, undefined, '', 'grow')) {
      await ended();
      return false;
    }
    // No asynchronous work between confirmation and get(): preserve the iOS tap gesture.
    const fa = await approve(challenge, e.id);
    if (!await verifyBindingAssertion(e, fa, challenge, { rp: location.hostname, origin: location.origin })) throw new Error('binding assertion');
    if (channel() && channel() !== ch) throw new Error('different host');
    const reg = await bindRegistration(e, dev, key, location.hostname);
    await remember(reg, dev, ch, location.hostname);
    const out = { t: 'roster_bind', fa, roster_sig: reg.roster_sig };
    const ok = await put('roster-bind', { send: g2 => sendApp(out, g2), ch, until: Date.now() + 120000 });
    if (!ok) throw new Error('offline');
    return true;
  } catch {
    if (!(ours && await ended())) toast(t('p574.bindFailed'), 7000);
    return false;
  }
  finally { busy = false; }
}
export function result(m) {
  answered('roster-bind');
  const flow = sameOwnerStart();
  // #0306 A5: the start in progress continues by itself (or offers a retry) — no 「choose it again」, no introduction detour
  if (flow?.active()) { flow.bound(m.ok === true).catch(() => {}); return; }
  // 0304 R-F5: a grant card is waiting — bound now, so 「批准」 works on the next tap; no introduction detour
  if (grantCard && m.ok === true) { grantCard = null; toast(t('p0306.grant.bound'), 7000); return; }
  if (m.ok === true) toastAction(t('p574.bound'), t('p574.introduce'), () => startRosterIntro(), 12000);
  else toast(t('p574.bindFailed'), 7000);
}

// An upgraded receiver may be shown the other host's invitation before starting one itself.
// Ask only for public metadata; the existing confirmation card still requires an explicit UV tap.
export async function ensureForCeremony(m) {
  const ch = channel();
  if (m.tool === 'peer_question' && m.pq?.pairing && typeof m.pq.pairing_scope === 'string' && ch && !await descriptor(ch)) {
    await sendApp({ t: 'roster_bind_get' });
  }
}
