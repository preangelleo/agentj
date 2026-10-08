// Phone controls (PROTOCOL §8): what it remembers, the activity record, scheduled tasks, 全部停下 / 恢复, batch grants.
// Reads are plain requests answered to this session only; every write is signed with the approval key over (action,
// nonce, time, SHA-256 of the target as shown) — the host does nothing without a valid signature. Nothing is stored here.
import { controlMessage, b64u } from '../proto/wire.js';
import { request, done, isReady, channel, sendApp, randHex, gen } from './session.js';
import { signKey } from './store.js';
import { myDeviceId } from './api.js';
import { t, lang, locale, plain } from './t.js';
import { el, mk, toast, toastAction, confirmSheet } from './ui.js';

let H = null;          // {show(view), estopChanged(), friends()}
export function configure(h) { H = h; }
export let panel = 'chat';
export const estop = { on: false, by: '' };

const GEN = Symbol('gen');                            // the session a signed write was made for (never serialized)
async function signed(action, target, extra) {
  const sk = await signKey();
  const g = gen();
  if (!g || !sk) throw new Error(sk ? 'offline' : 'no_key');
  const n = randHex(16), ts = Date.now();
  const msg = await controlMessage(channel(), await myDeviceId(), action, n, ts, target);
  const sig = new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, sk.priv, msg));
  return { ...extra, n, ts, sig: b64u(sig), [GEN]: g };
}
function command(obj) {                               // one signed write → the host's ctl_res
  return new Promise((resolve) => {
    const r = request(obj, (m) => { if (m.t === 'ctl_res') { done(r); resolve(m); } }, obj[GEN] ?? gen());
    if (!r) resolve({ ok: false, why: 'offline' });
    else setTimeout(() => { done(r); resolve({ ok: false, why: 'timeout' }); }, 20000);
  });
}
/** One signed write → ctl_res, never throws (the friends page, §17.7: fr_set pg_set pg_del fr_add fr_discoverable fr_card). */
export async function signedWrite(action, target, extra) {
  try { return await command(await signed(action, target, extra)); } catch (e) { return { ok: false, why: e.message }; }
}
const WHY_CODES = ['changed', 'unknown_item', 'bad_signature', 'no_key', 'stale', 'timeout', 'offline', 'not_found', 'exists', 'symlink',
  'invalid', 'unknown', 'io', 'shape', 'replay'];
const why = (code) => (WHY_CODES.includes(code) ? t('why.' + code) : String(code ?? ''));
export const whyText = why;

export function openPanel(v) {
  if (!['chat', 'mem', 'act', 'tasks', 'friends'].includes(v)) return;
  panel = v;
  H.show(v === 'chat' ? 'chat' : v);
  if (v === 'friends') H.friends();                   // §17.7: the friends page re-reads on every open / reconnect
  if (v === 'mem') loadMemory();
  if (v === 'act') loadActivity(true);
  if (v === 'tasks') loadTasks();
  el('pages').scrollTop = 0;
}
export function reloadPanel() { if (isReady() && panel !== 'chat') openPanel(panel); }

// ---- stop everything
export function setEstop(m) {
  estop.on = m.on === true;
  estop.by = estop.on && typeof m.by === 'string' && m.by ? m.by.slice(0, 64) : '';
  el('estop-banner').hidden = !estop.on;
  renderEstop();
  H.estopChanged();
}
export function renderEstop() { el('estop-by').textContent = estop.by ? t('estop.by', { by: estop.by }) : ''; }
export async function onEstop() {
  if (!await confirmSheet(t('estop.confirmTitle'), t('estop.confirmText'), t('estop.confirmYes'))) return;
  const res = await command(await signed('estop', {}, { t: 'estop' }).catch(() => ({ t: 'estop' })));
  if (!res.ok) toast(t('estop.fail', { why: why(res.why) }), 4000);
}
export async function onResume() {
  if (!await confirmSheet(t('estop.resumeTitle'), t('estop.resumeText'), t('estop.resumeYes'))) return;
  const res = await command(await signed('resume', {}, { t: 'resume' }).catch(() => ({ t: 'resume' })));
  if (!res.ok) toast(t('estop.resumeFail', { why: why(res.why) }), 4000);
}

