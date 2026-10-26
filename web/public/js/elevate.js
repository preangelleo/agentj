import { authOf, paintAuth } from './authcard.js';
import {phoneConfirmation} from './tvremote.js';
// F17 (PROTOCOL §11): the sudo card and the secret card. The Agent asked for an admin password (to run one exact command)
// or for an API key (to be saved on the computer); the human types it here into a masked field. It is sealed to the card's
// one-time host key and signed with this device's approval key, then the field and the bytes are cleared. Nothing of it is
// kept, logged, drafted or shown again — the Agent only ever gets the command's output or a receipt.
import { sendApp, gen, channel, randHex } from './session.js';
import { elevateFields, elevateDigest, elevateMessage, sealValue, controlMessage, sha256hex, b64u, unb64u } from '../proto/wire.js';
import { SVG } from './icons.js';
import { signKey } from './store.js';
import { myDeviceId, myIdSync } from './api.js';
import { t, lang, onLang } from './t.js';
import { mk, executionToast as toast, notify, replyNotice } from './ui.js';
import { registerLayer, TOP_RANK } from './layers.js';
import { copyable } from './copycode.js';   // P292
import { paintMailPassword } from './mail-password.js';
import { elevateChallenge, canAssert, approve } from './faceid.js';
import { paintPk, errName } from './pkassist.js';   // P539: no own passkey here · why a passkey tap failed
// P414 (PROTOCOL §37.10 / §37.11, ADR-P414-bang): the computer-password vault on sudo cards — 「用 Face ID 填入电脑密码」 (the
// PRF value sealed with kind `vault`, answer `vault:1`), a typed answer that also remembers it (`pepk`/`pct`, kind `vault_save`,
// signed `save:` tail, M2), 「记住的电脑密码不对了」 after `stale`, and the `vshare` card (another remote asks to use it; 「同意」 =
// Face ID + PRF over this remote's wrap salt, sealed kind `vshare`). PRF eval.second = the host's next salt `vn` (M3).
import { assertPrf, sealPrf, salt32, prfSupport } from './vault.js';
let prfCap = null;
prfSupport().then((v) => { prfCap = v; }, () => {});

const cards = new Map();           // id → card (in arrival order)
const completed = new Set();
let recoveryTimer = 0;
let recoverCards = () => Promise.resolve();
let secretSaved = () => {};
export function configure({ recover, onSecretSaved }) { if (typeof recover === 'function') recoverCards = recover; if (typeof onSecretSaved === 'function') secretSaved = onSecretSaved; }
function recoverRunning() {
  if (recoveryTimer || ![...cards.values()].some(c => c.running)) return;
  recoveryTimer = setTimeout(async () => {
    recoveryTimer = 0;
    // A terminal event may be lost while the page stays foregrounded. Ask for authoritative ids/receipts.
    try { await recoverCards(); } catch { /* a disconnected session retries on the next tick */ }
    recoverRunning();
  }, 15000);
}
let root = null, timer = 0, busy = false, shownId = null, denyArmed = 0;
const MAX_FIELD = 8192;

function build() {
  if (root) return root;
  root = mk('div', 'ajmodal dlgcard elev');
  root.id = 'elev';
  // P142 (A16) → P406 #173: a layer — ESC collapses the card (never a deny: it stays open and the status strip reopens it)
  registerLayer({ element: () => root, open: () => !!root && !root.hidden, rank: TOP_RANK, fallback: () => document.getElementById('input') || document.getElementById('setBtn'),
    close: () => collapse() });
  root.hidden = true;
  root.setAttribute('role', 'dialog');
  root.setAttribute('aria-modal', 'true');
  root.setAttribute('aria-labelledby', 'elev-title');
  const form = mk('form', 'box');
  form.id = 'elev-form'; form.autocomplete = 'off'; form.noValidate = true;
  const node = (tag, id, cls) => { const n = mk(tag, cls); n.id = id; return n; };
  const input = node('input', 'elev-input', 'elev-input');
  Object.assign(input, { type: 'password', autocomplete: 'off', spellcheck: false });
  for (const [k, v] of [['autocapitalize', 'off'], ['autocorrect', 'off'], ['enterkeyhint', 'done']]) input.setAttribute(k, v);
  const label = node('label', 'elev-label', 'elev-label');
  label.htmlFor = 'elev-input';
  const bad = node('p', 'elev-bad', 'elev-bad');
  bad.hidden = true;
  const empty = node('p', 'elev-empty', 'elev-bad');
  empty.hidden = true;
  // P414: 「用 Face ID 填入电脑密码」 first (the typed field below it is the second way), 「用 Face ID 记住」 under the field
  const vault = node('button', 'elev-vault', 'aj-btn aj-btn--primary elev-vault'); vault.type = 'button'; vault.hidden = true;
  const stale = node('p', 'elev-stale', 'elev-bad'); stale.hidden = true;
  const saveOpt = node('label', 'elev-save-opt', 'elev-grant-opt elev-save-opt'); saveOpt.hidden = true;
  const saveBox = node('input', 'elev-save-on', 'elev-grant-on'); saveBox.type = 'checkbox';
  saveOpt.append(saveBox, node('span', 'elev-save-text'));
  // P73 (ADR-A180): the main action alone on its row; 「拒绝 / 不提供」 a quiet text button on its own line under it, so a
  // thumb aiming at 「保存到Agent J 主机」 cannot land on it. The keyboard's Enter / 完成 key only ever submits.
  const acts = mk('div', 'ajrow confirm-row elev-acts');
  const allow = node('button', 'elev-allow', 'aj-btn aj-btn--primary'); allow.type = 'submit';
  acts.append(allow);
  const deny = node('button', 'elev-deny', 'aj-btn aj-btn--quiet elev-deny'); deny.type = 'button';
  // #116: the 10-minute toggle, off by default, shown only when the host offers it for this exact command format
  const opt = node('label', 'elev-grant-opt', 'elev-grant-opt'); opt.hidden = true;
  const box = node('input', 'elev-grant-on', 'elev-grant-on'); box.type = 'checkbox';
  const optText = mk('span', 'elev-grant-opt-text');
  optText.append(node('span', 'elev-grant-opt-main'), node('small', 'elev-grant-opt-fmt'));
  opt.append(box, optText);
  // P406 #173: 「收起」 (down arrow) at every stage — the card leaves the screen, nothing is declined, the strip brings it back
  const hide = node('button', 'elev-hide', 'elev-hide'); hide.type = 'button';
  hide.append(chevron());
  form.append(hide, node('p', 'elev-kicker', 'elev-kicker'), node('h2', 'elev-title'), node('dl', 'elev-rows', 'elev-rows'), node('section', 'elev-auth', 'elev-auth'), bad, stale, vault, label, input,
    empty, opt, saveOpt, node('p', 'elev-note', 'elev-note'), node('p', 'elev-left', 'elev-left'), acts, deny);
  acts.after(node('div', 'elev-pk', 'pkassist'));   // P539: right under 「同意」, above the quiet 「拒绝」
  root.appendChild(form);
  document.body.appendChild(root);
  root.querySelector('#elev-form').addEventListener('submit', (e) => { e.preventDefault(); decide(true); });
  root.querySelector('#elev-deny').addEventListener('click', () => askDeny());
  hide.addEventListener('click', () => collapse());
  root.querySelector('#elev-vault').addEventListener('click', () => decide(true, 'vault'));   // P414
  root.querySelector('#elev-save-on').addEventListener('change', (e) => { $('elev-note').textContent = t(e.target.checked ? 'p414.e.saveNote' : 'elev.sudo.note'); });
  root.querySelector('#elev-input').addEventListener('input', () => { $('elev-empty').hidden = true; paintButtons(); });
  root.querySelector('#elev-input').addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || e.isComposing) return;
    e.preventDefault(); e.stopPropagation();
    decide(true);                                  // Enter = submit, never decline (an empty field says so)
  });
  onLang(() => { shownId = null; render(); });
  return root;
}
const $ = (id) => root.querySelector('#' + id);

