// P365 · Settings → 自动化消息 (`agentj notify`): the owner's own scripts / timers may post a message + files to the phone.
// Shown only when the computer lists `notify.enabled` in `settable` (an older host: no entry). The master switch is that
// preference (settings.js setPref → `pref_set`); the list is `notify_get` → `notify_list` {enabled, automations:[{id, allow}],
// tasks:[{id, title:{zh,en}, declared, allow}]}; a row's switch / 移除 = `notify_set {id, allow: true | false | null}` and the
// answer is a fresh `notify_list` (`ok:false, why:"shape"|"rejected"` when refused). Agent J tasks use the id `task.<id>`
// and cannot be removed. Names and titles are data: textContent only, never markup.
import { t, lang } from './t.js';

export const PREF_KEY = 'notify.enabled';
export const TASK_PREFIX = 'task.';
const NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;   // the host's NAME_RE (notify.py)
const ROWS_MAX = 100;

let P = {
  el: (id) => document.getElementById(id),
  ready: () => false,
  ask: async () => ({ t: 'offline' }),
  pget: (_k, d) => d,
  setPref: async () => false,
  settable: () => false,
  confirm: async () => false,
  explain: () => {},
};
let list = null;      // the last good notify_list (parsed)
let busy = false;     // one notify_set in flight

/** settings.js: is the entry there at all (the computer knows `agentj notify`)? */
export const available = () => !!P.settable(PREF_KEY);

/** The message a row's switch / 移除 sends (session.request adds `r`). allow null = forget a --from name. */
export function setMessage(id, allow) { return { t: 'notify_set', id, allow: allow === null ? null : !!allow }; }
export const taskId = (id) => TASK_PREFIX + id;

/** A `notify_list` answer → {enabled, automations, tasks} with every field checked, or null when it is not a good one. */
export function parseList(m) {
  if (!m || m.t !== 'notify_list' || m.ok !== true) return null;
  const autos = (Array.isArray(m.automations) ? m.automations : [])
    .filter((r) => r && typeof r.id === 'string' && NAME.test(r.id) && !r.id.startsWith(TASK_PREFIX) && typeof r.allow === 'boolean')
    .slice(0, ROWS_MAX).map((r) => ({ id: r.id, allow: r.allow }));
  const s = (v, id) => (typeof v === 'string' && v.trim() ? v.slice(0, 64) : id);
  const tasks = (Array.isArray(m.tasks) ? m.tasks : [])
    .filter((r) => r && typeof r.id === 'string' && NAME.test(r.id))
    .slice(0, ROWS_MAX).map((r) => {
      const ti = r.title && typeof r.title === 'object' ? r.title : {};
      return { id: r.id, title: { zh: s(ti.zh, r.id), en: s(ti.en, r.id) }, declared: r.declared === true, allow: r.allow === true };
    });
  return { enabled: m.enabled !== false, automations: autos, tasks };
}

/** The words for a failed answer ({t:'timeout'|'offline'} or notify_list ok:false). */
export function failText(m) {
  if (!m || m.t === 'offline') return t('set.offline');
  if (m.t === 'timeout') return t('p365.timeout');
  return t(m.why === 'shape' ? 'p365.whyShape' : 'p365.whyRejected');
}

function say(s) { const n = P.el('ntf-msg'); if (n) n.textContent = s || ''; }

function sw(on, label, onClick) {
  const b = document.createElement('button');
  b.type = 'button'; b.className = 'tgl'; b.setAttribute('role', 'switch');
  b.setAttribute('aria-checked', String(!!on)); b.setAttribute('aria-label', label);
  b.disabled = busy;
  b.append(document.createElement('i'));
  b.addEventListener('click', onClick);
  return b;
}
function row(kind, id, label, on, extra) {
  const r = document.createElement('div');
  r.className = 'setrow ntfrow'; r.dataset.kind = kind; r.dataset.id = id;
  const lab = document.createElement('span'); lab.className = 'setlab ntfname'; lab.textContent = label;
  const ctl = document.createElement('span'); ctl.className = 'ntfctl';
  if (extra.remove) {
    const rm = document.createElement('button');
    rm.type = 'button'; rm.className = 'setlink setdanger ntfrm'; rm.textContent = t('p365.remove');
    rm.setAttribute('aria-label', t('p365.removeName', { name: label }));
    rm.disabled = busy;
    rm.addEventListener('click', extra.remove);
    ctl.append(rm);
  }
  ctl.append(sw(on, label, extra.toggle));
  r.append(lab, ctl);
  if (extra.note) { const n = document.createElement('p'); n.className = 'setnote ntfnote'; n.textContent = extra.note; r.append(n); }
  return r;
}

export function render() {
  const on = P.pget(PREF_KEY, list ? list.enabled : true) !== false;
  const master = P.el('ntf-on');
  if (master) master.setAttribute('aria-checked', String(on));
  const offNote = P.el('ntf-off');
  if (offNote) offNote.hidden = on;
  const autos = list ? list.automations : [], tasks = list ? list.tasks : [];
  const zh = lang() !== 'en';
  P.el('ntf-autos').replaceChildren(...autos.map((a) => row('auto', a.id, a.id, a.allow, {
    toggle: () => set(a.id, !a.allow),
    remove: () => remove(a.id),
  })));
  P.el('ntf-autos-empty').hidden = !list || autos.length > 0;
  P.el('ntf-how').hidden = !list || autos.length === 0;
  P.el('ntf-tasks-h').hidden = tasks.length === 0;
  P.el('ntf-tasks').replaceChildren(...tasks.map((x) => row('task', x.id, zh ? x.title.zh : x.title.en, x.allow, {
    toggle: () => set(taskId(x.id), !x.allow),
    note: x.allow === x.declared ? '' : t(x.allow ? 'p365.taskOnOverride' : 'p365.taskOffOverride'),
  })));
  const box = P.el('set-notify');
  if (box) box.dataset.off = on ? '0' : '1';
}

function take(m) {
  const l = parseList(m);
  if (l) { list = l; say(''); } else say(failText(m));
  render();
  return !!l;
}

/** showSub('notify'): fetch the list. */
export async function load() {
  render();
  if (!P.ready()) { say(t('set.offline')); return false; }
  say(t('p365.loading'));
  return take(await P.ask({ t: 'notify_get' }, 'notify_list', 10000));
}

async function send(id, allow) {
  if (busy) return false;
  if (!P.ready()) { say(t('set.offline')); render(); return false; }
  busy = true; render();
  let m;
  try { m = await P.ask(setMessage(id, allow), 'notify_list', 10000); } finally { busy = false; }
  return take(m);
}
export const set = (id, allow) => send(id, !!allow);
export async function remove(id) {
  if (!await P.confirm(t('p365.rmTitle', { name: id }), t('p365.rmText', { name: id }), t('p365.rmYes'), true)) return false;
  return send(id, null);
}

/** hooks: { el(id), ready(), ask(obj, want, ms), pget(key, dflt), setPref(key, value), settable(key), confirm(title, text,
 *  yes, danger), explain() } — settings.js wires them. */
export function configure(hooks) {
  P = { ...P, ...hooks };
  const master = P.el('ntf-on');
  master.addEventListener('click', () => {
    const next = master.getAttribute('aria-checked') !== 'true';
    master.setAttribute('aria-checked', String(next));
    P.setPref(PREF_KEY, next).then((ok) => { if (!ok) { render(); P.explain(); } });
  });
}
export function close() { say(''); }
export const _debug = () => ({ list, busy });
export function _reset() { list = null; busy = false; }
