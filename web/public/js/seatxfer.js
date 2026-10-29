// P547 (PASSKEY-SEAT-MODEL §3): Settings → 「Agent 与遥控器」 → 「转移席位到另一台 Agent J 主机」. Everything happens in the
// remote: what will happen (sheet) → this remote's Face ID (signed `seat_transfer`, the host asks for the passkey; a remote
// without one is sent to the peer-approved setup) → the whole install prompt for the new host, the code masked until the
// eye is tapped, 「复制」 (the whole prompt), three steps on where to paste it, and 「撤销」 (signed `seat_transfer_cancel`)
// until the new host has used the code. The code lives only in this panel's memory: never stored, never logged, gone when
// the panel closes. `seat_xfer_state` (unsigned read) drives the status line and 「撤销」 on the row; a host from before
// P547 answers nothing and the row stays hidden.
import { t, lang, locale, onLang } from './t.js';
import { el, mk, toast, notify, confirmSheet } from './ui.js';
import { registerLayer } from './layers.js';
import { request, done, isReady } from './session.js';
import { signedWrite, signedWriteFa, whyText } from './controls.js';
import { copyButton } from './copycode.js';
import { approve, canAssert } from './faceid.js';
import { unb64u } from '../proto/wire.js';
import { SVG } from './icons.js';

let H = { setupFaceId: () => {}, say: () => {} };
export function configure(h) {
  H = { ...H, ...h };
  el('set-seat-xfer')?.addEventListener('click', () => start());
  el('set-seat-xfer-cancel')?.addEventListener('click', () => cancel());
  onLang(() => { paintRow(); if (root && !root.hidden) paint(); });
}

