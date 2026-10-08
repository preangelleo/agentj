// Agent friends (PROTOCOL §17.7, 0.16): the /friends page and the two cards the host puts in the main chat.
// The page is READ-ONLY for conversations: there is no field that sends text to a friend (the host would refuse it anyway,
// `fr_send_refused`); the owner speaks to a friend only through the main Agent. What the page can do is what Leo asked for:
// see friends and their conversations, add a friend by ID, change a friend's group, see usage, block / unblock / delete,
// show / edit my card, edit policy groups. Reads are plain requests on the paired session (fr_list, fr_hist, fr_usage);
// every write carries the §8 control signature of this device's approval key (controls.signedWrite).
// Host data is untrusted display text: textContent only, lengths capped.
import { ask1, isReady, gen, sendApp, channel } from './session.js';
import { friendAnswerMessage, parseAgentId, friendLink, GROUP_ID_RE, b64u, FRIEND_CTX_MAX } from '../proto/wire.js';
import { signKey } from './store.js';
import { myDeviceId, approve } from './api.js';
import { signedWrite, whyText } from './controls.js';
import { qrSvg } from './qr.js';
import { t, locale } from './t.js';
import { el, mk, toast, confirmSheet } from './ui.js';

let H = { openPanel: () => {}, agentName: () => 'Agent J' };
export function configure(h) { H = { ...H, ...h }; }

const V = { view: 'list', friend: null, group: null, addId: '', addNote: '', sent: false };
let data = null;                  // the last fr_list_res (normalized)
let hist = { friend: null, items: [], more: false, loading: false };
let usage = null;                 // the last fr_usage_res for V.friend
let ctxText = { friend: null, text: null };   // P73: the last fr_ctx_res (this friend's 「补充设定」, the owner's own text)
let ctxDraft = { friend: null, text: null };  // P73: what is typed and not saved yet (survives a redraw)
let loadSeq = 0;
const BUILTIN = ['default', 'friend', 'colleague'];
const WIN = ['min', 'hour', 'day', 'month'];

