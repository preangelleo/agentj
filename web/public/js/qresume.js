// P314 (§32): a turn stopped by a used-up usage limit continues by itself after the reset. The host pushes
// `quota_resume {items:[{id, scope, harness, at, title?, to?}]}` (on ready while any waits, and on every change); the chat
// shows one thin banner 「额度用完，预计 HH:MM 自动继续」 for the earliest item, ✕ = `qr_cancel {r, id}` (only stops an
// automatic action, so no signature, like grant_off). Everything is textContent.
import { t, onLang } from './t.js';
import { el } from './ui.js';
import { ask1, isReady } from './session.js';

const NAMES = { claude: 'Claude Code', codex: 'Codex', opencode: 'OpenCode', kimi: 'Kimi Code', qwen: 'Qwen Code' };
const MARGIN = 90;        // the host continues this long after the reported reset (quota_resume.RESUME_MARGIN)
let items = [];

const valid = (x) => x && typeof x.id === 'string' && /^[0-9a-f]{1,32}$/.test(x.id) && Number.isFinite(x.at)
  && (x.scope === 'main' || x.scope === 'task') && typeof x.harness === 'string';

/** HH:MM today, 「明天 HH:MM」, else MM-DD HH:MM — the phone's own clock and zone. */
export function when(at, now = Date.now()) {
  const d = new Date(at * 1000), n = new Date(now), p = (v) => String(v).padStart(2, '0');
  const hm = `${p(d.getHours())}:${p(d.getMinutes())}`;
  if (d.toDateString() === n.toDateString()) return hm;
  const tm = new Date(n); tm.setDate(n.getDate() + 1);
  if (d.toDateString() === tm.toDateString()) return t('p314.tomorrow').replace('{t}', hm);
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${hm}`;
}

export function text(x, more = 0) {
  const at = when(x.at + (x.to ? 0 : MARGIN)), title = (x.title || '').slice(0, 64);
  const s = x.to ? t('p314.bannerSwitch').replace('{title}', title).replace('{to}', NAMES[x.to] || x.to).replace('{t}', at)
    : x.scope === 'task' ? t('p314.bannerTask').replace('{title}', title).replace('{t}', at)
      : t('p314.banner').replace('{t}', at);
  return more > 0 ? s + t('p314.more').replace('{n}', String(more)) : s;
}

function render() {
  const b = el('qr-banner');
  if (!b) return;
  b.hidden = !items.length;
  if (!items.length) return;
  const open = el('qr-banner-text');
  if (open) open.textContent = text(items[0], items.length - 1);
}

/** app.js: the host's push (and qr_res). */
export function onPush(m) {
  items = Array.isArray(m && m.items) ? m.items.filter(valid).slice(0, 16).sort((a, b) => a.at - b.at) : [];
  render();
}

async function cancel() {
  const x = items[0];
  if (!x || !isReady()) return;
  const m = await ask1({ t: 'qr_cancel', id: x.id }, 'qr_res');
  if (m.t === 'qr_res') onPush(m);
}

export function configure() {
  el('qr-banner-x')?.addEventListener('click', cancel);
  onLang(render);
}
export const current = () => items.slice();
