// 0.26.9 §E3 (#473 ③, chairman 11-04 17:1x 「预算之内花钱都不应该弹卡」): Settings → 安全 → 预算. The owner sets a day / month
// budget; in-budget spending runs without a card, beyond it asks Face ID (host policy, lane w3-policy).
// Wire (assumed, CONTRACT §E3): host → phone `budget_state {day, month, spent_day?, spent_month?, currency}`; phone → host the
// signed control `budget_set {day?, month?}` (only the changed fields). Raising asks Face ID, lowering does not — the host
// decides (`passkey_required` → the same Face ID step as 审批档位, settings.faceIdStep); the page only labels the button.
// Spent is shown only when the host sends it — never estimated here; absent → 「—」. The row stays hidden until a
// budget_state arrives (an older host has no budget).
import { t, onLang, lang as curLang } from './t.js';
import { el, notify, toast } from './ui.js';
import { isReady } from './session.js';
import { signedWriteFa, whyText } from './controls.js';

let st = null;            // the last budget_state
let saving = false;

const num = (v) => (typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 1e9 ? v : null);
const CUR = /^[A-Z]{3}$/;
/** Pure: a validated budget_state (null = not one). */
export function parseState(m) {
  if (!m || typeof m !== 'object') return null;
  const day = m.day === null ? null : num(m.day), month = m.month === null ? null : num(m.month);
  if ((m.day !== null && m.day !== undefined && day === null) || (m.month !== null && m.month !== undefined && month === null)) return null;
  return { day: day ?? null, month: month ?? null, spent_day: num(m.spent_day), spent_month: num(m.spent_month),
    currency: typeof m.currency === 'string' && CUR.test(m.currency) ? m.currency : '' };
}
/** Pure: money in the host's currency (no currency = the bare number). */
export function money(v, currency, lang = 'zh') {
  if (v === null || v === undefined) return '—';
  if (currency) { try { return new Intl.NumberFormat(lang === 'en' ? 'en' : 'zh-CN', { style: 'currency', currency, maximumFractionDigits: 2 }).format(v); } catch { /* unknown code */ } }
  return String(Math.round(v * 100) / 100);
}
/** Pure: the field text → number | null (empty = 0 = nothing runs without asking); NaN for invalid. */
export function readField(text) {
  const s = String(text ?? '').trim();
  if (!s) return 0;
  const v = Number(s);
  return Number.isFinite(v) && v >= 0 && v <= 1e9 ? Math.round(v * 100) / 100 : NaN;
}
/** Pure: what to send and whether it raises anything ({} = no change). A missing budget counts as 0 (nothing in budget). */
export function plan(cur, day, month) {
  const target = {};
  const was = (k) => (cur && typeof cur[k] === 'number' ? cur[k] : 0);
  if (day !== was('day')) target.day = day;
  if (month !== was('month')) target.month = month;
  const raise = (Object.hasOwn(target, 'day') && target.day > was('day')) || (Object.hasOwn(target, 'month') && target.month > was('month'));
  return { target, raise, changed: Object.keys(target).length > 0 };
}

function msg(text) { const n = el('set-budget-msg'); if (!n) return; n.textContent = text || ''; n.hidden = !text; }
function lang() { return curLang() === 'en' ? 'en' : 'zh'; }
function fields() {
  const d = readField(el('set-budget-day').value), m = readField(el('set-budget-month').value);
  return { d, m, bad: Number.isNaN(d) || Number.isNaN(m) };
}
function paintButton() {
  const b = el('set-budget-save');
  if (!b || !st) return;
  const { d, m, bad } = fields();
  const p = bad ? { changed: false, raise: false } : plan(st, d, m);
  b.disabled = saving || bad || !p.changed;
  b.textContent = t(p.raise ? 'p0269c.budget.saveFa' : 'p0269c.budget.save');
  if (bad) msg(t('p0269c.budget.bad')); else if (!saving) msg('');
}
function render() {
  const row = el('set-budget-row');
  if (!row) return;
  row.hidden = !st;
  if (!st) return;
  const L = lang();
  for (const k of ['day', 'month']) {
    const input = el('set-budget-' + k);
    if (document.activeElement !== input) input.value = st[k] === null ? '' : String(st[k]);
    input.placeholder = t('p0269c.budget.unset');
  }
  el('set-budget-spent-day').textContent = money(st.spent_day, st.currency, L);
  el('set-budget-spent-month').textContent = money(st.spent_month, st.currency, L);
  paintButton();
}
export function onState(m) {
  const s = parseState(m);
  if (!s) return;
  st = s;
  render();
}
async function save() {
  if (!st || saving) return;
  const { d, m, bad } = fields();
  if (bad) { paintButton(); return; }
  const p = plan(st, d, m);
  if (!p.changed) return;
  if (!isReady()) { msg(t('set.offline')); return; }
  saving = true; paintButton(); msg(t('set.saving'));
  const { faceIdStep } = await import('./settings.js');   // lazy: settings.js is the page's, not needed to read a state
  const res = await signedWriteFa('budget_set', p.target, { t: 'budget_set', ...p.target },
    (r) => faceIdStep(r, { title: 'p0269c.budget.faTitle', text: 'p0269c.budget.faText', yes: 'p0269c.budget.faYes' }));
  saving = false;
  if (res.ok) {
    st = { ...st, ...p.target };
    msg('');
    notify({ kind: 'success', shape: 'circle', text: t('set.saved') });
  } else if (res.why === 'cancel') msg('');
  else msg(t('p0269c.budget.fail', { why: whyText(res.why) }));
  render();
}
export function init() {
  if (!el('set-budget-row')) return;
  for (const k of ['day', 'month']) el('set-budget-' + k).addEventListener('input', paintButton);
  el('set-budget-save').addEventListener('click', () => { save().catch(() => { saving = false; render(); toast(t('p0269c.budget.fail', { why: '' }), 3000); }); });
  onLang(render);
}
export const _state = () => st;   // tests only
