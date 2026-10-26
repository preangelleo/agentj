// P414 (PROTOCOL §37.9 / §37.11, ADR-P414-bang): Settings → 安全 → 「电脑密码」. The host block `vault` decides the row
// (absent on an older host → no row): remembered and usable here (Face ID) · remembered but not on this remote yet →
// 「让这台也能用」 (vault_share: host nonce, Face ID + PRF over a fresh salt, then a remote that already can approves it on a
// `vshare` card) · not remembered (the first sudo asks once). 「清除这台」 = P408's signed term_forget (this remote's wrap only).
// The PRF output is sealed to the host's one-time key and zeroed; nothing is stored on this phone.
import { request, done, gen, channel } from './session.js';
import { controlMessage, controlObject, sha256hex, b64u, unb64u } from '../proto/wire.js';
import { signKey } from './store.js';
import { myDeviceId } from './api.js';
import { signedWrite, whyText } from './controls.js';
import { canAssert } from './faceid.js';
import { assertPrf, sealPrf, salt32 } from './vault.js';
import { t, onLang } from './t.js';
import { el, toast, toastAction } from './ui.js';
import { paintPk, pkSheet, faState, webauthnErr } from './pkassist.js';   // P539: no own passkey here · why the tap failed

let info = null, busy = false, last = '', H = { setupFaceId() {} };
let pkErr = null;   // P539: {err, fa_s} after a failed 「用 Face ID 继续」 — the row says why + 再试一次
const HEX32 = /^[0-9a-f]{32}$/;
/** The host block `vault` (§37.9) → {has, any, peers, mode} | null. Pure. */
export function parse(x) {
  if (!x || typeof x !== 'object' || typeof x.has !== 'boolean' || typeof x.any !== 'boolean') return null;
  return { has: x.has, any: x.any || x.has, peers: Number.isInteger(x.peers) && x.peers > 0 ? x.peers : 0,
    mode: x.mode === 'prf' || x.mode === 'local' ? x.mode : null };
}
/** 'none' (no row) · 'has' (Face ID here) · 'local' (terminal only) · 'share' (another remote can approve) · 'nopeer' · 'off'. */
export function state(v = info) {
  if (!v) return 'none';
  if (v.has) return 'has';
  if (v.mode === 'local') return 'local';
  if (!v.any) return 'off';
  return v.peers ? 'share' : 'nopeer';
}
export const vaultInfo = () => info && { ...info };

export function configure(h) {
  H = { ...H, ...h };
  el('set-vault-share')?.addEventListener('click', share);
  el('set-vault-forget')?.addEventListener('click', forget);
  onLang(paint);
}
export function render(hostInfo) { info = parse(hostInfo && hostInfo.vault); paint(); }
export function paint() {
  const row = el('set-vault-row');
  if (!row) return;
  const s = state();
  row.hidden = s === 'none';
  // P414: with the vault row, P408's 「清除已记住的电脑密码」 under 网页终端 would be the same button twice
  const tf = el('set-term-forget'); if (tf && s !== 'none') tf.hidden = true;
  if (s === 'none') return;
  el('set-vault-state').textContent = t('p414.v.st.' + s);
  el('set-vault-share').hidden = s !== 'share';
  el('set-vault-forget').hidden = !(s === 'has' || s === 'local');
  el('set-vault-acts').hidden = el('set-vault-share').hidden && el('set-vault-forget').hidden;
  el('set-vault-share').disabled = busy;
  el('set-vault-share').textContent = t(busy ? 'p414.v.waitBtn' : 'p414.v.share');
  el('set-vault-last').textContent = last; el('set-vault-last').hidden = !last;
  paintPk(el('set-vault-pk'), pkErr && s === 'share' && !busy ? { fa: pkErr.fa_s, err: pkErr.err, retry: share } : {});   // P539
}
function say(text) { last = text || ''; paint(); }

async function forget() {
  const b = el('set-vault-forget');
  b.disabled = true;
  const res = await signedWrite('term_forget', {}, { t: 'term_forget' });
  b.disabled = false;
  if (res.ok) { if (info) { info.has = false; if (info.mode) info.mode = null; } say(''); toast(t('p414.v.forgot'), 3200); }
  else toast(t('p408.forgetFail', { why: whyText(res.why) || res.why || '' }), 4000, 'error');
}

const WHY = ['no_vault', 'has', 'no_peer', 'no_passkey', 'temporary', 'web_not_allowed', 'busy', 'estop', 'denied', 'timeout', 'failed',
  'passkey_failed', 'offline', 'bad_signature', 'replay', 'stale', 'shape'];