// ---------------------------------------------------------------- small helpers
const rowIn = (box) => { const r = mk('div', 'ajrow'); box.append(r); return r; };
const str = (v, n = 200) => (typeof v === 'string' ? v.slice(0, n) : '');
const int = (v) => (Number.isInteger(v) ? v : null);
const q = (s) => t('fr.quote', { s });
const shortId = (id) => (typeof id === 'string' && id.length > 12 ? id.slice(0, 7) + '…' + id.slice(-4) : String(id ?? ''));
function btn(cls, text, on, id) {
  const b = mk('button', 'aj-btn ' + (cls || ''), text); b.type = 'button';
  if (id) b.id = id;
  if (on) b.addEventListener('click', on);
  return b;
}
function when(ts) {
  if (!Number.isFinite(ts) || ts <= 0) return '';
  const d = new Date(ts), now = new Date();
  const same = d.toDateString() === now.toDateString();
  return d.toLocaleString(locale(), same ? { hour: '2-digit', minute: '2-digit', hour12: false } : { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
}
const nf = () => new Intl.NumberFormat(locale(), { notation: 'compact', maximumFractionDigits: 1 });
const num = (v) => (v == null ? '—' : nf().format(v));
export function groupName(g) {
  if (!g) return '';
  return g.builtin && BUILTIN.includes(g.id) ? t('fr.g.' + g.id) : str(g.name, 32) || String(g.id);
}
const groupById = (id) => (data ? data.groups.find((g) => g.id === id) : null);

function normGroup(g) {
  if (!g || typeof g !== 'object' || typeof g.id !== 'string' || !GROUP_ID_RE.test(g.id)) return null;
  const lim = g.limits && typeof g.limits === 'object' ? g.limits : {};
  const win = (o) => Object.fromEntries(WIN.map((w) => [w, int(o && o[w])]));
  const auto = g.auto && typeof g.auto === 'object' ? g.auto : {};
  const topics = (a) => (Array.isArray(a) ? a.filter((x) => typeof x === 'string').map((x) => x.slice(0, 80)).slice(0, 20) : []);
  return {
    id: g.id, name: str(g.name, 32), builtin: g.builtin === true,
    limits: { msg: win(lim.msg), tok: win(lim.tok), min_interval_s: int(lim.min_interval_s), max_len: int(lim.max_len) },
    auto: { mode: ['off', 'scoped', 'all'].includes(auto.mode) ? auto.mode : 'scoped', allow: topics(auto.allow), ask: topics(auto.ask),
      max_auto_rounds: int(auto.max_auto_rounds) ?? 6 },
  };
}
function normList(m) {
  const me = m.me && typeof m.me === 'object' ? m.me : {};
  const card = me.card && typeof me.card === 'object' ? me.card : {};
  const lastOf = (v) => (Number.isFinite(v) ? { ts: v, text: '' } : v && typeof v === 'object' ? { ts: Number(v.ts) || 0, text: str(v.text, 120) } : { ts: 0, text: '' });
  return {
    me: { id: parseAgentId(str(me.id, 64)) || str(me.id, 64), discoverable: me.discoverable !== false, on: me.on !== false,
      card: { name: str(card.name, 64), owner: str(card.owner, 32), intro: str(card.intro, 140) } },
    friends: (Array.isArray(m.friends) ? m.friends : []).filter((f) => f && typeof f.id === 'string').slice(0, 500).map((f) => ({
      id: f.id.slice(0, 64), name: str(f.name, 64) || shortId(f.id), owner: str(f.owner, 32), intro: str(f.intro, 140), group: str(f.group, 32),
      state: str(f.state, 32), blocked: f.blocked === true, last: lastOf(f.last), unread: Number.isInteger(f.unread) ? f.unread : f.unread ? 1 : 0 })),
    pending: (Array.isArray(m.pending) ? m.pending : []).filter((p) => p && typeof p.id === 'string').slice(0, 100).map((p) => ({
      id: p.id.slice(0, 64), state: str(p.state, 32), ts: Number(p.ts) || 0, dir: p.dir === 'in' ? 'in' : 'out' })),
    groups: (Array.isArray(m.groups) ? m.groups : []).map(normGroup).filter(Boolean).slice(0, 64),
    global: m.global && typeof m.global === 'object' ? { used: int(m.global.used), limit: int(m.global.limit) } : null,
  };
}
const friendOf = (id) => (data ? data.friends.find((f) => f.id === id) : null);

// ---------------------------------------------------------------- navigation
/** Open the page at a view (list | chat | detail | card | add | groups | group), then the panel shows it and loads. */
export function open(view = 'list', arg = null) {
  V.view = view;
  if (view === 'chat' || view === 'detail') V.friend = arg;
  if (view === 'group') V.group = arg;
  if (view === 'add') { V.addId = typeof arg === 'string' ? arg : ''; V.addNote = ''; V.sent = false; }
  delete el('fr-main').dataset.key;                   // an explicit open always redraws (a new #add= prefill included)
  H.openPanel('friends');
}
function go(view, arg) {
  if (view === 'chat' && arg !== V.friend) { hist = { friend: arg, items: [], more: false, loading: false }; usage = null; }
  if (view === 'detail' && arg !== V.friend) usage = null;
  V.view = view;
  if (view === 'chat' || view === 'detail') V.friend = arg;
  if (view === 'group') V.group = arg;
  if (view === 'add') V.sent = false;
  render();
  if (view === 'chat') loadHist(true);
  if (view === 'chat' || view === 'detail') loadUsage();
  if (view === 'detail') loadCtx();
  el('pages').scrollTop = 0;
}
function back() {
  if (V.view === 'list') { H.openPanel('chat'); return; }
  if (V.view === 'detail') return go('chat', V.friend);
  if (V.view === 'group') return go('groups');
  go('list');
}
export function wire() {
  el('fr-back').addEventListener('click', back);
}

// ---------------------------------------------------------------- loading
/** Called whenever the panel opens (menu, reconnect, /friends): re-read the list and the current view's data. */
export function refresh() {
  render(false);
  if (!isReady()) return;
  const seq = ++loadSeq;
  ask1({ t: 'fr_list' }, 'fr_list_res').then((m) => {
    if (seq !== loadSeq) return;
    if (m.t !== 'fr_list_res') { status(m.t === 'timeout' ? t('fr.err.timeout') : t('fr.err.offline')); return; }
    data = normList(m);
    render(false);                                    // a form being filled in (add / card / group) keeps what was typed
    if (V.view === 'chat') loadHist(true);
    if (V.view === 'chat' || V.view === 'detail') loadUsage();
    if (V.view === 'detail' && ctxText.friend !== V.friend) loadCtx();
  });
}
let changedTimer = 0;
/** host → device fr_changed: re-read within the same second (spec: the page refreshes ≤ 2 s after a change). */
export function changed() {
  if (document.body.dataset.view !== 'friends') return;
  clearTimeout(changedTimer);
  changedTimer = setTimeout(refresh, 250);
}
function loadHist(fresh) {
  const friend = V.friend;
  if (!friend || !isReady() || hist.loading) return;
  if (fresh) hist = { friend, items: [], more: false, loading: false };
  hist.loading = true;
  const before = fresh || !hist.items.length ? null : hist.items[0].ts;
  ask1({ t: 'fr_hist', friend, before }, 'fr_hist_res').then((m) => {
    hist.loading = false;
    if (hist.friend !== friend || m.t !== 'fr_hist_res') { if (V.view === 'chat') renderMain(); return; }
    const items = (Array.isArray(m.items) ? m.items : []).filter((x) => x && typeof x.text === 'string').slice(0, 50).map((x) => ({
      mid: str(x.mid, 32), dir: x.dir === 'out' ? 'out' : 'in', text: x.text.slice(0, 20000), ts: Number(x.ts) || 0, s: str(x.s, 32), auto: x.auto === true }));
    const known = new Set(hist.items.map((x) => x.mid));
    const older = items.filter((x) => !x.mid || !known.has(x.mid)).reverse();    // newest first on the wire → oldest first on screen
    hist.items = fresh ? older : older.concat(hist.items);
    hist.more = m.more === true;
    if (V.view === 'chat') renderMain();
  });
}
function loadUsage() {
  const friend = V.friend;
  if (!friend || !isReady()) return;
  ask1({ t: 'fr_usage', friend }, 'fr_usage_res').then((m) => {
    if (V.friend !== friend || m.t !== 'fr_usage_res') return;
    const u = m.used && typeof m.used === 'object' ? m.used : {};
    const lim = m.limits && typeof m.limits === 'object' ? m.limits : {};
    const win = (o, nullOk) => Object.fromEntries(WIN.map((w) => [w, o && Number.isInteger(o[w]) ? o[w] : nullOk ? null : 0]));
    usage = { group: str(m.group, 32), used: { msg: win(u.msg), tok: win(u.tok, true) }, limits: { msg: win(lim.msg, true), tok: win(lim.tok, true) },
      tokSource: m.tok_source === 'harness' ? 'harness' : null, blocked: int(m.blocked) ?? 0 };
    if (V.view === 'chat' || V.view === 'detail') renderMain();
  });
}

/** P73: this friend's 「补充设定」 (fr_ctx_get → fr_ctx_res). Only the owner's own text; read when the details open. */
function loadCtx() {
  const friend = V.friend;
  if (!friend || !isReady()) return;
  ask1({ t: 'fr_ctx_get', friend }, 'fr_ctx_res').then((m) => {
    if (V.friend !== friend || m.t !== 'fr_ctx_res') return;
    ctxText = { friend, text: typeof m.text === 'string' ? m.text.slice(0, FRIEND_CTX_MAX * 2) : '' };
    if (V.view === 'detail') renderMain();
  });
}
const cps = (s) => [...s].length;                 // characters as the host counts them (code points)

// ---------------------------------------------------------------- rendering
function status(text) { const s = el('fr-status'); s.textContent = text; s.hidden = !text; }
export function relang() { if (document.body.dataset.view === 'friends') render(); }
function render(force = true) {
  const root = el('friends-view');
  root.dataset.fv = V.view;
  el('fr-title').textContent = title();
  status(!isReady() ? t('fr.connecting') : !data ? t('fr.loading') : '');
  renderSide();
  renderMain(force);
}
function title() {
  const f = friendOf(V.friend);
  switch (V.view) {
    case 'chat': return f ? f.name : shortId(V.friend);
    case 'detail': return t('fr.detail.title', { name: f ? f.name : shortId(V.friend) });
    case 'card': return t('fr.card.title');
    case 'add': return t('fr.add.title');
    case 'groups': return t('fr.groups.title');
    case 'group': return V.group ? t('fr.group.title', { name: groupName(groupById(V.group)) }) : t('fr.group.newTitle');
    default: return t('fr.title');
  }
}

// the list (left column on a wide screen; the whole page on a phone when view = list)
function renderSide() {
  const box = el('fr-side'); box.replaceChildren();
  const acts = mk('div', 'fr-acts');
  acts.append(btn('aj-btn--sm', t('fr.card.btn'), () => go('card'), 'fr-open-card'),
    btn('aj-btn--primary aj-btn--sm', t('fr.add.btn'), () => go('add'), 'fr-open-add'),
    btn('aj-btn--sm', t('fr.groups.btn'), () => go('groups'), 'fr-open-groups'));
  box.append(acts);
  if (!data) return;
  if (!data.me.on) box.append(mk('p', 'note fr-off', t('fr.off')));
  const ul = mk('ul', 'fr-list'); ul.id = 'fr-list';
  for (const f of data.friends) {
    const li = mk('li');
    const b = mk('button', 'fr-item'); b.type = 'button'; b.dataset.id = f.id;
    if (V.friend === f.id && (V.view === 'chat' || V.view === 'detail')) b.setAttribute('aria-current', 'true');
    const top = mk('span', 'fr-item__top');
    top.append(mk('b', 'fr-item__name', f.name));
    const g = groupById(f.group);
    if (g || f.group) top.append(mk('span', 'tag', g ? groupName(g) : f.group));
    if (f.blocked) top.append(mk('span', 'tag tag-off', t('fr.blocked')));
    top.append(mk('span', 'fr-item__time', when(f.last.ts)));
    if (f.unread) { const d = mk('span', 'fr-dot'); d.setAttribute('aria-label', t('fr.unread', { n: f.unread })); top.append(d); }
    const sub = [f.owner, f.last.text || f.intro].filter(Boolean).join(' · ');
    b.append(top, mk('span', 'fr-item__sub small', sub || shortId(f.id)));
    b.addEventListener('click', () => go('chat', f.id));
    li.append(b); ul.append(li);
  }
  box.append(ul);
  if (!data.friends.length) box.append(mk('p', 'small fr-empty', t('fr.empty')));
  if (data.pending.length) {
    box.append(mk('h2', 'fr-h', t('fr.pending', { n: data.pending.length })));
    const pl = mk('ul', 'fr-pending'); pl.id = 'fr-pending';
    for (const p of data.pending) {
      const li = mk('li', 'fr-pend');
      li.dataset.state = p.state;
      li.append(mk('span', '', p.dir === 'in' ? t('fr.pend.in', { id: shortId(p.id) }) : t('fr.pend.out', { id: shortId(p.id) })),
        mk('span', 'small', p.dir === 'in' ? t('fr.pend.inHint') : p.state === 'expired' ? t('fr.pend.expired') : t('fr.pend.waiting')));
      pl.append(li);
    }
    box.append(pl);
  }
  if (data.global && data.global.limit != null) box.append(mk('p', 'small fr-global', t('fr.global', { used: num(data.global.used), limit: num(data.global.limit) })));
}

const FORM_VIEWS = ['add', 'card', 'group'];
function renderMain(force = true) {
  const box = el('fr-main');
  const key = [V.view, V.friend, V.group, V.sent].join('|');
  if (!force && FORM_VIEWS.includes(V.view) && box.dataset.key === key && box.childElementCount) return;
  // P73: never redraw the details under the owner's fingers while they type the 「补充设定」 (the draft is kept anyway)
  if (V.view === 'detail' && box.dataset.key === key && document.activeElement && document.activeElement.id === 'fr-ctx') return;
  box.replaceChildren();
  box.dataset.key = data ? key : '';
  if (!data) return;
  switch (V.view) {
    case 'chat': return viewChat(box);
    case 'detail': return viewDetail(box);
    case 'card': return viewCard(box);
    case 'add': return viewAdd(box);
    case 'groups': return viewGroups(box);
    case 'group': return viewGroup(box);
    default: box.append(mk('p', 'small fr-pick', data.friends.length ? t('fr.pick') : t('fr.empty')));
  }
}

// ---- a conversation (read only)
const S_KNOWN = ['pending', 'sent', 'got', 'replied', 'undelivered', 'queued_for_owner', 'limited', 'withheld', 'silent'];
function viewChat(box) {
  const f = friendOf(V.friend);
  if (!f) { box.append(mk('p', 'small', t('fr.gone'))); return; }
  const head = mk('div', 'fr-chathead');
  head.append(mk('p', 'fr-pair', t('fr.chat.pair', { me: H.agentName(), name: f.name })));
  if (usage) head.append(mk('p', 'small fr-today', t('fr.chat.today', { n: usage.used.msg.day, nmax: usage.limits.msg.day == null ? t('fr.u.nolimit') : usage.limits.msg.day,
    tok: num(usage.used.tok.day), tmax: usage.limits.tok.day == null ? t('fr.u.nolimit') : num(usage.limits.tok.day) })));
  head.append(btn('aj-btn--sm', t('fr.chat.detail'), () => go('detail', f.id), 'fr-open-detail'));
  box.append(head);
  if (hist.more) box.append(btn('aj-btn--sm fr-older', t('fr.chat.older'), () => loadHist(false), 'fr-older'));
  const ul = mk('ul', 'fr-msgs'); ul.id = 'fr-msgs';
  for (const x of hist.items) {
    const li = mk('li', 'fr-msg fr-msg--' + x.dir);
    li.dataset.mid = x.mid;
    const meta = [x.dir === 'in' ? f.name : H.agentName(), when(x.ts)];
    if (x.dir === 'out') meta.push(x.auto ? t('fr.s.auto') : t('fr.s.owner'));
    if (S_KNOWN.includes(x.s)) meta.push(t('fr.s.' + x.s));
    li.append(mk('p', 'fr-msg__meta small', meta.filter(Boolean).join(' · ')), mk('p', 'fr-msg__text', x.text));
    if (x.s === 'undelivered' || x.s === 'withheld') li.classList.add('fr-msg--bad');
    ul.append(li);
  }
  box.append(ul);
  if (!hist.items.length) box.append(mk('p', 'small fr-empty', hist.loading ? t('fr.loading') : t('fr.chat.none')));
  const lock = mk('div', 'fr-lock');
  lock.append(mk('p', 'small', t('fr.chat.lock', { me: H.agentName() })),
    btn('aj-btn--sm', t('fr.chat.tell', { me: H.agentName() }), () => tellAgent(f.name), 'fr-tell'));
  box.append(lock);
}
/** 「我来说」 / the conversation's button: back to the main chat, the field prefilled 「告诉 <name>：」 (the owner finishes it). */
export function tellAgent(name) {
  H.openPanel('chat');
  const input = el('input');
  const pre = t('fr.tellPrefix', { name });
  input.value = input.value.trim() ? input.value.replace(/\s+$/, '') + '\n' + pre : pre;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.focus();
  try { input.setSelectionRange(input.value.length, input.value.length); } catch { /* not focusable yet */ }
}

// ---- details: group, usage, block / delete
function viewDetail(box) {
  const f = friendOf(V.friend);
  if (!f) { box.append(mk('p', 'small', t('fr.gone'))); return; }
  const card = mk('section', 'fr-cardbox');
  card.append(mk('p', 'fr-cardbox__name', f.owner ? t('fr.nameOwner', { name: f.name, owner: f.owner }) : f.name),
    mk('p', 'fr-id small', f.id));
  if (f.intro) card.append(mk('p', 'small', f.intro));
  box.append(card);
  // group
  const row = mk('label', 'fr-row');
  row.append(mk('span', 'fr-row__label', t('fr.detail.group')));
  const sel = groupSelect(f.group, 'fr-group');
  sel.addEventListener('change', async () => {
    sel.disabled = true;
    const res = await write('fr_set', { friend: f.id, op: 'group', value: sel.value });
    sel.disabled = false;
    toast(res.ok ? t('fr.detail.groupDone', { group: groupName(groupById(sel.value)) }) : t('fr.fail', { why: why(res.why) }), 3000);
    refresh();
  });
  row.append(sel);
  box.append(row);
  // usage
  box.append(mk('h2', 'fr-h', t('fr.u.title')));
  if (!usage) box.append(mk('p', 'small', t('fr.loading')));
  else {
    // one row per window (每分钟 / 每小时 / 今天 / 本月), columns 条数 · tokens: fits a 360 px phone
    const tb = mk('table', 'fr-usage'); tb.id = 'fr-usage';
    const hr = mk('tr'); hr.append(mk('th', '', ''), mk('th', '', t('fr.u.msg')), mk('th', '', t('fr.u.tok')));
    const thead = mk('thead'); thead.append(hr); tb.append(thead);
    const body = mk('tbody');
    for (const w of WIN) {
      const tr = mk('tr'); tr.dataset.w = w;
      tr.append(mk('th', '', t('fr.u.w.' + w)));
      for (const k of ['msg', 'tok']) {
        const used = usage.used[k][w], lim = usage.limits[k][w];
        const u = k === 'tok' ? num(used) : String(used ?? '—');
        const td = mk('td', '', t('fr.u.of', { used: u, limit: lim == null ? t('fr.u.nolimit') : k === 'tok' ? num(lim) : lim }));
        td.dataset.k = k;
        tr.append(td);
      }
      body.append(tr);
    }
    tb.append(body);
    const wrap = mk('div', 'fr-usage-wrap'); wrap.append(tb); box.append(wrap);
    box.append(mk('p', 'small', t('fr.u.blocked', { n: usage.blocked })));
    if (!usage.tokSource) box.append(mk('p', 'small fr-tok-none', t('fr.u.noTok')));
  }
  ctxSection(box, f);
  const acts = mk('div', 'ajrow');
  acts.append(btn('', f.blocked ? t('fr.detail.unblock') : t('fr.detail.block'), () => blockFriend(f), 'fr-block'),
    btn('fr-danger', t('fr.detail.delete'), () => deleteFriend(f), 'fr-delete'));
  box.append(acts);
}
// P73 (ADR-A176): 「补充设定」 — the owner's own notes for this friend's peer session (signed fr_ctx, ≤ 4000 characters). Not a
// message: nothing here reaches the friend; it is the last layer of the peer session's prompt from the next message on.
function ctxSection(box, f) {
  box.append(mk('h2', 'fr-h', t('fr.ctx.title')));
  box.append(mk('p', 'small fr-ctx-hint', t('fr.ctx.hint')));
  const ta = mk('textarea', 'aj-textarea fr-ctx'); ta.id = 'fr-ctx'; ta.rows = 5; ta.placeholder = t('fr.ctx.ph');
  const loaded = ctxText.friend === f.id && ctxText.text !== null;
  ta.value = ctxDraft.friend === f.id && ctxDraft.text !== null ? ctxDraft.text : loaded ? ctxText.text : '';
  ta.disabled = !loaded;
  const count = mk('p', 'small fr-count'); count.id = 'fr-ctx-count';
  const save = btn('aj-btn--primary', t('fr.ctx.save'), null, 'fr-ctx-save');
  const check = () => {
    const n = cps(ta.value);
    count.textContent = t('fr.ctx.count', { n, max: FRIEND_CTX_MAX });
    count.dataset.over = n > FRIEND_CTX_MAX ? '1' : '';
    save.disabled = !loaded || n > FRIEND_CTX_MAX || !isReady() || ta.value === (ctxText.text ?? '');
  };
  ta.addEventListener('input', () => { ctxDraft = { friend: f.id, text: ta.value }; check(); });
  save.addEventListener('click', async () => {
    const text = ta.value.replace(/\r\n?/g, '\n');
    if (cps(text) > FRIEND_CTX_MAX) { toast(t('fr.ctx.tooLong', { max: FRIEND_CTX_MAX }), 3000); return; }
    save.disabled = true;
    const res = await write('fr_ctx', { friend: f.id, text: text.trim() ? text : '' });
    if (res.ok) { ctxText = { friend: f.id, text: text.trim() ? text : '' }; ctxDraft = { friend: null, text: null }; toast(t('fr.ctx.saved'), 3000); }
    else toast(t('fr.fail', { why: why(res.why) }), 3000);
    check();
  });
  box.append(fieldLabel(t('fr.ctx.label'), ta), ta, count);
  rowIn(box).append(save);
  check();
}
function groupSelect(value, id) {
  const sel = mk('select', 'aj-select'); if (id) sel.id = id;
  const gs = data ? data.groups : [];
  for (const g of gs) { const o = mk('option', '', groupName(g)); o.value = g.id; sel.append(o); }
  if (!gs.some((g) => g.id === value) && value) { const o = mk('option', '', value); o.value = value; sel.append(o); }
  sel.value = value || 'default';
  return sel;
}
async function blockFriend(f) {
  const on = !f.blocked;
  if (!await confirmSheet(t(on ? 'fr.block.title' : 'fr.unblock.title', { name: f.name }), t(on ? 'fr.block.text' : 'fr.unblock.text'), t(on ? 'fr.detail.block' : 'fr.detail.unblock'))) return;
  const res = await write('fr_set', { friend: f.id, op: on ? 'block' : 'unblock', value: '' });
  toast(res.ok ? t(on ? 'fr.block.done' : 'fr.unblock.done') : t('fr.fail', { why: why(res.why) }), 3000);
  refresh();
}
async function deleteFriend(f) {
  if (!await confirmSheet(t('fr.delete.title', { name: f.name }), t('fr.delete.text'), t('fr.detail.delete'))) return;
  const res = await write('fr_set', { friend: f.id, op: 'delete', value: '' });
  toast(res.ok ? t('fr.delete.done') : t('fr.fail', { why: why(res.why) }), 3000);
  if (res.ok) go('list');
  refresh();
}

// ---- my card: ID, link, QR, discoverable, owner name / intro
function viewCard(box) {
  const me = data.me;
  if (!me.on) box.append(mk('p', 'note fr-off', t('fr.off')));
  const card = mk('section', 'fr-cardbox fr-mycard');
  card.append(mk('p', 'fr-cardbox__name', me.card.name || H.agentName()));
  if (me.card.owner || me.card.intro) card.append(mk('p', 'small', [me.card.owner && t('fr.ownerIs', { owner: me.card.owner }), me.card.intro].filter(Boolean).join(' · ')));
  const idp = mk('p', 'fr-bigid', me.id || '—'); idp.id = 'fr-my-id';
  card.append(idp);
  const link = parseAgentId(me.id) ? friendLink(me.id) : '';
  const acts = mk('div', 'ajrow');
  acts.append(btn('aj-btn--sm', t('fr.card.copy'), () => copy(me.id, t('fr.card.copied')), 'fr-copy-id'),
    btn('aj-btn--sm', t('fr.card.share'), () => share(link), 'fr-share'));
  card.append(acts);
  const svg = link ? qrSvg(link, t('fr.card.qrAria')) : null;
  if (svg) { const qr = mk('div', 'fr-qr'); qr.id = 'fr-qr'; qr.append(svg); card.append(qr, mk('p', 'small', t('fr.card.qrHint'))); }
  box.append(card);
  // allow others to add me
  const sw = mk('div', 'fr-row');
  const lab = mk('span', 'fr-row__label', t('fr.card.discoverable'));
  const tg = mk('button', 'tgl'); tg.type = 'button'; tg.id = 'fr-discoverable'; tg.setAttribute('role', 'switch');
  tg.setAttribute('aria-checked', String(me.discoverable)); tg.setAttribute('aria-label', t('fr.card.discoverable')); tg.append(mk('i'));
  tg.addEventListener('click', async () => {
    const on = tg.getAttribute('aria-checked') !== 'true';
    tg.disabled = true;
    const res = await write('fr_discoverable', { on });
    tg.disabled = false;
    if (res.ok) { tg.setAttribute('aria-checked', String(on)); data.me.discoverable = on; }
    toast(res.ok ? t(on ? 'fr.card.discOn' : 'fr.card.discOff') : t('fr.fail', { why: why(res.why) }), 3000);
  });
  sw.append(lab, tg);
  box.append(sw, mk('p', 'small', t('fr.card.discHint')));
  // owner display name + intro (shown to a friend only after both owners agreed)
  box.append(mk('h2', 'fr-h', t('fr.card.edit')));
  const owner = mk('input', 'aj-input'); owner.id = 'fr-owner'; owner.maxLength = 32; owner.value = me.card.owner; owner.autocomplete = 'off';
  const intro = mk('textarea', 'aj-textarea fr-intro'); intro.id = 'fr-intro'; intro.maxLength = 140; intro.value = me.card.intro; intro.rows = 2;
  box.append(fieldLabel(t('fr.card.owner'), owner), owner, mk('p', 'small', t('fr.card.ownerHint')), fieldLabel(t('fr.card.intro'), intro), intro);
  rowIn(box).append(btn('aj-btn--primary', t('fr.save'), async (e) => {
    const b = e.currentTarget; b.disabled = true;
    const o = { owner: owner.value.trim().slice(0, 32), intro: intro.value.replace(/\s+/g, ' ').trim().slice(0, 140) };
    const res = await write('fr_card', o);
    toast(res.ok ? t('fr.saved') : t('fr.fail', { why: why(res.why) }), 3000);
    if (res.ok && data) { Object.assign(data.me.card, o); renderMain(); }
    else b.disabled = false;
    refresh();
  }, 'fr-card-save'));
}
function fieldLabel(text, input) { const l = mk('label', 'label', text); l.htmlFor = input.id; return l; }
async function copy(text, done) {
  try { await navigator.clipboard.writeText(text); toast(done); } catch { toast(t('fr.copyFail'), 3000); }
}
async function share(link) {
  if (!link) return;
  if (navigator.share) { try { await navigator.share({ url: link }); return; } catch (e) { if (e && e.name === 'AbortError') return; } }
  copy(link, t('fr.card.linkCopied'));
}

// ---- add a friend: ID (format + check character checked here) + a note ≤ 280
function viewAdd(box) {
  if (V.sent) {
    const ok = mk('section', 'fr-sent'); ok.id = 'fr-sent';
    ok.append(mk('p', 'fr-sent__title', t('fr.add.sent')), mk('p', 'small', t('fr.add.sentText')));
    box.append(ok);
    rowIn(box).append(btn('', t('fr.add.backList'), () => go('list'), 'fr-sent-back'));
    return;
  }
  if (!data.me.on) box.append(mk('p', 'note fr-off', t('fr.off')));
  const id = mk('input', 'aj-input fr-idin'); id.id = 'fr-add-id'; id.autocomplete = 'off'; id.spellcheck = false;
  id.setAttribute('autocapitalize', 'characters'); id.placeholder = 'AJ-XXXX-XXXX-XXXX-XXXX'; id.maxLength = 40; id.value = V.addId;
  const hint = mk('p', 'small fr-idhint'); hint.id = 'fr-add-hint'; hint.setAttribute('aria-live', 'polite');
  const note = mk('textarea', 'aj-textarea fr-note'); note.id = 'fr-add-note'; note.maxLength = 280; note.rows = 3; note.value = V.addNote || '';
  note.placeholder = t('fr.add.notePh');
  const count = mk('p', 'small fr-count');
  const go1 = btn('aj-btn--primary', t('fr.add.send'), null, 'fr-add-go');
  const check = () => {
    const p = parseAgentId(id.value);
    const typed = id.value.replace(/[\s-]/g, '').length;
    hint.textContent = p ? t('fr.add.ok', { id: p }) : typed >= 16 ? t('fr.add.bad') : t('fr.add.hint');
    hint.dataset.ok = p ? '1' : typed >= 16 ? '0' : '';
    id.setAttribute('aria-invalid', String(!p && typed >= 16));
    count.textContent = t('fr.add.count', { n: note.value.length });
    go1.disabled = !p || !isReady();
  };
  id.addEventListener('input', () => { V.addId = id.value; check(); });
  note.addEventListener('input', check);
  go1.addEventListener('click', async () => {
    const p = parseAgentId(id.value);
    if (!p) return;
    go1.disabled = true;
    if (!await sendAdd(p, note.value)) check();
  });
  box.append(fieldLabel(t('fr.add.id'), id), id, hint, fieldLabel(t('fr.add.note'), note), note, count);
  rowIn(box).append(go1);
  box.append(mk('p', 'small', t('fr.add.explain')));
  check();
}

/** The one way a friend request leaves this phone (the page's button and `/add-friend`): the signed fr_add. → true = shown
 *  as sent. Q5: whatever happens on the other side looks the same; only a failure HERE (or a fact about this computer:
 *  my own ID, already a friend, friends off) is told. */
async function sendAdd(id, noteText) {
  const res = await write('fr_add', { id, note: String(noteText || '').replace(/\r\n?/g, '\n').trim().slice(0, 280) });
  if (!res.ok && ['offline', 'timeout', 'no_key', 'bad_signature', 'stale', 'replay', 'self', 'exists', 'off', 'bad_id', 'shape'].includes(res.why)) {
    toast(t('fr.fail', { why: why(res.why) }), 4000);
    return false;
  }
  V.sent = true; V.addId = ''; V.addNote = '';
  if (document.body.dataset.view === 'friends' && V.view === 'add') renderMain();
  refresh();
  return true;
}
/** Split `/add-friend` arguments: the ID (also typed with spaces between its groups) and the note. */
export function splitAddArg(arg) {
  const a = String(arg || '').trim();
  if (!a) return { id: '', note: '' };
  const w = a.split(/\s+/);
  for (let n = Math.min(5, w.length); n > 1; n--) {
    let s = w.slice(0, n).join('').replace(/-/g, '').toUpperCase();
    if (s.startsWith('AJ')) s = s.slice(2);
    if (s.length === 16 && /^[0-9A-HJKMNP-TV-Z]+$/.test(s.replace(/[ILO]/g, '0'))) return { id: w.slice(0, n).join(' '), note: w.slice(n).join(' ') };
  }
  return { id: w[0], note: a.slice(w[0].length).trim() };
}
/** "format" | "check" (16 characters of the alphabet but the check character is off — one character copied wrong) | null. */
export function idProblem(text) {
  if (parseAgentId(text)) return null;
  let s = String(text || '').replace(/[\s-]/g, '').toUpperCase();
  if (s.startsWith('AJ')) s = s.slice(2);
  s = s.replace(/[IL]/g, '1').replace(/O/g, '0');
  return s.length === 16 && /^[0-9A-HJKMNP-TV-Z]{16}$/.test(s) ? 'check' : 'format';
}
/** P73 (ADR-A176) `/add-friend AJ-XXXX-XXXX-XXXX-XXXX [note]` typed in the main chat — intercepted here so it is the same
 *  signed fr_add as the page's button (the approval key never leaves this page). → true = handled (clear the field),
 *  false = keep the text (a wrong ID: the owner fixes one character). No ID → 「加好友」 opens. Not connected → the page
 *  opens prefilled and its button waits for the connection. */
export async function addFriendCmd(arg) {
  const { id, note } = splitAddArg(arg);
  if (!id) { open('add'); return true; }
  const why1 = idProblem(id);
  if (why1) { toast(t(why1 === 'check' ? 'fr.add.badCheck' : 'fr.add.badFormat', { id: id.slice(0, 40) }), 5000); return false; }
  const p = parseAgentId(id);
  if (!isReady()) { open('add', p); V.addNote = note.slice(0, 280); renderMain(); return true; }
  open('add', p);
  V.addNote = note.slice(0, 280);
  const ok = await sendAdd(p, note);
  if (!ok) renderMain();                                  // the form stays, prefilled, with the reason in the toast
  return true;
}
/** A friend command's answer (§8 cmd card `open`): "fr_card" → 我的名片, "fr_add" / "fr_add:<ID>" → 加好友 (prefilled). */
export function openFromCmd(o) {
  if (o === 'fr_card') return open('card');
  if (typeof o === 'string' && o.startsWith('fr_add')) {
    const p = parseAgentId(o.slice(7));
    return open('add', p || '');
  }
}

// ---- policy groups
function viewGroups(box) {
  box.append(mk('p', 'small', t('fr.groups.intro')));
  const ul = mk('ul', 'fr-groups'); ul.id = 'fr-groups';
  for (const g of data.groups) {
    const li = mk('li');
    const b = mk('button', 'fr-gitem'); b.type = 'button'; b.dataset.id = g.id;
    const top = mk('span', 'fr-item__top');
    top.append(mk('b', '', groupName(g)));
    if (g.builtin) top.append(mk('span', 'tag', t('fr.g.builtin')));
    const n = data.friends.filter((f) => f.group === g.id).length;
    top.append(mk('span', 'fr-item__time', t('fr.g.count', { n })));
    b.append(top, mk('span', 'small fr-item__sub', t('fr.g.sum', { n: lim(g.limits.msg.day), tok: g.limits.tok.day == null ? t('fr.u.nolimit') : num(g.limits.tok.day), auto: t('fr.g.auto.' + g.auto.mode) })));
    b.addEventListener('click', () => go('group', g.id));
    li.append(b); ul.append(li);
  }
  box.append(ul);
  rowIn(box).append(btn('', t('fr.groups.new'), () => go('group', null), 'fr-group-new'));
}
const lim = (v) => (v == null ? t('fr.u.nolimit') : String(v));
function viewGroup(box) {
  const base = V.group ? groupById(V.group) : null;
  if (V.group && !base) { box.append(mk('p', 'small', t('fr.gone'))); return; }
  const tpl = base || groupById('default') || normGroup({ id: 'default', limits: {}, auto: {} });
  const g = JSON.parse(JSON.stringify(tpl));
  const fields = {};
  const numIn = (key, value, label, extra = '') => {
    const i = mk('input', 'aj-input fr-num'); i.id = 'fr-g-' + key; i.type = 'text'; i.inputMode = 'numeric'; i.autocomplete = 'off';
    i.value = value == null ? '' : String(value); i.placeholder = extra || t('fr.u.nolimit');
    fields[key] = i;
    const row = mk('div', 'fr-numrow'); row.append(fieldLabel(label, i), i);
    return row;
  };
  const name = mk('input', 'aj-input'); name.id = 'fr-g-name'; name.maxLength = 32; name.value = base ? groupName(base) : ''; name.disabled = !!(base && base.builtin);
  box.append(fieldLabel(t('fr.group.name'), name), name);
  const sec = (title) => { box.append(mk('h2', 'fr-h', title)); const d = mk('div', 'fr-grid'); box.append(d); return d; };
  const m1 = sec(t('fr.group.msg'));
  m1.append(numIn('msg_min', g.limits.msg.min, t('fr.u.w.min')), numIn('msg_day', g.limits.msg.day, t('fr.u.w.day')), numIn('msg_month', g.limits.msg.month, t('fr.u.w.month')));
  const t1 = sec(t('fr.group.tok'));
  t1.append(numIn('tok_day', g.limits.tok.day, t('fr.u.w.day')), numIn('tok_month', g.limits.tok.month, t('fr.u.w.month')));
  box.append(mk('h2', 'fr-h', t('fr.group.auto')));
  const seg = mk('div', 'seg fr-mode'); seg.id = 'fr-g-mode'; seg.setAttribute('role', 'group');
  let mode = g.auto.mode;
  for (const m of ['off', 'scoped', 'all']) {
    const b = mk('button', '', t('fr.g.auto.' + m)); b.type = 'button'; b.dataset.mode = m;
    b.setAttribute('aria-pressed', String(m === mode));
    b.addEventListener('click', () => { mode = m; for (const x of seg.children) x.setAttribute('aria-pressed', String(x.dataset.mode === m)); });
    seg.append(b);
  }
  box.append(seg);
  const topics = (key, list, label, hint) => {
    const ta = mk('textarea', 'aj-textarea fr-topics'); ta.id = 'fr-g-' + key; ta.rows = 3; ta.value = list.join('\n');
    fields[key] = ta;
    box.append(fieldLabel(label, ta), ta, mk('p', 'small', hint));
  };
  topics('allow', g.auto.allow, t('fr.group.allow'), t('fr.group.allowHint'));
  topics('ask', g.auto.ask, t('fr.group.ask'), t('fr.group.askHint'));
  const more = mk('details', 'fr-more'); more.id = 'fr-g-more';
  more.append(mk('summary', '', t('fr.group.more')));
  const m2 = mk('div', 'fr-grid');
  m2.append(numIn('msg_hour', g.limits.msg.hour, t('fr.group.msgHour')), numIn('tok_min', g.limits.tok.min, t('fr.group.tokMin')),
    numIn('tok_hour', g.limits.tok.hour, t('fr.group.tokHour')), numIn('min_interval_s', g.limits.min_interval_s, t('fr.group.interval')),
    numIn('max_len', g.limits.max_len, t('fr.group.maxLen')), numIn('max_auto_rounds', g.auto.max_auto_rounds, t('fr.group.rounds'), '1–100'));
  more.append(m2);
  box.append(more);
  const err = mk('p', 'error small'); err.id = 'fr-g-err'; err.hidden = true;
  box.append(err);
  const acts = mk('div', 'ajrow');
  acts.append(btn('aj-btn--primary', t('fr.save'), async (e) => {
    const out = collectGroup(base, name.value, mode, fields);
    if (typeof out === 'string') { err.textContent = out; err.hidden = false; return; }
    err.hidden = true;
    const b = e.currentTarget; b.disabled = true;
    const res = await write('pg_set', { group: out });
    toast(res.ok ? t('fr.saved') : t('fr.fail', { why: why(res.why) }), 3000);
    if (res.ok) { V.group = out.id; go('groups'); }
    else b.disabled = false;
    refresh();
  }, 'fr-g-save'));
  if (base && !base.builtin) acts.append(btn('fr-danger', t('fr.group.delete'), () => deleteGroup(base), 'fr-g-delete'));
  box.append(acts);
  if (base && base.builtin) box.append(mk('p', 'small', t('fr.group.builtinHint')));
}
/** The form → a §17.9 Group object, or the message to show. Empty number = no limit (null). */
function collectGroup(base, nameText, mode, f) {
  const n = (k, { min = 0, max = 1e12, nullOk = true } = {}) => {
    const s = f[k].value.trim().replace(/[,\s]/g, '');
    if (s === '') return nullOk ? null : NaN;
    return /^\d+$/.test(s) && +s >= min && +s <= max ? +s : NaN;
  };
  const name = nameText.replace(/\s+/g, ' ').trim();
  if (!base && !name) return t('fr.group.errName');
  const v = {
    msg_min: n('msg_min'), msg_hour: n('msg_hour'), msg_day: n('msg_day'), msg_month: n('msg_month'),
    tok_min: n('tok_min'), tok_hour: n('tok_hour'), tok_day: n('tok_day'), tok_month: n('tok_month'),
    min_interval_s: n('min_interval_s', { max: 86400 }), max_len: n('max_len', { min: 1, max: 20000 }), max_auto_rounds: n('max_auto_rounds', { min: 1, max: 100, nullOk: false }),
  };
  if (Object.values(v).some((x) => Number.isNaN(x))) return t('fr.group.errNum');
  const lines = (k) => f[k].value.split('\n').map((s) => s.trim()).filter(Boolean);
  const allow = lines('allow'), askL = lines('ask');
  if (allow.length > 20 || askL.length > 20 || [...allow, ...askL].some((s) => s.length > 80)) return t('fr.group.errTopics');
  return {
    id: base ? base.id : 'g-' + Array.from(crypto.getRandomValues(new Uint8Array(4)), (b) => b.toString(16).padStart(2, '0')).join(''),
    name: base && base.builtin ? base.name : name.slice(0, 32),
    builtin: !!(base && base.builtin),
    limits: { msg: { min: v.msg_min, hour: v.msg_hour, day: v.msg_day, month: v.msg_month }, tok: { min: v.tok_min, hour: v.tok_hour, day: v.tok_day, month: v.tok_month },
      min_interval_s: v.min_interval_s, max_len: v.max_len },
    auto: { mode, allow, ask: askL, max_auto_rounds: v.max_auto_rounds },
  };
}
async function deleteGroup(g) {
  const n = data.friends.filter((f) => f.group === g.id).length;
  if (!await confirmSheet(t('fr.group.delTitle', { name: groupName(g) }), t('fr.group.delText', { n }), t('fr.group.delete'))) return;
  const res = await write('pg_del', { id: g.id });
  toast(res.ok ? t('fr.group.deleted') : t('fr.fail', { why: why(res.why) }), 3000);
  if (res.ok) go('groups');
  refresh();
}

// ---------------------------------------------------------------- signed writes (§8 control signature)
const WRITE_MSG = {
  fr_set: (o) => ({ t: 'fr_set', friend: o.friend, op: o.op, value: o.value }),
  pg_set: (o) => ({ t: 'pg_set', group: o.group }),
  pg_del: (o) => ({ t: 'pg_del', id: o.id }),
  fr_add: (o) => ({ t: 'fr_add', id: o.id, note: o.note }),
  fr_discoverable: (o) => ({ t: 'fr_discoverable', on: o.on }),
  fr_card: (o) => ({ t: 'fr_card', owner: o.owner, intro: o.intro }),
  fr_ctx: (o) => ({ t: 'fr_ctx', friend: o.friend, text: o.text }),
};
const write = (action, o) => signedWrite(action, o, WRITE_MSG[action](o));
const FR_WHY = ['not_friend', 'builtin', 'in_use', 'self', 'off', 'exists', 'too_many', 'too_long', 'bad_id'];
const why = (code) => (FR_WHY.includes(code) ? t('fr.why.' + code) : whyText(code));

// ---------------------------------------------------------------- the two cards in the main chat (§17.7)
// friend_request: the requester's card + note + the group to put them in; 同意 signs friendAnswerMessage (+ the group line),
// 拒绝 = a plain deny. peer_question: the friend's words + the draft; 照草稿回 = allow, 不回 = deny, 我来说 = deny + the main
// chat field prefilled. The signature covers tool + summary (what the host composed), never the structured copy shown here.
const sentAsk = new Map();           // ask id → 'allow' | 'deny' | 'tell' (sent, waiting for ask_done)
const pickedGroup = new Map();       // ask id → the group chosen on the card
const pickedCtx = new Map();         // ask id → the 「补充设定」 typed on the card (P73)
export const askKind = (p) => (p && (p.tool === 'friend_request' || p.tool === 'peer_question') ? p.tool : null);
function frOf(p) {
  const f = p.fr && typeof p.fr === 'object' ? p.fr : null;
  if (!f) return null;
  const groups = (Array.isArray(f.groups) ? f.groups : []).filter((g) => g && typeof g.id === 'string' && GROUP_ID_RE.test(g.id)).slice(0, 64)
    .map((g) => ({ id: g.id, name: str(g.name, 32) }));
  return { id: str(f.id, 64), name: str(f.name, 64), owner: str(f.owner, 32), intro: str(f.intro, 140), note: str(f.note, 280), groups };
}
function pqOf(p) {
  const x = p.pq && typeof p.pq === 'object' ? p.pq : {};
  return { friend: str(x.friend, 64), name: str(x.name, 64) || shortId(x.friend), text: str(x.text, 4000), draft: str(x.draft, 4000), reason: str(x.reason, 200) || str(p.why, 200) };
}
/** Fill #frAsk (and the sheet title) for a friend card. ctx: {open, sign, repaint(), dismiss()}. */
export function renderAsk(p, ctx) {
  const box = el('frAsk'); box.replaceChildren();
  const kind = askKind(p);
  const st = !ctx.open ? p.state : sentAsk.get(p.id) || 'open';
  const final = { approved: 'allow', denied: 'deny', timeout: 'timeout', ended: 'ended', stopped: 'stopped', terminal: 'ended' }[p.state];
  if (kind === 'friend_request') {
    const f = frOf(p);
    el('sheetTitle').textContent = t('fr.ask.title');
    const card = mk('div', 'fr-askcard');
    if (f) {
      card.append(mk('p', 'fr-askcard__name', f.owner ? t('fr.nameOwner', { name: f.name, owner: f.owner }) : f.name));
      if (f.intro) card.append(mk('p', 'small', f.intro));
      if (f.id) card.append(mk('p', 'fr-id small', f.id));
      if (f.note) card.append(mk('p', 'fr-askcard__note', t('fr.ask.note', { note: f.note })));
    } else card.append(mk('pre', 'fr-askcard__raw', p.summary));
    box.append(card);
    if (st === 'open' && ctx.sign) {
      const row = mk('label', 'fr-row');
      row.append(mk('span', 'fr-row__label', t('fr.ask.group')));
      const sel = mk('select', 'aj-select'); sel.id = 'frAskGroup';
      const gs = f && f.groups.length ? f.groups : [{ id: 'default', name: '' }];
      for (const g of gs) { const o = mk('option', '', BUILTIN.includes(g.id) ? t('fr.g.' + g.id) : g.name || g.id); o.value = g.id; sel.append(o); }
      sel.value = pickedGroup.get(p.id) || (gs.some((g) => g.id === 'friend') ? 'friend' : gs[0].id);
      sel.addEventListener('change', () => pickedGroup.set(p.id, sel.value));
      row.append(sel);
      box.append(row, mk('p', 'small', t('fr.ask.groupHint')));
      // P73: 「同意」 may carry this friend's 「补充设定」 (optional; signed with the answer, editable later in the details)
      const more = mk('details', 'fr-askctx'); more.id = 'frAskCtxBox';
      more.append(mk('summary', '', t('fr.ask.ctx')));
      const cta = mk('textarea', 'aj-textarea fr-ctx'); cta.id = 'frAskCtx'; cta.rows = 3; cta.placeholder = t('fr.ctx.ph');
      cta.value = pickedCtx.get(p.id) || '';
      if (cta.value) more.open = true;
      cta.addEventListener('input', () => pickedCtx.set(p.id, cta.value));
      more.append(cta, mk('p', 'small', t('fr.ask.ctxHint', { max: FRIEND_CTX_MAX })));
      box.append(more);
      const acts = mk('div', 'fr-askbtns');
      acts.append(btn('', t('fr.ask.deny'), () => answerFriend(p, false, null, ctx), 'frAskDeny'),
        btn('aj-btn--primary', t('fr.ask.allow'), () => answerFriend(p, true, sel.value, ctx, cta.value), 'frAskAllow'));
      box.append(acts);
      box.append(mk('p', 'small fr-askfoot', t('fr.ask.foot')));
    }
  } else {
    const x = pqOf(p);
    el('sheetTitle').textContent = t('fr.q.title', { name: x.name });
    const card = mk('div', 'fr-askcard');
    if (x.text) card.append(mk('p', 'fr-askcard__note', q(x.text)));
    card.append(mk('p', 'small', t('fr.q.draft')));
    card.append(mk('p', 'fr-askcard__draft', x.draft ? q(x.draft) : t('fr.q.noDraft')));
    if (!p.pq) card.append(mk('pre', 'fr-askcard__raw', p.summary));
    box.append(card);
    if (st === 'open' && ctx.sign) {
      const acts = mk('div', 'fr-askbtns fr-askbtns--3');
      const send = btn('aj-btn--primary', t('fr.q.send'), () => answerQuestion(p, 'allow', x, ctx), 'frQSend');

      acts.append(send, btn('', t('fr.q.skip'), () => answerQuestion(p, 'deny', x, ctx), 'frQSkip'),
        btn('', t('fr.q.tell'), () => answerQuestion(p, 'tell', x, ctx), 'frQTell'));
      acts.append(btn('', t('fr.q.auto'), () => answerFriend(p, true, 'colleague', ctx), 'frQAuto'));
      box.append(acts);
    }
    if (x.reason) box.append(mk('p', 'small fr-askfoot', t('fr.q.reason', { why: x.reason })));
  }
  let text = '';
  if (ctx.open && !ctx.sign) text = t('ask.noSign');
  else if (ctx.open && st !== 'open') text = t('fr.ask.sent.' + st);
  else if (!ctx.open && final) text = t('fr.ask.done.' + (kind === 'friend_request' ? 'fr_' : 'pq_') + final);
  if (text) { const s = mk('p', 'askstate fr-askstate', text); s.id = 'frAskState'; s.setAttribute('role', 'status'); box.append(s); }
}
async function answerFriend(p, ok, group, ctx, ctxText0) {
  const g = gen();
  const sk = await signKey();
  if (!g || !sk) { toast(t(sk ? 'r.appr.net' : 'ask.noSign'), 3000); return; }
  const note = ok && typeof ctxText0 === 'string' && ctxText0.trim() ? ctxText0.replace(/\r\n?/g, '\n') : '';
  if (cps(note) > FRIEND_CTX_MAX) { toast(t('fr.ctx.tooLong', { max: FRIEND_CTX_MAX }), 3000); return; }
  try {
    const grp = ok && group && GROUP_ID_RE.test(group) ? group : null;
    const msg = await friendAnswerMessage(channel(), await myDeviceId(), p.id, ok ? 'allow' : 'deny', p.tool, p.summary, grp, note || null);
    const sig = b64u(new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, sk.priv, msg)));
    const ans = grp ? { t: 'answer', id: p.id, ok: true, sig, group: grp } : { t: 'answer', id: p.id, ok, sig };
    if (note) ans.ctx = note;
    await sendApp(ans, g);
    sentAsk.set(p.id, ok ? 'allow' : 'deny');
  } catch { toast(t('r.appr.net'), 3000); }
  ctx.repaint();
}
async function answerQuestion(p, action, x, ctx) {
  const r = await approve(p, action === 'allow' ? 'allow' : 'deny');
  if (!r.ok) { toast(r.why === 'offline' ? t('r.appr.net') : t('r.appr.fail'), 3000); ctx.repaint(); return; }
  sentAsk.set(p.id, action);
  ctx.repaint();
  if (action === 'tell') { ctx.dismiss(); tellAgent(x.name); }
}
/** Unpair / re-pair / revoke: nothing of the previous computer's friends stays on screen or in memory. */
export function forget() {
  sentAsk.clear(); pickedGroup.clear(); pickedCtx.clear();
  data = null; usage = null; ctxText = { friend: null, text: null }; ctxDraft = { friend: null, text: null }; hist = { friend: null, items: [], more: false, loading: false };
  Object.assign(V, { view: 'list', friend: null, group: null, addId: '', addNote: '', sent: false });
}
export { V as _state };