const str = (v, n) => (typeof v === 'string' && v.length <= n ? v : null);
/** A card from the host (§11 `elev`); a re-sent one (wrong password → new nonce and key) replaces the old. */
export function add(m) {
  if (typeof m.id !== 'string' || !/^[0-9a-f]{32}$/.test(m.id) || !['sudo', 'secret', 'vshare'].includes(m.kind)) return;
  if ((m.state !== 'running' && !/^[0-9a-f]{32}$/.test(m.n || '')) || typeof m.epk !== 'string') return;
  let epk;
  try { epk = unb64u(m.epk); } catch { return; }
  if (epk.length !== 32) return;
  const ttl = Number.isInteger(m.ttl) && m.ttl >= 0 && m.ttl <= 600 ? m.ttl : 120;
  const c = { id: m.id, kind: m.kind, n: m.n, epk, passkey_required: m.passkey_required === true, deadline: Date.now() + ttl * 1000, bad: Number.isInteger(m.bad) ? m.bad : 0,
    tries: Number.isInteger(m.tries) ? m.tries : 3 };
  if (m.kind === 'sudo') {
    c.cmd = str(m.cmd, 2000); c.why = str(m.why, 300); c.effect = str(m.effect ?? '', 300);
    if (c.cmd === null || c.why === null || c.effect === null) return;
    c.interpreter = m.interpreter === true;
    c.helper = Array.isArray(m.helper) ? m.helper.filter((x) => typeof x === 'string' && x.length <= 64).slice(0, 64) : [];
    // #116 (§11.1): the host's opt-in toggle — program, fixed options, class hash. Off until the owner switches it on.
    const g = m.grant;
    c.grant = !c.interpreter && g && typeof g === 'object' && Number.isInteger(g.secs) && g.secs > 0 && g.secs <= 3600
      && str(g.prog, 64) && str(g.fmt, 400) && /^[0-9a-f]{64}$/.test(g.h || '') ? { secs: g.secs, prog: g.prog, fmt: g.fmt, h: g.h } : null;
    c.v = vaultOf(m.v); c.stale = m.stale === 1 || m.stale === true;   // P414 (§37.10)
  } else if (m.kind === 'vshare') {
    // P414 (§37.11, M4): who asks — its id, passkey credential id, pairing date, flags; all of it inside the signed digest
    // name: the host's label is ≤ 64 code points (an emoji = 2 UTF-16 units here)
    c.dev = str(m.dev, 64); c.name = str(m.name ?? '', 128); c.cred = str(m.cred, 1400);
    c.paired = Number.isInteger(m.paired) && m.paired >= 0 ? m.paired : null;
    c.flags = Array.isArray(m.flags) && m.flags.length <= 4 && m.flags.every((f) => typeof f === 'string' && f.length <= 16 && !f.includes(',')) ? m.flags.slice() : null;
    c.v = vaultOf(m.v);
    if (!c.dev || !c.cred || c.paired === null || !c.flags || c.name === null || !c.v || !c.v.has) return;
  } else {
    c.name = str(m.name, 128); c.purpose = str(m.purpose, m.name === 'AGENTJ_MAIL_PASSWORD' ? 2000 : 300); c.dest = str(m.dest, 1100); c.verify = str(m.verify ?? '', 800);
    if (!c.name || c.purpose === null || c.dest === null || c.verify === null) return;
    if (m.auth) { c.auth = authOf(m.auth); if (!c.auth) return; }
  }
  // ADR-A163: this device saved a passkey (F20) → 「同意」 needs Face ID. The host names our own credential id; the challenge
  // (card id + this arming's nonce + what is shown) is made now, so the tap can go straight to the system sheet.
  if (m.state !== 'running' && typeof m.fa === 'string' && m.fa.length > 0 && m.fa.length <= 1400) {
    c.fa = m.fa;
    c.fa_s = ['local', 'borrowed', 'unknown'].includes(m.fa_s) ? m.fa_s : null;   // P539: does this remote hold that passkey itself (= pkrules faState, inline: test/p414_contract_phone.mjs runs add() from source)
    (async () => {
      const digest = await elevateDigest(c.kind, elevateFields(c));
      c.faCh = await elevateChallenge(channel(), await myDeviceId(), c.id, c.kind, c.n, digest);
    })().catch(() => { c.faCh = null; });
  }
  if (completed.has(m.id)) return;
  c.running = m.state === 'running';
  // P406 (§11.2): the command already runs (a replay after a reconnect) → straight to the strip, never the card
  c.started = c.running && m.phase === 'started';
  if (c.started) c.t0 = Date.now() - (Number.isInteger(m.elapsed) ? m.elapsed : 0) * 1000;
  const old = cards.get(m.id);
  if (old) { c.out = old.out || ''; if (old.n === c.n) c.collapsed = old.collapsed; if (old.started && c.started) c.t0 = old.t0; }
  // P414 (§37.10, host build): the same card re-armed after this remote's 「用 Face ID 填入电脑密码」 with `v.has` 0 and no
  // `stale` = the remembered password did not open with this Face ID (nothing counted); say so above the field
  if (c.kind === 'sudo' && c.v && !c.v.has && !c.stale && old && (old.sentVault || old.vaultMiss)) c.vaultMiss = true;
  cards.set(m.id, c);
  if (c.started && old?.watching && runShown === c.id && panelOpen) watch(c, true);   // a reconnect: ask again (a new session)
  if (old && shownId === m.id) { shownId = null; busy = false; }
  render();
}
/** P527: a settings tap owns the visible input, even if an older card was collapsed or waiting. */
export function openSecret(m) {
  add(m);
  const c = cards.get(m.id);
  if (!c || c.kind !== 'secret' || c.name !== m.name) return false;
  for (const other of cards.values()) other.collapsed = other !== c;
  render();
  return shownId === c.id && !root.hidden;
}
/** Repeating a settings tap reopens its existing live card instead of stranding it behind the page. */
export function reopenSecret(name) {
  const c = [...cards.values()].find(c => c.kind === 'secret' && c.name === name && c.deadline > Date.now());
  if (!c) return false;
  for (const other of cards.values()) other.collapsed = other !== c;
  render();
  return shownId === c.id && !root.hidden;
}
/** Reopen a collapsed rescue card from the Settings page that still owns the flow. */
export function reopenAuth() {
  const c = [...cards.values()].find(c => c.auth);
  if (c) { c.collapsed = false; render(); }
}
/** P414 (§37.10): `v` of a sudo / vshare card → {has, vs, vn} (vs / vn = 32-byte salts) or null. Pure. */
export function vaultOf(v) {
  if (!v || typeof v !== 'object') return null;
  const vs = salt32(v.vs);
  return vs ? { has: v.has === 1 || v.has === true, vs, vn: salt32(v.vn) } : null;
}
const RESULT = { done: 'elev.r.done', saved: 'elev.r.saved', denied: 'elev.r.denied', timeout: 'elev.r.timeout', gone: 'elev.r.gone',
  stopped: 'elev.r.stopped', locked: 'elev.r.locked', bad_password: 'elev.r.badPassword', failed: 'elev.r.failed' };