// ---- batch approval grants: the bar above the field + 「收回授权」; an automatic approval is one toast line
const grants = new Map();
export function renderGrants() {
  const live = [...grants.values()];
  el('grant-bar').hidden = live.length === 0;
  el('grant-text').textContent = live.length === 0 ? '' :
    t('grant.live', { list: live.map((g) => t('grant.item', { scope: g.scope, n: g.left })).join(lang() === 'en' ? '; ' : '；') });
}
export function setGrant(m) {
  if (typeof m.id !== 'string' || !/^[0-9a-f]{32}$/.test(m.id) || typeof m.scope !== 'string') return;
  grants.set(m.id, { scope: m.scope.slice(0, 400), left: Number.isInteger(m.left) ? m.left : 0 });
  renderGrants();
}
const GRANT_END = ['turn_end', 'revoked', 'limit', 'expired', 'device_gone', 'estop'];
export function endGrant(id, code) {
  if (!grants.delete(id)) return;
  renderGrants();
  if (code !== 'turn_end') toast(t('grant.end.' + (GRANT_END.includes(code) ? code : 'other')), 3200);
}
export function clearGrants() { grants.clear(); renderGrants(); }
export async function revokeGrants() {
  if (!isReady() || grants.size === 0) return;
  await sendApp({ t: 'grant_off', id: null }).catch(() => {});
}
export function showAuto(m) {
  if (typeof m.tool !== 'string' || typeof m.summary !== 'string') return;
  toast(t('ask.auto', { tool: m.tool, summary: m.summary.slice(0, 120) }), 2600);
}

// ---- 它记住了什么
const memItems = new Map();
export function loadMemory() {
  el('mem-status').textContent = t('mem.loading');
  const list = el('mem-list'); list.replaceChildren(); memItems.clear();
  const boxes = new Map();
  const r = request({ t: 'mem_list' }, (m) => {
    if (m.t === 'mem_sources') {
      const HN = { claude: 'Claude Code', codex: 'Codex', opencode: 'OpenCode' };
      el('mem-status').textContent = m.harness ? t('mem.sources', { agent: HN[m.harness] ?? m.harness }) : t('mem.noAgent');
      for (const src of Array.isArray(m.sources) ? m.sources : []) {
        const sec = mk('section', 'mem-src');
        const head = mk('h2', 'mem-src-h', String(src.label ?? ''));
        const path = mk('p', 'small mem-path', String(src.path ?? ''));
        const PROB = ['not_found', 'symlink', 'too_large', 'io', 'too_many_files'];
        const note = mk('span', 'small mem-note', src.problem ? (PROB.includes(src.problem) ? t('mem.prob.' + src.problem) : `(${src.problem})`) : t('mem.count', { n: Number.isInteger(src.n) ? src.n : 0 }));
        head.append(' ', note);
        const ul = mk('ul', 'mem-items');
        sec.append(head, path, ul);
        sec.dataset.src = String(src.id ?? '');
        if (src.problem === 'not_found') sec.classList.add('mem-empty');
        list.append(sec);
        boxes.set(src.id, ul);
      }
      renderTrash(Array.isArray(m.trash) ? m.trash : []);
      return;
    }
    if (m.t === 'mem_items') {
      for (const it of Array.isArray(m.items) ? m.items : []) {
        const ul = boxes.get(it.src);
        if (!ul || typeof it.text !== 'string') continue;
        const key = `${it.src}\n${it.file}\n${it.iid}`;
        memItems.set(key, it);
        const li = mk('li', 'mem-item');
        li.dataset.kind = String(it.kind ?? '');
        const body = mk('div', 'mem-body');
        if (it.title) body.append(mk('p', 'mem-title', String(it.title) + (it.file ? `  ·  ${it.file}` : '')));
        if (it.desc) body.append(mk('p', 'small mem-desc', String(it.desc)));
        body.append(mk('pre', 'mem-text', it.text + (it.cut ? '\n' + t('mem.cut', { shown: it.text.length, total: it.cut }) : '')));
        const del = mk('button', 'aj-btn aj-btn--sm mem-del', t('mem.del')); del.type = 'button';
        del.setAttribute('aria-label', t('mem.delAria'));
        del.addEventListener('click', () => deleteMemory(key));
        li.append(body, del);
        ul.append(li);
      }
      if (m.more === false) done(r);
    }
  });
}
function renderTrash(items) {
  const ul = el('mem-trash'); ul.replaceChildren();
  if (!items.length) { ul.append(mk('li', 'small', t('mem.trashEmpty'))); return; }
  for (const x of items) {
    const li = mk('li', 'trash-item');
    li.append(mk('span', 'trash-text', `${String(x.label ?? '')}${lang() === 'en' ? ': ' : '：'}${String(x.text ?? '')}`));
    const b = mk('button', 'aj-btn aj-btn--sm', t('mem.restore')); b.type = 'button';
    b.addEventListener('click', () => undoMemory(String(x.id)));
    li.append(b);
    ul.append(li);
  }
}
async function deleteMemory(key) {
  const it = memItems.get(key);
  if (!it) return;
  const preview = it.text.replace(/\s+/g, ' ').slice(0, 120);
  if (!await confirmSheet(t('mem.delTitle'), (lang() === 'en' ? `"${preview}"` : `「${preview}」`) + '\n' + plain(t('mem.delText')), t('mem.delYes'))) return;
  const target = { src: it.src, file: it.file, fsha: it.fsha, iid: it.iid };
  let res;
  try { res = await command(await signed('mem_rm', target, { t: 'mem_rm', ...target })); } catch (e) { res = { ok: false, why: e.message }; }
  if (res.ok) toastAction(t('mem.deleted'), t('mem.undo'), () => undoMemory(String(res.undo)), 12000);
  else toast(t('mem.delFail', { why: why(res.why) }), 4000);
  loadMemory();
}
async function undoMemory(id) {
  let res;
  try { res = await command(await signed('mem_undo', { id }, { t: 'mem_undo', id })); } catch (e) { res = { ok: false, why: e.message }; }
  toast(res.ok ? t('mem.restored') : t('mem.restoreFail', { why: why(res.why) }), 3000);
  if (panel === 'mem') loadMemory();
}

