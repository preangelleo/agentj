// Phone controls (PROTOCOL §8): what it remembers, the activity record, scheduled tasks, 全部停下 / 恢复, batch grants.
// Reads are plain requests answered to this session only; every write is signed with the approval key over (action,
// nonce, time, SHA-256 of the target as shown) — the host does nothing without a valid signature. Nothing is stored here.
import { controlMessage, b64u, unb64u, workflowResumeMessage } from '../proto/wire.js';
import { request, done, isReady, channel, sendApp, randHex, gen, peer, whenReady } from './session.js';
import { signKey } from './store.js';
import { myDeviceId } from './api.js';
import { t, lang, locale, plain, fillText } from './t.js';
import { el, mk, toast, executionToast, toastAction, confirmSheet } from './ui.js';
import { approve as passkeyApprove } from './faceid.js';
import { copyable } from './copycode.js';   // P292
import { faState, webauthnErr, failNotice } from './pkassist.js';   // P539: why a passkey tap failed, said with one way on

let H = null;          // {show(view), estopChanged(), friends()}
export function configure(h) { H = h; el('set-estop')?.addEventListener('click', onEstop); // @ix app.stop_all
 }
export let panel = 'chat';
export const estop = { on: false, by: '' };

const GEN = Symbol('gen');                            // the session a signed write was made for (never serialized)
export async function signed(action, target, extra) {   // P203: cardsync signs its read the same way
  const sk = await signKey();
  const g = gen(), ch = channel();
  if (!g || !sk) throw new Error(sk ? 'offline' : 'no_key');
  let prepared = null, fa;
  if (peer.mailuv1 && action === 'host_write') {
    prepared = await new Promise(resolve => {
      let r; const timer = setTimeout(() => { if (r) done(r); resolve(null); }, 10000);
      r = request({t:'policy_prepare', action, target}, m => {
        if (m.t !== 'policy_ready' && m.t !== 'offline') return;
        clearTimeout(timer); done(r); resolve(m.t === 'policy_ready' ? m : null);
      }, g);
    });
    if (!prepared) throw new Error('offline');
    target = prepared.target || target;
    if (prepared.level === 'uv') {
      if (!prepared.fa || !prepared.fa_ch) { toast(t('ask.passkeyRequired'),6000); throw new Error('no_passkey'); }
      if (!await confirmSheet(t('p336.faTitle'), prepared.summary, t('p336.faYes'), false, undefined, prepared.details || '')) throw new Error('cancel');
      fa = await passkeyApprove(unb64u(prepared.fa_ch), prepared.fa);
    }
  }
  // P566 (#364): a Face ID sheet may have cost the socket — its answer (bound to this remote and the host's nonce, never to
  // the socket) goes on the reconnected one; signed for the computer it was asked on (channel() is null in the gap)
  let g2 = gen();
  if (g2 !== g) { if (!fa) throw new Error('offline'); g2 = g2 || await whenReady(); if (!g2 || channel() !== ch) throw new Error('offline'); }
  const n = prepared?.n || randHex(16), ts = Date.now();
  const msg = await controlMessage(ch, await myDeviceId(), action, n, ts, target);
  const sig = new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, sk.priv, msg));
  return { ...extra, ...(fa ? {fa} : {}), n, ts, sig: b64u(sig), [GEN]: g2 };
}
function command(obj, onSent = null) {               // pending acknowledgement extends only this request
  return new Promise((resolve) => {
    let timer, extended = false;
    const finish = (m) => { clearTimeout(timer); done(r); resolve(m); };
    const arm = (ms) => { clearTimeout(timer); timer = setTimeout(() => finish({ ok: false, why: 'timeout' }), ms); };
    const r = request(obj, (m) => {
      if (m.t === 'ctl_res' || m.t === 'offline') finish(m.t === 'offline' ? { ok: false, why: 'offline' } : m);
      else if (!extended && m.t === 'ctl_pending' && obj.t === 'wf_resume_do' && m.action === 'wf_resume_do' && m.timeout_ms === 620000) { extended = true; arm(620000); }
      else if (!extended && m.t === 'ctl_pending' && obj.t === 'bots_write' && m.action === 'bots_write' && m.timeout_ms === 620000) { extended = true; arm(620000); }
      else if (!extended && m.t === 'ctl_pending' && obj.t === 'cmd_run' && m.action === 'cmd_run' && m.timeout_ms === 150000) { extended = true; arm(150000); }   // P361
      // P570c: 重新迁移 test-runs the task on the system scheduler (the host's sysmigrate.PENDING_MS = 2 200 000 at most)
      else if (!extended && m.t === 'ctl_pending' && obj.t === 'task_migrate' && m.action === 'task_migrate' && Number.isInteger(m.timeout_ms) && m.timeout_ms >= 20000 && m.timeout_ms <= 2200000) { extended = true; arm(m.timeout_ms); }
    }, obj[GEN] ?? gen(), onSent);
    if (!r) resolve({ ok: false, why: 'offline' });
    else arm(20000);
  });
}
/** One signed write → ctl_res, never throws (the friends page, §17.7: fr_set pg_set pg_del fr_add fr_discoverable fr_card). */
export async function signedWrite(action, target, extra) {
  try { return await command(await signed(action, target, extra)); } catch (e) { return { ok: false, why: e.message }; }
}
/** P336: like signedWrite, but a `passkey_required` answer asks `faceId(res)` for the Face ID `fa` object and resends the SAME
 *  signed object (same n / ts / sig) with it; null from faceId = the owner cancelled → {ok:false, why:'cancel'}. */