/** The card ended (§11 `elev_done`). */
export function done(m) {
  if (completed.has(m.id)) return;
  const c = cards.get(m.id);
  completed.add(m.id);
  if (completed.size > 100) completed.delete(completed.values().next().value);
  cards.delete(m.id);
  if (c?.kind === 'secret' && ['saved','done'].includes(m.result)) secretSaved(c.name, m.verify);
  if (shownId === m.id) { shownId = null; busy = false; }
  if (c?.started) {                               // P406: the strip shows the result (+ the masked output tail) a while
    finished = { id: c.id, kind: c.kind, cmd: c.cmd, result: m.result, code: m.code, tail: str(m.tail, 20000) ?? c.out ?? '',
      until: Date.now() + 12000 };
  }
  const key = RESULT[m.result];
  if (key && c) {
    let text = t(key, { code: m.code ?? '' });
    if (m.result === 'done' && Number.isInteger(m.code) && m.code !== 0) text = t('elev.r.doneFail', { code: m.code }) + (typeof m.summary === 'string' && m.summary ? ' ' + m.summary.slice(0,600) : '');
    if (c.name === 'AGENTJ_MAIL_PASSWORD' && m.result === 'saved') text = t('mail.passwordSaved');
    if (c.kind === 'secret' && m.result === 'done') text = t('elev.r.saved');
    if (c.kind === 'vshare' && (m.result === 'saved' || m.result === 'done')) text = t('p414.e.vsDone');   // P414: the host ends it `saved`
    if (m.elsewhere === 1) text = t('p549.elsewhere') + ' ' + text;          // P549 (#314): saved through another card
    if (m.saved === 1 || m.saved === true) text += ' ' + t('p414.e.saved');       // P414 (§37.10): the typed password is remembered
    if (m.verify === 'ok') text += ' ' + t('elev.r.verifyOk');
    if (m.verify === 'fail') text += ' ' + t('elev.r.verifyFail');
    if (c.kind === 'secret' && m.key_hint === 'short') text += ' ' + t('elev.r.keyShort');   // P163: from the length only
    // P318: a clean save / run is the success format (the mail key: the centred ✓ circle, P256's place); a refusal is error
    const fail = ['denied', 'bad_password', 'failed', 'locked'].includes(m.result) || (m.result === 'done' && Number.isInteger(m.code) && m.code !== 0) || m.verify === 'fail';
    const ok = !fail && (m.result === 'saved' || m.result === 'done') && m.key_hint !== 'short';
    const mailSaved = c.name === 'AGENTJ_MAIL_PASSWORD' && m.result === 'saved';
    notify({ kind: fail ? 'error' : ok ? 'success' : 'info', text, ms: m.key_hint === 'short' ? 8000 : 3600, shape: c.kind === 'secret' && ok ? 'circle' : 'rect' });
  }
  render();
}
/** The computer did not accept this approval's Face ID (§16.1 `elev_refused`): the card stays open, nothing was done. */
export function refused(m) {
  const c = cards.get(m.id);
  if (!c) return;
  c.running = false;
  if (shownId === m.id) { busy = false; paintButtons(); paintLeft(c); }
  toast(t('elev.fa.refused'), 3600);
}
/** Unpair / revoke / a new pairing: forget every card at once. */
export function clear() {
  clearTimeout(recoveryTimer); recoveryTimer = 0;
  grants([]);
  completed.clear(); cards.clear(); shownId = null; busy = false; finished = null; panelOpen = false;
  if (runEl) paintRun();
  if (root) { $('elev-input').value = ''; root.hidden = true; }
  clearTimeout(timer);
}
export const openCount = () => cards.size;
export const isOpen = () => !!root && !root.hidden;
export const ids = () => [...cards.keys()];                                                          // P203 (§29.1)
export function prune(gone) { const now = Date.now(); for (const c of [...cards.values()]) if (gone(c.id)) done({ id: c.id, result: c.running ? 'gone' : c.deadline <= now ? 'timeout' : 'gone' }); }
/** Metadata only: execution cannot renew or grant an approval. */
export function progress(m) {
  const c = cards.get(m.id);
  if (!c || m.state !== 'running') return;
  c.running = true;
  if (m.phase === 'started' && !c.started) {     // P406 (§11.2): the password was accepted and the command runs — the card goes
    c.started = true;
    c.t0 = Date.now() - (Number.isInteger(m.elapsed) ? m.elapsed : 0) * 1000;
    if (shownId === c.id) { shownId = null; busy = false; }
    render();
    return;
  }
  if (shownId === c.id) { busy = true; clearTimeout(timer); paintButtons(); paintLeft(c); }
}
/** P406 (§11.2) `elev_out`: the running command's output, masked on the host; `full` replaces what this page has. */
export function output(m) {
  const c = cards.get(m.id);
  const text = str(m.text, 20000);
  if (!c || text === null) return;
  c.out = ((m.full ? '' : c.out || '') + text).slice(-OUT_KEEP);
  if (runShown === c.id && panelOpen) paintOut(c.out);
}

