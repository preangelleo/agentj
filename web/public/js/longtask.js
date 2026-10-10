// 0.18 long tasks (ADR-A193, PROTOCOL §20): the one opening card and the 「我的 Agent 会什么」 sheet. Both only appear when
// the main Agent opens them from a sentence in chat — there is no button for them. The opening card collects the owner's
// real choices (≤ 5), shows what still needs setup (never a secret field: keys go to the secret card), the task brief and,
// for a new workflow or a schedule, what 「确认并开始」 confirms. One tap signs up to three things with this device's approval
// key: the card answer (agentj.preflight.v1), the brief (agentj.brief.v1) and enabling the schedule (an ordinary task_on
// control). The computer checks each one; nothing here approves spending, deleting, sending, credentials or prices.
import { sendApp, gen, channel, randHex } from './session.js';
import { preflightBytes, briefBytes, controlMessage, b64u } from '../proto/wire.js';
import { signKey } from './store.js';
import { myDeviceId } from './api.js';
import { t, onLang, lang } from './t.js';
import { mk, toast } from './ui.js';

const cards = new Map();            // id → card message (arrival order)
let root = null, caps = null, shownId = null, busy = false, timer = 0;
let capsMsg = null, capsFilter = 'all';
const picks = new Map();            // item id → value (text) | string | string[]

const ID32 = /^[0-9a-f]{32}$/;
const loc = (o) => (o && typeof o === 'object' ? (lang() === 'en' ? o.en : o.zh) || o.zh || o.en || '' : String(o ?? ''));

function build() {
  if (root) return root;
  root = mk('div', 'ajmodal lt');
  root.id = 'lt';
  root.hidden = true;
  root.setAttribute('role', 'dialog');
  root.setAttribute('aria-modal', 'true');
  root.setAttribute('aria-labelledby', 'lt-title');
  const box = mk('div', 'box');
  box.id = 'lt-box';
  root.appendChild(box);
  document.body.appendChild(root);
  onLang(() => { shownId = null; render(); paintCaps(); });
  return root;
}

/** lt_card: a new (or re-sent) opening card. Malformed → ignored (the Agent is told by the computer, not by us). */
export function add(m) {
  if (!m || typeof m.id !== 'string' || !ID32.test(m.id) || !m.card || m.card.card_id !== m.id) return;
  const c = m.card;
  if (!Array.isArray(c.required) || c.required.length > 5 || !Array.isArray(c.optional) || typeof c.nonce !== 'string') return;
  const ttl = Number.isInteger(m.ttl) && m.ttl >= 0 && m.ttl <= 1800 ? m.ttl : 1800;
  cards.set(m.id, { ...m, deadline: Date.now() + ttl * 1000 });
  if (shownId === m.id) shownId = null;
  render();
}
/** lt_done: the computer finished with a card (ready / needs_input / cancelled / blocked). */
export function done(m) {
  if (!m || !cards.has(m.id)) {
    if (m && m.status === 'ready' && m.enabled) toast(t('lt.done.enabled'), 3600);
    return;
  }
  cards.delete(m.id);
  if (shownId === m.id) { shownId = null; busy = false; }
  const key = { ready: m.enabled ? 'lt.done.enabled' : 'lt.done.ready', needs_input: 'lt.done.needs', cancelled: 'lt.done.cancelled',
    blocked: 'lt.done.blocked', expired: 'lt.done.expired' }[m.status];
  if (key) toast(t(key), 4200);
  render();
}
/** lt_res: the computer's answer to OUR submit (ok, or why it refused — the card stays open on a refusal). */
export function res(m) {
  if (!m) return;
  const c = cards.get(m.id);
  if (m.ok) {
    if (c) { cards.delete(m.id); if (shownId === m.id) shownId = null; }
    busy = false; render();
    return;
  }
  busy = false;
  const why = ['expired', 'replay', 'changed', 'bad_signature', 'no_key', 'picks', 'secret', 'unknown'].includes(m.why) ? m.why : 'other';
  toast(t('lt.why.' + why), 5000);
  if (why === 'expired' || why === 'unknown' || why === 'changed') { cards.delete(m.id); shownId = null; }
  render();
}
export function clear() {
  cards.clear(); picks.clear(); shownId = null; busy = false; capsMsg = null;
  if (root) root.hidden = true;
  if (caps) caps.hidden = true;
  clearTimeout(timer);
}
export const openCount = () => cards.size;
export const isOpen = () => (!!root && !root.hidden) || (!!caps && !caps.hidden);

