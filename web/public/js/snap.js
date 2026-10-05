// App messages → relay's snapshot (the object relay's render(s) was written for: status, kind, card, ask, approval,
// usage, switch, compacting, history). relay received the whole snapshot from GET /state + SSE; Agent J receives
// separate end-to-end messages (§8, §10), and this module folds them into the same shape so the ported page logic stays.
// It also owns the page's view of the history (§10.5): turns from hist_page / hist_turn on a p33 host, or — on a host
// that did not announce p33 — turns built here in memory from §8's msg stream (never stored).
import { peer, request, done as reqDone } from './session.js';
import { sanitize as mediaOf } from './mediawire.js';   // F21 (§13)

export const S = {
  conn: false,                 // a ready session with the host up
  status: 'none', agent: null, kind: null,
  asks: new Map(),             // id → approval request (§8 ask)
  qs: new Map(),               // id → question (§10.7)
  order: [],                   // ids in arrival order (asks and questions)
  lastAsk: null, lastQ: null,  // the newest finished ones (shown with their result for a few seconds)
  meter: null, meterAt: 0, models: null, estop: { on: false, by: '' },
  hist: { epoch: 0, first: 0, last: 0, count: 0 },
  synth: { turns: [], next: 1 }, // old hosts: turns built from msg
};
let onChange = () => {};
let onTurn = () => {};
export function configure(h) { onChange = h.onChange; onTurn = h.onTurn; }
const nowS = () => Date.now() / 1000;
const CATS = ['spend', 'delete', 'send', 'credentials', 'price'];

// ---------------------------------------------------------------- requests that need a human
export function addAsk(m) {
  if (typeof m.id !== 'string' || !/^[0-9a-f]{32}$/.test(m.id) || typeof m.tool !== 'string' || typeof m.summary !== 'string') return;
  if (S.asks.has(m.id)) return;
  const ttl = Number.isInteger(m.ttl) && m.ttl >= 0 && m.ttl <= 600 ? m.ttl : 120;
  const cats = Array.isArray(m.cat) ? m.cat.filter((c) => CATS.includes(c)) : [];
  const scope = !cats.length && typeof m.batch === 'string' && m.batch.length > 0 && m.batch.length <= 400 ? m.batch : null;
  S.asks.set(m.id, { id: m.id, kind: 'permission', tool: m.tool.slice(0, 64), summary: m.summary, cats, why: typeof m.why === 'string' ? m.why.slice(0, 200) : '',
    scope, batch_max: Number.isInteger(m.batch_max) ? m.batch_max : 20, batch_secs: Number.isInteger(m.batch_secs) ? m.batch_secs : 600,
    task: typeof m.task === 'string' ? m.task.slice(0, 80) : '', at: nowS(), deadline: Date.now() + ttl * 1000, state: 'open', final_at: null });
  S.order.push(m.id);
  onChange();
}
const ASK_RESULT = { allow: 'approved', deny: 'denied', timeout: 'timeout', gone: 'ended', stopped: 'stopped' };
export function askDone(m) {
  const a = S.asks.get(m.id);
  if (!a || !ASK_RESULT[m.result]) return;
  a.state = ASK_RESULT[m.result]; a.final_at = nowS();
  S.asks.delete(m.id); S.order = S.order.filter((x) => x !== m.id);
  S.lastAsk = a;
  onChange();
}
export function addQuestion(m) {
  if (typeof m.id !== 'string' || !/^[0-9a-f]{32}$/.test(m.id) || !Array.isArray(m.qs) || m.qs.length < 1 || m.qs.length > 8) return;
  if (S.qs.has(m.id)) return;
  const qs = [];
  for (const x of m.qs) {
    if (!x || typeof x.q !== 'string' || !Array.isArray(x.o) || x.o.length < 1 || x.o.length > 16) return;
    const o = [];
    for (const y of x.o) { if (!y || typeof y.l !== 'string') return; o.push({ l: y.l, d: typeof y.d === 'string' ? y.d : undefined }); }
    if (new Set(o.map((y) => y.l)).size !== o.length) return;
    qs.push({ q: x.q, h: typeof x.h === 'string' ? x.h : '', m: x.m === true, o });
  }
  const ttl = Number.isInteger(m.ttl) && m.ttl >= 0 && m.ttl <= 3600 ? m.ttl : 180;
  S.qs.set(m.id, { id: m.id, kind: 'question', raw: qs, task: typeof m.task === 'string' ? m.task.slice(0, 80) : '',
    questions: qs.map((x) => ({ question: x.q, header: x.h, multi: x.m, options: x.o.map((y) => ({ label: y.l, description: y.d || '' })) })),
    at: nowS(), deadline: Date.now() + ttl * 1000, state: 'open', choices: null, final_at: null });
  S.order.push(m.id);
  onChange();
}
const Q_RESULT = { answered: 'answered', cancelled: 'cancelled', timeout: 'timeout', gone: 'ended', stopped: 'stopped' };
export function questionDone(m) {
  const q = S.qs.get(m.id);
  if (!q || !Q_RESULT[m.result]) return;
  q.state = Q_RESULT[m.result]; q.final_at = nowS();
  S.qs.delete(m.id); S.order = S.order.filter((x) => x !== m.id);
  S.lastQ = q;
  onChange();
}
/** The open request on screen: the oldest one still open. */
export function openItem() {
  for (const id of S.order) { const x = S.asks.get(id) || S.qs.get(id); if (x) return x; }
  return null;
}
export const openCount = () => S.order.length;