function row(dl, label, value, cls) {
  if (!value) return;
  dl.appendChild(mk('dt', '', label));
  const dd = mk('dd', cls || '', value);
  dl.appendChild(cls === 'mono' ? copyable(dd, { variant: 'block' }) : dd);   // P292: a command / path / name can be copied (⧉ inside)
}
function render() {
  build();
  const c = [...cards.values()].find((x) => !x.started && !x.collapsed);   // P406: running / collapsed ones live in the strip
  clearTimeout(timer);
  paintRun();
  if (!c) { root.hidden = true; $('elev-auth').replaceChildren(); $('elev-input').value = ''; document.body.dataset.elev = '0'; shownId = null; return; }
  document.body.dataset.elev = '1';
  if (shownId !== c.id) {
    shownId = c.id; busy = false;
    $('elev-input').value = '';
    const sudo = c.kind === 'sudo', vs = c.kind === 'vshare';
    const nopw = viaHelper(c) || vs;
    root.classList.toggle('nopw', nopw);
    root.classList.toggle('interpreter', !!c.interpreter);
    $('elev-label').hidden = nopw; $('elev-input').hidden = nopw;
    root.classList.toggle('secret', c.kind === 'secret');
    $('elev-kicker').textContent = t(sudo ? 'elev.sudo.kicker' : vs ? 'p414.e.vsKicker' : 'elev.secret.kicker');
    $('elev-title').textContent = sudo ? t('elev.sudo.title') : vs ? t('p414.e.vsTitle', { name: c.name || c.dev }) : t('elev.secret.title', { name: c.name });
    paintAuth($('elev-auth'), c.auth);
    const dl = $('elev-rows'); dl.replaceChildren();
    if (vs) {   // P414 (§37.11, M4): the requesting remote, as the digest covers it
      row(dl, t('p414.e.vsDev'), c.dev, 'mono');
      row(dl, t('p414.e.vsCred'), c.cred.length > 16 ? c.cred.slice(0, 16) + '…' : c.cred, 'mono');
      row(dl, t('p414.e.vsPaired'), c.paired ? new Date(c.paired * 1000).toLocaleDateString(lang() === 'en' ? 'en' : 'zh-CN') : t('p414.e.vsUnknown'));
      const fl = c.flags.filter((f) => f === 'web' || f === 'account').map((f) => t('p414.e.flag.' + f));
      row(dl, t('p414.e.vsKind'), fl.length ? fl.join(' · ') : t('p414.e.flag.app'));
    } else if (sudo) {
      // P339 (backlog #44 ④): what it is for comes first and largest, right under the title; the command after it
      row(dl, t('elev.sudo.why') + ' · ' + t('elev.agentClaim'), c.why, 'purpose');
      row(dl, t('elev.sudo.cmd'), c.cmd, 'mono');
      row(dl, t('elev.sudo.effect') + ' · ' + t('elev.agentClaim'), c.effect);
    } else {
      row(dl, t('elev.secret.name'), c.name, 'mono');
      row(dl, t('elev.secret.purpose'), c.purpose);
      row(dl, t('elev.secret.dest'), c.dest, 'mono');
      row(dl, t('elev.secret.verify'), c.verify, 'mono');
    }
    $('elev-label').textContent = t(sudo || vs ? 'elev.sudo.label' : 'elev.secret.label', { name: c.name || '' });
    const mail = paintMailPassword(c, $('elev-label'), $('elev-input'), $('elev-rows'));
    if (mail) { $('elev-title').textContent = c.purpose.split('\n')[0]; $('elev-kicker').textContent = t('mail.key'); }
    $('elev-input').setAttribute('aria-label', $('elev-label').textContent);
    $('elev-note').textContent = vs ? t('p414.e.vsNote') : (c.interpreter ? t('elev.interpreter') + ' ' : '') + t(nopw ? 'elev.sudo.noteHelper' : sudo ? 'elev.sudo.note' : 'elev.secret.note');
    // P414 (§37.10): 「用 Face ID 填入电脑密码」 when this remote holds the vault (the field stays as the second way); else, when
    // it could hold it, 「用 Face ID 记住」 (on by default); `stale` says the remembered one stopped working
    const useV = useVault(c);
    $('elev-vault').hidden = !useV;
    $('elev-vault').textContent = t('p414.e.use');
    $('elev-allow').classList.toggle('aj-btn--primary', !useV);
    if (useV) $('elev-label').textContent = t('p414.e.orType');
    const save = offerSave(c);
    $('elev-save-opt').hidden = !save; $('elev-save-on').checked = save;
    if (useV) $('elev-note').textContent = t('p414.e.useNote');
    else if (save) $('elev-note').textContent = t('p414.e.saveNote');   // 「只用这一次，不保存」 is not true when it remembers
    $('elev-save-text').textContent = t('p414.e.save');
    $('elev-stale').hidden = !(sudo && (c.stale || c.vaultMiss));
    $('elev-stale').textContent = c.stale ? t('p414.e.stale') : c.vaultMiss ? t('p414.e.noPrf') : '';
    const offer = sudo && !nopw && c.grant;   // #116: off by default on every card shown
    $('elev-grant-opt').hidden = !offer; $('elev-grant-on').checked = false;
    if (offer) {
      $('elev-grant-opt-main').textContent = t('elevGrant.toggle', { n: Math.round(c.grant.secs / 60), prog: c.grant.prog });
      $('elev-grant-opt-fmt').textContent = t('elevGrant.toggleFmt', { fmt: c.grant.fmt });
    }
    if (mail) $('elev-note').textContent = t('mail.sealNote');
    denyArmed = 0;
    $('elev-empty').hidden = true;
    $('elev-deny').textContent = t(sudo || vs ? 'elev.deny' : 'elev.secret.cancel');
    $('elev-allow').textContent = vs ? t('p414.e.vsAllow') : t(nopw ? 'elev.sudo.allowHelper' : sudo ? 'elev.sudo.allow' : 'elev.secret.allow');
    $('elev-hide').setAttribute('aria-label', t('p406.sudo.hide')); $('elev-hide').title = t('p406.sudo.hide');
    root.hidden = false;
    if (!nopw && !useV) setTimeout(() => { try { $('elev-input').focus({ preventScroll: true }); } catch { /* old browsers */ } }, 60);
    else if (document.querySelector('.gdlg:not([hidden])')) setTimeout(() => $('elev-allow')?.focus({ preventScroll: true }), 60);   // P549 (#316)
  }
  const bad = $('elev-bad');
  bad.hidden = !c.bad;
  bad.textContent = c.bad ? t('elev.sudo.bad', { n: c.tries }) : '';
  paintButtons();
  paintLeft(c);
  paintPkFor(c);
}
/** P539: under 「同意」 — the quiet line when this remote has no passkey of its own, or why the last tap failed + 再试一次. */
function paintPkFor(c) {
  if (!root) return;
  paintPk($('elev-pk'), c && c.fa && !c.running ? { fa: c.fa_s, err: c.faErr || null, retry: () => decide(true, c.faHow) } : {});
}
/** P414: this remote holds the vault for its passkey (M5: `prf` wraps only) and the card asks for a typed password. */
const useVault = (c) => c.kind === 'sudo' && !!c.v && c.v.has && !!c.fa && !viaHelper(c);
/** P414: it could remember a typed one (a passkey; the browser does not say it lacks PRF). */
const offerSave = (c) => c.kind === 'sudo' && !!c.v && !c.v.has && !!c.fa && !viaHelper(c) && prfCap !== false;
/** The computer's admin helper accepts this phone's signature: 同意 alone, no password (elevate_helper on the host). */
const viaHelper = (c) => c.kind === 'sudo' && !c.interpreter && !c.bad && c.helper && c.helper.length > 0 && c.helper.includes(myIdSync());
function paintButtons() {
  if (!root) return;
  const c = cards.get(shownId);
  const v = $('elev-input').value;
  const pending = busy || !!c?.running;
  if (c) $('elev-allow').textContent = c.running ? t('p392.executing') : c.kind === 'vshare' ? t('p414.e.vsAllow') : t(viaHelper(c) ? 'elev.sudo.allowHelper' : c.kind === 'sudo' ? 'elev.sudo.allow' : 'elev.secret.allow');
  $('elev-allow').disabled = pending || !c || (!viaHelper(c) && v.length > MAX_FIELD);   // empty: enabled, says what is missing
  $('elev-deny').disabled = pending;
  $('elev-input').disabled = pending;
  $('elev-grant-on').disabled = pending;
  $('elev-vault').disabled = pending; $('elev-save-on').disabled = pending;   // P414
}
function paintLeft(c) {
  if (c.running) { recoverRunning(); $('elev-left').textContent = t('p392.executing'); return; }
  const deadline = c.auth ? Math.min(c.deadline, c.auth.expires_at * 1000) : c.deadline;
  const left = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
  $('elev-left').textContent = left > 0 ? t('elev.left', { n: left }) : t('elev.timeUp');
  if (c.auth) {
    $('elev-left').textContent += ' · ' + t('ar.round', { n: c.auth.round, max: c.auth.rounds });
    for (const n of $('elev-auth').querySelectorAll('a,button')) { if (!left) { n.removeAttribute('href'); n.setAttribute('aria-disabled','true'); if (n.tagName === 'BUTTON') n.disabled = true; } }
  }
  if (left > 0) timer = setTimeout(() => { if (cards.get(c.id) === c) paintLeft(c); }, 1000);
}