function section(box, title, cls) {
  const s = mk('section', 'lt-sec ' + (cls || ''));
  if (title) s.appendChild(mk('h3', 'lt-h', title));
  box.appendChild(s);
  return s;
}
function choiceInput(item, spec) {
  const wrap = mk('div', 'lt-input');
  const cur = picks.get(item.id);
  if (spec.type === 'text') {
    const i = mk('textarea', 'lt-text');
    i.id = 'lt-in-' + item.id;
    i.rows = 2; i.maxLength = 500;
    i.value = typeof cur === 'string' ? cur : (typeof spec.default === 'string' ? spec.default : '');
    picks.set(item.id, i.value);
    i.setAttribute('aria-label', loc(item.why));
    i.addEventListener('input', () => { picks.set(item.id, i.value); paintSubmit(); });
    wrap.appendChild(i);
    return wrap;
  }
  const multi = spec.type === 'multi';
  let sel = multi ? (Array.isArray(cur) ? cur : (Array.isArray(spec.default) ? spec.default.slice() : [])) : (typeof cur === 'string' ? cur : (typeof spec.default === 'string' ? spec.default : ''));
  picks.set(item.id, sel);
  for (const o of spec.options) {
    const b = mk('button', 'lt-opt', o);
    b.type = 'button';
    const on = () => (multi ? sel.includes(o) : sel === o);
    b.setAttribute('aria-pressed', on() ? 'true' : 'false');
    b.addEventListener('click', () => {
      if (multi) sel = on() ? sel.filter((x) => x !== o) : [...sel, o];
      else sel = o;
      picks.set(item.id, sel);
      for (const x of wrap.querySelectorAll('.lt-opt')) x.setAttribute('aria-pressed', (multi ? sel.includes(x.textContent) : sel === x.textContent) ? 'true' : 'false');
      paintSubmit();
    });
    wrap.appendChild(b);
  }
  return wrap;
}
const STATUS = { ready: 'lt.st.ready', missing: 'lt.st.missing', unverified: 'lt.st.unverified', verifying: 'lt.st.verifying', failed: 'lt.st.failed' };
const ACTION = { login: 'lt.act.login', secret_card: 'lt.act.secret', verify: 'lt.act.verify', choose: 'lt.act.choose' };
function itemRow(parent, item) {
  const r = mk('div', 'lt-item');
  r.appendChild(mk('p', 'lt-why', loc(item.why)));
  const meta = mk('p', 'lt-meta');
  const choose = item.action_kind === 'choose';
  meta.append(mk('span', 'lt-badge lt-badge--' + (choose ? 'choose' : item.status), t(choose ? 'lt.act.choose' : (STATUS[item.status] || 'lt.st.unverified'))), document.createTextNode(' ' + loc(item.how)));
  r.appendChild(meta);
  r.appendChild(mk('p', 'lt-reuse', t('lt.reuse', { text: loc(item.reuse) })));
  parent.appendChild(r);
  return r;
}
/** "0 9 * * 1" → 「每周一 09:00」 / "Mondays 09:00"; "30 8 * * *" → 「每天 08:30」; anything else stays as written. */
export function cronText(expr) {
  const m = /^(\d{1,2}) (\d{1,2}) \* \* (\*|[0-7])$/.exec(String(expr || ''));
  if (!m) return String(expr || '');
  const hm = String(m[2]).padStart(2, '0') + ':' + String(m[1]).padStart(2, '0');
  if (m[3] === '*') return t('lt.cron.daily', { hm });
  return t('lt.cron.weekly', { day: t('lt.cron.d' + (Number(m[3]) % 7)), hm });
}
function planText(b) {
  if (!b || !b.plan) return '';
  if (b.plan.kind === 'weekly') return t('lt.plan.weekly', { when: cronText(b.schedule), tz: b.tz || b.plan.timezone || '' });
  return t('lt.plan.once');
}
function render() {
  build();
  clearTimeout(timer);
  const c = cards.values().next().value;
  if (!c) { root.hidden = true; document.body.dataset.lt = '0'; return; }
  document.body.dataset.lt = '1';
  if (shownId !== c.id) {
    shownId = c.id;
    picks.clear();
    const box = root.querySelector('#lt-box');
    box.replaceChildren();
    const b = c.brief || {};
    box.appendChild(mk('p', 'lt-kicker', t('lt.kicker')));
    const h = mk('h2', 'lt-title', loc(b.title) || b.goal || '');
    h.id = 'lt-title';
    box.appendChild(h);
    const what = section(box, t('lt.what'));
    what.appendChild(mk('p', 'lt-goal', b.goal || ''));
    const dl = mk('ul', 'lt-list');
    for (const d of b.deliverables || []) dl.appendChild(mk('li', '', t(d.audience === 'owner' ? 'lt.deliver.owner' : 'lt.deliver.local', { path: d.path })));
    const p = planText(b);
    if (p) dl.appendChild(mk('li', '', p));
    what.appendChild(dl);
    const choose = c.card.required.filter((x) => x.action_kind === 'choose');
    const setup = c.card.required.filter((x) => x.action_kind !== 'choose');
    if (choose.length) {
      const s = section(box, t('lt.choose', { n: choose.length }));
      for (const it of choose) { const r = itemRow(s, it); const spec = c.asks && c.asks[it.id]; if (spec) r.appendChild(choiceInput(it, spec)); }
    }
    if (setup.length) {
      const s = section(box, t('lt.setup'));
      for (const it of setup) itemRow(s, it);
      s.appendChild(mk('p', 'lt-note', t('lt.setupNote')));
    }
    if (c.card.optional.length) {
      const det = mk('details', 'lt-sec lt-opt-sec');
      det.appendChild(mk('summary', '', t('lt.optional', { n: c.card.optional.length })));
      for (const it of c.card.optional) itemRow(det, it);
      box.appendChild(det);
    }
    const clauses = section(box, t('lt.confirms'), 'lt-clauses');
    const ul = mk('ul', 'lt-list');
    if (b.confirm) ul.appendChild(mk('li', '', t('lt.clause.brief', { wf: b.workflow || '' })));
    if (c.enable) ul.appendChild(mk('li', '', t('lt.clause.enable', { when: cronText(b.schedule), tz: b.tz || '' })));
    if (choose.length) ul.appendChild(mk('li', '', t('lt.clause.choices')));
    ul.appendChild(mk('li', 'lt-safe', t('lt.clause.safe')));
    clauses.appendChild(ul);
    const full = mk('details', 'lt-sec lt-full');
    full.appendChild(mk('summary', '', t('lt.fullBrief')));
    const fl = mk('ul', 'lt-list');
    for (const a of b.acceptance || []) fl.appendChild(mk('li', '', t('lt.full.accept', { text: a })));
    for (const r of b.red_lines || []) fl.appendChild(mk('li', '', t('lt.full.red', { text: r })));
    full.appendChild(fl);
    box.appendChild(full);
    box.appendChild(mk('p', 'lt-left', ''));
    const go = mk('button', 'aj-btn aj-btn--primary lt-go', t(b.confirm || c.enable ? 'lt.go' : 'lt.goShort'));
    go.type = 'button'; go.id = 'lt-go';
    go.addEventListener('click', () => decide('submit'));
    const back = mk('button', 'aj-btn aj-btn--quiet lt-back', t('lt.back'));
    back.type = 'button'; back.id = 'lt-back';
    back.addEventListener('click', () => decide('cancel'));
    box.append(go, back);
    root.hidden = false;
  }
  paintSubmit();
  paintLeft(c);
}
function complete(c) {
  for (const it of c.card.required) {
    if (it.action_kind !== 'choose') continue;
    const spec = c.asks && c.asks[it.id];
    const v = picks.get(it.id);
    if (!spec) return false;
    if (spec.type === 'text' && !(typeof v === 'string' && v.trim())) return false;
    if (spec.type === 'single' && !(typeof v === 'string' && v)) return false;
    if (spec.type === 'multi' && !(Array.isArray(v) && v.length)) return false;
  }
  return true;
}
function paintSubmit() {
  if (!root) return;
  const c = cards.get(shownId);
  const go = root.querySelector('#lt-go'), back = root.querySelector('#lt-back');
  if (go) go.disabled = busy || !c || !complete(c);
  if (back) back.disabled = busy;
}
function paintLeft(c) {
  const n = root.querySelector('.lt-left');
  if (!n) return;
  const left = Math.max(0, Math.ceil((c.deadline - Date.now()) / 1000));
  n.textContent = left > 0 ? t('lt.left', { m: Math.ceil(left / 60) }) : t('lt.timeUp');
  if (left > 0) timer = setTimeout(() => { if (cards.get(c.id) === c) paintLeft(c); }, 15000);
}