export async function signedWriteFa(action, target, extra, faceId) {
  let obj;
  const ch = channel();
  try { obj = await signed(action, target, extra); } catch (e) { return { ok: false, why: e.message }; }
  const res = await command(obj);
  if (res.ok || res.why !== 'passkey_required') return res;
  let fa;
  // P539: a WebAuthn rejection keeps its name (never its message) and the host's `fa_s`, so the card can say why
  try { fa = await faceId(res); } catch (e) { return { ok: false, why: 'passkey_failed', err: webauthnErr(e), fa_s: faState(res.fa_s) }; }
  return fa ? commandFa({ ...obj, fa }, ch) : { ok: false, why: 'cancel' };
}
export const FA_HANDIN_MS = 60000;
/** P566 (#364): the request that carries the Face ID result — on the session it was signed for, else on the first one after
 *  the drop the sheet caused (same computer); ≤ FA_HANDIN_MS. Sent again ONLY while no copy has reached the socket (security
 *  review S1: the page-chosen nonce lives in the Agent J host's memory — a copy after a serve restart could run twice); once a
 *  copy went out, a lost answer stays 「offline」 as before. */
export async function commandFa(obj, ch, until = Date.now() + FA_HANDIN_MS) {
  for (let first = true; ; first = false) {
    if (!first) await new Promise((r) => setTimeout(r, 250));
    const g = first && obj[GEN] && obj[GEN] === gen() ? obj[GEN] : gen() || await whenReady(Math.max(0, until - Date.now()));
    if (!g || (ch && channel() !== ch)) return { ok: false, why: 'offline' };
    let sent = false;
    const res = await command({ ...obj, [GEN]: g }, () => { sent = true; });
    if (res.why !== 'offline' || sent || Date.now() >= until) return res;
  }
}
const WHY_CODES = ['changed', 'unknown_item', 'bad_signature', 'no_key', 'stale', 'timeout', 'offline', 'not_found', 'exists', 'symlink',
  'invalid', 'unknown', 'io', 'shape', 'replay', 'rejected', 'passkey_failed'];
const why = (code) => (WHY_CODES.includes(code) ? t('why.' + code) : String(code ?? ''));
export const whyText = why;