/** 「不提供」 on a secret card needs a second tap within 4 s (the label asks 「确定不提供？」); sudo's 拒绝 is one tap. */
function askDeny() {
  const c = cards.get(shownId);
  if (!c || busy || c.running) return;
  if (c.kind === 'secret' && Date.now() > denyArmed) {
    denyArmed = Date.now() + 4000;
    $('elev-deny').textContent = t('elev.secret.cancelSure');
    setTimeout(() => { if (Date.now() >= denyArmed && cards.get(shownId) === c) { denyArmed = 0; $('elev-deny').textContent = t('elev.secret.cancel'); } }, 4100);
    return;
  }
  denyArmed = 0;
  decide(false);
}

/** `how` (P414): 'vault' = 「用 Face ID 填入电脑密码」 (no field). A `vshare` card's 「同意」 is the same Face ID + PRF. */
async function decide(ok, how) {
  const c = cards.get(shownId);
  if (!c || busy || c.running) return;
  const input = $('elev-input');
  const nopw = viaHelper(c);
  const vaultUse = ok && (c.kind === 'vshare' || (how === 'vault' && useVault(c)));   // P414: the PRF value instead of a typed one
  const typed = ok && !nopw && !vaultUse && c.kind !== 'vshare';
  if (typed && !(c.kind === 'secret' ? input.value.trim() : input.value)) {   // nothing typed: say so, never send false
    const e = $('elev-empty');
    e.textContent = t(c.kind === 'secret' ? 'elev.secret.empty' : 'elev.sudo.empty', { name: c.name === 'AGENTJ_MAIL_PASSWORD' ? $('elev-label').textContent : c.name || '' });
    e.hidden = false;
    try { input.focus({ preventScroll: true }); } catch { /* old browsers */ }
    return;
  }
  const g = gen();
  if (!g) { toast(t('elev.net'), 3000); return; }
  // ADR-A163: Face ID first, straight from the tap (before any await). Cancel / failure → nothing sent, the card stays.
  // P414: the same assertion carries the PRF — over the wrap salt (+ the next salt) to use the vault, over `vs` to remember a
  // typed password (「用 Face ID 记住」 ticked).
  const save = typed && c.kind === 'sudo' && offerSave(c) && $('elev-save-on').checked;
  let fa = null, prf = null;
  if (ok && c.passkey_required && !c.fa) { toast(t('ask.passkeyRequired'), 6000); return; }
  if (ok && (c.fa || vaultUse)) {
    if (!canAssert() && phoneConfirmation()) return;
    if (!canAssert() || !c.fa) { toast(t('elev.fa.none'), 5000); return; }
    if (!c.faCh) { toast(t('elev.fail'), 3000); return; }
    busy = true; paintButtons();
    let faErr = null;
    try {
      if (vaultUse || save) { const r = await assertPrf(c.faCh, c.fa, c.v.vs, vaultUse ? c.v.vn : null); fa = r.fa; prf = r.prf; }
      else fa = await approve(c.faCh, c.fa);
    } catch (e) { fa = null; faErr = errName(e); }
    if (!fa || cards.get(c.id) !== c) {
      prf?.fill(0);
      busy = false; paintButtons();
      if (!fa) { c.faErr = faErr || 'Error'; c.faHow = how; paintPkFor(c); toast(t('elev.fa.cancel'), 3000); }
      return;
    }
    if (c.faErr) { c.faErr = null; paintPkFor(c); }
    if (vaultUse && !prf) {   // this authenticator gave no PRF: nothing sent, the typed field is the way
      busy = false; paintButtons();
      toast(t(c.kind === 'vshare' ? 'p414.e.vsNoPrf' : 'p414.e.noPrf'), 5000);
      return;
    }
  }
  const sk = await signKey();
  if (!sk) { prf?.fill(0); busy = false; paintButtons(); toast(t('elev.noSign'), 3600); return; }
  busy = true; paintButtons();
  let value = null;
  try {
    const ch = channel(), dev = await myDeviceId();
    const digest = await elevateDigest(c.kind, elevateFields(c));
    const ts = Date.now();
    const m = { t: 'elev_answer', id: c.id, ok, n: c.n, ts };
    let ctSha = null, saveSha = null;
    const grant = ok && !nopw && c.grant && $('elev-grant-on').checked ? c.grant.h : null;   // #116: only when switched on
    if (vaultUse) {
      // §37.10 / §37.11: the PRF value (prf1, or prf1 ‖ prf2 with the next salt) sealed kind `vault` / `vshare`; zeroed
      const s = await sealPrf(c.epk, prf, ch, dev, c.id, c.kind === 'vshare' ? 'vshare' : 'vault', c.n, digest);
      prf = null;
      m.epk = b64u(s.epk); m.ct = b64u(s.ct); ctSha = s.ctSha;
      if (c.kind === 'sudo') m.vault = 1;
    } else if (typed) {
      value = new TextEncoder().encode(c.kind === 'secret' ? input.value.trim() : input.value);
      input.value = '';
      const s = await sealValue(c.epk, value, ch, dev, c.id, c.kind, c.n, digest);
      value.fill(0);
      m.epk = b64u(s.epk); m.ct = b64u(s.ct); ctSha = s.ctSha;
      if (prf) {   // M2: 「用 Face ID 记住」 — bound to this answer (AAD digest|sha256(ct)) and signed (save:<sha256(pct)>)
        const p2 = await sealPrf(c.epk, prf, ch, dev, c.id, 'vault_save', c.n, digest + '|' + ctSha);
        prf = null;
        m.pepk = b64u(p2.epk); m.pct = b64u(p2.ct); saveSha = await sha256hex(p2.ct);
      }
    }
    input.value = '';
    const sig = new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, sk.priv, elevateMessage(ch, dev, c.id, c.kind, ok ? 'allow' : 'deny', c.n, ts, digest, ctSha, grant, saveSha)));
    m.sig = b64u(sig);
    if (fa) m.fa = fa;
    if (grant) m.grant = grant;   // #116: inside the signature too — the audit proves the owner chose it
    await sendApp(m, g);
    if (m.vault) c.sentVault = true;   // P414: a re-armed copy without `stale` then means this Face ID did not open it
    if (ok) { c.running = true; clearTimeout(timer); paintButtons(); paintLeft(c); }
    toast(t(!ok ? 'elev.sent.deny' : c.kind === 'vshare' ? 'p414.e.vsSent' : c.kind === 'sudo' ? 'elev.sent.sudo' : 'elev.sent.secret'), 2600);
  } catch {
    busy = false; paintButtons();
    toast(t('elev.fail'), 3000);
  } finally {
    if (value) value.fill(0);
    prf?.fill(0);
    input.value = '';
  }
}