// ---------------------------------------------------------------- the row: state from the host
let st = null;                                       // {state, expires_at} | null (no answer: an older host)
export const _state = () => st && { ...st };
const TOKEN = /\baj[th]_[A-Za-z0-9_-]{20,64}\b/g;   // P576: the host mints ajh_ (seat_transfer.PREFIX), never ajt_
/** ajh_••••…末 4 位 (the code's own prefix, like the host's mask) — what a screenshot of the panel may show. */
export const maskToken = (tok) => String(tok).slice(0, 4) + '••••…' + String(tok).slice(-4);
export function maskPrompt(text, token) {
  const s = String(text ?? '');
  return token ? s.split(token).join(maskToken(token)) : s.replace(TOKEN, (m) => maskToken(m));
}
const secs = (v) => (typeof v === 'number' ? (v > 1e12 ? v / 1000 : v) : Date.parse(v) / 1000);
export function until(v) {
  const s = secs(v);
  if (!Number.isFinite(s) || s <= 0) return '';
  return new Date(s * 1000).toLocaleString(locale(), { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}
/** Settings opened (paired + connected): one unsigned read. */
export function refresh() {
  if (!isReady()) return Promise.resolve(null);
  return new Promise((resolve) => {
    let timer = 0;
    const r = request({ t: 'seat_xfer_state' }, (m) => {
      if (m.t === 'offline') { clearTimeout(timer); done(r); return resolve(null); }
      clearTimeout(timer); done(r);
      st = typeof m.state === 'string' ? { state: m.state, expires_at: m.expires_at ?? null } : null;
      paintRow(); resolve(st);
    });
    if (!r) return resolve(null);
    timer = setTimeout(() => { done(r); resolve(null); }, 8000);
  });
}
/** host → every remote: the transfer state changed (issued from the terminal, revoked, moved). */
export function onState(m) {
  if (!m || typeof m.state !== 'string' || typeof m.r === 'string') return;
  st = { state: m.state, expires_at: m.expires_at ?? null };
  paintRow();
  if (m.state !== 'issued' && isOpen() && P) close();   // a revoked / used code: the panel's prompt is dead
}
function paintRow() {
  const row = el('set-seat-xfer');
  if (!row) return;
  row.hidden = !st;
  const line = el('set-seat-xfer-state'), cancelBtn = el('set-seat-xfer-cancel');
  const issued = !!st && st.state === 'issued';
  const moved = !!st && (st.state === 'bound' || st.state === 'moved');
  line.hidden = !issued && !moved;
  line.textContent = issued ? t('sxf.issued', { time: until(st.expires_at) }) : moved ? t('sxf.bound') : '';
  cancelBtn.hidden = !issued;
  const acts = el('set-seat-xfer-acts');
  if (acts) acts.hidden = !issued;
}

// ---------------------------------------------------------------- the flow
/** P555: the sheet left-aligned — 「1. …」 lines an ordered list, 「· …」 lines a bulleted one, a wrapped
 *  line under the words of its own item (not under the number or dot); a line ending in 「：」 is a heading. */
export function listify(node, text) {
  const kids = [];
  let list = null;
  for (const line of String(text).split('\n')) {
    const n = /^\s*\d+\.\s*(.*)$/.exec(line), b = /^\s*·\s*(.*)$/.exec(line), m = n || b;
    const tag = n ? 'ol' : 'ul';
    if (m) { if (!list || list.tagName !== tag.toUpperCase()) { list = mk(tag, 'sxf-list'); kids.push(list); } list.append(mk('li', '', m[1])); }
    else if (line.trim()) { list = null; kids.push(mk('p', /[：:]\s*$/.test(line) ? 'sxf-lead sxf-head' : 'sxf-lead', line)); }
  }
  node.replaceChildren(...kids);
}
async function explain() {
  const box = el('confirm');
  box.dataset.kind = 'sxf';
  const shown = confirmSheet(t('sxf.explainTitle'), t('sxf.explainText'), t('sxf.explainYes'));
  listify(el('confirm-text'), t('sxf.explainText'));
  try { return await shown; } finally { if (box.dataset.kind === 'sxf') delete box.dataset.kind; }
}
let busy = false;
export async function start() {
  if (busy) return;
  if (!await explain()) return;
  if (!isReady()) { toast(t('set.offline'), 3000); return; }
  busy = true;
  let res;
  try { res = await signedWriteFa('seat_transfer', {}, { t: 'seat_transfer' }, faceId); } finally { busy = false; }
  H.say('');
  if (res.why === 'cancel') return;
  if (!res.ok && res.why === 'passkey_needed') return needKey();
  if (!res.ok) { toast(res.why === 'not_bound' ? t('sxf.notBound') : res.why === 'rate' ? t('sxf.rate') : t('sxf.fail', { why: whyText(res.why) }), 6000); return; }
  if (typeof res.token !== 'string' || typeof res.prompt_zh !== 'string' || typeof res.prompt_en !== 'string') { toast(t('sxf.fail', { why: whyText('shape') }), 6000); return; }
  st = { state: 'issued', expires_at: res.expires_at ?? null };
  paintRow();
  open({ token: res.token, zh: res.prompt_zh, en: res.prompt_en, exp: res.expires_at ?? null });
}
/** passkey_required {fa, fa_ch}: settings.faceIdStep's pattern — get() starts inside the confirm tap (iOS gesture rule). */
export async function faceId(res) {
  if (typeof res.fa !== 'string' || typeof res.fa_ch !== 'string' || !canAssert()) throw new Error('passkey_failed');
  const yes = el('confirm-yes');
  let p = null;
  const grab = () => { p = approve(unb64u(res.fa_ch), res.fa); p.catch(() => {}); };
  yes.addEventListener('click', grab);
  let ok;
  try { ok = await confirmSheet(t('p336.faTitle'), t('sxf.faText'), t('p336.faYes')); } finally { yes.removeEventListener('click', grab); }
  return ok && p ? p : null;
}
/** No Face ID on this remote: set it up here (another remote approves), or go to the remote that has one. */
export async function needKey() {
  const box = el('confirm');
  if (box) box.dataset.long = '1';
  let here;
  try { here = await confirmSheet(t('sxf.pkTitle'), t('sxf.pkText'), t('pkp.setupHere'), false, t('sxf.pkElsewhere')); } finally { if (box) delete box.dataset.long; }
  if (here) return H.setupFaceId();
  notify({ kind: 'info', text: t('sxf.elsewhereHint'), ms: 6000 });
}
export async function cancel() {
  if (!await confirmSheet(t('sxf.cancelTitle'), t('sxf.cancelText'), t('sxf.cancelYes'), true)) return;
  if (!isReady()) { toast(t('set.offline'), 3000); return; }
  const res = await signedWrite('seat_transfer_cancel', {}, { t: 'seat_transfer_cancel' });
  if (res.ok && res.status === 'bound') toast(t('sxf.alreadyBound'), 6000);
  else if (res.ok) { notify({ kind: 'success', text: t('sxf.cancelled'), ms: 3000 }); close(); }
  else toast(t('sxf.cancelFail', { why: whyText(res.why) }), 5000);
  st = res.ok ? { state: res.status === 'bound' ? 'bound' : 'none', expires_at: null } : st;
  paintRow();
  refresh();
}

// ---------------------------------------------------------------- the result panel (centred Noir card)
let root = null, P = null, shown = false;
function build() {
  if (root) return root;
  root = mk('div', 'ajmodal dlgcard sxf');
  root.id = 'sxf'; root.hidden = true;
  root.setAttribute('role', 'dialog'); root.setAttribute('aria-modal', 'true'); root.setAttribute('aria-labelledby', 'sxf-title');
  document.body.appendChild(root);
  registerLayer({ element: () => root, open: () => !!root && !root.hidden, rank: 50, close: () => close(),
    fallback: () => el('set-seat-xfer') || el('setBtn') });
  return root;
}
export function open(p) { P = p; shown = false; paint(); }
export function close() { P = null; shown = false; if (root) { root.hidden = true; root.replaceChildren(); } }
export const isOpen = () => !!root && !root.hidden;
const promptText = () => (P ? (lang() === 'en' ? P.en : P.zh) : '');
function paint() {
  if (!P) return;
  const r = build();
  const box = mk('div', 'box');
  const title = mk('h2', '', t('sxf.resultTitle')); title.id = 'sxf-title';
  const exp = until(P.exp);
  const lead = mk('p', 'sxf-lead', exp ? t('sxf.resultLead', { time: exp }) : t('sxf.resultLeadNoTime'));
  const pr = mk('div', 'sxf-prompt'); pr.id = 'sxf-prompt';
  pr.textContent = shown ? promptText() : maskPrompt(promptText(), P.token);
  pr.dataset.masked = shown ? '0' : '1';
  const tools = mk('div', 'ajrow sxf-tools');
  const eye = mk('button', 'aj-btn aj-btn--quiet sxf-eye'); eye.type = 'button'; eye.id = 'sxf-eye';
  eye.setAttribute('aria-pressed', String(shown));
  eye.append(eyeIcon(shown), mk('span', '', t(shown ? 'sxf.hide' : 'sxf.show')));
  eye.addEventListener('click', () => { shown = !shown; paint(); });
  const copy = mk('button', 'aj-btn aj-btn--primary sxf-copy'); copy.type = 'button'; copy.id = 'sxf-copy'; copy.textContent = t('ar.copy');
  copy.addEventListener('click', () => copyButton(promptText(), copy));   // the whole prompt, the code unmasked
  tools.append(copy, eye);
  const how = mk('h3', 'sxf-how', t('sxf.howTitle'));
  const ol = mk('ol', 'sxf-steps'); ol.id = 'sxf-steps';
  for (const k of ['sxf.step1', 'sxf.step2', 'sxf.step3']) ol.append(mk('li', '', t(k)));
  const acts = mk('div', 'ajrow sxf-acts');
  const undo = mk('button', 'aj-btn aj-btn--quiet sxf-undo'); undo.type = 'button'; undo.id = 'sxf-undo'; undo.textContent = t('sxf.cancelBtn');
  undo.addEventListener('click', () => cancel());
  const fin = mk('button', 'aj-btn aj-btn--primary'); fin.type = 'button'; fin.id = 'sxf-done'; fin.textContent = t('sxf.done');
  fin.addEventListener('click', () => close());
  acts.append(fin, undo);
  box.append(mk('p', 'elev-kicker', t('sxf.kicker')), title, lead, pr, tools, how, ol, mk('p', 'elev-note', t('sxf.undoNote')), acts);
  r.replaceChildren(box);
  r.hidden = false;
}
function eyeIcon(open) {
  const NS = SVG, s = document.createElementNS(NS, 'svg');
  s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('aria-hidden', 'true'); s.setAttribute('class', 'mi');
  s.setAttribute('fill', 'none'); s.setAttribute('stroke', 'currentColor'); s.setAttribute('stroke-width', '1.9');
  s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round');
  const ds = ['M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z', 'M12 9a3 3 0 1 0 0 6a3 3 0 1 0 0-6z'];
  if (open) ds.push('M4 4l16 16');
  for (const d of ds) { const p = document.createElementNS(NS, 'path'); p.setAttribute('d', d); s.appendChild(p); }
  return s;
}