export function openPanel(v) {
  if (!['chat', 'mem', 'act', 'tasks', 'friends', 'bots', 'setup'].includes(v)) return;
  panel = v;
  H.show(v === 'chat' ? 'chat' : v);
  if (v === 'bots') H.bots();
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
  const res = await signedWriteFa('resume', {}, { t: 'resume' }, H.faceId);
  if (!res.ok && res.err) failNotice(res.err, res.fa_s, onResume);   // P539: the reason + 再试一次 / 在本机设置通行密钥
  else if (!res.ok) toast(t('estop.resumeFail', { why: why(res.why) }), 4000);
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
  if (code !== 'turn_end') executionToast(t('grant.end.' + (GRANT_END.includes(code) ? code : 'other')), 3200);
}
export function clearGrants() { grants.clear(); renderGrants(); }
export const grantIds = () => [...grants.keys()];                                                     // P203 (§29.1)
export function pruneGrants(gone) { for (const id of [...grants.keys()]) if (gone(id)) endGrant(id, 'turn_end'); }   // quietly
export async function revokeGrants() {
  if (!isReady() || grants.size === 0) return;
  await sendApp({ t: 'grant_off', id: null }).catch(() => {});
}
export function showAuto(m) {
  if (typeof m.tool !== 'string' || typeof m.summary !== 'string') return;
  executionToast(t(m.by === 'jev' ? 'ask.autoJev' : m.by === 'goal' ? 'ask.autoGoal' : m.by === 'task' ? 'p573.autoTask' : m.by === 'full' ? 'p336.autoFull' : 'ask.auto', { tool: m.tool, summary: m.summary.slice(0, 120) }), 2600);
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
        body.append(copyable(mk('pre', 'mem-text', it.text + (it.cut ? '\n' + t('mem.cut', { shown: it.text.length, total: it.cut }) : '')), { click: false, text: () => it.text }));   // P292: ⧉ copies the memory
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
const CATS = ['spend', 'delete', 'send', 'credentials', 'price', 'opaque'];
const ACT_RESULTS = ['allow', 'deny', 'allow_batch', 'timeout', 'no_device', 'estop', 'serve_stop', 'agent_gone', 'too_many', 'policy', 'goal_auto', 'parked'];   // P336: parked = nobody online, the card waits
const ACT_KINDS = ['turn_start', 'turn_end', 'ask', 'decision', 'auto', 'grant', 'grant_end', 'estop', 'resume', 'message_refused', 'mem_rm',
  'mem_undo', 'task_on', 'task_off', 'task_run', 'task_done', 'control_refused', 'slash', 'truncated', 'jev',
  'goal_propose', 'goal_start', 'goal_declined', 'goal_auto', 'goal_progress', 'goal_end'];   // P158
function actText(r) {
  const by = r.by ? t('act.by', { by: r.by }) : '';
  const tx = String(r.text ?? r.summary ?? '').replace(/\s+/g, ' ').slice(0, 200);
  const sep = lang() === 'en' ? ', ' : '、';
  const cats = Array.isArray(r.cats) && r.cats.length ? `[${r.cats.map((c) => (CATS.includes(c) ? t('ask.cat.' + (c === 'credentials' && r.cred === 'read' ? 'credentials_read' : c)) : c)).join(sep)}] ` : '';
  const task = r.task ? t('act.task', { task: r.task }) : '';
  if (r.k === 'auto_update') return tx;
  if (!ACT_KINDS.includes(r.k)) return String(r.k ?? '');
  const v = {
    by, t: tx, task, cats, tool: r.tool ?? '', scope: r.scope ?? '', why: (lang() === 'en' && r.why_en ? r.why_en : r.why) ?? '', label: r.label ?? '', id: r.id ?? '',
    how: r.how ?? '', tier: r.tier ?? '',
    title: r.title ?? r.id ?? '', verdict: r.verdict ?? '', line: r.line ?? '', action: r.action ?? '', cmd: r.cmd ?? '',
    ro: r.readonly ? t('act.k.readonly') : '',
    stopped: r.result === 'stopped' ? t('act.k.turn_end_stopped') : '',
    secs: r.secs !== undefined ? t('act.k.secs', { n: r.secs }) : '',
    result: r.k === 'slash' ? (['ok', 'info', 'error', 'refused'].includes(r.result) ? t('cmd.result.' + r.result) : r.result ?? '')
      : ACT_RESULTS.includes(r.result) ? t('act.result.' + r.result) : r.result ?? '',
  };
  if (r.k === 'decision') v.t = tx ? ' ' + tx : '';
  if (r.k === 'auto' && r.by === 'task') return t('p573.act.task', v);   // P573: under this task's grant
  if (r.k === 'auto' && ['level_full', 'goal_nocard', 'parked', 'background', 'dispatch_headless'].includes(r.by)) return t('p336.act.' + r.by, v);   // P336 · P552 background
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
      fillText(el('act-status'), (m.on === false ? t('act.off') + ' ' : '') + (n ? t('act.recent', { n }) : t('act.none')));   // P292: `agentj config activity on` + ⧉
    }
  });
}