// #116 (PROTOCOL §11.1): the host's 10-minute same-class approvals. One quiet bar while any is active, with
// 「收回授权」; a command that ran under it is announced (the chat keeps the host's own record).
let grantList = [], grantBar = null, grantTimer = 0;
function bar() {
  if (grantBar) return grantBar;
  grantBar = mk('div', 'elev-grant'); grantBar.id = 'elev-grant'; grantBar.hidden = true;
  grantBar.setAttribute('role', 'status');
  const text = mk('span', 'elev-grant-text'); text.id = 'elev-grant-text';
  const btn = mk('button', 'aj-btn aj-btn--quiet elev-grant-revoke'); btn.id = 'elev-grant-revoke'; btn.type = 'button';
  btn.addEventListener('click', () => revokeAll());
  grantBar.append(text, btn);
  replyNotice(grantBar);
  onLang(() => paintGrants());
  return grantBar;
}
function paintGrants() {
  clearTimeout(grantTimer);
  const now = Date.now();
  grantList = grantList.filter((x) => x.until > now);
  const b = bar();
  if (!grantList.length) { b.hidden = true; return; }
  const left = Math.max(...grantList.map((x) => x.until)) - now;
  const names = [...new Set(grantList.map((x) => x.exe))].join(', ');
  b.querySelector('#elev-grant-text').textContent = t('elevGrant.active', { exe: names, n: Math.max(1, Math.ceil(left / 60000)) });
  b.querySelector('#elev-grant-revoke').textContent = t('elevGrant.revoke');
  b.hidden = false;
  grantTimer = setTimeout(paintGrants, Math.min(15000, left + 50));
}
/** `elev_grants`: the whole current list (an empty list hides the bar). */
export function grants(m) {
  const items = Array.isArray(m) ? m : Array.isArray(m?.items) ? m.items : [];
  const now = Date.now();
  grantList = items.filter((x) => x && typeof x.g === 'string' && /^[0-9a-f]{16}$/.test(x.g) && typeof x.exe === 'string' && x.exe.length <= 128
    && Number.isInteger(x.left) && x.left > 0 && x.left <= 3600).map((x) => ({ g: x.g, exe: x.exe, until: now + x.left * 1000 }));
  if (grantBar || grantList.length) paintGrants();
}
export const grantIds = () => grantList.map((x) => x.g);
async function revokeAll() {
  const g = gen();
  if (!g) { toast(t('elev.net'), 3000); return; }
  try {
    await sendApp({ t: 'elev_revoke' }, g);
    grants([]);
    toast(t('elevGrant.revoked'), 3600);
  } catch { toast(t('elev.fail'), 3000); }
}
/** `elev_auto`: a command ran under an approval, without a card. */
export function auto(m) {
  if (typeof m?.id !== 'string' || !/^[0-9a-f]{32}$/.test(m.id) || completed.has(m.id) || typeof m.exe !== 'string') return;
  completed.add(m.id);
  if (completed.size > 100) completed.delete(completed.values().next().value);
  const fail = m.result !== 'done' || (Number.isInteger(m.code) && m.code !== 0);
  notify({ kind: fail ? 'error' : 'info', text: t(fail ? 'elevGrant.autoFail' : 'elevGrant.autoRan', { exe: m.exe.slice(0, 64) }), ms: 3600, shape: 'rect' });
}

