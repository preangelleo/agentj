import { useOf } from './useof.js';
// App messages → relay's snapshot (the object relay's render(s) was written for: status, kind, card, ask, approval,
// usage, switch, compacting, history). relay received the whole snapshot from GET /state + SSE; Agent J receives
// separate end-to-end messages (§8, §10), and this module folds them into the same shape so the ported page logic stays.
// It also owns the page's view of the history (§10.5): turns from hist_page / hist_turn on a p33 host, or — on a host
// that did not announce p33 — turns built here in memory from §8's msg stream (never stored).
import { carry as selfhealCarry } from './selfheal.js';
import { assistCarry } from './assistsrc.js';   // 0.26.9 #468 (§B3): a turn an official remote assist task started
import { channel, peer, request, done as reqDone } from './session.js';
import { sanitize as mediaOf } from './mediawire.js';   // F21 (§13)
import { sanitize as formOf } from './formwire.js';         // P212 (§29)
import { preauthOffer, faState, whyKey } from './pkrules.js';   // P539

import { elevateChallenge } from './faceid.js';
import { grantChallenge, verifyPublicGrant, pairingScope, pairingCode, ownerConfirmChallenge } from './ownergrant.js';
import { deviceKey, signKey, dbGet } from './store.js';
import { deviceId, b64u, approvalEnvelopeDigest, sha256hex } from '../proto/wire.js';