// ---------------------------------------------------------------- status / meter / models
export function setStatus(m) {
  const ok = ['none', 'idle', 'working', 'compacting', 'waiting', 'down', 'stopped'];
  S.status = ok.includes(m.s) ? m.s : 'none';
  S.agent = ['claude', 'codex', 'opencode'].includes(m.agent) ? m.agent : null;
  S.kind = m.kind === 'question' ? 'question' : null;
  if (!peer.p33 && S.status === 'idle') endSynth();
  onChange();
}
export function setMeter(m) { S.meter = m; S.meterAt = Date.now(); onChange(); }
export function setModels(m) { S.models = m; onChange(); }

const pct = (x) => (x && typeof x.pct === 'number' && isFinite(x.pct) ? Math.max(0, Math.min(100, Math.round(x.pct))) : null);
function usage() {
  const m = S.meter;
  if (!m) return {};
  const ctx = m.ctx && Number.isFinite(m.ctx.used) && Number.isFinite(m.ctx.max) && m.ctx.max > 0 ? Math.max(0, Math.min(100, Math.round(m.ctx.used / m.ctx.max * 100))) : null;
  return { source: 'meter', model: typeof m.model_name === 'string' && m.model_name ? m.model_name.slice(0, 64) : (typeof m.model === 'string' ? m.model : null),
    model_id: typeof m.model === 'string' ? m.model : null, effort: typeof m.effort === 'string' ? m.effort.slice(0, 16) : null,
    week_pct: pct(m.week), five_hour_pct: pct(m.h5), context_pct: ctx, h5_reset: m.h5 && m.h5.reset, week_reset: m.week && m.week.reset,
    age_s: (Date.now() - S.meterAt) / 1000 };
}
function swCat() {
  const m = S.models;
  if (!m || !Array.isArray(m.models) || !m.models.length) return null;
  const models = m.models.filter((x) => x && typeof x.id === 'string').slice(0, 40)
    .map((x) => ({ id: x.id, name: typeof x.name === 'string' && x.name ? x.name.slice(0, 64) : x.id, efforts: Array.isArray(x.efforts) ? x.efforts.filter((e) => typeof e === 'string').slice(0, 8) : null }));
  const d = m.default && typeof m.default === 'object' ? m.default : {};
  return { models, default: { model: typeof d.model === 'string' ? d.model : models[0].id, effort: typeof d.effort === 'string' ? d.effort : null } };
}

