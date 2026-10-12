// P181 (owner, 2026-10-12): a long press on the Agent's name in the reply card header → the global centred dialog
// 「给 Agent 改个名字」. The same rename as `agentj name` on the computer (linked: the account decides first; the short-name
// rule: 中文最多 8 个字, English / digits / - up to 13, mixed = 中文 1.6 each, ≤ 13 in all) — sent as a signed phone control
// (`agent_name`, PROTOCOL §22.2), so only this paired remote's own key can ask. The new name comes back in the status like
// any rename. ESC / a tap outside / 取消 closes it.
import { t } from './t.js';
import { registerLayer } from './layers.js';
import { signedWrite } from './controls.js';
import { toast } from './ui.js';
import { longPress } from './longpress.js';

const $ = (id) => document.getElementById(id);
export const UNITS_MAX = 13;
const WIDE = /[ᄀ-ᅟ⺀-〾ぁ-㏿㐀-䶿一-鿿ꀀ-꓏가-힣豈-﫿︰-﹏＀-｠￠-￦]/u;
/** The host's unit count (text.new_agent_name_problem): a wide (CJK) character 1.6, anything else 1. */
export function units(s) { let n = 0; for (const ch of s) n += WIDE.test(ch) ? 1.6 : 1; return Math.round(n * 10) / 10; }
const norm = (v) => String(v || '').replace(/[\p{Cc}\p{Cf}]/gu, '').replace(/\s+/gu, ' ').trim();
let current = () => '';
let busy = false;

function hint() {
  const v = norm($('nameInput').value), u = units(v);
  const bad = !v || u > UNITS_MAX;
  $('nameHint').textContent = !v ? t('p181.name.empty') : u > UNITS_MAX ? t('p181.name.tooLong') : t('p181.name.rule');
  $('nameHint').dataset.bad = bad && v ? '1' : '0';
  $('nameSave').disabled = bad || busy || v === current();
}
export function open() {
  if (!$('nameDlg')) return;
  busy = false;
  $('nameInput').value = current();
  $('nameDlg').hidden = false;
  hint();
  $('nameInput').focus({ preventScroll: true });
  $('nameInput').select();
}
export function close() { if ($('nameDlg')) $('nameDlg').hidden = true; }
const WHY = ['name_required', 'bad_name', 'name_too_long', 'name_taken', 'unreachable', 'not_bound', 'rate_limited', 'failed',
  'bad_signature', 'no_key', 'stale', 'timeout', 'offline', 'shape'];
async function save() {
  const name = norm($('nameInput').value);
  if (!name || units(name) > UNITS_MAX || busy) return;
  if (name === current()) { close(); return; }
  busy = true; hint();
  const r = await signedWrite('agent_name', { name }, { t: 'agent_name', name });
  busy = false;
  if (!r.ok) {
    const why = WHY.includes(r.why) ? r.why : 'failed';
    const sug = Array.isArray(r.suggestions) && r.suggestions.length ? t('p181.name.suggest', { s: r.suggestions.slice(0, 3).join(' / ') }) : '';
    $('nameHint').textContent = t('p181.name.why.' + why) + sug;
    $('nameHint').dataset.bad = '1';
    $('nameSave').disabled = false;
    return;
  }
  close();
  toast(t('p181.name.done', { name: typeof r.name === 'string' ? r.name : name }), 2400);
}

/** `name` = () => the shown Agent name; `canRename` = () => true when this page may ask (paired, ready). */
export function init({ name, canRename } = {}) {
  if (name) current = name;
  if (!$('nameDlg')) return;
  $('nameInput').addEventListener('input', hint);
  $('nameForm').addEventListener('submit', (e) => { e.preventDefault(); save(); });
  $('nameCancel').addEventListener('click', close);
  $('nameDlg').addEventListener('click', (e) => { if (e.target === e.currentTarget) close(); });
  registerLayer({ element: () => $('nameDlg'), open: () => !$('nameDlg').hidden, close, fallback: () => $('agent'), rank: 5 });
  // on the release: inside the user gesture, so iOS raises the keyboard for the focused field
  longPress($('agent'), () => { if (!canRename || canRename()) open(); else toast(t('p181.name.offline'), 2600); }, { at: 'release' });
}