// ---- 操作与审批记录 (activity)
let actNext = null;
const CATS = ['spend', 'delete', 'send', 'credentials', 'price'];
const ACT_RESULTS = ['allow', 'deny', 'allow_batch', 'timeout', 'no_device', 'estop', 'serve_stop', 'agent_gone', 'too_many', 'policy'];
const ACT_KINDS = ['turn_start', 'turn_end', 'ask', 'decision', 'auto', 'grant', 'grant_end', 'estop', 'resume', 'message_refused', 'mem_rm',
  'mem_undo', 'task_on', 'task_off', 'task_run', 'task_done', 'control_refused', 'slash', 'truncated'];
function actText(r) {
  const by = r.by ? t('act.by', { by: r.by }) : '';
  const tx = String(r.text ?? r.summary ?? '').replace(/\s+/g, ' ').slice(0, 200);
  const sep = lang() === 'en' ? ', ' : '、';
  const cats = Array.isArray(r.cats) && r.cats.length ? `[${r.cats.map((c) => (CATS.includes(c) ? t('ask.cat.' + c) : c)).join(sep)}] ` : '';
  const task = r.task ? t('act.task', { task: r.task }) : '';
  if (r.k === 'auto_update') return tx;
  if (!ACT_KINDS.includes(r.k)) return String(r.k ?? '');
  const v = {
    by, t: tx, task, cats, tool: r.tool ?? '', scope: r.scope ?? '', why: r.why ?? '', label: r.label ?? '', id: r.id ?? '',
    title: r.title ?? r.id ?? '', verdict: r.verdict ?? '', line: r.line ?? '', action: r.action ?? '', cmd: r.cmd ?? '',
    ro: r.readonly ? t('act.k.readonly') : '',
    stopped: r.result === 'stopped' ? t('act.k.turn_end_stopped') : '',
    secs: r.secs !== undefined ? t('act.k.secs', { n: r.secs }) : '',
    result: r.k === 'slash' ? (['ok', 'info', 'error', 'refused'].includes(r.result) ? t('cmd.result.' + r.result) : r.result ?? '')
      : ACT_RESULTS.includes(r.result) ? t('act.result.' + r.result) : r.result ?? '',
  };
  if (r.k === 'decision') v.t = tx ? ' ' + tx : '';
  return t('act.k.' + r.k, v);
}
export function loadActivity(fresh) {
  if (fresh) { el('act-list').replaceChildren(); actNext = null; }
  el('act-status').textContent = t('act.loading');
  el('act-more').hidden = true;
  const r = request({ t: 'act_list', before: fresh ? null : actNext }, (m) => {
    if (m.t !== 'act_page') return;
    for (const it of Array.isArray(m.items) ? m.items : []) {
      const li = mk('li', 'act-item');
      li.dataset.k = String(it.k ?? '');
      if (it.result) li.dataset.result = String(it.result);
      const when = Number.isInteger(it.ts) ? new Date(it.ts).toLocaleString(locale(), { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
      li.append(mk('span', 'act-time', when), mk('span', 'act-text', actText(it)));
      el('act-list').append(li);
    }
    if (m.more === false) {
      done(r);
      actNext = typeof m.next === 'string' ? m.next : null;
      el('act-more').hidden = !actNext;
      const n = el('act-list').children.length;
      el('act-status').textContent = (m.on === false ? t('act.off') + ' ' : '') + (n ? t('act.recent', { n }) : t('act.none'));
    }
  });
}

// ---- 定时任务 (scheduled tasks)
export function loadTasks() {
  el('tasks-status').textContent = t('tasks.loading');
  const ul = el('tasks-list'); ul.replaceChildren();
  const r = request({ t: 'task_list' }, (m) => {
    if (m.t !== 'tasks') return;
    for (const x of Array.isArray(m.items) ? m.items : []) ul.append(taskCard(x, m.paused === true));
    if (m.more === false) {
      done(r);
      const n = ul.children.length;
      el('tasks-status').textContent = (m.paused ? t('tasks.paused') : '') + (n ? '' : (m.agent ? t('tasks.none') : t('tasks.noAgent')));
    }
  });
}
const tzText = (tk) => (tk.tz === 'local' ? t('tasks.localTz') : tk.tz ?? '');
function taskCard(tk, paused) {
  const li = mk('li', 'task-card');
  const title = tk.title && typeof tk.title[lang()] === 'string' ? tk.title[lang()] : tk.title && typeof tk.title.zh === 'string' ? tk.title.zh : String(tk.id);
  li.dataset.id = String(tk.id); li.dataset.enabled = String(!!tk.enabled);
  li.append(mk('p', 'task-title', title));
  const tags = mk('p', 'ask-tags');
  if (tk.mode === 'research') tags.append(mk('span', 'tag tag-low', t('tasks.readonly')));
  if (tk.mode === 'normal') tags.append(mk('span', 'tag', t('tasks.normal')));
  tags.append(mk('span', 'tag ' + (tk.enabled ? 'tag-on' : 'tag-off'), t(tk.problems?.length ? 'tasks.invalid' : tk.enabled ? (paused ? 'tasks.onPaused' : 'tasks.on') : tk.stale ? 'tasks.stale' : 'tasks.off')));
  if (tk.running) tags.append(mk('span', 'tag tag-task', t('tasks.running')));
  li.append(tags);
  li.append(mk('p', 'small', t('tasks.when', { when: cronText(tk.schedule), tz: tzText(tk) }) + (Number.isInteger(tk.next) ? t('tasks.next', { at: new Date(tk.next * 1000).toLocaleString(locale(), { hour12: false, month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) }) : '')));
  if (tk.problems?.length) li.append(mk('p', 'error small', t('tasks.bad', { p: tk.problems[0] })));
  if (tk.last && typeof tk.last === 'object') li.append(mk('p', 'small', t('tasks.last', { verdict: tk.last.verdict ?? '', line: tk.last.line ?? '' })));
  if (!tk.problems?.length || tk.enabled) {
    const on = !tk.enabled;
    const b = mk('button', on ? 'aj-btn aj-btn--primary aj-btn--sm' : 'aj-btn aj-btn--sm', t(on ? 'tasks.enable' : 'tasks.disable')); b.type = 'button';
    b.addEventListener('click', () => setTask(tk, on, title));
    li.append(b);
  }
  return li;
}
function cronText(expr) {
  if (typeof expr !== 'string') return '?';
  const f = expr.split(' ');
  if (f.length !== 5) return expr;
  const [m, h, dom, mon, dow] = f;
  const week = t('cron.days').split('|');
  const hm = /^\d+$/.test(m) && /^\d+$/.test(h) ? `${h.padStart(2, '0')}:${m.padStart(2, '0')}` : null;
  if (expr === '* * * * *') return t('cron.everyMin');
  if (/^\*\/\d+$/.test(m) && h === '*' && dom === '*' && mon === '*' && dow === '*') return t('cron.everyN', { n: m.slice(2) });
  if (/^\d+$/.test(m) && h === '*' && dom === '*' && mon === '*' && dow === '*') return t('cron.hourly', { m });
  if (hm && dom === '*' && mon === '*' && dow === '*') return t('cron.daily', { hm });
  if (hm && dom === '*' && mon === '*' && /^[0-7](,[0-7])*$/.test(dow)) return t('cron.weekly', { days: dow.split(',').map((d) => week[+d]).join(t('cron.daySep')), hm });
  if (hm && dom === '*' && mon === '*' && dow === '1-5') return t('cron.weekdays', { hm });
  if (hm && /^\d+$/.test(dom) && mon === '*' && dow === '*') return t('cron.monthly', { d: dom, hm });
  return t('cron.raw', { expr });
}
async function setTask(tk, on, title) {
  const text = on ? t('tasks.textOn', { title, when: cronText(tk.schedule), tz: tzText(tk), ro: tk.mode === 'research' ? t('tasks.textOnRo') : '' }) : t('tasks.textOff', { title });
  if (!await confirmSheet(t(on ? 'tasks.confirmOn' : 'tasks.confirmOff'), text, t(on ? 'tasks.enable' : 'tasks.disable'))) return;
  const target = { id: String(tk.id), tsha: typeof tk.tsha === 'string' ? tk.tsha : '' };
  let res;
  try { res = await command(await signed(on ? 'task_on' : 'task_off', target, { t: 'task_set', on, ...target })); } catch (e) { res = { ok: false, why: e.message }; }
  toast(res.ok ? t(on ? 'tasks.enabled' : 'tasks.disabled') : t('tasks.fail', { why: why(res.why) }), 3200);
  loadTasks();
}