async function decide(action) {
  const c = cards.get(shownId);
  if (!c || busy) return;
  if (action === 'submit' && !complete(c)) { toast(t('lt.incomplete'), 3000); return; }
  const g = gen();
  if (!g) { toast(t('elev.net'), 3000); return; }
  const sk = await signKey();
  if (!sk) { toast(t('elev.noSign'), 3600); return; }
  busy = true; paintSubmit();
  try {
    const ch = channel(), dev = await myDeviceId();
    const p = {};
    if (action === 'submit') for (const it of c.card.required) if (it.action_kind === 'choose' && c.asks && c.asks[it.id]) {
      const v = picks.get(it.id);
      p[it.id] = typeof v === 'string' ? v.trim() : v;
    }
    const sign = async (bytes) => b64u(new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, sk.priv, bytes)));
    const m = { t: 'lt_answer', id: c.id, action, n: c.card.nonce, sig: await sign(preflightBytes(ch, dev, c.card, action, p)) };
    if (action === 'submit') {
      m.picks = p;
      if (c.brief && c.brief.confirm) m.bsig = await sign(briefBytes(ch, dev, c.card, c.brief.scope_digest, 'submit'));
      if (c.enable) {
        const n = randHex(16), ts = Date.now();
        m.en = { n, ts, sig: await sign(await controlMessage(ch, dev, 'task_on', n, ts, c.enable)) };
      }
    }
    await sendApp(m, g);
    toast(t(action === 'submit' ? 'lt.sent' : 'lt.sentBack'), 3000);
  } catch {
    busy = false; paintSubmit();
    toast(t('elev.fail'), 3000);
  }
}

