import { copyButton } from './copycode.js';
import { paintPk, faState, errName } from './pkassist.js';   // P539: no own passkey here · why a passkey tap failed
import {phoneConfirmation} from './tvremote.js';
// F32 (P73, ADR-A180, PROTOCOL §18): the secret pickup card. The owner asked their own Agent for something that holds a secret
// (an ss:// link, a proxy YAML with a password); the Agent ran `agentj secret send`. The card arrives WITHOUT the value: name,
// purpose, text / file, size, time left. 「用 Face ID 查看」 runs a WebAuthn assertion straight from the tap over (channel, device,
// card id, "secret_out", nonce, shown digest) — the host checks it and only then sends the value, to this session only. It is
// shown here, can be copied or downloaded, and is cleared when the card is closed (or after 2 minutes, or when the page is
// hidden). It is never written to localStorage / IndexedDB / the history: the chat keeps one host line 「已领取 … HH:MM」.
// A phone without a passkey cannot open it: the card says so and offers 「设置 Face ID」 (F20 registration), never a fallback.
import { sendApp, gen, channel } from './session.js';
import { put as handIn } from './pkhandin.js';   // P566 (#364): the answer outlives the dropped socket
import { myDeviceId } from './api.js';
import { t, onLang } from './t.js';
import { mk, toast, human } from './ui.js';
import { copyable } from './copycode.js';   // P292
import { registerLayer, TOP_RANK } from './layers.js';
import { createSecretPreview } from './secretpreview.js';
import { elevateChallenge, canAssert, approve, SECRET_OUT_CTX, SECRET_OUT_KIND, secretOutFields, secretOutDigest } from './faceid.js';

export const CTX = SECRET_OUT_CTX;
export const KIND = SECRET_OUT_KIND;
const SHOW_MS = 120000;            // an opened value is cleared after this long
const cards = new Map();           // id → card (arrival order)
let root = null, timer = 0, busy = false, shownId = null, opened = null, hooks = {}, previewUI = null;

/** app.js: {setupFaceId()} — ask the computer for a passkey offer and show the F20 「用 Face ID 记住这台手机？」 sheet. */
export function configure(h) { hooks = h || {}; }

export const shownFields = secretOutFields;
export const shownDigest = secretOutDigest;

function build() {
  if (root) return root;
  root = mk('div', 'ajmodal dlgcard sout');
  root.id = 'sout'; root.hidden = true;
  // P142 (A16): a layer — ESC = 「完成」 once opened (the value is wiped), else the card's 「不需要了」; shortcuts stay off
  registerLayer({ element: () => root, open: () => !!root && !root.hidden, rank: TOP_RANK, fallback: () => document.getElementById('input') || document.getElementById('setBtn'),
    close: () => { if (previewUI?.close()) return; const d = $('sout-done'); (d && !d.hidden ? d : $('sout-decline'))?.click(); } });
  root.setAttribute('role', 'dialog'); root.setAttribute('aria-modal', 'true'); root.setAttribute('aria-labelledby', 'sout-title');
  const box = mk('div', 'box');
  const node = (tag, id, cls) => { const n = mk(tag, cls); n.id = id; return n; };
  const val = node('div', 'sout-value', 'sout-value');
  val.hidden = true;
  previewUI = createSecretPreview(root, val, copyValue);
  const mask = node('p', 'sout-mask', 'sout-mask');
  const main = mk('div', 'ajrow sout-main');
  const open = node('button', 'sout-open', 'aj-btn aj-btn--primary'); open.type = 'button';
  const setup = node('button', 'sout-setup', 'aj-btn aj-btn--primary'); setup.type = 'button'; setup.hidden = true;
  const copy = node('button', 'sout-copy', 'aj-btn aj-btn--primary'); copy.type = 'button'; copy.hidden = true;
  const dl = node('button', 'sout-download', 'aj-btn'); dl.type = 'button'; dl.hidden = true;
  const fin = node('button', 'sout-done', 'aj-btn'); fin.type = 'button'; fin.hidden = true;
  main.append(open, setup, copy, dl, fin);
  // 「不要了」 is a quiet text button on its own line, away from the main action
  const decline = node('button', 'sout-decline', 'aj-btn aj-btn--quiet sout-decline'); decline.type = 'button';
  box.append(node('p', 'sout-kicker', 'elev-kicker'), node('h2', 'sout-title'), node('dl', 'sout-rows', 'elev-rows'), mask, val,
    node('p', 'sout-note', 'elev-note'), node('p', 'sout-left', 'elev-left'), main, node('div', 'sout-pk', 'pkassist'), decline);
  root.appendChild(box);
  document.body.appendChild(root);
  open.addEventListener('click', () => openCard());
  setup.addEventListener('click', () => { if (hooks.setupFaceId) hooks.setupFaceId(); else toast(t('sout.noFaceId'), 4000); });
  copy.addEventListener('click', (e) => copyValue(e));
  dl.addEventListener('click', () => download());
  fin.addEventListener('click', () => finish());
  decline.addEventListener('click', () => declineCard());
  onLang(() => { shownId = null; render(); });
  addEventListener('pagehide', () => { if (opened) finish(); });
  document.addEventListener('visibilitychange', () => { if (document.hidden && opened) finish(); });
  return root;
}
const $ = (id) => root.querySelector('#' + id);
const str = (v, n) => (typeof v === 'string' && v.length <= n ? v : null);

