// F17 (PROTOCOL §11): the sudo card and the secret card. The Agent asked for an admin password (to run one exact command)
// or for an API key (to be saved on the computer); the human types it here into a masked field. It is sealed to the card's
// one-time host key and signed with this device's approval key, then the field and the bytes are cleared. Nothing of it is
// kept, logged, drafted or shown again — the Agent only ever gets the command's output or a receipt.
import { sendApp, gen, channel } from './session.js';
import { elevateFields, elevateDigest, elevateMessage, sealValue, b64u, unb64u } from '../proto/wire.js';
import { signKey } from './store.js';
import { myDeviceId, myIdSync } from './api.js';
import { t, onLang } from './t.js';
import { mk, toast } from './ui.js';
import { elevateChallenge, canAssert, approve } from './faceid.js';

const cards = new Map();           // id → card (in arrival order)
let root = null, timer = 0, busy = false, shownId = null, denyArmed = 0;
const MAX_FIELD = 8192;

function build() {
  if (root) return root;
  root = mk('div', 'ajmodal elev');
  root.id = 'elev';
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
  // thumb aiming at 「保存到电脑」 cannot land on it. The keyboard's Enter / 完成 key only ever submits.
  const acts = mk('div', 'ajrow confirm-row elev-acts');
  const allow = node('button', 'elev-allow', 'aj-btn aj-btn--primary'); allow.type = 'submit';
  acts.append(allow);
  const deny = node('button', 'elev-deny', 'aj-btn aj-btn--quiet elev-deny'); deny.type = 'button';
  form.append(node('p', 'elev-kicker', 'elev-kicker'), node('h2', 'elev-title'), node('dl', 'elev-rows', 'elev-rows'), bad, label, input,
    empty, node('p', 'elev-note', 'elev-note'), node('p', 'elev-left', 'elev-left'), acts, deny);
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
  if (!/^[0-9a-f]{32}$/.test(m.n || '') || typeof m.epk !== 'string') return;
  let epk;
  try { epk = unb64u(m.epk); } catch { return; }
  if (epk.length !== 32) return;
  const ttl = Number.isInteger(m.ttl) && m.ttl >= 0 && m.ttl <= 600 ? m.ttl : 120;
  const c = { id: m.id, kind: m.kind, n: m.n, epk, deadline: Date.now() + ttl * 1000, bad: Number.isInteger(m.bad) ? m.bad : 0,
    tries: Number.isInteger(m.tries) ? m.tries : 3 };
  if (m.kind === 'sudo') {
    c.cmd = str(m.cmd, 2000); c.why = str(m.why, 300); c.effect = str(m.effect ?? '', 300);
    if (c.cmd === null || c.why === null || c.effect === null) return;
    c.helper = Array.isArray(m.helper) ? m.helper.filter((x) => typeof x === 'string' && x.length <= 64).slice(0, 64) : [];
  } else {
    c.name = str(m.name, 128); c.purpose = str(m.purpose, 300); c.dest = str(m.dest, 1100); c.verify = str(m.verify ?? '', 800);
    if (!c.name || c.purpose === null || c.dest === null || c.verify === null) return;
  }
  // ADR-A163: this device saved a passkey (F20) → 「同意」 needs Face ID. The host names our own credential id; the challenge
  // (card id + this arming's nonce + what is shown) is made now, so the tap can go straight to the system sheet.
  if (typeof m.fa === 'string' && m.fa.length > 0 && m.fa.length <= 1400) {
    c.fa = m.fa;
    (async () => {
      const digest = await elevateDigest(c.kind, elevateFields(c));
      c.faCh = await elevateChallenge(channel(), await myDeviceId(), c.id, c.kind, c.n, digest);
    })().catch(() => { c.faCh = null; });
  }
  const old = cards.get(m.id);
  cards.set(m.id, c);
  if (old && shownId === m.id) { shownId = null; busy = false; }
  render();
}
const RESULT = { done: 'elev.r.done', saved: 'elev.r.saved', denied: 'elev.r.denied', timeout: 'elev.r.timeout', gone: 'elev.r.gone',
  stopped: 'elev.r.stopped', locked: 'elev.r.locked', bad_password: 'elev.r.badPassword', failed: 'elev.r.failed' };
/** The card ended (§11 `elev_done`). */
export function done(m) {
  const c = cards.get(m.id);
  if (!c) return;
  cards.delete(m.id);
  if (shownId === m.id) { shownId = null; busy = false; }
  const key = RESULT[m.result];
  if (key) {
    let text = t(key, { code: m.code ?? '' });
    if (m.result === 'done' && Number.isInteger(m.code) && m.code !== 0) text = t('elev.r.doneFail', { code: m.code });
    if (c.kind === 'secret' && m.result === 'done') text = t('elev.r.saved');
    if (m.verify === 'ok') text += ' ' + t('elev.r.verifyOk');
    if (m.verify === 'fail') text += ' ' + t('elev.r.verifyFail');
    toast(text, 3600);
  }
  render();
}
/** The computer did not accept this approval's Face ID (§16.1 `elev_refused`): the card stays open, nothing was done. */
export function refused(m) {
  const c = cards.get(m.id);
  if (!c) return;
  if (shownId === m.id) { busy = false; paintButtons(); }
  toast(t('elev.fa.refused'), 3600);
}
/** Unpair / revoke / a new pairing: forget every card at once. */
export function clear() {
  cards.clear(); shownId = null; busy = false;
  if (root) { $('elev-input').value = ''; root.hidden = true; }
  clearTimeout(timer);
}
export const openCount = () => cards.size;
export const isOpen = () => !!root && !root.hidden;