export const S = {
  conn: false,                 // a ready session with the host up
  status: 'none', agent: null, kind: null,
  handledWaiting: false,
  touched: new Set(),
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
let onHistoryHint = () => {};
let prepareOwnerConfirm = null;
export function configure(h) { onChange = h.onChange; onTurn = h.onTurn; onHistoryHint = h.onHistoryHint || (() => {}); prepareOwnerConfirm = h.prepareOwnerConfirm || null; }
const nowS = () => Date.now() / 1000;
const CATS = ['spend', 'delete', 'send', 'credentials', 'price', 'opaque', 'service'];

// ---------------------------------------------------------------- requests that need a human
// P339: a goal card in plain words — who wants it (≤ 60), what, as a verb phrase in the host's language (≤ 160), and on a
// `hold` card the extra goal grant the host offers (≤ 300). who / what are display text: one line, control characters out.
// goal_add is SIGNED (api.approve → mailAllowMessage over [goal_add]), so it is kept byte-exact or dropped, never cut.
const oneLine = (v, n) => (typeof v === 'string' ? v.replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, n) : '');
export function goalWords(m) {
  const goal = ['auto', 'hold', 'attack'].includes(m.goal) ? m.goal : '';
  if (!goal) return { goal_who: '', goal_what: '', goal_add: '' };
  const add = goal === 'hold' && typeof m.goal_add === 'string' && m.goal_add.trim() && m.goal_add.length <= 300
    && !/[\u0000-\u001f\u007f]/.test(m.goal_add) ? m.goal_add : '';
  return { goal_who: oneLine(m.goal_who, 60), goal_what: oneLine(m.goal_what, 160), goal_add: add };
}
export function addAsk(m) {
  if(m.approval_v!==undefined&&m.approval_v!==2||Number(m.minimum_approval_protocol)>2)return false;
  if (typeof m.id !== 'string' || !/^[0-9a-f]{32}$/.test(m.id) || typeof m.tool !== 'string' || typeof m.summary !== 'string') return;
  const existing=S.asks.get(m.id);
  if(existing){if(m.approval_v!==2||Number(m.revision)<=existing.revision)return false;S.asks.delete(m.id);S.order=S.order.filter(id=>id!==m.id);}
  // §17.7 friend requests wait up to 7 days; tolerate an old host's 24h question deadline.
  const maxTtl = m.tool === 'friend_request' ? 604800 : ['peer_question','wechat_confirm'].includes(m.tool) ? 86400 : 600;
  const ttl = Number.isInteger(m.ttl) && m.ttl >= 0 && m.ttl <= maxTtl ? m.ttl : 120;
  const cats = Array.isArray(m.cat) ? m.cat.filter((c) => CATS.includes(c)) : [];
  const scope = !cats.length && typeof m.batch === 'string' && m.batch.length > 0 && m.batch.length <= 400 ? m.batch : null;
  S.asks.set(m.id, { id: m.id, kind: 'permission', tool: m.tool.slice(0, 64), summary: m.summary, cats, why: typeof m.why === 'string' ? m.why.slice(0, 200) : '',
    why_en: typeof m.why_en === 'string' ? m.why_en.slice(0, 200) : '', cred: m.cred === 'read' ? 'read' : '',   // P128: one language; 查看 vs 改
    passkey_required: m.passkey_required === true, fa: typeof m.fa === 'string' ? m.fa : '', fa_ch: typeof m.fa_ch === 'string' ? m.fa_ch : '',
    pk_kind:typeof m.pk_kind==='string'?m.pk_kind:'',
    scope, batch_max: Number.isInteger(m.batch_max) ? m.batch_max : 20, batch_secs: Number.isInteger(m.batch_secs) ? m.batch_secs : 600,
    task: typeof m.task === 'string' ? m.task.slice(0, 80) : '',
    learn: m.learn === "proposal",
    host_tool: m.host_tool === true,   // 0.26.9 w4-web C-2: the host's own card (AuthRescue), not a harness tool's name
    cls: oneLine(m.cls, 60), why_scope: oneLine(m.why_scope, 300),   // 0.26.9 #472 ②: the card's class + why the task scope does not cover it (optional)
    reason: typeof m.reason === 'string' ? m.reason : null,   // P572: the Agent's reason ("" = none given; absent = an older host)
    // P573: this task's later steps of the same class will not ask again (host note; shown under the card, not signed)
    task_scope: typeof m.task_scope === 'string' ? m.task_scope : undefined,
    task_grant: typeof m.task_grant === 'string' ? m.task_grant.slice(0, 200) : '', task_grant_en: typeof m.task_grant_en === 'string' ? m.task_grant_en.slice(0, 200) : '',
    allow_offer: m.tool === 'agentj mail send' ? (Array.isArray(m.allow_offer) ? m.allow_offer.filter((a) => typeof a === 'string' && a.length <= 254).slice(0, 10) : [])   // P296
      : preauthOffer(m.allow_offer),   // P539: any other tool may offer ≤ 3 `preauth:<action>` lines (「以后这台遥控器批准的 … 不再问」)
    fa_s: faState(m.fa_s),             // P539: local | borrowed | unknown — does this remote hold the passkey itself
    goal: ['auto', 'hold', 'attack', 'grant'].includes(m.goal) ? m.goal : '', goal_why: typeof m.goal_why === 'string' ? m.goal_why.slice(0, 120) : '',   // P158
    ...goalWords(m),                                                                                                       // P339
    at: nowS(), deadline: Date.now() + ttl * 1000, state: 'open', final_at: null, touched: S.touched.has(m.id),
    approval_invalid: (m.approval_v !== undefined && m.approval_v !== 2) || (m.approval_v !== 2 && (m.envelope !== undefined || m.envelope_sha256 !== undefined)) || Number(m.minimum_approval_protocol) > 2 || (Number(m.minimum_approval_protocol) > 1 && m.approval_v !== 2),
    ...(m.approval_v === 2 ? {approval_v:2,envelope:structuredClone(m.envelope),envelope_sha256:m.envelope_sha256,revision:m.revision,envelope_verified:false,review_state:m.review_state||'pending'} : {}),
    ...(m.tool === 'friend_request' && m.fr && typeof m.fr === 'object' ? { fr: m.fr } : {}),     // display-only copies (friends.js)
    ...(m.tool === 'peer_question' && m.pq && typeof m.pq === 'object' ? { pq: m.pq } : {}) });
  if (m.approval_v === 2) {
    const a = S.asks.get(m.id), e = a.envelope;
    Promise.resolve().then(async () => {
      if (!e || e.v !== 2 || e.request_id !== m.id || e.channel !== channel() || e.revision !== m.revision || !Number.isInteger(e.revision) || e.revision < 1 || typeof e.nonce !== 'string') throw Error('envelope');
      if (e.details !== m.summary || typeof e.source !== 'object' || !e.source || typeof e.action !== 'string') throw Error('display');
      if ((e.controls?.batch || null) !== (m.batch || null) || (e.controls?.goal_add || null) !== (m.goal_add || null) || JSON.stringify(e.controls?.allow_offer || []) !== JSON.stringify(m.allow_offer || [])) throw Error('controls');
      const digest = await approvalEnvelopeDigest(e);
      if (digest !== m.envelope_sha256) throw Error('digest');
      if (m.passkey_required && m.fa && (!m.pk_kind || b64u(await elevateChallenge(channel(),await deviceId((await deviceKey()).pub),m.id,m.pk_kind,m.id,digest)) !== (m.envelope_fa_ch ?? m.fa_ch))) throw Error('challenge');
      if (S.asks.get(m.id) === a) { if (m.passkey_required && m.fa) a.fa_ch = m.envelope_fa_ch ?? m.fa_ch; a.envelope_verified = true; onChange(); }
    }).catch(() => {});
  }
  if (m.tool === 'agentj secret grant' && m.approval_v===2 && m.passkey_required){
    const a=S.asks.get(m.id);a.owner_grant=m.owner_grant;a.owner_grant_verified=false;Promise.resolve().then(()=>verifyPublicGrant(m)).then(()=>{if(S.asks.get(m.id)===a){a.owner_grant_verified=true;onChange();}}).catch(()=>{});
  }
  if (m.tool === 'agentj secret grant' && m.approval_v!==2 && m.passkey_required) {
    const a = S.asks.get(m.id), ch = channel();
    a.owner_grant = m.owner_grant; a.owner_grant_verified = false;
    a.fa_ch = ''; // P574: the assertion must cover the shown authorization, never an opaque host challenge.
    Promise.resolve().then(async () => grantChallenge(m, ch, await deviceId((await deviceKey()).pub))).then((prepared) => {
      if (S.asks.get(m.id) === a && b64u(prepared) === m.fa_ch) { a.fa_ch = b64u(prepared); a.owner_grant_verified = true; onChange(); }
    }).catch(() => {});
  }
  if (m.tool === 'peer_question' && m.pq?.pairing && m.pq.pairing_scope !== undefined) {
    const a = S.asks.get(m.id);
    a.pairing_scope_ok = false;
    const prepare = async () => {
      if (S.asks.get(m.id) !== a) return;
      if (typeof m.pq.pairing_scope !== 'string' || m.pq.pairing_scope.length > 48000) throw new Error('pairing scope');
      const digest = (await sha256hex(new TextEncoder().encode(m.pq.pairing_scope))).slice(0, 32);
      if (digest !== m.pq.challenge || (m.pq.code !== undefined && m.pq.code !== pairingCode(digest))) throw new Error('pairing digest');
      a.pairing_code = pairingCode(digest);
      const ch = channel(), dev = await deviceId((await deviceKey()).pub), local = await dbGet('owner-roster:' + ch);
      a.pairing_bind_needed = !local; onChange();
      const roster = await pairingScope(m.pq, b64u((await signKey()).pub), dev, local);
      const target = { friend: m.pq.friend, op: 'owner-confirm-v3', value: m.pq.challenge };
      if (!prepareOwnerConfirm) throw new Error('unconfigured owner confirmation');
      const obj = await prepareOwnerConfirm(target); // local only, sent after the owner's UV tap
      const challenge = await ownerConfirmChallenge(target, obj.n, ch, dev);
      if (S.asks.get(m.id) === a) {
        a.owner_confirm = { obj, ch, challenge, cred: local.id };
        a.pairing_scope_ok = true; a.pairing_roster = roster; a.pairing_code = roster.code; onChange();
        clearTimeout(a.pairing_refresh_timer);
        a.pairing_refresh_timer = setTimeout(() => { prepare().catch(() => {}); }, 60000); // keep the signed request fresh while this card stays open
      }
    };
    a.prepare_pairing = () => Promise.resolve().then(prepare).catch(() => {
      if (S.asks.get(m.id) === a) { a.pairing_prepare_failed = true; onChange(); }
    });
    a.prepare_pairing();
  }
  S.order.push(m.id);
  onChange();
  return true;                                    // P417: a NEW card (app.js brings it in front of Settings)
}
// Re-render the same verified ceremony after the existing key binding completes.
export function retryPairingPreparation() {
  for (const a of S.asks.values()) if (a.prepare_pairing && a.pairing_scope_ok !== true) a.prepare_pairing();
}
// P396: waiting means an owner action is still needed. A tapped card may stay visible for a receipt/retry.
export function touch(id) {
  const a = S.asks.get(id) || S.qs.get(id);
  if (a) {
    a.touched = true; S.handledWaiting = true; S.touched.add(id);
    if (S.touched.size > 256) S.touched.delete(S.touched.values().next().value);
    onChange();
  }
}
export function answerRefused(m) {
  const a = S.asks.get(m.id);
  if (!a) return;
  // Never sign a digest supplied by a caller. Replace the stale card with the host's current shown text.
  if (m.ask && m.ask.id === a.id && m.ask.tool === a.tool) {
    S.asks.delete(a.id);
    S.order = S.order.filter((id) => id !== a.id);
    S.touched.add(a.id);
    addAsk(m.ask);
    const fresh = S.asks.get(a.id);
    if (fresh) fresh.touched = true;
  }
  const current = S.asks.get(m.id);
  if (current) { current.touched = true; current.answer_failed = true; }
  onChange();
}
// P417 #186: 0.25.0a1 hosts close friend / same-owner cards with `ended` — accept it as well as `gone`.
const ASK_RESULT = { allow: 'approved', deny: 'denied', timeout: 'timeout', expired: 'expired', gone: 'ended', ended: 'ended', stopped: 'stopped' };
export function askDone(m) {
  const a = S.asks.get(m.id);
  if (!a || !ASK_RESULT[m.result]) return;
  a.state = ASK_RESULT[m.result]; a.final_at = nowS();
  a.done_why = whyKey(m.why);   // P539: why it closed (timeout / denied / passkey / nobody / estop / serve_stop), null = as before
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
  S.qs.set(m.id, { id: m.id, kind: 'question', raw: qs, textMax: m.text_max === 2000 ? 2000 : 0, task: typeof m.task === 'string' ? m.task.slice(0, 80) : '',
    questions: qs.map((x) => ({ question: x.q, header: x.h, multi: x.m, options: x.o.map((y) => ({ label: y.l, description: y.d || '' })) })),
    at: nowS(), deadline: Date.now() + ttl * 1000, state: 'open', choices: null, final_at: null });
  S.order.push(m.id);
  onChange();
}
const Q_RESULT = { answered: 'answered', cancelled: 'cancelled', timeout: 'timeout', expired: 'expired', gone: 'ended', stopped: 'stopped' };
export function questionDone(m) {
  const q = S.qs.get(m.id);
  if (!q || !Q_RESULT[m.result]) return;
  q.state = Q_RESULT[m.result]; q.final_at = nowS();
  S.qs.delete(m.id); S.order = S.order.filter((x) => x !== m.id);
  S.lastQ = q;
  onChange();
}
/** P203 (§29.1): the approval / question cards this page shows (the snapshot's `have`). */
export const cardIds = () => [...S.asks.keys(), ...S.qs.keys()];
/** P203: cards the computer no longer lists: expired here (past their deadline) → 「已过期」, else answered elsewhere / ended. */
export function prune(gone) {
  const now = Date.now();
  for (const a of [...S.asks.values()]) if (gone(a.id)) askDone({ id: a.id, result: a.deadline <= now ? 'expired' : 'gone' });
  for (const q of [...S.qs.values()]) if (gone(q.id)) questionDone({ id: q.id, result: q.deadline <= now ? 'expired' : 'gone' });
}
/** The open request on screen: the oldest one still open. */
export function openItem() {
  for (const id of S.order) { const x = S.asks.get(id) || S.qs.get(id); if (x) return x; }
  return null;
}
export const openCount = () => S.order.length;