// ---------------------------------------------------------------- 「我的 Agent 会什么」 (lt_caps, read-only)
function buildCaps() {
  if (caps) return caps;
  caps = mk('div', 'ajmodal lt lt-caps');
  caps.id = 'lt-caps';
  caps.hidden = true;
  caps.setAttribute('role', 'dialog');
  caps.setAttribute('aria-modal', 'true');
  caps.setAttribute('aria-labelledby', 'lt-caps-title');
  const box = mk('div', 'box');
  box.id = 'lt-caps-box';
  caps.appendChild(box);
  document.body.appendChild(caps);
  return caps;
}
const READY = (s) => s === 'ready';
export function showCaps(m) {
  if (!m || !Array.isArray(m.items)) return;
  capsMsg = { items: m.items.slice(0, 1000).filter((x) => x && typeof x.id === 'string' && x.title) };
  capsFilter = 'all';
  paintCaps();
}
function fmtTime(s) {
  const d = typeof s === 'string' ? new Date(s) : null;
  return d && !isNaN(d) ? d.toLocaleString(lang() === 'en' ? 'en-GB' : 'zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
}
function paintCaps() {
  if (!capsMsg) return;
  buildCaps();
  const box = caps.querySelector('#lt-caps-box');
  box.replaceChildren();
  const h = mk('h2', 'lt-title', t('lt.caps.title'));
  h.id = 'lt-caps-title';
  box.appendChild(h);
  const items = capsMsg.items;
  const ready = items.filter((x) => READY(x.status)).length;
  box.appendChild(mk('p', 'lt-goal', t('lt.caps.sum', { ready, setup: items.length - ready })));
  const bar = mk('div', 'lt-filter');
  for (const f of ['all', 'ready', 'setup']) {
    const b = mk('button', 'lt-opt', t('lt.caps.f.' + f));
    b.type = 'button';
    b.setAttribute('aria-pressed', capsFilter === f ? 'true' : 'false');
    b.addEventListener('click', () => { capsFilter = f; paintCaps(); });
    bar.appendChild(b);
  }
  box.appendChild(bar);
  const list = mk('div', 'lt-caps-list');
  for (const it of items) {
    if (capsFilter === 'ready' && !READY(it.status)) continue;
    if (capsFilter === 'setup' && READY(it.status)) continue;
    const det = mk('details', 'lt-cap');
    const sum = mk('summary', '');
    sum.append(mk('span', 'lt-badge lt-badge--' + (READY(it.status) ? 'ready' : it.status === 'stale' ? 'unverified' : 'missing'),
      t(READY(it.status) ? 'lt.st.ready' : it.status === 'stale' ? 'lt.st.stale' : 'lt.st.setup')), document.createTextNode(' ' + loc(it.title)));
    det.appendChild(sum);
    det.appendChild(mk('p', 'lt-meta', t('lt.caps.kind.' + (['skill', 'cli', 'mcp', 'browser', 'credential', 'ceo'].includes(it.kind) ? it.kind : 'cli'))));
    det.appendChild(mk('p', 'lt-meta', t('lt.caps.verified', { at: fmtTime(it.verified), until: fmtTime(it.expires) })));
    det.appendChild(mk('p', 'lt-reuse', t(READY(it.status) ? 'lt.caps.reuse' : 'lt.caps.how')));
    list.appendChild(det);
  }
  box.appendChild(list);
  const close = mk('button', 'aj-btn aj-btn--primary lt-go', t('lt.caps.close'));
  close.type = 'button';
  close.addEventListener('click', () => { caps.hidden = true; capsMsg = null; });
  box.appendChild(close);
  caps.hidden = false;
}