/** A card from the host (§18 `secret_out`); a re-sent one (new nonce, now with `fa`) replaces the old. */
export function add(m) {
  if (typeof m.id !== 'string' || !/^[0-9a-f]{32}$/.test(m.id) || !/^[0-9a-f]{32}$/.test(m.n || '')) return;
  if (m.kind !== 'text' && m.kind !== 'file') return;
  const c = { id: m.id, n: m.n, kind: m.kind, name: str(m.name, 80), purpose: str(m.purpose ?? '', 300),
    filename: str(m.filename ?? '', 128), size: Number.isInteger(m.size) && m.size >= 0 ? m.size : null,
    deadline: Date.now() + (Number.isInteger(m.ttl) && m.ttl >= 0 && m.ttl <= 600 ? m.ttl : 600) * 1000 };
  if (!c.name || c.purpose === null || c.filename === null || c.size === null) return;
  if (typeof m.fa === 'string' && m.fa.length > 0 && m.fa.length <= 1400) {
    c.fa = m.fa;
    c.fa_s = faState(m.fa_s);   // P539
    (async () => { c.faCh = await elevateChallenge(channel(), await myDeviceId(), c.id, KIND, c.n, await shownDigest(shownFields(c))); })()
      .catch(() => { c.faCh = null; });
  }
  const old = cards.get(m.id);
  // P566 (#364): the Agent J host re-sends open cards after a reconnect — the same nonce is the same card, its tap still under way
  const same = !!old && old.n === c.n;
  if (same) { c.sending = old.sending; c.faErr = old.faErr; }
  cards.set(m.id, c);
  if (old && shownId === m.id && !opened && !same) { shownId = null; busy = false; }
  render();
}

/** The value, after Face ID (§18 `secret_out_val`): only for the card on screen, only once. */
export function value(m) {
  const c = cards.get(m.id);
  if (!c || shownId !== m.id || opened) return;
  let text = null, bytes = null;
  if (c.kind === 'text' && typeof m.value === 'string') text = m.value;
  else if (c.kind === 'file' && typeof m.data === 'string') { try { bytes = Uint8Array.from(atob(m.data), (ch) => ch.charCodeAt(0)); } catch { bytes = null; } }
  if (text === null && bytes === null) return;
  opened = { id: c.id, text, bytes, filename: c.filename || 'secret.txt', at: Date.now() };
  busy = false;
  previewUI.reset();
  if (text !== null) previewUI.show(text, false);
  else {
    let preview = null;
    try { preview = new TextDecoder('utf-8', { fatal: true }).decode(bytes); } catch { preview = null; }
    if (preview !== null && preview.length <= 200000) {
      opened.preview = preview;
      previewUI.show(preview, /\.(?:md|markdown)$/i.test(opened.filename));
    }
    else previewUI.note(t('sout.binary', { size: human(bytes.length) }));
  }
  clearTimeout(opened.wipe);
  opened.wipe = setTimeout(() => finish(), SHOW_MS);
  render();
}

/** The card ended everywhere (§18 `secret_out_done`): picked (by this or another phone) / expired / declined / stopped / gone. */
export function done(m) {
  const c = cards.get(m.id);
  if (!c) return;
  if (opened && opened.id === m.id && m.result === 'picked') { c.picked = true; render(); return; }   // keep showing until 完成
  cards.delete(m.id);
  if (shownId === m.id) { shownId = null; busy = false; }
  const key = { picked: 'sout.r.picked', expired: 'sout.r.expired', declined: 'sout.r.declined', stopped: 'sout.r.stopped', gone: 'sout.r.gone' }[m.result];
  if (key) toast(t(key), 3600);
  render();
}