// ---------------------------------------------------------------- status / meter / models
export function setStatus(m) {
  if (S.agent !== (m.agent || null)) {
    S.meter = null; S.meterAt = 0; S.models = null;
  }
  const ok = ['none', 'idle', 'working', 'compacting', 'waiting', 'down', 'stopped'];
  S.status = ok.includes(m.s) ? m.s : 'none';
  if (S.status !== 'waiting') S.handledWaiting = false;
  S.agent = ['claude', 'codex', 'opencode'].includes(m.agent) ? m.agent : null;
  S.kind = m.kind === 'question' ? 'question' : null;
  S.tg = m.tg === 'down';                 // P129: the host's Telegram channel is down (status area line, app.js renderTg)
  // P204 (§29): while working, a tool at work (「执行工具中 · Bash」) or the model writing (「生成中」)
  S.ph = S.status === 'working' && ['tool', 'gen'].includes(m.ph) ? m.ph : null;
  S.tool = S.ph === 'tool' && typeof m.tool === 'string' ? m.tool.replace(/[\u0000-\u001f]/g, ' ').slice(0, 40) : null;
  if (!peer.p33 && S.status === 'idle') endSynth();
  onChange();
}
export function setMeter(m) { S.meter = m; S.meterAt = Date.now(); onChange(); }
export function setModels(m) { S.models = m; onChange(); }