// ---- 定时任务 (scheduled tasks)
export function loadTasks() {
  loadSysTasks();
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

// ---- P207 §2: the computer's own scheduled tasks (systemd timers / crontab / LaunchAgents / Task Scheduler), read-only.
// Only a host announcing `systasks` is asked; rows carry name, schedule, next / last run, state, source — never a command.
const SYS_STATES = { active: 'sysActive', waiting: 'sysWaiting', failed: 'sysFailed', disabled: 'sysDisabled' };
const SYS_SRC = { systemd: 'srcSystemd', cron: 'srcCron', launchd: 'srcLaunchd', schtasks: 'srcSchtasks' };
function loadSysTasks() {
  const box = el('systasks'), ul = el('systasks-list'), st = el('systasks-status');
  const on = peer.systasks === true;
  box.hidden = !on; el('tasks-h-own').hidden = !on;
  ul.replaceChildren();
  if (!on) return;
  st.textContent = t('tasks.sysLoading');
  const r = request({ t: 'systask_list' }, (m) => {
    if (m.t !== 'systasks') return;
    for (const x of Array.isArray(m.items) ? m.items.slice(0, 200) : []) if (x && typeof x === 'object') ul.append(sysCard(x));
    if (m.more === false) {
      done(r);
      const n = ul.children.length;
      st.textContent = n ? t('tasks.sysCount', { n }) + (Number.isInteger(m.max) && n >= m.max ? ' · ' + t('tasks.sysMax', { n: m.max }) : '') : t('tasks.sysNone');
    }
  });
}
const str = (v, n = 120) => (typeof v === 'string' ? v.slice(0, n) : '');
const atText = (x) => new Date(x * 1000).toLocaleString(locale(), { hour12: false, month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
function durText(secs) {
  const n = Number(secs);
  if (!Number.isFinite(n) || n <= 0) return '';
  if (n % 86400 === 0) return t('tasks.durD', { n: n / 86400 });
  if (n % 3600 === 0) return t('tasks.durH', { n: n / 3600 });
  if (n % 60 === 0 || n >= 3600) return t('tasks.durM', { n: Math.round(n / 60) });
  return t('tasks.durS', { n });
}
const CRON_AT = { '@hourly': '0 * * * *', '@daily': '0 0 * * *', '@midnight': '0 0 * * *', '@weekly': '0 0 * * 0', '@monthly': '0 0 1 * *' };
const CAL_DAYS = { sun: 0, mon: 1, tue: 2, wed: 3, thu: 4, fri: 5, sat: 6 };
function calText(v) {                                   // systemd OnCalendar → the cron sentence when it is a simple one
  const short = { hourly: '0 * * * *', daily: '0 0 * * *', weekly: '0 0 * * 1', monthly: '0 0 1 * *', minutely: '* * * * *' }[v.trim().toLowerCase()];
  if (short) return cronText(short);
  const m = /^(?:([A-Za-z]{3}(?:,[A-Za-z]{3})*) )?(?:\*-\*-\* )?(\d{1,2}|\*):(\d{1,2})(?::00)?(?: [A-Za-z_]+(?:\/[A-Za-z_]+)?)?$/.exec(v.trim());
  if (m) {
    const dow = m[1] ? m[1].toLowerCase().split(',').map((d) => CAL_DAYS[d]) : null;
    if (!dow || dow.every((d) => d !== undefined)) return cronText(`${+m[3]} ${m[2] === '*' ? '*' : +m[2]} * * ${dow ? dow.join(',') : '*'}`);
  }
  return t('tasks.cal', { v });
}
export function sysWhen(list) {
  const parts = [];
  for (const w of Array.isArray(list) ? list.slice(0, 3) : []) {
    if (!w || typeof w !== 'object') continue;
    if (w.k === 'cron' && typeof w.v === 'string') parts.push(cronText(CRON_AT[w.v] ?? w.v));
    else if (w.k === 'every' && Number.isFinite(w.v)) parts.push(t('tasks.every', { d: durText(w.v) }));
    else if (w.k === 'boot' && Number.isFinite(w.v)) parts.push(w.v > 0 ? t('tasks.boot', { d: durText(w.v) }) : t('tasks.bootNow'));
    else if (w.k === 'cal' && typeof w.v === 'string') parts.push(calText(str(w.v, 80)));
    else if (w.k === 'raw' && typeof w.v === 'string') parts.push(str(w.v, 60));
  }
  return parts.length ? parts.join(lang() === 'en' ? '; ' : '；') : t('tasks.whenUnknown');
}
function sysCard(x) {
  const li = mk('li', 'task-card systask-card');
  const state = SYS_STATES[x.state] ? x.state : 'waiting';
  const src = SYS_SRC[x.src] ? x.src : '';
  li.dataset.src = src; li.dataset.state = state;
  const name = str(x.name) || str(x.id) || '?';
  li.append(mk('p', 'task-title', name));
  const tags = mk('p', 'ask-tags');
  tags.append(mk('span', 'tag ' + ({ active: 'tag-task', waiting: 'tag-on', failed: 'tag-fail', disabled: 'tag-off' }[state]), t('tasks.' + SYS_STATES[state])));
  if (src) tags.append(mk('span', 'tag', t('tasks.' + SYS_SRC[src])));
  li.append(tags);
  let line = t('tasks.sysWhen', { when: sysWhen(x.when) });
  if (Number.isInteger(x.next) && x.next > 0) line += t('tasks.next', { at: atText(x.next) });
  else if (str(x.next_s, 40)) line += t('tasks.next', { at: str(x.next_s, 40) });
  if (Number.isInteger(x.last) && x.last > 0) line += t('tasks.sysLast', { at: atText(x.last) });
  else if (str(x.last_s, 40)) line += t('tasks.sysLast', { at: str(x.last_s, 40) });
  li.append(mk('p', 'small', line));
  const id = str(x.id, 80);
  if (id && id !== name) li.append(mk('p', 'small muted systask-id', id));
  return li;
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
  const sys = SYS_MOVED.includes(tk.sys) ? tk.sys : '';   // P570c: moved to the Agent J host's scheduler (an old host never sends it)
  if (sys) li.dataset.sys = sys;
  if (sys) tags.append(mk('span', 'tag ' + (sys === 'migrated' ? 'tag-on' : 'tag-fail'), t('p570c.' + sys)));
  else tags.append(mk('span', 'tag ' + (tk.enabled ? 'tag-on' : 'tag-off'), t(tk.problems?.length ? 'tasks.invalid' : tk.enabled ? (paused ? 'tasks.onPaused' : 'tasks.on') : tk.stale ? 'tasks.stale' : 'tasks.off')));
  if (tk.running) tags.append(mk('span', 'tag tag-task', t('tasks.running')));
  li.append(tags);
  li.append(mk('p', 'small', t('tasks.when', { when: cronText(tk.schedule), tz: tzText(tk) }) + (Number.isInteger(tk.next) ? t('tasks.next', { at: new Date(tk.next * 1000).toLocaleString(locale(), { hour12: false, month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) }) : '')));
  if (tk.problems?.length) li.append(mk('p', 'error small', t('tasks.bad', { p: tk.problems[0] })));
  if (tk.last && typeof tk.last === 'object') li.append(mk('p', 'small', t('tasks.last', { verdict: tk.last.verdict ?? '', line: tk.last.line ?? '' })));
  if (peer.wfresume1 && /^[0-9a-f]{32}$/.test(tk.resume?.run_id || '')) li.append(resumeButton(tk.resume.run_id));
  if (sys) {                                            // P570c: 退回 (task_set off) · 重新迁移 (task_migrate) — no plain 停用 here
    const row = mk('div', 'aj-actions task-sys-acts');
    if (sys === 'changed') {
      const m = mk('button', 'aj-btn aj-btn--primary aj-btn--sm', t('p570c.remigrate')); m.type = 'button'; m.dataset.act = 'remigrate';
      m.addEventListener('click', () => remigrateTask(tk, title));
      row.append(m);
    }
    const b = mk('button', sys === 'migrated' ? 'aj-btn aj-btn--primary aj-btn--sm' : 'aj-btn aj-btn--sm', t('p570c.back')); b.type = 'button'; b.dataset.act = 'back';
    b.addEventListener('click', () => backTask(tk, title));
    row.append(b);
    li.append(row);
  } else if (!tk.problems?.length || tk.enabled) {
    const on = !tk.enabled;
    const b = mk('button', on ? 'aj-btn aj-btn--primary aj-btn--sm' : 'aj-btn aj-btn--sm', t(on ? 'tasks.enable' : 'tasks.disable')); b.type = 'button';
    b.addEventListener('click', () => setTask(tk, on, title));
    li.append(b);
  }
  return li;
}
export function cronText(expr) {
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
const SYS_MOVED = ['migrated', 'changed'];
const taskTarget = (tk) => ({ id: String(tk.id), tsha: typeof tk.tsha === 'string' ? tk.tsha : '' });
/** P570c: 退回 = task_set on:false on a moved task — it comes back to Agent J, still off. */
async function backTask(tk, title) {
  if (!await confirmSheet(t('p570c.backTitle'), t('p570c.backText', { title }), t('p570c.back'))) return;
  const target = taskTarget(tk);
  let res;
  try { res = await command(await signed('task_off', target, { t: 'task_set', on: false, ...target })); } catch (e) { res = { ok: false, why: e.message }; }
  toast(res.ok ? t('p570c.backDone') : t('tasks.fail', { why: why(res.why) }), 3200);
  loadTasks();
}
/** P570c: 重新迁移 = task_migrate {id, tsha} for a moved task that changed since; the host test-runs it and answers {ok, text}. */
async function remigrateTask(tk, title) {
  if (!await confirmSheet(t('p570c.migTitle'), t('p570c.migText', { title }), t('p570c.remigrate'))) return;
  const target = taskTarget(tk);
  let res;
  try { res = await command(await signed('task_migrate', target, { t: 'task_migrate', ...target })); } catch (e) { res = { ok: false, why: e.message }; }
  const said = typeof res.text === 'string' && res.text ? res.text.slice(0, 300) : '';   // the host's summary (moved / rolled back + why)
  toast(said || (res.ok ? t('p570c.migDone') : t('tasks.fail', { why: why(res.why) })), said ? 6000 : 3200);
  loadTasks();
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

// P498: fetch one exact checkpoint challenge, then sign and send on the SAME Noise generation.
export async function workflowResume(runId) {
  const g = gen(), ch = channel();
  if (!g || !peer.wfresume1 || !/^[0-9a-f]{32}$/.test(runId)) return {ok:false, why:'unsupported'};
  try {
    const res = await command({t:'wf_resume_get', run_id:runId, [GEN]:g});
    if (!res.ok) return res;
    const obj = res.challenge, device = await myDeviceId(), sk = await signKey();
    if (gen() !== g || channel() !== ch || !sk || obj.channel !== ch || obj.device !== device || obj.run_id !== runId) return {ok:false, why:'changed'};
    const signature = new Uint8Array(await crypto.subtle.sign('Ed25519', sk.priv, workflowResumeMessage(obj)));
    if (gen() !== g || channel() !== ch) return {ok:false, why:'changed'};
    return await command({t:'wf_resume_do', challenge:obj, sig:b64u(signature), [GEN]:g});
  } catch { return {ok:false, why:'refused'}; }
}

export function resumeButton(runId) {
  const button = mk('button', 'cmdundo', t('p498.resume'));
  button.type = 'button'; button.dataset.wfResume = runId; button.disabled = !isReady();
  button.addEventListener('click', async () => {
    if (button.disabled) return;
    button.disabled = true;
    const res = await workflowResume(runId);
    toast(t(res.ok ? 'p498.queued' : 'p498.refused'), 4000);
    button.disabled = !!res.ok || !isReady();
  });
  return button;
}
