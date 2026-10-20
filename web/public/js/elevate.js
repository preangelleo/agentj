import {phoneConfirmation} from './tvremote.js';
// F17 (PROTOCOL §11): the sudo card and the secret card. The Agent asked for an admin password (to run one exact command)
// or for an API key (to be saved on the computer); the human types it here into a masked field. It is sealed to the card's
// one-time host key and signed with this device's approval key, then the field and the bytes are cleared. Nothing of it is
// kept, logged, drafted or shown again — the Agent only ever gets the command's output or a receipt.
import { sendApp, gen, channel } from './session.js';
import { elevateFields, elevateDigest, elevateMessage, sealValue, b64u, unb64u } from '../proto/wire.js';
import { signKey } from './store.js';
import { myDeviceId, myIdSync } from './api.js';
import { t, onLang } from './t.js';
import { mk, toast, notify } from './ui.js';
import { registerLayer } from './layers.js';
import { copyable } from './copycode.js';   // P292
import { paintMailPassword } from './mail-password.js';
import { elevateChallenge, canAssert, approve } from './faceid.js';

const cards = new Map();           // id → card (in arrival order)
const completed = new Set();
let recoveryTimer = 0;
let recoverCards = () => Promise.resolve();
export function configure({ recover }) { if (typeof recover === 'function') recoverCards = recover; }
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
  // P142 (A16): a layer — ESC = the card's deny / 「不提供」 (a secret card's first press only arms it, as the button does)
  registerLayer({ element: () => root, open: () => !!root && !root.hidden, rank: 55, fallback: () => document.getElementById('input') || document.getElementById('setBtn'),
    close: () => document.getElementById('elev-deny')?.click() });
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
  form.append(node('p', 'elev-kicker', 'elev-kicker'), node('h2', 'elev-title'), node('dl', 'elev-rows', 'elev-rows'), bad, label, input,
    empty, opt, node('p', 'elev-note', 'elev-note'), node('p', 'elev-left', 'elev-left'), acts, deny);
  root.appendChild(form);
  document.body.appendChild(root);
  root.querySelector('#elev-form').addEventListener('submit', (e) => { e.preventDefault(); decide(true); });
  root.querySelector('#elev-deny').addEventListener('click', () => askDeny());
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
  if (typeof m.id !== 'string' || !/^[0-9a-f]{32}$/.test(m.id) || (m.kind !== 'sudo' && m.kind !== 'secret')) return;
  if ((m.state !== 'running' && !/^[0-9a-f]{32}$/.test(m.n || '')) || typeof m.epk !== 'string') return;
  let epk;
  try { epk = unb64u(m.epk); } catch { return; }
  if (epk.length !== 32) return;
  const ttl = Number.isInteger(m.ttl) && m.ttl >= 0 && m.ttl <= 600 ? m.ttl : 120;
  const c = { id: m.id, kind: m.kind, n: m.n, epk, deadline: Date.now() + ttl * 1000, bad: Number.isInteger(m.bad) ? m.bad : 0,
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
  } else {
    c.name = str(m.name, 128); c.purpose = str(m.purpose, m.name === 'AGENTJ_MAIL_PASSWORD' ? 2000 : 300); c.dest = str(m.dest, 1100); c.verify = str(m.verify ?? '', 800);
    if (!c.name || c.purpose === null || c.dest === null || c.verify === null) return;
  }
  // ADR-A163: this device saved a passkey (F20) → 「同意」 needs Face ID. The host names our own credential id; the challenge
  // (card id + this arming's nonce + what is shown) is made now, so the tap can go straight to the system sheet.
  if (m.state !== 'running' && typeof m.fa === 'string' && m.fa.length > 0 && m.fa.length <= 1400) {
    c.fa = m.fa;
    (async () => {
      const digest = await elevateDigest(c.kind, elevateFields(c));
      c.faCh = await elevateChallenge(channel(), await myDeviceId(), c.id, c.kind, c.n, digest);
    })().catch(() => { c.faCh = null; });
  }
  if (completed.has(m.id)) return;
  c.running = m.state === 'running';
  const old = cards.get(m.id);
  cards.set(m.id, c);
  if (old && shownId === m.id) { shownId = null; busy = false; }
  render();
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
  if (shownId === m.id) { shownId = null; busy = false; }
  const key = RESULT[m.result];
  if (key && c) {
    let text = t(key, { code: m.code ?? '' });
    if (m.result === 'done' && Number.isInteger(m.code) && m.code !== 0) text = t('elev.r.doneFail', { code: m.code });
    if (c.name === 'AGENTJ_MAIL_PASSWORD' && m.result === 'saved') text = t('mail.passwordSaved');
    if (c.kind === 'secret' && m.result === 'done') text = t('elev.r.saved');
    if (m.verify === 'ok') text += ' ' + t('elev.r.verifyOk');
    if (m.verify === 'fail') text += ' ' + t('elev.r.verifyFail');
    if (c.kind === 'secret' && m.key_hint === 'short') text += ' ' + t('elev.r.keyShort');   // P163: from the length only
    // P318: a clean save / run is the success format (the mail key: the centred ✓ circle, P256's place); a refusal is error
    const fail = ['denied', 'bad_password', 'failed', 'locked'].includes(m.result) || (m.result === 'done' && Number.isInteger(m.code) && m.code !== 0) || m.verify === 'fail';
    const ok = !fail && (m.result === 'saved' || m.result === 'done') && m.key_hint !== 'short';
    const mailSaved = c.name === 'AGENTJ_MAIL_PASSWORD' && m.result === 'saved';
    notify({ kind: fail ? 'error' : ok ? 'success' : 'info', text, ms: m.key_hint === 'short' ? 8000 : 3600, shape: mailSaved && ok ? 'circle' : undefined });
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
  completed.clear(); cards.clear(); shownId = null; busy = false;
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
  if (shownId === c.id) { busy = true; clearTimeout(timer); paintButtons(); paintLeft(c); }
}