const pct = (x) => (x && typeof x.pct === 'number' && isFinite(x.pct) ? Math.max(0, Math.min(100, Math.round(x.pct))) : null);
function usage() {
  const m = S.meter;
  if (!m) return {};
  const ctx = m.ctx && Number.isFinite(m.ctx.pct) ? Math.max(0, Math.min(100, Math.round(m.ctx.pct))) : m.ctx && Number.isFinite(m.ctx.used) && Number.isFinite(m.ctx.max) && m.ctx.max > 0 ? Math.max(0, Math.min(100, Math.round(m.ctx.used / m.ctx.max * 100))) : null;
  return { heartbeat_usage: m.heartbeat_usage && typeof m.heartbeat_usage === 'object' ? m.heartbeat_usage : null, source: 'meter', shared_follow: m.shared_follow && ['claude','codex','opencode'].includes(m.shared_follow.agent) && typeof m.shared_follow.id === 'string' && /^[a-f0-9]{32}$/.test(m.shared_follow.id) ? {agent:m.shared_follow.agent,id:m.shared_follow.id} : null, shared_writer: m.shared_writer && typeof m.shared_writer === 'object' ? Object.fromEntries(['zh','en'].filter(l => typeof m.shared_writer[l] === 'string').map(l => [l, m.shared_writer[l].slice(0, 600)])) : null, shared_status: ['following','desktop_writer'].includes(m.shared_status) ? m.shared_status : null, model: typeof m.model_name === 'string' && m.model_name ? m.model_name.slice(0, 64) : (typeof m.model === 'string' ? m.model : null),
    model_id: typeof m.model === 'string' ? m.model : null, effort: typeof m.effort === 'string' ? m.effort.slice(0, 16) : null,
    ...(Array.isArray(m.quota_windows) ? {quota_windows:m.quota_windows} : {}),
    ...(m.reset_credits && typeof m.reset_credits === 'object' ? {reset_credits:m.reset_credits} : {}),
    context_used:m.ctx?.used, context_limit:m.ctx?.max,
    week_pct: pct(m.week), five_hour_pct: pct(m.h5), context_pct: ctx, h5_reset: m.h5 && m.h5.reset, week_reset: m.week && m.week.reset,
    // P433 (#196): when each reading was measured (the host stamps `at`; a status-line reading has source_at) and whether a
    // confirmed limit made it stale — never the moment this phone happened to receive it
    h5_at: measured(m.h5, m.source_at), week_at: measured(m.week, m.source_at), h5_stale: !!(m.h5 && m.h5.stale), week_stale: !!(m.week && m.week.stale),
    h5_expired: !!(m.h5 && m.h5.expired), week_expired: !!(m.week && m.week.expired),   // P565 追加 B: the status line stopped reporting
    age_s: oldest(m) != null ? Math.max(0, Date.now() / 1000 - oldest(m)) : (Date.now() - S.meterAt) / 1000 };
}
const measured = (w, src) => (w && Number.isFinite(w.at) && w.at > 0 ? w.at : w && Number.isFinite(src) && src > 0 ? src : null);
function oldest(m) {
  const xs = [measured(m.h5, m.source_at), measured(m.week, m.source_at)].filter((x) => x != null);
  return xs.length ? Math.min(...xs) : Number.isFinite(m.source_at) && m.source_at > 0 ? m.source_at : null;
}
function swCat() {
  const m = S.models;
  if (!m || !Array.isArray(m.models) || !m.models.length) return null;
  const models = m.models.filter((x) => x && typeof x.id === 'string').slice(0, 40)
    .map((x) => ({ id: x.id, disabled: x.disabled === true, name: typeof x.name === 'string' && x.name ? x.name.slice(0, 64) : x.id, efforts: Array.isArray(x.efforts) ? x.efforts.filter((e) => typeof e === 'string').slice(0, 8) : null }));
  const d = m.default && typeof m.default === 'object' ? m.default : {};
  return { models, default: { model: typeof d.model === 'string' ? d.model : models[0].id, effort: typeof d.effort === 'string' ? d.effort : null } };
}