/** The computer refused (§18 `secret_out_err`): Face ID not accepted · no passkey on this phone · not delivered · gone. */
export function failed(m) {
  if (opened && opened.id === m.id) return;          // P566: a second hand-in of an answer whose value is already on screen
  const c = cards.get(m.id);
  if (c) c.sending = false;
  if (shownId === m.id) busy = false;
  const key = { passkey: 'elev.fa.refused', no_passkey: 'pkp.soutNoPasskey', send: 'sout.sendFail', gone: 'sout.r.gone' }[m.why] || 'elev.fail';
  toast(t(key), 4200);
  if (m.why === 'gone' && c) { cards.delete(m.id); if (shownId === m.id) shownId = null; }
  render();
}

export const ids = () => [...cards.keys()];                                                          // P203 (§29.1)
export function prune(gone) { const now = Date.now(); for (const c of [...cards.values()]) if (gone(c.id) && !(opened && opened.id === c.id)) done({ id: c.id, result: c.deadline <= now ? 'expired' : 'gone' }); }   // a value on screen stays
/** Unpair / revoke: forget everything (and wipe a shown value). A connection blip (`keepOpened`): the host re-sends the open
 *  cards after ready, but a value already on screen (its card ended at the host) stays until 完成 / 2 minutes. */
export function clear({ keepOpened = false } = {}) {
  const keep = keepOpened && opened ? cards.get(opened.id) : null;
  // P566 (#364): a connection blip keeps the card whose Face ID / hand-in is under way (the host re-sends it after ready)
  const going = keepOpened && !keep && shownId ? cards.get(shownId) : null;
  if (!keep) wipeOpened();
  if (going && (busy || going.sending)) { cards.clear(); cards.set(going.id, going); return; }
  cards.clear(); busy = false;
  if (keep) { cards.set(keep.id, keep); render(); return; }
  shownId = null;
  if (root) root.hidden = true;
  clearTimeout(timer);
}
export const openCount = () => cards.size;

function wipeOpened() {
  if (!opened) return;
  clearTimeout(opened.wipe);
  if (opened.bytes) opened.bytes.fill(0);
  opened = null;
  previewUI?.reset();
}

function row(dl, label, value) {
  if (!value) return;
  dl.appendChild(mk('dt', '', label));
  dl.appendChild(mk('dd', '', value));
}
function render() {
  build();
  const c = (opened && cards.get(opened.id)) || cards.values().next().value;
  clearTimeout(timer);
  if (!c) { root.hidden = true; wipeOpened(); document.body.dataset.sout = '0'; return; }
  document.body.dataset.sout = '1';
  const isOpen = !!opened && opened.id === c.id;
  if (shownId !== c.id || root.dataset.state !== (isOpen ? 'open' : 'closed')) {
    shownId = c.id;
    root.dataset.state = isOpen ? 'open' : 'closed';
    $('sout-kicker').textContent = t('sout.kicker');
    $('sout-title').textContent = c.name;
    const dl = $('sout-rows'); dl.replaceChildren();
    row(dl, t('sout.purpose'), c.purpose);
    row(dl, t('sout.what'), c.kind === 'file' ? t('sout.file', { name: c.filename, size: human(c.size) }) : t('sout.text', { size: human(c.size) }));
    $('sout-mask').textContent = isOpen ? '' : '••••••••••••';
    $('sout-mask').hidden = isOpen;
    $('sout-value').hidden = !isOpen;
    $('sout-note').textContent = t(isOpen ? 'sout.noteOpen' : c.fa ? 'sout.note' : 'pkp.soutNoPasskey');   // P547: names the new button
    $('sout-open').textContent = t('sout.open');
    $('sout-setup').textContent = t('pkp.setupHere');   // P547: another remote approves the new key
    $('sout-copy').textContent = t(c.kind === 'file' ? 'sout.copyFile' : 'sout.copy');
    $('sout-download').textContent = t('sout.download');
    $('sout-done').textContent = t('sout.done');
    $('sout-decline').textContent = t('sout.decline');
    $('sout-open').hidden = isOpen || !c.fa;
    $('sout-setup').hidden = isOpen || !!c.fa;
    $('sout-copy').hidden = !isOpen || (c.kind === 'file' && !(opened && opened.preview !== undefined));
    $('sout-download').hidden = !isOpen || c.kind !== 'file';
    $('sout-done').hidden = !isOpen;
    $('sout-decline').hidden = isOpen;
    root.hidden = false;
  }
  $('sout-open').disabled = busy;
  $('sout-decline').disabled = busy;
  paintPk($('sout-pk'), c.fa && !isOpen && !busy ? { fa: c.fa_s, err: c.faErr || null, retry: () => openCard() } : {});   // P539
  paintLeft(c, isOpen);
}
function paintLeft(c, isOpen) {
  if (isOpen) { $('sout-left').textContent = t('sout.picked'); return; }
  const left = Math.max(0, Math.ceil((c.deadline - Date.now()) / 1000));
  $('sout-left').textContent = left > 0 ? t('sout.left', { m: Math.floor(left / 60), s: String(left % 60).padStart(2, '0') }) : t('elev.timeUp');
  if (left > 0) timer = setTimeout(() => { if (cards.get(c.id) === c && !opened) paintLeft(c, false); }, 1000);
}