// P406 #173 (PROTOCOL §11.2): collapse never declines. A collapsed card, a command that runs and a just-finished result live in
// one status strip above the composer: tap → the card again (asking / checking) or the masked output (running / finished);
// 「停止」 (two taps) sends a signed `elev_stop`; the result stays a while with its output tail.
const OUT_KEEP = 16000;
let runEl = null, runShown = null, panelOpen = false, finished = null, runTimer = 0, stopArmed = 0;
function chevron() {
  const v = document.createElementNS(SVG, 'svg');
  v.setAttribute('viewBox', '0 0 24 24'); v.setAttribute('aria-hidden', 'true'); v.setAttribute('fill', 'none');
  v.setAttribute('stroke', 'currentColor'); v.setAttribute('stroke-width', '2'); v.setAttribute('stroke-linecap', 'round');
  v.setAttribute('stroke-linejoin', 'round');
  const p = document.createElementNS(SVG, 'path'); p.setAttribute('d', 'm6 9 6 6 6-6'); v.appendChild(p);
  return v;
}
function collapse() {
  const c = cards.get(shownId);
  if (!c) return;
  c.collapsed = true;
  try { document.activeElement?.blur?.(); } catch { /* old browsers */ }
  render();
  toast(t('p406.sudo.collapsed'), 3000);
}
function runBar() {
  if (runEl) return runEl;
  runEl = mk('div', 'elev-run'); runEl.id = 'elev-run'; runEl.hidden = true;
  runEl.setAttribute('role', 'status');
  const panel = mk('div', 'elev-run-panel'); panel.id = 'elev-run-panel'; panel.hidden = true;
  const out = mk('pre', 'elev-run-out'); out.id = 'elev-run-out'; out.tabIndex = 0;
  const note = mk('p', 'elev-run-note'); note.id = 'elev-run-note';
  panel.append(copyable(out, { variant: 'block', click: false }), note);   // P292: the masked output can be copied (⧉)
  const row = mk('div', 'elev-run-row');
  const main = mk('button', 'elev-run-main'); main.id = 'elev-run-main'; main.type = 'button';
  const text = mk('span', 'elev-run-text'); text.id = 'elev-run-text';
  const ch = mk('span', 'elev-run-ch'); ch.append(chevron());
  main.append(mk('span', 'elev-run-dot'), text, ch);
  const stop = mk('button', 'aj-btn aj-btn--quiet elev-run-stop'); stop.id = 'elev-run-stop'; stop.type = 'button';
  const x = mk('button', 'aj-btn aj-btn--quiet elev-run-x'); x.id = 'elev-run-x'; x.type = 'button';
  row.append(main, stop, x);
  runEl.append(panel, row);
  main.addEventListener('click', () => tapRun());
  stop.addEventListener('click', () => stopRun());
  x.addEventListener('click', () => { finished = null; panelOpen = false; paintRun(); });
  replyNotice(runEl);
  // the open output panel is a layer: ESC / Back folds it (the command keeps running)
  registerLayer({ element: () => runEl, open: () => !!runEl && !runEl.hidden && panelOpen, rank: 54, trap: false,
    fallback: () => document.getElementById('input'), close: () => fold() });
  onLang(() => paintRun());
  return runEl;
}
const clock = (ms) => { const s = Math.max(0, Math.floor(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
function resultText(f) {
  if (f.result === 'done') return Number.isInteger(f.code) && f.code !== 0 ? t('p406.sudo.doneFail', { code: f.code }) : t('p479.sudo.done', { code: f.code ?? '—' });
  return t({ stopped: 'p406.sudo.stopped', timeout: 'p406.sudo.timeout' }[f.result] || 'p406.sudo.failed');
}
function paintOut(text) {
  const out = runEl.querySelector('#elev-run-out');
  const end = out.scrollHeight - out.scrollTop - out.clientHeight < 24;
  out.textContent = text || t('p406.sudo.noOutput');
  if (end) out.scrollTop = out.scrollHeight;
}
function paintRun() {
  const b = runBar();
  clearTimeout(runTimer);
  const list = [...cards.values()].filter((c) => c.started || c.collapsed);
  const it = list.find((c) => c.collapsed && !c.running) || list.find((c) => c.started) || list[0] || null;
  const fin = !it && finished && (panelOpen && runShown === finished.id || Date.now() < finished.until) ? finished : null;
  if (!it && !fin) { b.hidden = true; panelOpen = false; runShown = null; document.body.dataset.elevrun = '0'; return; }
  const id = it ? it.id : fin.id;
  if (runShown !== id) { panelOpen = false; stopArmed = 0; }
  runShown = id;
  const state = it ? (it.started ? (it.stopping ? 'stopping' : 'running') : it.running ? 'verify' : 'ask') : 'done';
  b.dataset.state = state;
  if (fin) b.dataset.ok = fin.result === 'done' && fin.code === 0 ? '1' : '0'; else delete b.dataset.ok;
  let text = state === 'ask' ? t(it.kind === 'sudo' ? 'p406.sudo.waiting' : 'p406.secret.waiting')
    : state === 'verify' ? t('p406.sudo.verifying')
    : state === 'running' ? t('p406.sudo.running', { time: clock(Date.now() - (it.t0 || Date.now())) })
    : state === 'stopping' ? t('p406.sudo.stopping') : resultText(fin);
  if (list.length > 1) text += ' ' + t('p406.sudo.more', { n: list.length - 1 });
  b.querySelector('#elev-run-text').textContent = text;
  const main = b.querySelector('#elev-run-main');
  // P406 F1: output only after this phone asked for it (signed elev_watch); a result nobody watched has no tail to show
  const outputs = state === 'running' || state === 'stopping' || (state === 'done' && !!fin.tail);
  main.setAttribute('aria-expanded', outputs && panelOpen ? 'true' : 'false');
  main.setAttribute('aria-label', text + ' · ' + t(outputs ? 'p406.sudo.expand' : 'p406.sudo.open'));
  const stop = b.querySelector('#elev-run-stop');
  stop.hidden = !(state === 'running' || state === 'stopping') || it.kind !== 'sudo';
  stop.disabled = state === 'stopping';
  stop.textContent = Date.now() < stopArmed ? t('p406.sudo.stopSure') : t('p406.sudo.stop');
  const x = b.querySelector('#elev-run-x');
  x.hidden = state !== 'done'; x.textContent = t('p406.sudo.dismiss');
  const panel = b.querySelector('#elev-run-panel');
  panel.hidden = !(outputs && panelOpen);
  b.classList.toggle('open', !panel.hidden);
  b.querySelector('.elev-run-ch').hidden = !outputs && state === 'done';
  if (!panel.hidden) {
    paintOut(it ? it.out : fin.tail);
    b.querySelector('#elev-run-note').textContent = ((it || fin).cmd ? (it || fin).cmd + ' · ' : '') + t('p406.sudo.masked');
  }
  b.hidden = false;
  document.body.dataset.elevrun = '1';
  if (state === 'running' || state === 'stopping' || state === 'verify') recoverRunning();   // P392: a lost elev_done is re-read
  if (state === 'running' || state === 'stopping') runTimer = setTimeout(paintRun, 1000);
  else if (state === 'done' && !panelOpen) runTimer = setTimeout(paintRun, Math.max(50, finished.until - Date.now() + 20));
}
function tapRun() {
  const c = cards.get(runShown);
  if (c && !c.started) { c.collapsed = false; render(); return; }   // the card again, exactly as it was (nothing was declined)
  if (!c && !(finished && finished.tail)) return;
  if (panelOpen) return fold();
  panelOpen = true;
  if (c) watch(c, true);
  paintRun();
}
function fold() {
  panelOpen = false;
  const c = cards.get(runShown);
  if (c?.watching) watch(c, false);
  if (finished) finished.until = Date.now() + 4000;
  paintRun();
}
/** P406 F1 (§11.2): ask the host for this command's masked output (on) or stop it (off) — a signed `elev_watch`. */
async function watch(c, on) {
  c.watching = on;
  try { await sendApp(await signedCtl('elev_watch', { id: c.id, on }, { t: 'elev_watch', id: c.id, on })); }
  catch { if (on) { c.watching = false; toast(t('elev.fail'), 3000); } }
}
async function signedCtl(action, target, extra) {
  const g = gen(), sk = await signKey();
  if (!g) throw new Error('offline');
  if (!sk) throw new Error('no_key');
  const n = randHex(16), ts = Date.now();
  const msg = await controlMessage(channel(), await myDeviceId(), action, n, ts, target);
  return { ...extra, n, ts, sig: b64u(new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, sk.priv, msg))) };
}
async function stopRun() {
  const c = cards.get(runShown);
  if (!c || !c.started || c.stopping || c.kind !== 'sudo') return;
  if (Date.now() >= stopArmed) { stopArmed = Date.now() + 4000; paintRun(); setTimeout(() => { if (Date.now() >= stopArmed) paintRun(); }, 4100); return; }
  stopArmed = 0;
  const g = gen();
  if (!g) { toast(t('elev.net'), 3000); return; }
  const sk = await signKey();
  if (!sk) { toast(t('elev.noSign'), 3600); return; }
  try {
    const n = randHex(16), ts = Date.now();
    const msg = await controlMessage(channel(), await myDeviceId(), 'elev_stop', n, ts, { id: c.id });
    const sig = b64u(new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, sk.priv, msg)));
    await sendApp({ t: 'elev_stop', id: c.id, n, ts, sig }, g);
    c.stopping = true;
    paintRun();
    toast(t('p406.sudo.stopSent'), 2600);
  } catch { toast(t('elev.fail'), 3000); }
}