/** relay's snapshot. */
export function snapshot() {
  const it = openItem();
  let status;
  if (!S.conn) status = 'unknown';
  else if ([...S.asks.values(), ...S.qs.values()].some((a) => !a.touched)) status = 'waiting';
  else if (S.handledWaiting && S.status === 'waiting') status = 'working';
  else status = { idle: 'idle', working: 'working', compacting: 'working', waiting: 'waiting' }[S.status] || 'unknown';
  const kind = status === 'waiting' ? (it ? it.kind : (S.kind === 'question' ? 'question' : 'permission')) : null;
  const card = it ? { kind: it.kind, id: it.id, tool_name: it.tool, summary: it.summary, received_at: it.at } : null;
  return {
    status, kind, card, hostStatus: S.status, agentKind: S.agent,
    approval: it && it.kind === 'permission' ? it : S.lastAsk,
    ask: it && it.kind === 'question' ? it : S.lastQ,
    usage: usage(), switch: swCat(), compacting: S.conn && S.status === 'compacting',
    phase: S.conn && S.ph ? { ph: S.ph, tool: S.tool } : null,
    history: { count: S.hist.count, last_id: S.hist.last, epoch: S.hist.epoch, first_id: S.hist.first },
    estop: S.estop,
  };
}

// ---------------------------------------------------------------- history (§10.5)
// P127: the Agent's source line before an owner's Telegram words (host telegram.OWNER_PRIVATE). Older pages stored it in
// the page's text; the card's label already says Telegram, so the page shows only the words.
const TG_SOURCE_LINE = /^Telegram owner private chat\.\n/;
/** P281: U+FFFD — drawn 「◆?」 — and a lone UTF-16 surrogate (drawn the same) are never meant for the reader. A model's
    stray block (Opus 5.5 opened a /switch-harness turn with one, 2026-10-14) was stored before the host dropped it, so
    old pages are cleaned here too: lines made only of it and any at the very start or end go; one inside a sentence stays. */