function fail(why) {
  busy = false;
  if (why === 'no_passkey') { say(''); toastAction(t('p414.v.why.no_passkey'), t('pkp.setupHere'), () => H.setupFaceId(), 8000); return; }
  say(t('p414.v.failed', { why: t('p414.v.why.' + (WHY.includes(why) ? why : 'failed')) }));
}
/** 「让这台也能用」: step 1 (host nonce, challenge, fresh salt, one-time key) → 「用 Face ID 继续」 → step 2 → wait for a yes. */
async function share() {
  if (busy) return;
  if (!canAssert()) { fail('no_passkey'); return; }
  const G = gen();
  if (!G) { fail('offline'); return; }
  busy = true; pkErr = null; say('');
  const c = await new Promise((resolve) => {
    const r = request({ t: 'vault_share' }, (m) => {
      if (m.t !== 'ctl_res' && m.t !== 'offline') return;
      done(r); clearTimeout(timer); resolve(m);
    }, G);
    const timer = setTimeout(() => { if (r) done(r); resolve({ t: 'ctl_res', ok: false, why: 'timeout' }); }, 20000);
    if (!r) { clearTimeout(timer); resolve({ t: 'offline' }); }
  });
  if (c.t === 'offline') return fail('offline');
  if (!(c.ok === false && c.why === 'passkey_required' && HEX32.test(c.n || '') && typeof c.fa === 'string' && typeof c.fa_ch === 'string'
    && salt32(c.salt) && salt32(c.epk))) return fail(c.ok === false ? String(c.why || 'failed') : 'shape');
  // the Face ID starts inside the tap on 「用 Face ID 继续」 (iOS gesture rule), before the sheet's promise resolves
  const yes = el('confirm-yes');
  let p = null;
  const grab = () => { try { p = assertPrf(unb64u(c.fa_ch), c.fa, salt32(c.salt), null); } catch { p = Promise.reject(new Error('webauthn')); } p.catch(() => {}); };
  yes.addEventListener('click', grab);
  const ok = await pkSheet(faState(c.fa_s), t('p414.v.shareTitle'), t('p414.v.shareText'), t('p414.v.shareYes'));   // P539
  yes.removeEventListener('click', grab);
  if (!ok || !p) { p?.then((r) => r.prf?.fill(0), () => {}); busy = false; say(''); return; }
  let got;
  try { got = await p; } catch (e) {
    busy = false;
    const err = webauthnErr(e);
    if (err) { pkErr = { err, fa_s: faState(c.fa_s) }; say(''); return; }   // P539: the row says why, 再试一次 runs share() again
    say(''); toast(t('p408.faCancel'), 3600); return;
  }
  if (!got.prf) { busy = false; say(t('p414.v.noPrf')); return; }
  let msg;
  try {
    const sk = await signKey();
    if (!sk) throw new Error('no_key');
    const ch = channel(), dev = await myDeviceId(), ts = Date.now();
    const sig = b64u(new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, sk.priv, await controlMessage(ch, dev, 'vault_share', c.n, ts, {}))));
    const digest = await sha256hex(new TextEncoder().encode(controlObject('vault_share', {})));
    const s = await sealPrf(unb64u(c.epk), got.prf, ch, dev, c.n, 'vault_share', c.n, digest);
    msg = { t: 'vault_share', n: c.n, ts, sig, fa: got.fa, pepk: b64u(s.epk), pct: b64u(s.ct) };
  } catch { got.prf?.fill(0); return fail('failed'); }
  say(t('p414.v.waiting'));
  const res = await new Promise((resolve) => {
    let timer = 0;
    const arm = (ms) => { clearTimeout(timer); timer = setTimeout(() => { done(r); resolve({ ok: false, why: 'timeout' }); }, ms); };
    const r = request(msg, (m) => {
      if (m.t === 'ctl_pending' && m.action === 'vault_share') { arm(Number.isInteger(m.timeout_ms) && m.timeout_ms > 0 && m.timeout_ms <= 600000 ? m.timeout_ms + 5000 : 155000); return; }
      if (m.t === 'ctl_res' || m.t === 'offline') { clearTimeout(timer); done(r); resolve(m.t === 'offline' ? { ok: false, why: 'offline' } : m); }
    }, G);
    if (!r) { resolve({ ok: false, why: 'offline' }); return; }
    arm(20000);
  });
  if (res.ok === true) { busy = false; if (info) { info.has = true; info.mode = 'prf'; } say(t('p414.v.shared')); toast(t('p414.v.shared'), 3600); return; }
  fail(String(res.why || 'failed'));
}
// tests
export const _debug = () => ({ info: info && { ...info }, state: state(), busy, last });