function row(dl, label, value, cls) {
  if (!value) return;
  dl.appendChild(mk('dt', '', label));
  const dd = mk('dd', cls || '', value);
  dl.appendChild(cls === 'mono' ? copyable(dd, { variant: 'block' }) : dd);   // P292: a command / path / name can be copied (⧉ inside)
}
function render() {
  build();
  const c = cards.values().next().value;
  clearTimeout(timer);
  if (!c) { root.hidden = true; $('elev-input').value = ''; document.body.dataset.elev = '0'; return; }
  document.body.dataset.elev = '1';
  if (shownId !== c.id) {
    shownId = c.id;
    $('elev-input').value = '';
    const sudo = c.kind === 'sudo';
    const nopw = viaHelper(c);
    root.classList.toggle('nopw', nopw);
    root.classList.toggle('interpreter', !!c.interpreter);
    $('elev-label').hidden = nopw; $('elev-input').hidden = nopw;
    root.classList.toggle('secret', !sudo);
    $('elev-kicker').textContent = t(sudo ? 'elev.sudo.kicker' : 'elev.secret.kicker');
    $('elev-title').textContent = sudo ? t('elev.sudo.title') : t('elev.secret.title', { name: c.name });
    const dl = $('elev-rows'); dl.replaceChildren();
    if (sudo) {
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
    $('elev-label').textContent = t(sudo ? 'elev.sudo.label' : 'elev.secret.label', { name: c.name || '' });
    const mail = paintMailPassword(c, $('elev-label'), $('elev-input'), $('elev-rows'));
    if (mail) { $('elev-title').textContent = c.purpose.split('\n')[0]; $('elev-kicker').textContent = t('mail.key'); }
    $('elev-input').setAttribute('aria-label', $('elev-label').textContent);
    $('elev-note').textContent = (c.interpreter ? t('elev.interpreter') + ' ' : '') + t(nopw ? 'elev.sudo.noteHelper' : sudo ? 'elev.sudo.note' : 'elev.secret.note');
    const offer = sudo && !nopw && c.grant;   // #116: off by default on every card shown
    $('elev-grant-opt').hidden = !offer; $('elev-grant-on').checked = false;
    if (offer) {
      $('elev-grant-opt-main').textContent = t('elevGrant.toggle', { n: Math.round(c.grant.secs / 60), prog: c.grant.prog });
      $('elev-grant-opt-fmt').textContent = t('elevGrant.toggleFmt', { fmt: c.grant.fmt });
    }
    if (mail) $('elev-note').textContent = t('mail.sealNote');
    denyArmed = 0;
    $('elev-empty').hidden = true;
    $('elev-deny').textContent = t(sudo ? 'elev.deny' : 'elev.secret.cancel');
    $('elev-allow').textContent = t(nopw ? 'elev.sudo.allowHelper' : sudo ? 'elev.sudo.allow' : 'elev.secret.allow');
    root.hidden = false;
    if (!nopw) setTimeout(() => { try { $('elev-input').focus({ preventScroll: true }); } catch { /* old browsers */ } }, 60);
  }
  const bad = $('elev-bad');
  bad.hidden = !c.bad;
  bad.textContent = c.bad ? t('elev.sudo.bad', { n: c.tries }) : '';
  paintButtons();
  paintLeft(c);
}
/** The computer's admin helper accepts this phone's signature: 同意 alone, no password (elevate_helper on the host). */
const viaHelper = (c) => c.kind === 'sudo' && !c.interpreter && !c.bad && c.helper && c.helper.length > 0 && c.helper.includes(myIdSync());
function paintButtons() {
  if (!root) return;
  const c = cards.get(shownId);
  const v = $('elev-input').value;
  const pending = busy || !!c?.running;
  if (c) $('elev-allow').textContent = c.running ? t('p392.executing') : t(viaHelper(c) ? 'elev.sudo.allowHelper' : c.kind === 'sudo' ? 'elev.sudo.allow' : 'elev.secret.allow');
  $('elev-allow').disabled = pending || !c || (!viaHelper(c) && v.length > MAX_FIELD);   // empty: enabled, says what is missing
  $('elev-deny').disabled = pending;
  $('elev-input').disabled = pending;
  $('elev-grant-on').disabled = pending;
}
function paintLeft(c) {
  if (c.running) { recoverRunning(); $('elev-left').textContent = t('p392.executing'); return; }
  const left = Math.max(0, Math.ceil((c.deadline - Date.now()) / 1000));
  $('elev-left').textContent = left > 0 ? t('elev.left', { n: left }) : t('elev.timeUp');
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

async function decide(ok) {
  const c = cards.get(shownId);
  if (!c || busy || c.running) return;
  const input = $('elev-input');
  const nopw = viaHelper(c);
  if (ok && !nopw && !(c.kind === 'secret' ? input.value.trim() : input.value)) {   // nothing typed: say so, never send false
    const e = $('elev-empty');
    e.textContent = t(c.kind === 'secret' ? 'elev.secret.empty' : 'elev.sudo.empty', { name: c.name === 'AGENTJ_MAIL_PASSWORD' ? $('elev-label').textContent : c.name || '' });
    e.hidden = false;
    try { input.focus({ preventScroll: true }); } catch { /* old browsers */ }
    return;
  }
  const g = gen();
  if (!g) { toast(t('elev.net'), 3000); return; }
  // ADR-A163: Face ID first, straight from the tap (before any await). Cancel / failure → nothing sent, the card stays.
  let fa = null;
  if (ok && c.fa) {
    if (!canAssert() && phoneConfirmation()) return;
    if (!canAssert()) { toast(t('elev.fa.none'), 5000); return; }
    if (!c.faCh) { toast(t('elev.fail'), 3000); return; }
    busy = true; paintButtons();
    try { fa = await approve(c.faCh, c.fa); } catch { fa = null; }
    if (!fa || cards.get(c.id) !== c) {
      busy = false; paintButtons();
      if (!fa) toast(t('elev.fa.cancel'), 3000);
      return;
    }
  }
  const sk = await signKey();
  if (!sk) { busy = false; paintButtons(); toast(t('elev.noSign'), 3600); return; }
  busy = true; paintButtons();
  let value = null;
  try {
    const ch = channel(), dev = await myDeviceId();
    const digest = await elevateDigest(c.kind, elevateFields(c));
    const ts = Date.now();
    const m = { t: 'elev_answer', id: c.id, ok, n: c.n, ts };
    let ctSha = null;
    const grant = ok && !nopw && c.grant && $('elev-grant-on').checked ? c.grant.h : null;   // #116: only when switched on
    if (ok && !nopw) {
      value = new TextEncoder().encode(c.kind === 'secret' ? input.value.trim() : input.value);
      input.value = '';
      const s = await sealValue(c.epk, value, ch, dev, c.id, c.kind, c.n, digest);
      value.fill(0);
      m.epk = b64u(s.epk); m.ct = b64u(s.ct); ctSha = s.ctSha;
    }
    input.value = '';
    const sig = new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, sk.priv, elevateMessage(ch, dev, c.id, c.kind, ok ? 'allow' : 'deny', c.n, ts, digest, ctSha, grant)));
    m.sig = b64u(sig);
    if (fa) m.fa = fa;
    if (grant) m.grant = grant;   // #116: inside the signature too — the audit proves the owner chose it
    await sendApp(m, g);
    if (ok) { c.running = true; clearTimeout(timer); paintButtons(); paintLeft(c); }
    toast(t(ok ? (c.kind === 'sudo' ? 'elev.sent.sudo' : 'elev.sent.secret') : 'elev.sent.deny'), 2600);
  } catch {
    busy = false; paintButtons();
    toast(t('elev.fail'), 3000);
  } finally {
    if (value) value.fill(0);
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
  document.body.appendChild(grantBar);
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
  notify({ kind: fail ? 'error' : 'info', text: t(fail ? 'elevGrant.autoFail' : 'elevGrant.autoRan', { exe: m.exe.slice(0, 64) }), ms: 3600 });
}