/** relay's snapshot. */
export function snapshot() {
  const it = openItem();
  let status;
  if (!S.conn) status = 'unknown';
  else if (it) status = 'waiting';
  else status = { idle: 'idle', working: 'working', compacting: 'working', waiting: 'waiting' }[S.status] || 'unknown';
  const kind = status === 'waiting' ? (it ? it.kind : (S.kind === 'question' ? 'question' : 'permission')) : null;
  const card = it ? { kind: it.kind, id: it.id, tool_name: it.tool, summary: it.summary, received_at: it.at } : null;
  return {
    status, kind, card, hostStatus: S.status, agentKind: S.agent,
    approval: it && it.kind === 'permission' ? it : S.lastAsk,
    ask: it && it.kind === 'question' ? it : S.lastQ,
    usage: usage(), switch: swCat(), compacting: S.conn && S.status === 'compacting',
    history: { count: S.hist.count, last_id: S.hist.last, epoch: S.hist.epoch, first_id: S.hist.first },
    estop: S.estop,
  };
}

// ---------------------------------------------------------------- history (§10.5)
/** Agent J turn (wire) → the page's turn {id, ts (s), source, reply, end, card, part}. */
export function toPage(t) {
  if (!t || !Number.isInteger(t.id) || t.id < 0) return null;
  const src = t.src && typeof t.src === 'object' ? t.src : {};
  const k = ['phone', 'host', 'agent', 'sys', 'task', 'cmd', 'telegram'].includes(src.k) ? src.k : 'sys';
  const q = src.quote && typeof src.quote === 'object' && Number.isInteger(src.quote.id) ? {
    id: src.quote.id, who: typeof src.quote.who === 'string' ? src.quote.who.slice(0, 64) : '', ts: Number(src.quote.ts) || null,
    text: typeof src.quote.text === 'string' ? src.quote.text.slice(0, 2000) : '', excerpt: src.quote.ex === true } : null;
  const att = Array.isArray(src.att) ? src.att.slice(0, 10).filter((a) => a && typeof a.name === 'string')
    .map((a) => ({ name: a.name.slice(0, 128), mime: typeof a.mime === 'string' ? a.mime : '', bytes: Number(a.bytes) || 0, kind: ['image', 'file', 'audio'].includes(a.kind) ? a.kind : 'file' })) : [];
  const reply = t.reply && typeof t.reply === 'object' ? t.reply : { text: typeof t.reply === 'string' ? t.reply : '' };
  return {
    id: t.id, ts: typeof t.ts === 'number' ? t.ts / 1000 : null,
    source: { k, dev: typeof src.dev === 'string' ? src.dev : null, name: typeof src.name === 'string' ? src.name.slice(0, 64) : '',
      text: typeof src.text === 'string' ? src.text : '', quote: q, att, local: src.local === true, ...(k === 'sys' && typeof src.notice_id === 'string' && /^[A-Za-z0-9_-]{22}$/.test(src.notice_id) ? {notice_id:src.notice_id} : {}) },
    reply: typeof reply.text === 'string' ? reply.text : '',
    part: Array.isArray(reply.part) && reply.part.length === 2 ? reply.part.map(Number) : null,
    end: ['open', 'done', 'stopped', 'failed'].includes(t.end) ? t.end : 'done',
    card: t.card && typeof t.card === 'object' ? t.card : null,
    ...((m) => ({ media: m.media, mediaSkip: m.skip }))(mediaOf(t)),   // F21: files the reply shows (§13)
  };
}
export function setMeta(m) {
  const reset = Number.isInteger(m.epoch) && m.epoch !== S.hist.epoch;
  S.hist = { epoch: Number.isInteger(m.epoch) ? m.epoch : 0, first: Number(m.first) || 0, last: Number(m.last) || 0, count: Number(m.count) || 0 };
  onChange(reset);
}
export function pushTurn(m) {
  if (Number.isInteger(m.epoch) && m.epoch !== S.hist.epoch) return;      // a stale one from before a reset
  const p = toPage(m.turn);
  if (!p) return;
  if (p.id > S.hist.last) { S.hist.last = p.id; S.hist.count += 1; if (!S.hist.first) S.hist.first = p.id; }
  onTurn(p);
}