/** 「用 Face ID 查看」: the system sheet straight from the tap (iOS gesture rule), then the request; no value is sent by us. */
async function openCard() {
  const c = cards.get(shownId);
  if (!c || opened) return;
  if (busy || c.sending) { toast(t(c.sending ? 'p566.handing' : 'p566.busy'), 3000); return; }   // P566 (#354): never a silent tap
  if ((!c.fa || !canAssert()) && phoneConfirmation(!c.fa)) return;
  if (!c.fa) { toast(t('sout.noPasskey'), 4200); return; }
  if (!canAssert()) { toast(t('elev.fa.none'), 5000); return; }
  if (!c.faCh) { toast(t('elev.fail'), 3000); return; }
  const g = gen();
  if (!g) { toast(t('elev.net'), 3000); return; }
  const id = c.id, n = c.n, ch = channel();
  busy = true; render();
  let fa = null;
  let faErr = null;
  try { fa = await approve(c.faCh, c.fa); } catch (e) { fa = null; faErr = errName(e); }
  // P566 (#364): the sheet may have cost the socket; the re-sent card (same nonce) is the same card
  const cur = cards.get(id), same = !!cur && cur.n === n;
  if (cur) cur.faErr = fa ? null : faErr || 'Error';   // P539: the card says why and offers 再试一次
  if (!fa || !same) { busy = false; render(); toast(t(!fa ? 'elev.fa.cancel' : cur ? 'p566.rearmed' : 'sout.r.gone'), !fa ? 3000 : 5000); return; }
  // on this connection, or on the first one after the drop (「正在补交…」); the host checks nonce, signature and counter as before
  cur.sending = true;
  const sent = await handIn('sout:' + id, { send: (g2) => sendApp({ t: 'secret_out_open', id, n, fa }, g2), until: cur.deadline, ch,
    live: () => { const x = cards.get(id); return !!x && x.n === n && !opened; },
    waiting: () => { const x = cards.get(id); if (x) x.sending = true; toast(t('p566.handing'), 0); } });
  if (!sent) {
    const x = cards.get(id);
    if (x) x.sending = false;
    busy = false; render();
    if (x && !opened) toast(t(Date.now() >= x.deadline ? 'p566.r.late' : 'p566.notSent'), 5000, 'error');
  }
}
async function declineCard() {
  const c = cards.get(shownId);
  if (!c || busy || opened) return;
  const g = gen();
  if (!g) { toast(t('elev.net'), 3000); return; }
  busy = true; render();
  try { await sendApp({ t: 'secret_out_decline', id: c.id }, g); } catch { busy = false; render(); toast(t('elev.fail'), 3000); }
}
async function copyValue(e) {
  if (!opened) return;
  const text = opened.text !== null ? opened.text : new TextDecoder().decode(opened.bytes);
  await copyButton(text, e?.currentTarget || $('sout-copy'), async value => { await navigator.clipboard.writeText(value); return true; });
}
function download() {
  if (!opened || !opened.bytes) return;
  const url = URL.createObjectURL(new Blob([opened.bytes], { type: 'application/octet-stream' }));
  const a = mk('a');
  a.href = url; a.download = opened.filename; a.rel = 'noopener';
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}
/** 「完成」 / 2 minutes / page hidden: the value leaves the page; the card is gone (it was picked up). */
function finish() {
  if (!opened) return;
  const id = opened.id;
  wipeOpened();
  cards.delete(id);
  if (shownId === id) shownId = null;
  render();
}
