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
let busy = false;
export const descriptorKey = ch => 'owner-roster:' + ch;
export const descriptor = ch => dbGet(descriptorKey(ch));
export async function remember(reg, dev, ch, rp) {
  if (!ch) throw new Error('binding host');
  await dbPut(descriptorKey(ch), { ...rosterCore(reg, dev, rp), sign_pub: reg.roster_sign_pub, bind: reg.roster_sig });
}
export async function offer(m) {
  if (busy || !canAssert()) return false;
  busy = true;
  try {
    const e = m.entry, ch = channel(), g = gen(), key = await signKey(), dev = await deviceId((await deviceKey()).pub);
    if (!ch || !g || !e || e.dev !== dev || e.sign_pub !== b64u(key.pub) || e.rp !== location.hostname) return false;
    const challenge = await bindChallenge(e);
    if (!await confirmSheet(t('p336.faTitle'), t('p574.bindWhy'), t('p336.faYes'))) return false;
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
  } catch { toast(t('p574.bindFailed'), 7000); return false; }
  finally { busy = false; }
}
export function result(m) {
  answered('roster-bind');
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