/** relay's GET /history?… → {turns, count, first_id, epoch}. p33: hist_get / hist_page; old hosts: from memory. */
export function history(q = {}) {
  if (!peer.p33) {
    let ts = S.synth.turns.slice();
    if (q.before) ts = ts.filter((x) => x.id < q.before).slice(-(q.limit || 50));
    else if (q.after) ts = ts.filter((x) => x.id > q.after);
    else ts = ts.slice(-(q.limit || 50));
    const all = S.synth.turns;
    return Promise.resolve({ turns: ts.map((x) => ({ ...x })), count: all.length, first_id: all.length ? all[0].id : 0, epoch: 0 });
  }
  return new Promise((resolve) => {
    const req = { t: 'hist_get', limit: Math.min(50, q.limit || 50) };
    if (q.before) req.before = q.before;
    if (q.after) req.after = q.after;
    const r = request(req, (m) => {
      if (m.t !== 'hist_page') return;
      reqDone(r);
      const turns = (Array.isArray(m.turns) ? m.turns : []).map(toPage).filter(Boolean);
      resolve({ turns, count: Number(m.count) || turns.length, first_id: Number(m.first) || 0, epoch: Number(m.epoch) || 0, more: m.more === true });
    });
    if (!r) { resolve(null); return; }
    setTimeout(() => { reqDone(r); resolve(null); }, 20000);
  });
}

// ---------------------------------------------------------------- old hosts (no p33): turns built from §8 msg
function synthPush(src, reply, end = 'open', card = null) {
  const T = S.synth;
  const t = { id: T.next++, ts: Date.now() / 1000, source: { k: src.k, dev: src.dev || null, name: src.name || '', text: src.text || '', quote: null, att: [], local: false },
    reply, part: null, end, card };
  T.turns.push(t);
  if (T.turns.length > 100) T.turns.shift();
  S.hist.last = t.id; S.hist.count = T.turns.length; S.hist.first = T.turns[0].id;
  onTurn({ ...t });
  return t;
}
function endSynth() {
  const t = S.synth.turns[S.synth.turns.length - 1];
  if (t && t.end === 'open') { t.end = 'done'; onTurn({ ...t }); }
}
/** §8 msg from an old host → turns. myDev = this phone's device id. */
export function synthMsg(m, myDev) {
  const T = S.synth.turns, last = T[T.length - 1];
  if (m.from === 'agent') {
    if (last && last.end === 'open' && last.source.k !== 'cmd') {
      last.reply = last.reply ? last.reply + '\n\n' + m.text : m.text;
      onTurn({ ...last });
    } else synthPush({ k: 'agent' }, m.text);
    return;
  }
  if (m.from === 'you' && last && last.source.k === 'phone' && last.source.dev === myDev && last.source.text === m.text) return;
  if (m.from === 'you' || m.from === 'device') { endSynth(); synthPush({ k: 'phone', dev: m.from === 'you' ? myDev : null, name: m.name || '', text: m.text }, ''); return; }
  if (m.from === 'host') { endSynth(); synthPush({ k: 'host', text: m.text }, ''); return; }
  if (m.from === 'cmd') {
    synthPush({ k: 'cmd', text: typeof m.cmd === 'string' ? '/' + m.cmd : '' }, m.text, 'done',
      { cmd: m.cmd, ok: m.ok, kind: m.kind, models: m.models, undo: m.undo, sep: m.sep });
    return;
  }
  synthPush({ k: 'sys' }, m.text, 'done');
}
/** This phone said something to an old host (it does not echo live). */
export function synthSaid(text, myDev) { endSynth(); synthPush({ k: 'phone', dev: myDev, text }, ''); }
export function synthReset() { S.synth = { turns: [], next: S.synth.next }; S.hist.count = 0; S.hist.first = 0; }
