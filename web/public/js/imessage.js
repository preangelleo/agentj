// P274 · Settings → iMessage 助手 (PROTOCOL §10.21): shown only when the computer is a Mac (host_info.platform). The switch
// and the parameters go through settings.js setPref → `pref_set` (the host's one validator, the same one `agentj imessage
// set` uses); the rules text, VIP and mute lists are read-only here — 「让 Agent J 帮我改」 puts a request in the chat
// box (never sends). 「重新检测」 / 「在电脑上打开授权页」 = `imsg_check` / `imsg_fda` → `imsg_res`. No message text is ever
// shown on this page; everything is inside the Noise session and rendered with textContent.
import { t } from './t.js';
import { el } from './ui.js';
import { ask1, isReady } from './session.js';

let P = { pget: () => undefined, setPref: async () => false, hostInfo: () => null, assist: () => {} };
const KEYS = ['imessage.enabled', 'imessage.interval_minutes', 'imessage.delivery', 'imessage.digest_at', 'imessage.quiet_hours',
  'imessage.strictness', 'imessage.codes', 'imessage.groups', 'imessage.llm'];
export const PREF_KEYS = KEYS;

/** settings.js: is the card for this computer at all (a Mac whose Agent J knows the keys)? */
export function available() {
  const h = P.hostInfo();
  return !!(h && h.platform === 'macos' && Array.isArray(h.settable) && h.settable.includes('imessage.enabled'));
}

function say(s) { el('imsg-msg').textContent = s || ''; }
const info = () => (P.hostInfo() && P.hostInfo().imessage) || {};
const statusKey = (s) => ['off', 'ok', 'no_fda', 'no_db', 'schema', 'error', 'pending'].includes(s) ? s : 'pending';

/** One <select>: its value, or an extra 「自定义」 option for a value set from the computer (agentj imessage set …). */
function fillSelect(id, v) {
  const sel = el(id);
  if (document.activeElement === sel) return;
  sel.querySelector('option[data-custom]')?.remove();
  const s = String(v);
  if (![...sel.options].some((o) => o.value === s)) {
    const o = document.createElement('option');
    o.value = s; o.dataset.custom = '1';
    o.textContent = t('imsg.custom', { v: id === 'imsg-every' ? t('imsg.minutes', { n: s }) : (s || '—') });
    sel.append(o);
  }
  sel.value = s;
}

export function render() {
  const on = P.pget('imessage.enabled', false) === true;
  el('imsg-on').setAttribute('aria-checked', String(on));
  const i = info(), st = on ? statusKey(i.status) : 'off';
  const bits = [t('imsg.st.' + st)];
  if (on && st === 'ok') {
    bits.push(t('imsg.today', { a: Number(i.alerts_today) || 0, b: Number(i.blocked_today) || 0 }));
    if (i.next_digest) bits.push(t('imsg.next', { at: String(i.next_digest).slice(0, 5) }));
  }
  el('imsg-status').textContent = bits.join(' · ');
  el('imsg-status').dataset.state = st;
  el('imsg-fda').hidden = !(on && st === 'no_fda');
  el('imsg-grant').textContent = String(i.grant_to || 'python3').slice(0, 64);
  el('imsg-fda-open').hidden = !(on && (st === 'no_fda' || st === 'pending'));
  el('imsg-check').hidden = !on;
  fillSelect('imsg-every', Number(P.pget('imessage.interval_minutes', 5)) || 5);
  fillSelect('imsg-delivery', P.pget('imessage.delivery', 'instant_digest'));
  fillSelect('imsg-digest', P.pget('imessage.digest_at', '09:00,18:00'));
  fillSelect('imsg-quiet', P.pget('imessage.quiet_hours', ''));
  fillSelect('imsg-strict', P.pget('imessage.strictness', 'standard'));
  fillSelect('imsg-codes', P.pget('imessage.codes', 'show'));
  fillSelect('imsg-llm', P.pget('imessage.llm', 'off'));
  el('imsg-groups').setAttribute('aria-checked', String(P.pget('imessage.groups', true) !== false));
  el('imsg-digest').disabled = P.pget('imessage.delivery', 'instant_digest') === 'instant';
  const rules = String(P.pget('imessage.instructions', '') || '');
  el('imsg-rules').textContent = rules || t('imsg.rulesNone');
  el('imsg-rules').dataset.empty = rules ? '0' : '1';
  const vip = P.pget('imessage.vip', []), mute = P.pget('imessage.mute', []);
  el('imsg-lists').textContent = t('imsg.lists', { v: Array.isArray(vip) ? vip.length : 0, m: Array.isArray(mute) ? mute.length : 0 });
  if (i.hint === 'check_sms_forwarding' && on && st === 'ok' && !el('imsg-msg').textContent) say(t('imsg.hintFwd'));
}

async function call(type, ms = 40000) {
  if (!isReady()) { say(t('set.offline')); return null; }
  say(t(type === 'imsg_check' ? 'imsg.checking' : 'imsg.opening'));
  const m = await ask1({ t: type }, 'imsg_res', ms);
  if (m.t !== 'imsg_res') { say(m.t === 'timeout' ? t('imsg.timeout') : t('set.offline')); return null; }
  const st = m.check && m.check.status;
  if (type === 'imsg_fda') say(m.ok ? t('imsg.opened') : t('imsg.why', { why: String(m.why || '?').slice(0, 32) }));
  else say(m.ok ? t('imsg.checkOk') : t('imsg.st.' + statusKey(st || m.why)));
  return m;
}

export function load() { say(''); render(); }

/** hooks: { pget(key, dflt), setPref(key, value), hostInfo(), assist(text) } — settings.js wires them. */
export function configure(hooks) {
  P = { ...P, ...hooks };
  el('imsg-on').addEventListener('click', async () => {
    const next = el('imsg-on').getAttribute('aria-checked') !== 'true';
    el('imsg-on').setAttribute('aria-checked', String(next));
    if (await P.setPref('imessage.enabled', next) && next) say(t('imsg.turnedOn'));
  });
  el('imsg-groups').addEventListener('click', () => {
    const next = el('imsg-groups').getAttribute('aria-checked') !== 'true';
    el('imsg-groups').setAttribute('aria-checked', String(next));
    P.setPref('imessage.groups', next);
  });
  for (const sel of el('set-imessage').querySelectorAll('select[data-imsg]')) sel.addEventListener('change', () => {
    const key = sel.dataset.imsg;
    P.setPref(key, key === 'imessage.interval_minutes' ? Number(sel.value) : sel.value);
  });
  el('imsg-check').addEventListener('click', () => call('imsg_check'));
  el('imsg-fda-open').addEventListener('click', () => call('imsg_fda', 15000));
  el('imsg-assist').addEventListener('click', () => P.assist(t('imsg.assistText')));
}