export function dropReplacement(s) {
  if (typeof s !== 'string') return s;
  s = s.replace(/[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/g, '');
  if (!s.includes('\ufffd')) return s;
  return s.replace(/^\s*\ufffd[\s\ufffd]*|[\s\ufffd]*\ufffd\s*$/g, '').replace(/^[ \t]*\ufffd+[ \t]*(?:\n|$)/gm, '');
}
/** P363 (#72): a voice attachment's transcript (host ≥ 0.23, PROTOCOL §10.5) → {asr: {ok, text, cut} | {ok: false, why}}. */
export function asrOf(a) {
  if (!a || typeof a !== 'object') return {};
  const notice = ['local_fallback', 'remote_unavailable'].includes(a.notice) ? { notice: a.notice } : {};
  if (a.ok === true && typeof a.text === 'string' && a.text.trim()) return { asr: { ok: true, text: dropReplacement(a.text.slice(0, 20000)), cut: a.cut === true, ...notice } };
  if (a.ok === false) return { asr: { ok: false, why: typeof a.why === 'string' ? a.why.slice(0, 32) : 'broken', ...notice } };
  return {};
}
/** Agent J turn (wire) → the page's turn {id, ts (s), source, reply, end, card, part, pre?}. */
export function toPage(t) {
  if (!t || !Number.isInteger(t.id) || t.id < 0) return null;
  const src = t.src && typeof t.src === 'object' ? t.src : {};
  const k = ['phone', 'host', 'agent', 'sys', 'task', 'cmd', 'telegram', 'email', 'wechat', 'auto', 'friend_report'].includes(src.k) ? src.k : 'sys';   // P365: auto = the owner's own automation (`agentj notify`)
  const q1 = (x) => x && typeof x === 'object' && Number.isInteger(x.id) ? {
    id: x.id, who: typeof x.who === 'string' ? x.who.slice(0, 64) : '', ts: Number(x.ts) || null,
    text: typeof x.text === 'string' ? x.text.slice(0, 2000) : '', excerpt: x.ex === true } : null;
  const q = q1(src.quote);
  if (q && Array.isArray(src.quote.more)) q.more = src.quote.more.slice(0, 4).map(q1).filter(Boolean);   // P286: quotes 2…5
  const att = Array.isArray(src.att) ? src.att.slice(0, 10).filter((a) => a && typeof a.name === 'string')
    .map((a) => ({ name: a.name.slice(0, 128), mime: typeof a.mime === 'string' ? a.mime : '', bytes: Number(a.bytes) || 0, kind: ['image', 'file', 'audio'].includes(a.kind) ? a.kind : 'file', ...asrOf(a.asr) })) : [];
  const reply = t.reply && typeof t.reply === 'object' ? t.reply : { text: typeof t.reply === 'string' ? t.reply : '' };
  return {
    id: t.id, ts: typeof t.ts === 'number' ? t.ts / 1000 : null,
    source: { k, ...(src.k === 'sys' && src.repair === 'reconnect' ? {repair:'reconnect'} : {}), ...(k === 'sys' && ['wsl', 'loop'].includes(src.guide) ? { guide: src.guide } : {}), dev: typeof src.dev === 'string' ? src.dev : null, name: typeof src.name === 'string' ? src.name.slice(0, 64) : '',
      text: typeof src.text !== 'string' ? '' : k === 'telegram' ? src.text.replace(TG_SOURCE_LINE, '') : src.text, quote: q, att, local: src.local === true, ...(k === 'sys' && typeof src.notice_id === 'string' && /^[A-Za-z0-9_-]{22}$/.test(src.notice_id) ? {notice_id:src.notice_id} : {}), ...(k === 'agent' && ['loop', 'task', 'quota', 'auto', 'hook', 'heartbeat', 'api', 'mcp', 'background_done'].includes(src.via) ? {via: src.via} : {}), ...(k === 'task' && src.via === 'same-owner' ? {via: src.via} : {}),
      ...(src.form && typeof src.form.id === "string" ? {form: {id: src.form.id.slice(0, 18), title: String(src.form.title || "").slice(0, 80)}} : {}),
      ...assistCarry(src),   // 0.26.9 #468: k = official_assist (+ the task id) whichever way the host wrote it
      ...(k === 'phone' && src.term && typeof src.term === 'object' ? { term: src.term } : {}) },   // P414 (§37.12): a 「!」 line's result (js/bang.js termOf)
    reply: typeof reply.text === 'string' ? dropReplacement(reply.text) : '',
    silent: t.silent === true,
    silent_reply: t.silent === true && typeof reply.pre === "string" ? dropReplacement(reply.pre) : "",
    part: Array.isArray(reply.part) && reply.part.length === 2 ? reply.part.map(Number) : null,
    ...(typeof reply.pre === 'string' && reply.pre ? { pre: dropReplacement(reply.pre) } : {}),   // P351: a silent turn's notes
    end: ['open', 'done', 'stopped', 'failed'].includes(t.end) ? t.end : 'done',
    card: t.card && typeof t.card === 'object' ? t.card : null,
    ...selfhealCarry(t),   // 0.26.9 (CONTRACT §C): `login` / `waited_s` / `stuck` a host put on the turn instead of its card
    ...((m) => ({ media: m.media, mediaSkip: m.skip }))(mediaOf(t)),   // F21: files the reply shows (§13)
    use: useOf(t.use),
    form: formOf(t.form),                                                // P212 (§29): the form this page carries
  };
}
export function setMeta(m) {
  const epoch = Number.isInteger(m.epoch) ? m.epoch : 0, last = Number(m.last) || 0;
  // Epochs identify stores/modes, not a globally ordered clock. Every differing
  // epoch is only a hint; a fresh generation-bound head confirms the switch.
  if (epoch !== S.hist.epoch || last < S.hist.last) { onHistoryHint({epoch,kind:'meta'}); return; }
  S.hist = { epoch, first: Number(m.first) || 0, last, count: Number(m.count) || 0 };
  onChange(false);
}
/** Only relay's current-generation fresh head calls this after its merge. Metadata alone cannot lower an epoch. */
export function acceptHistoryHead(meta) {
  S.hist = {epoch:meta.epoch,first:meta.first,last:meta.last,count:meta.count};
}
export function pushTurn(m) {
  if (Number.isInteger(m.epoch) && m.epoch !== S.hist.epoch) { onHistoryHint({epoch:m.epoch,kind:'turn'}); return; } // verify another epoch, never apply a mismatched push
  const p = toPage(m.turn);
  if (!p) return;
  if (p.id > S.hist.last) { S.hist.last = p.id; S.hist.count += 1; if (!S.hist.first) S.hist.first = p.id; }
  onTurn(p);
}

/** relay's GET /history?… → {turns, count, first_id, epoch}. p33: hist_get / hist_page; old hosts: from memory. */
export function history(q = {}) {
  if (!peer.p33) {
    let ts = S.synth.turns.slice(), more = false;
    if (q.before) ts = ts.filter((x) => x.id < q.before).slice(-(q.limit || 50));
    else if (Number.isInteger(q.after) && q.after >= 0) { ts = ts.filter((x) => x.id > q.after); more = ts.length > (q.limit || 50); ts = ts.slice(0,q.limit || 50); }
    else ts = ts.slice(-(q.limit || 50));
    const all = S.synth.turns;
    return Promise.resolve({ turns: ts.map((x) => ({ ...x })), count: all.length, first_id: all.length ? all[0].id : 0, epoch: 0, ...(more ? {more:true} : {}) });
  }
  return new Promise((resolve) => {
    const req = { t: 'hist_get', limit: Math.min(50, q.limit || 50) };
    if (q.before) req.before = q.before;
    if (Number.isInteger(q.after) && q.after >= 0) req.after = q.after;
    const r = request(req, (m) => {
      if (m.t !== 'hist_page') return;
      reqDone(r);
      const turns = (Array.isArray(m.turns) ? m.turns : []).map(toPage).filter(Boolean);
      resolve({ turns, count: Number(m.count) || turns.length, first_id: Number(m.first) || 0, epoch: Number(m.epoch) || 0, more: m.more === true, ...(Number.isSafeInteger(m.read) ? {read: m.read} : {}) });
    });
    if (!r) { resolve(null); return; }
    setTimeout(() => { reqDone(r); resolve(null); }, 20000);
  });
}

/** P188 (§28): the computer's search of the whole current epoch → {epoch, hits, n, more} | {error} | null (offline / no answer
 *  in 15 s / a host without `hfind`). A hit = {id, ts (s), src {k, dev, name, via, local}, score, where, snip}. */
export function findHistory(q, sort = 'score') {
  if (!peer.p33 || !peer.hfind) return Promise.resolve(null);
  return new Promise((resolve) => {
    const r = request({ t: 'hist_find', q, sort, limit: 50 }, (m) => {
      if (m.t !== 'hist_found' && m.t !== 'offline') return;
      reqDone(r);
      if (m.t === 'offline') { resolve(null); return; }
      if (m.error) { resolve({ error: String(m.error) }); return; }
      const str = (x, n) => (typeof x === 'string' ? x.slice(0, n) : '');
      const hits = (Array.isArray(m.hits) ? m.hits : []).filter((h) => h && Number.isInteger(h.id)).slice(0, 50).map((h) => {
        const src = h.src && typeof h.src === 'object' ? h.src : {};
        const snip = {};
        for (const f of ['in', 'quote', 'att', 'reply']) if (h.snip && typeof h.snip[f] === 'string') snip[f] = h.snip[f].slice(0, 400);
        return { id: h.id, ts: typeof h.ts === 'number' ? h.ts / 1000 : null, score: Number(h.score) || 0,
          where: Array.isArray(h.where) ? h.where.filter((f) => f in snip) : Object.keys(snip), snip,
          src: { k: str(src.k, 16) || 'sys', dev: str(src.dev, 64) || null, name: str(src.name, 64), local: src.local === true,
            ...(src.k === 'agent' && ['loop', 'task', 'quota', 'auto', 'hook', 'heartbeat', 'api', 'mcp', 'background_done'].includes(src.via) ? { via: src.via } : {}), ...(src.k === 'task' && src.via === 'same-owner' ? {via: src.via} : {}) } };
      });
      resolve({ epoch: Number(m.epoch) || 0, hits, n: Number(m.n) || hits.length, more: m.more === true });
    });
    if (!r) { resolve(null); return; }
    setTimeout(() => { reqDone(r); resolve(null); }, 15000);
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

export function replaceApproval(m){
 const old=S.asks.get(m.id);if(old?.approval_v===2&&Number(m.revision)<=old.revision)return false;
 if(old){S.asks.delete(m.id);S.order=S.order.filter(id=>id!==m.id);}
 return addAsk(m);
}
export function approvalQuestioned(m){
 const a=S.asks.get(m.id);if(!a||a.approval_v!==2)return;
 if(m.ask)replaceApproval(m.ask);
 const fresh=S.asks.get(m.id);fresh.review_state='questioned';fresh.touched=true;
 onChange();
}
export const approvalById=id=>S.asks.get(id)||(S.lastAsk?.id===id?S.lastAsk:null);