function row(dl, label, value, cls) {
  if (!value) return;
  dl.appendChild(mk('dt', '', label));
  const dd = mk('dd', cls || '', value);
  dl.appendChild(dd);
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
    $('elev-label').hidden = nopw; $('elev-input').hidden = nopw;
    root.classList.toggle('secret', !sudo);
    $('elev-kicker').textContent = t(sudo ? 'elev.sudo.kicker' : 'elev.secret.kicker');
    $('elev-title').textContent = sudo ? t('elev.sudo.title') : t('elev.secret.title', { name: c.name });
    const dl = $('elev-rows'); dl.replaceChildren();
    if (sudo) {
      row(dl, t('elev.sudo.cmd'), c.cmd, 'mono');
      row(dl, t('elev.sudo.why'), c.why);
      row(dl, t('elev.sudo.effect'), c.effect);
    } else {
      row(dl, t('elev.secret.name'), c.name, 'mono');
      row(dl, t('elev.secret.purpose'), c.purpose);
      row(dl, t('elev.secret.dest'), c.dest, 'mono');
      row(dl, t('elev.secret.verify'), c.verify, 'mono');
    }
    $('elev-label').textContent = t(sudo ? 'elev.sudo.label' : 'elev.secret.label', { name: c.name || '' });
    $('elev-input').setAttribute('aria-label', $('elev-label').textContent);
    $('elev-note').textContent = t(nopw ? 'elev.sudo.noteHelper' : sudo ? 'elev.sudo.note' : 'elev.secret.note');
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
const viaHelper = (c) => c.kind === 'sudo' && !c.bad && c.helper && c.helper.length > 0 && c.helper.includes(myIdSync());
function paintButtons() {
  if (!root) return;
  const c = cards.get(shownId);
  const v = $('elev-input').value;
  $('elev-allow').disabled = busy || !c || (!viaHelper(c) && v.length > MAX_FIELD);   // empty: enabled, says what is missing
  $('elev-deny').disabled = busy;
  $('elev-input').disabled = busy;
}
function paintLeft(c) {
  const left = Math.max(0, Math.ceil((c.deadline - Date.now()) / 1000));
  $('elev-left').textContent = left > 0 ? t('elev.left', { n: left }) : t('elev.timeUp');
  if (left > 0) timer = setTimeout(() => { if (cards.get(c.id) === c) paintLeft(c); }, 1000);
}

/** 「不提供」 on a secret card needs a second tap within 4 s (the label asks 「确定不提供？」); sudo's 拒绝 is one tap. */
function askDeny() {
  const c = cards.get(shownId);
  if (!c || busy) return;
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
  if (!c || busy) return;
  const input = $('elev-input');
  const nopw = viaHelper(c);
  if (ok && !nopw && !(c.kind === 'secret' ? input.value.trim() : input.value)) {   // nothing typed: say so, never send false
    const e = $('elev-empty');
    e.textContent = t(c.kind === 'secret' ? 'elev.secret.empty' : 'elev.sudo.empty', { name: c.name || '' });
    e.hidden = false;
    try { input.focus({ preventScroll: true }); } catch { /* old browsers */ }
    return;
  }
  const g = gen();
  if (!g) { toast(t('elev.net'), 3000); return; }
  // ADR-A163: Face ID first, straight from the tap (before any await). Cancel / failure → nothing sent, the card stays.
  let fa = null;
  if (ok && c.fa) {
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
    if (ok && !nopw) {
      value = new TextEncoder().encode(c.kind === 'secret' ? input.value.trim() : input.value);
      input.value = '';
      const s = await sealValue(c.epk, value, ch, dev, c.id, c.kind, c.n, digest);
      value.fill(0);
      m.epk = b64u(s.epk); m.ct = b64u(s.ct); ctSha = s.ctSha;
    }
    input.value = '';
    const sig = new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, sk.priv, elevateMessage(ch, dev, c.id, c.kind, ok ? 'allow' : 'deny', c.n, ts, digest, ctSha)));
    m.sig = b64u(sig);
    if (fa) m.fa = fa;
    await sendApp(m, g);
    toast(t(ok ? (c.kind === 'sudo' ? 'elev.sent.sudo' : 'elev.sent.secret') : 'elev.sent.deny'), 2600);
  } catch {
    busy = false; paintButtons();
    toast(t('elev.fail'), 3000);
  } finally {
    if (value) value.fill(0);
    input.value = '';
  }
}
