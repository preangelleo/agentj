import {lang} from './t.js';
import * as layoutDiag from './layout-diag.js';
// P332 (PROTOCOL §33) · Settings → 紧急修复: the second Settings item, three numbered cards in this order —
//   ① 重置连接   signed `reset` (control-v1 action `reset`, object {"what":"connection"}): the host's reset ladder
//   ② 请 Agent J Father 远程帮忙  signed `rescue_do` (action `rescue_do`, object {"action":"help"}): diagnose → support → ticket
//   ③ AI 账号登录失效  the P258 / P288 login rescue, moved here unchanged (authrescue.js); this card only mirrors its flow
// The host streams `reset_step {r, flow, step, state, text, detail?}` and ends with `reset_done {r, flow, ok, step, text,
// old_session?, ticket?, why?}`. Its texts are plain sentences in the owner's language; technical words only in `detail`,
// which stays folded under 「详情」. Success paints the card green (palette Emerald Oasis), failure says what to try next
// (① failed → ② is highlighted; ② failed → the one line a helper at the computer can paste). ② is dimmed — still
// clickable, it asks 「建议先试第 ① 步」 first — until ① has failed in this visit.
// Rescue mode: a computer whose Agent J is stuck answers through its rescue watchdog (`ready {mode:"rescue"}` + `rescue
// {on:true,…}`): one chat banner with a button to this block; nothing else is expected to answer.
// Everything here is textContent / fillText (our dictionary's `code` → a copyable <code>); nothing is stored.
// ② offers (§33 ②, security review HIGH-1): support's `rollback` / `linger` / `new_session` never run on support's word —
// the host's `reset_step` / `reset_done` of a ② run carry `"offer":[…]`; once ② has ended the card shows one button per
// offered (whitelisted) action with a plain explanation, and only the owner's tap sends their own signed `rescue_do
// <action>` (flow = the action name). A used button is gone; a new ② run clears them. Support's advice rows carry the
// caution line the command-line version prints (LOW-12) and stay plain text.
import { request, done, isReady, peer, ask1 } from './session.js';
import { signed, estop, onResume } from './controls.js';
import { t, fillText, onLang } from './t.js';
import { el, mk, confirmSheet } from './ui.js';
import * as authrescue from './authrescue.js';
import * as assistTerminal from './assist-terminal.js';
import * as assist from './assist.js';   // 0.26.9 #468 (§B2): ② gains 「请 Agent J 官方远程协助」
import { registerLayer } from './layers.js';

export const HELPER_CMD = 'curl -fsSL https://agentj.app/rescue | sh';
export const STEPS = { reset: 4, help: 3, offer: 1, restart: 1, restart_session: 2, clear: 1, ark: 1, end_turn: 1, takeover: 4, repair: 4, stop_hard: 1, new_chat: 1, report: 1, deep_repair: 4, rollback: 1 };          // §33: the reset ladder has four steps, the help flow three
export const FIRST_MS = 30000;                       // no answer at all this long → the host is stuck or too old
export const IDLE_MS = { reset: 120000, help: 240000, offer: 240000, restart: 200000, restart_session: 180000, clear: 120000, ark: 240000, end_turn: 120000, takeover: 200000, repair: 240000, stop_hard: 120000, new_chat: 180000, report: 240000, deep_repair: 600000, rollback: 240000 };   // then: silence this long between two messages = no answer
const WHY = ['shape', 'stale', 'no_key', 'bad_signature', 'replay', 'busy', 'unavailable'];
const STATES = ['run', 'ok', 'fail', 'skip'];
const TICKET = /^st_[A-Za-z0-9_-]{8,40}$/;
const OLD = /^[A-Za-z0-9._:-]{1,80}$/;
export const OFFERS = ['rollback', 'linger', 'new_session'];   // §33 ②: the only actions support may offer the owner
// support's advice row (rescue_kit `_H.advice`); an explicit `"advice":true` on the step is honoured as well
const ADVICE = /^(Agent J Father ?的建议：|Agent J Father suggests:)/;

// P393 · a finished result never stays forever: × clears it (the P380 command-result close), a success folds to one line
// FOLD_MS after it ended on a live link (and every ended result folds once the link is back), and opening Settings again
// shows only the one-line summary — the earlier steps and log stay under 「详情」. ① tapped while the link is reconnecting
// waits silently (the five-second rule, reconnect.js) and runs once connected; only an outage past that says why it can't
// run and what to do next, with the time of the check so a second tap visibly answers.
export const FOLD_MS = 30000;
let H = { openSettings() {}, closeSettings() {}, toChat() {}, linkState: () => (isReady() ? 'up' : 'down') };
const fresh = () => ({ st: 'idle', step: 0, text: '', steps: [], details: [], ticket: '', old: '', why: '', r: null, timer: 0, heard: false, dropped: false,
  offers: [], used: [], name: '', advice: false, fold: false, foldTimer: 0, wait: false, at: 0,
  resume: false, next: '', report: false, cont: '', contSent: false });   // #585: what the host says to do next
const S = { reset: fresh(), help: fresh(), offer: fresh(), restart: fresh(), restart_session: fresh(), clear: fresh(), ark: fresh(), end_turn: fresh(), takeover: fresh(), repair: fresh(), stop_hard: fresh(), new_chat: fresh(), report: fresh(), deep_repair: fresh(), rollback: fresh(), stuck: false, failedOnce: false, askFirst: false, rescue: null, auth: null, authFold: '', authHide: '', bannerHide: '' };
const FLOWS = ['reset', 'help', 'offer', 'restart', 'restart_session', 'clear', 'ark', 'end_turn', 'takeover', 'repair', 'stop_hard', 'new_chat', 'report', 'deep_repair', 'rollback'];
const CONTS = ['probe', 'repair_cont', 'takeover', 'repair', 'deep_repair_cont'];
const CONT_MS = 600000;                 // a card waits at most 10 min for the owner's Resume          // #585: what the page sends once the link / the stop switch is back
const NEXTS = ['restart', 'restart_session', 'repair', 'help'];

const str = (v, n = 400) => (typeof v === 'string' ? v.replace(/[\u0000-\u0008\u000B-\u001F\u007F]/g, '').slice(0, n) : '');
const tx = (k, vars) => t(k, vars);

// ---------------------------------------------------------------- the two signed flows
async function start(flow, action = '') {
  const F = S[flow];
  if (F.st === 'run') return;
  if (flow === 'help' && S.offer.st === 'run') return;          // one repair at a time (the host would answer `busy`)
  clearTimeout(F.foldTimer);
  const expected = flow === 'ark' ? arkOffer?.model : null;
  Object.assign(F, fresh(), { st: 'run', name: action, expected, text: tx(flow === 'help' ? 'p332.help.start' : 'p332.reset.start') });
  if (flow === 'help') { S.askFirst = false; S.offer = fresh(); }   // a new ② run: the earlier offers and their result go
  if (!isReady() && H.linkState() === 'quiet') { F.wait = true; F.text = tx('p393.waitLink'); paint(); return; }   // starts once connected
  paint();
  if (!isReady()) return finish(flow, { ok: false, why: 'offline' });
  let obj;
  try {
    obj = flow === 'reset' ? await signed('reset', { what: 'connection' }, { t: 'reset' })
      : flow === 'help' ? await signed('rescue_do', { action: 'help' }, { t: 'rescue_do', action: 'help' })
        : await signed('rescue_do', { action }, { t: 'rescue_do', action });   // an offered repair: the owner's own tap
  } catch (e) { return finish(flow, { ok: false, why: e && e.message === 'no_key' ? 'no_key' : 'offline' }); }
  const r = request(obj, (m) => onMsg(flow, m));
  if (!r) return finish(flow, { ok: false, why: 'offline' });
  F.r = r;
  arm(flow, FIRST_MS);
}
function arm(flow, ms) {
  const F = S[flow];
  clearTimeout(F.timer);
  F.timer = setTimeout(() => finish(flow, { ok: false, why: 'timeout' }), ms);
}
function onMsg(flow, m) {
  const F = S[flow];
  if (F.st !== 'run') return;
  if (m.t === 'offline') {
    // the link dropped while the computer worked on it (step 1 re-dials, a restart ends this very connection): keep
    // waiting — the computer's `reset_done` reaches every remote, and a normal Agent J answering again is the goal
    F.r = null; F.dropped = true; F.text = tx('p332.reset.s1');
    arm(flow, IDLE_MS[flow]); paint();
    return;
  }
  if (m.t === 'reset_step') return step(flow, m);
  if (m.t === 'reset_done' && flow === 'ark' && m.pending) { F.text=m.text || tx('p419.arkWait'); F.dropped=true; if (F.r) done(F.r); F.r=null; arkOffer=null; arm('ark',IDLE_MS.ark); paint(); setTimeout(refreshArk,1500); return; }
  if (m.t === 'reset_done') { if (flow === 'ark' && m.ok) arkOffer=null; finish(flow,m); if (flow === 'ark') refreshArk(); return; }
}
/** ② only: the owner-confirmed actions support offered (whitelist, in order, each once; unknown names ignored). */
function takeOffers(flow, m) {
  if (flow !== 'help' || !Array.isArray(m.offer)) return;
  const F = S.help;
  for (const x of m.offer.slice(0, 12)) if (OFFERS.includes(x) && !F.offers.includes(x)) F.offers.push(x);
}
function step(flow, m) {
  const F = S[flow];
  takeOffers(flow, m);
  const n = Number.isInteger(m.step) && m.step > 0 && m.step < 20 ? m.step : (F.step || 1);
  const state = STATES.includes(m.state) ? m.state : 'run';
  const text = str(m.text, flow === 'help' ? 1600 : 400) || stepText(flow, n);
  const isAdv = flow === 'help' && (m.advice === true || ADVICE.test(text));
  F.heard = true;
  F.step = Math.max(F.step, n);
  F.text = text;
  // one row per step; a step that already ended and speaks again (② runs several of support's actions) gets a new row
  const row = [...F.steps].reverse().find((x) => x.n === n);
  if (row && (row.state === 'run' || row.text === text)) Object.assign(row, { state, text, advice: isAdv }); else F.steps.push({ n, state, text, advice: isAdv });
  if (F.steps.length > 24) F.steps.shift();
  const d = str(m.detail, 600);
  if (d) F.details.push(d);
  if (F.details.length > 24) F.details.shift();
  const tk = str(m.ticket, 64);
  if (TICKET.test(tk)) F.ticket = tk;
  arm(flow, IDLE_MS[flow]);
  paint();
}
function stepText(flow, n) {
  if (flow === 'offer' || FIX[flow] || flow === 'report') return tx('p332.reset.start');
  if (flow === 'end_turn') return tx('p0269.queue.ending');
  if (flow === 'takeover') return tx('p0269.take.s' + Math.min(4, Math.max(1, n)));
  if (flow === 'reset') return tx('p332.reset.s' + Math.min(4, Math.max(1, n)));
  return tx('p332.help.s' + Math.min(3, Math.max(1, n)));
}
function finish(flow, m) {
  const F = S[flow];
  takeOffers(flow, m);
  if (m.pending === true && CONTS.includes(m.cont)) {     // #585: a service restart was handed over — the card keeps running
    F.cont = m.cont; F.contSent = false; F.heard = true;
    const tx1 = str(m.text, 400); if (tx1) { F.text = tx1; F.steps.push({ n: Number.isInteger(m.step) ? m.step : F.step || 1, state: 'run', text: tx1 }); }
    arm(flow, IDLE_MS[flow]); paint(); return;
  }
  clearTimeout(F.timer); F.timer = 0;
  if (F.r) { done(F.r); F.r = null; }
  const ok = m.ok === true;
  if (Number.isInteger(m.step) && m.step > 0 && m.step < 20) F.step = m.step;
  const tk = str(m.ticket, 64);
  if (TICKET.test(tk)) F.ticket = tk;
  const old = str(m.old_session, 80);
  F.old = ok && OLD.test(old) ? old : '';
  F.why = ok ? '' : String(m.why || 'failed');
  // Repair/rollback results are fixed host sentences in the owner's language. An older or misconfigured host may
  // still answer a Chinese phone in English: then show our own plain sentence instead of the raw status.
  const said = str(m.text, flow === 'help' ? 1600 : 400);
  F.text = (flow === 'deep_repair' || flow === 'rollback') && (!said || (lang() !== 'en' && !/[㐀-鿿]/.test(said)))
    ? tx(ok ? (flow === 'rollback' ? 'p0304.rollback.restored' : 'p0304.deep.done') : 'p0304.deep.failed') : said;
  const d = str(m.detail, 600);
  if (d) F.details.push(d);
  F.resume = !ok && m.resume === true; F.cont = F.resume && CONTS.includes(m.cont) ? m.cont : '';
  F.next = !ok && NEXTS.includes(m.next) ? m.next : ''; F.report = !ok && m.report === true;
  if (FIX[flow] || flow === 'takeover') F.steps = F.steps.filter((x) => x.state !== 'run');
  // The host alone decides whether a rollback is safe; an offline or unsigned failure cannot offer one.
  F.rollback = flow === 'deep_repair' && !ok && m.rollback === true;
  F.st = ok ? 'ok' : 'fail'; F.wait = false; F.at = Date.now();
  if (ok && isReady()) foldLater(flow);
  if (flow === 'help' && F.text && (m.advice === true || ADVICE.test(F.text))) F.advice = true;
  // ① did not bring the link back (a real failure, or a computer that never answered) → ② is the next thing to try
  if (!ok && flow === 'reset' && !['offline', 'no_key', 'shape', 'stale', 'bad_signature', 'replay', 'busy'].includes(F.why)) S.failedOnce = true;
  paint();
}

// ---------------------------------------------------------------- painting
const node = (tag, cls, text) => mk(tag, cls, text, cls?.split(/\s+/).includes('aj-action') ? 'aj-action' : 'aj-rescue', 'dialog');
function btn(id, label, cls, fn) {
  const b = node('button', 'aj-action ' + (cls || ''), label);
  b.type = 'button'; b.id = id; b.addEventListener('click', fn);
  return b;
}
function bar(flow, F, total) {
  const box = node('div', 'rslive');
  const p = Math.max(8, Math.min(100, Math.round(((F.st === 'ok' ? total : Math.max(F.step, 1) - 0.5) / total) * 100)));
  const b = node('div', 'rsbar');
  b.setAttribute('role', 'progressbar'); b.setAttribute('aria-valuemin', '0'); b.setAttribute('aria-valuemax', '100');
  b.setAttribute('aria-valuenow', String(p)); b.setAttribute('aria-label', tx('p332.progress'));
  b.id = 'rs-' + flow + '-bar';
  const i = node('i'); i.style.setProperty('--p', p + '%'); b.append(i);
  const now = node('p', 'rsnow', F.text || stepText(flow, F.step || 1));
  now.id = 'rs-' + flow + '-now'; now.setAttribute('aria-live', 'polite');
  box.append(b, now);
  return box;
}
function whyText(why) {
  if (WHY.includes(why)) return tx('p332.why.' + why);
  if (why === 'offline') return tx('p332.offline');
  return '';
}
function helperLine(id) {
  const p = node('p', 'rshelper'); p.id = id;
  const a = mk('a', 'hint__link', tx('p375.help'), 'aj-prose');
  a.href = 'https://agentj.app/docs/reconnect-help/' + (lang() === 'en' ? '?lang=en' : '');
  a.target = '_blank'; a.rel = 'noopener noreferrer'; p.append(a); return p;
}
function details(F, extra = []) {
  const lines = [...F.details, ...extra].filter(Boolean);
  if (!lines.length) return null;
  const d = node('details', 'rsmore');
  d.append(node('summary', '', tx('p332.details')));
  const ul = node('ul');
  for (const l of lines.slice(-24)) ul.append(node('li', '', l));
  d.append(ul);
  return d;
}
/** Support's advice: a muted caution line above it, the advice itself strictly as text (LOW-12). */
function advice(cls, text) {
  const box = node('div', cls + ' rsadvbox');
  box.append(node('p', 'rscaution', tx('p332.advice.caution')), node('p', 'rsadvtext', text));
  return box;
}
function okBlock(flow, F) {
  const out = [node('p', 'rsok', tx('p332.ok'))];
  if (F.text && !(flow === 'help' && F.steps.some((x) => x.text === F.text))) out.push(F.advice ? advice('rsmsg', F.text) : node('p', 'rsmsg', F.text));
  if (F.old) { const p = node('p', 'rsmsg rsold'); p.id = 'rs-' + flow + '-old'; fillText(p, tx('p332.oldSession', { id: F.old })); out.push(p); }
  return out;
}
/** P393: the P380 command-result close (same look, same words). */
function closeBtn(id, fn) {
  const b = node('button', 'menuicon rscmdclose', '×'); b.type = 'button'; b.id = id;
  b.setAttribute('aria-label', tx('p380.closeResult')); b.title = tx('p380.closeResult');
  b.addEventListener('click', fn);
  return b;
}
/** The first line of an ended result with its × on the right. */
function headBar(flow, first, onClose) {
  const bar = node('div', 'rscmdstatus rshead2'); bar.id = 'rs-' + flow + '-bar2';
  bar.append(first, closeBtn('rs-' + flow + '-close', onClose));
  return bar;
}
const hm = (ms) => { const d = new Date(ms || Date.now()); return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); };
/** A folded result: one line (what happened, when) + × ; everything it said before stays under 「详情」. */
function folded(flow, F, lines, onClose) {
  const sum = node('p', 'rssum ' + (F.st === 'ok' ? 'rsok1' : 'rsfail'), tx(F.st === 'ok' ? 'p393.sumOk' : 'p393.sumFail', { time: hm(F.at) }));
  sum.id = 'rs-' + flow + '-sum';
  const out = [headBar(flow, sum, onClose)];
  const all = lines.filter(Boolean);
  if (all.length) {
    const d = node('details', 'rsmore'); d.id = 'rs-' + flow + '-log';
    d.append(node('summary', '', tx('p332.details')));
    const ul = node('ul'); for (const l of all.slice(-30)) ul.append(node('li', '', l));
    d.append(ul); out.push(d);
  }
  return out;
}
const logOf = (F) => [F.text, ...F.steps.map((x) => x.text), ...F.details, F.why && !['timeout', 'failed'].includes(F.why) ? 'why: ' + F.why : ''];
function clearFlow(flow) {
  const F = S[flow];
  if (F.st === 'run') return;
  clearTimeout(F.foldTimer);
  S[flow] = fresh();
  if (flow === 'help') { clearTimeout(S.offer.foldTimer); S.offer = fresh(); }
  paint();
  el(flow === 'help' ? 'rs-help-go' : 'rs-' + flow + '-go')?.focus({ preventScroll: true });
}
function foldLater(flow) {
  const F = S[flow];
  clearTimeout(F.foldTimer);
  F.foldTimer = setTimeout(() => { F.foldTimer = 0; if (F.st === 'ok' || F.st === 'fail') { F.fold = true; paint(); } }, FOLD_MS);
}
function acts(...bs) { const p = node('div', 'rsacts'); p.append(...bs.filter(Boolean)); return p; }
const backBtn = (flow) => btn('rs-' + flow + '-chat', tx('p332.backChat'), 'aj-action--primary', () => H.toChat());

function paintReset() {
  const card = el('rs-reset'), body = el('rs-reset-body');
  if (!card || !body) return;
  const F = S.reset;
  card.dataset.state = F.st;
  card.toggleAttribute('data-fold', F.fold);
  const parts = [];
  if (F.fold && F.st !== 'run' && F.st !== 'idle') {
    body.replaceChildren(...folded('reset', F, logOf(F), () => clearFlow('reset')), acts(btn('rs-reset-go', tx('p332.reset.go'), '', () => start('reset'))));
    return;
  }
  if (F.st === 'run') parts.push(bar('reset', F, STEPS.reset));
  if (F.st === 'ok') { const [first, ...rest] = okBlock('reset', F); parts.push(headBar('reset', first, () => clearFlow('reset')), ...rest, acts(backBtn('reset'), btn('rs-reset-go', tx('p332.again'), '', () => start('reset')))); }
  if (F.st === 'fail') {
    const known = whyText(F.why);
    const msg = node('p', 'rsmsg rsfail', known || (F.why === 'timeout' && !F.heard ? tx('p332.noAnswer') : tx('p332.reset.fail')));
    msg.id = 'rs-reset-msg';
    parts.push(headBar('reset', msg, () => clearFlow('reset')));
    if (F.why === 'offline') {               // why it can't run now is the line above; this is what to do next + when we checked
      const next = node('p', 'rsmsg rsnext'); next.id = 'rs-reset-next'; fillText(next, tx('p393.offlineNext'));
      const at = node('p', 'rsnote'); at.id = 'rs-reset-at'; at.setAttribute('role', 'status');
      at.textContent = tx('p393.checkedAt', { time: new Date(F.at || Date.now()).toTimeString().slice(0, 8) });
      parts.push(next, at);
    }
    if (!known && F.text) parts.push(node('p', 'rsmsg', F.text));
    if (F.why === 'offline' || F.why === 'unavailable') parts.push(helperLine('rs-reset-helper'));
    parts.push(acts(btn('rs-reset-go', tx('p332.again'), '', () => start('reset'))));
  }
  if (F.st === 'idle') parts.push(acts(btn('rs-reset-go', tx('p332.reset.go'), 'aj-action--primary', () => start('reset'))));
  const d = F.st === 'idle' ? null : details(F, F.why && !['timeout', 'failed'].includes(F.why) ? ['why: ' + F.why] : []);
  if (d) parts.push(d);
  body.replaceChildren(...parts);
}
function paintHelp() {
  const card = el('rs-help'), body = el('rs-help-body');
  if (!card || !body) return;
  const F = S.help, O = S.offer;
  // an offered repair the owner ran is now what this card reports (its progress, then green or what went wrong)
  card.dataset.state = O.st !== 'idle' ? O.st : F.st;
  card.toggleAttribute('data-dim', F.st === 'idle' && !S.failedOnce && !S.askFirst);
  card.toggleAttribute('data-hl', F.st === 'idle' && S.failedOnce);
  const parts = [];
  const ended = (F.st === 'ok' || F.st === 'fail') && O.st !== 'run';
  const fold = ended && (F.fold || (O.st !== 'idle' && O.fold));
  card.toggleAttribute('data-fold', fold);
  if (fold) {
    const end = O.st !== 'idle' ? O : F;
    body.replaceChildren(...folded('help', { ...end, at: Math.max(F.at, O.at) }, [...logOf(F), F.ticket ? tx('p332.help.ticket', { ticket: F.ticket }).replace(/`/g, '') : '', ...(O.st !== 'idle' ? logOf(O) : [])], () => clearFlow('help')),
      acts(btn('rs-help-go', tx('p332.help.go'), '', askHelp)));
    return;
  }
  if (ended) parts.push(headBar('help', node('p', 'rsnote', tx('p393.ended', { time: hm(Math.max(F.at, O.at)) })), () => clearFlow('help')));
  if (S.failedOnce && F.st === 'idle') parts.push(node('p', 'rstag', tx('p332.next')));
  if (F.st === 'run' || ((F.st === 'ok' || F.st === 'fail') && F.steps.length)) {
    if (F.st === 'run') parts.push(bar('help', F, STEPS.help));
    const ol = node('ol', 'rssteps'); ol.id = 'rs-help-steps'; ol.setAttribute('aria-live', 'polite');
    for (const s of F.steps) {
      const li = s.advice ? node('li', 'rsadvrow') : node('li', '', s.text);
      if (s.advice) li.append(advice('rsadvcol', s.text));
      li.dataset.state = s.state; ol.append(li);
    }
    if (F.steps.length) parts.push(ol);
  }
  if (F.ticket) {
    const p = node('p', 'rsticket'); p.id = 'rs-help-ticket'; fillText(p, tx('p332.help.ticket', { ticket: F.ticket }));
    parts.push(p, node('p', 'rsnote rsticketnote', tx('p332.help.ticketNote')));
  }
  if (F.st === 'ok' && O.st === 'idle') parts.push(...okBlock('help', F), acts(backBtn('help')));
  if (F.st === 'fail' && O.st === 'idle') {
    const known = whyText(F.why);
    const msg = node('p', 'rsmsg rsfail', known || (F.why === 'timeout' && !F.heard ? tx('p332.help.noAnswer') : tx('p332.help.fail')));
    msg.id = 'rs-help-msg';
    parts.push(msg);
    if (!known && F.text && !F.steps.some((x) => x.text === F.text)) parts.push(F.advice ? advice('rsmsg rsadvice', F.text) : node('p', 'rsmsg rsadvice', F.text));   // said once
    parts.push(helperLine('rs-help-helper'), acts(btn('rs-help-go', tx('p332.again'), '', () => start('help'))));
  }
  if (F.st === 'idle' && S.askFirst) {
    const box = node('div', 'rsask'); box.id = 'rs-help-ask'; box.setAttribute('role', 'group');
    box.append(node('p', 'rsmsg', tx('p332.help.first')),
      acts(btn('rs-help-first', tx('p332.help.tryFirst'), 'aj-action--primary', () => { S.askFirst = false; paint(); start('reset'); el('rs-reset')?.scrollIntoView({ block: 'nearest' }); }),
        btn('rs-help-anyway', tx('p332.help.anyway'), '', () => start('help'))));
    parts.push(box);
  } else if (F.st === 'idle') parts.push(acts(btn('rs-help-go', tx('p332.help.go'), S.failedOnce ? 'aj-action--primary' : '', askHelp)));
  const off = F.st === 'run' ? null : offerBox(F, O);
  if (off) parts.push(off);
  const d = F.st === 'idle' ? null : details(F, [...(F.why && !['timeout', 'failed'].includes(F.why) ? ['why: ' + F.why] : []), ...O.details,
    ...(O.why && !['timeout', 'failed'].includes(O.why) ? ['why: ' + O.why] : [])]);
  if (d) parts.push(d);
  body.replaceChildren(...parts);
}
/** ② ended with offers: one button per action the owner may confirm, then that action's progress / result. */
function offerBox(F, O) {
  const left = F.offers.filter((x) => OFFERS.includes(x) && !F.used.includes(x));
  if (!left.length && O.st === 'idle') return null;
  const box = node('div', 'rsoffer'); box.id = 'rs-offer'; box.setAttribute('role', 'group'); box.dataset.state = O.st;
  if (left.length) {
    box.setAttribute('aria-label', tx('p332.offer.intro'));
    box.append(node('p', 'rsnote', tx('p332.offer.intro')));
    for (const name of left) {
      const item = node('div', 'rsoffitem');
      const b = btn('rs-offer-' + name, tx('p332.offer.' + name), '', () => runOffer(name));
      b.dataset.offer = name;
      if (O.st === 'run') b.disabled = true;
      const note = node('p', 'rsnote', tx('p332.offer.' + name + 'Note')); note.id = 'rs-offer-' + name + '-note';
      b.setAttribute('aria-describedby', note.id);
      item.append(b, note);
      box.append(item);
    }
  }
  if (O.st !== 'idle') {
    const res = node('div', 'rsoffres'); res.id = 'rs-offer-res'; res.dataset.offer = O.name;
    if (O.st === 'run') res.append(bar('offer', { ...O, step: 1 }, STEPS.offer));
    if (O.steps.length) {
      const ol = node('ol', 'rssteps'); ol.id = 'rs-offer-steps'; ol.setAttribute('aria-live', 'polite');
      for (const s of O.steps) { const li = node('li', '', s.text); li.dataset.state = s.state; ol.append(li); }
      res.append(ol);
    }
    if (O.st === 'ok') res.append(...okBlock('offer', O), acts(backBtn('offer')));
    if (O.st === 'fail') {
      const why = O.why === 'unavailable' ? tx(O.name === 'rollback' ? 'p332.offer.noRollback' : 'p332.offer.unavailable') : whyText(O.why);
      const msg = node('p', 'rsmsg rsfail', why || (O.why === 'timeout' && !O.heard ? tx('p332.noAnswer') : tx('p332.offer.fail')));
      msg.id = 'rs-offer-msg';
      res.append(msg);
      if (!why && O.text && !O.steps.some((x) => x.text === O.text)) res.append(node('p', 'rsmsg', O.text));
      res.append(acts(btn('rs-help-go', tx('p332.help.go'), '', () => start('help'))));   // ask support to look again
    }
    box.append(res);
  }
  return box;
}
function runOffer(name) {
  const F = S.help;
  if (!OFFERS.includes(name) || !F.offers.includes(name) || F.used.includes(name) || F.st === 'run' || S.offer.st === 'run') return;
  F.used.push(name);          // the owner's tap is spent: the button goes, its progress takes its place
  start('offer', name);
  el('rs-offer-res')?.scrollIntoView({ block: 'nearest' });
}
function askHelp() {
  if (!S.failedOnce && S.reset.st !== 'fail') { S.askFirst = true; paint(); el('rs-help-anyway')?.focus({ preventScroll: true }); return; }
  start('help');
}
// ③ mirrors the moved P258 flow: starting / waiting for the owner / verifying → progress; done → green; failed → the reason
const AUTH_STEP = { starting: 1, device: 2, url: 2, paste: 2, verifying: 3, done: 4 };
function paintAuth() {
  const card = el('rs-auth'), body = el('rs-auth-body');
  if (!card || !body) return;
  const v = S.auth, f = v && v.flow;
  let st = 'idle';
  const parts = [];
  const sig = authSig(f);
  card.toggleAttribute('data-fold', !!sig && S.authFold === sig && S.authHide !== sig);
  if (sig && S.authHide === sig) { card.dataset.state = 'idle'; body.replaceChildren(); return; }   // × : gone until the next login run
  if (sig && S.authFold === sig) {
    card.dataset.state = f.stage === 'done' ? 'ok' : 'fail';
    body.replaceChildren(...folded('auth', { st: f.stage === 'done' ? 'ok' : 'fail', at: S.authAt }, [f.stage === 'done' ? tx('ar.done') : str(f.why, 200)], hideAuth));
    return;
  }
  if (f && AUTH_STEP[f.stage] && f.stage !== 'done') {
    st = 'run';
    const text = f.stage === 'starting' ? tx('ar.stStarting') : f.stage === 'verifying' ? tx('ar.verifying') : tx('p332.auth.wait');
    parts.push(bar('auth', { st, step: AUTH_STEP[f.stage], text }, 4));
  } else if (f && f.stage === 'done') {
    st = 'ok';
    parts.push(headBar('auth', node('p', 'rsok', tx('p332.ok')), hideAuth), node('p', 'rsmsg', tx('ar.done')), acts(backBtn('auth')));
  } else if (f && f.stage === 'failed') {
    st = 'fail';
    const p = node('p', 'rsmsg rsfail', tx('p332.auth.fail', { why: str(f.why, 200) || tx('ar.failed') })); p.id = 'rs-auth-msg';
    parts.push(headBar('auth', p, hideAuth));
  } else if (v && Array.isArray(v.harnesses)) {
    const need = v.harnesses.filter((h) => h && h.state === 'login' && typeof h.label === 'string').map((h) => h.label.slice(0, 40));
    if (need.length) { const p = node('p', 'rsmsg rsfail', tx('p332.auth.need', { name: need.join(' / ') })); p.id = 'rs-auth-msg'; parts.push(p); }
  }
  card.dataset.state = st;
  body.replaceChildren(...parts);
}
/** ③'s ended runs are told apart by stage + kind + reason (authrescue keeps no run id). */
function authSig(f) { return f && (f.stage === 'done' || f.stage === 'failed') ? [f.stage, f.kind || '', f.route || '', str(f.why, 200)].join('|') : ''; }
function hideAuth() { S.authHide = authSig(S.auth && S.auth.flow); paintAuth(); }
// P424 (#145): the banner can be closed (× / Esc) — for this rescue spell only (keyed by its start); a new one shows it again.
// It goes by itself as soon as Agent J is back (a normal ready — session.js resumes at the relay's `up` — or rescue off).
const spell = () => (S.rescue && S.rescue.on ? String(S.rescue.since || 'on') : '');
function hideBanner() { S.bannerHide = spell(); paintMode(); }
function paintMode() {
  const on = !!(S.rescue && S.rescue.on);
  if (!on) S.bannerHide = '';
  const b = el('rescue-banner'); if (b) b.hidden = !on || S.bannerHide === spell();
  const n = el('rs-mode'); if (n) n.hidden = !on;
  document.body.toggleAttribute('data-rescue', on);
}
// ---------------------------------------------------------------- #585: every button proves what it promises
// Chairman 11-07: 「点了没用，放在那里干啥呢？」 Each card below runs one signed `rescue_do` and shows every step the host took
// (✓ / ✗ and why), then the one thing to do next as a button — 「恢复」 when Stop everything blocks it, the next rung, or
// 「把诊断发给客服」 (the privacy-cleaned `report`, only after the owner agrees). 一键修复 climbs stop → restart Agent →
// restart service → new conversation and stops at the first rung where the Agent answers a test line.
const FIX = {
  repair: { id: 'rs-repair', title: 'p585.repair.title', note: 'p585.repair.note', go: 'p585.repair.go', primary: true },
  deep_repair: { id: 'rs-deep-repair', title: 'p0304.deep.title', note: 'p0304.deep.note', go: 'p0304.deep.go', confirm: 'p0304.deep.confirm' },
  stop_hard: { id: 'rs-stop', title: 'p585.stop.title', note: 'p585.stop.note', go: 'p585.stop.title', confirm: 'p585.stop.confirm' },
  restart_session: { id: 'rs-session', title: 'p435.session', note: 'p435.sessionNote', go: 'p435.session', confirm: 'p435.sessionConfirm' },
  restart: { id: 'rs-restart', title: 'p385.restart.go', note: 'p385.restart.note', go: 'p385.restart.go', confirm: 'p585.restart.confirm' },
  new_chat: { id: 'rs-newchat', title: 'p585.new.title', note: 'p585.new.note', go: 'p585.new.title', confirm: 'p585.new.confirm' },
  rollback: { id: 'rs-rollback', title: 'p0304.rollback.go', note: 'p0304.rollback.note', go: 'p0304.rollback.go', confirm: 'p0304.rollback.confirm' },
};
const P585_WHY = ['estop', 'no_reply', 'desktop_live', 'foreign', 'not_installed', 'all_failed'];   // a headline that names the real cause
const ORDER = ['stop_hard', 'restart_session', 'restart', 'new_chat'];      // the ladder's rungs, under ① 重置连接
const anyRun = () => FLOWS.some((f) => S[f].st === 'run');
async function sendCont(flow) {
  const F = S[flow];
  if (!F.cont || F.contSent) return;
  F.contSent = true; F.dropped = false;
  const action = F.cont;
  let obj;
  try { obj = await signed('rescue_do', { action }, { t: 'rescue_do', action }); } catch (e) { return finish(flow, { ok: false, why: e && e.message === 'no_key' ? 'no_key' : 'offline' }); }
  const r = request(obj, (m) => onMsg(flow, m));
  if (!r) return finish(flow, { ok: false, why: 'offline' });
  F.r = r; F.cont = '';
  arm(flow, IDLE_MS[flow]);
  paint();
}
/** controls.js estopChanged: Stop everything was lifted (the card's 「恢复」 or Settings → Safety) → the waiting card goes on. */
export function onEstopChange() {
  if (estop.on) return;
  for (const flow of FLOWS) {
    const F = S[flow];
    if (F.st !== 'fail' || !F.resume || !F.cont) continue;
    if (Date.now() - F.at > CONT_MS) { F.resume = false; F.cont = ''; continue; }   // security #585-6: an old card does not act
    Object.assign(F, { st: 'run', resume: false, contSent: false, fold: false, text: tx('p585.resumed') });
    F.steps.push({ n: F.step || 1, state: 'ok', text: tx('p585.resumed') });
    clearTimeout(F.foldTimer); sendCont(flow);
  }
  paint();
}
async function go(flow) {
  if (anyRun()) return;
  const c = FIX[flow];
  if (flow === 'repair') {
    const text = tx('p585.repair.confirm') + (estop.on ? '\n\n' + tx('p585.repair.confirmStop') : '');
    if (!await confirmSheet(tx('p585.repair.title'), text, tx('p585.repair.go'))) return;
  } else if (c && c.confirm && !await confirmSheet(tx(c.title), tx(c.confirm), tx(c.go))) return;
  if (flow === 'rollback') {
    if (!S.deep_repair.rollback || S.deep_repair.st !== 'fail') return;
    S.deep_repair.rollback = false; // one owner-confirmed offer; do not leave a duplicate action on the failed card
  }
  start(flow, flow === 'report' ? 'report' : flow);
}
async function sendReport(from) {
  if (anyRun()) return;
  if (!await confirmSheet(tx('p585.report.title'), tx('p585.report.confirm'), tx('p585.report.go'))) return;
  S.report = fresh(); S.reportFrom = from;
  start('report', 'report');
}
/** The buttons under an ended card: Resume · the next rung · send the diagnosis · back to the chat · again. */
function extras(flow, F) {
  const bs = [];
  if (F.st === 'fail' && F.resume) bs.push(btn('rs-' + flow + '-resume', tx('estop.resumeYes'), 'aj-action--primary', () => onResume()));
  if (F.st === 'fail' && F.next) bs.push(btn('rs-' + flow + '-next', tx('p585.next.' + F.next), F.resume ? '' : 'aj-action--primary', () => {
    if (F.next === 'help') { S.failedOnce = true; start('help'); el('rs-help')?.scrollIntoView({ block: 'nearest' }); } else go(F.next);
  }));
  if (flow === 'deep_repair' && F.st === 'fail' && F.rollback) bs.push(btn('rs-deep-rollback-go', tx('p0304.rollback.go'), '', () => go('rollback')));
  if (F.st === 'fail' && F.report) bs.push(btn('rs-' + flow + '-report', tx('p585.report.go'), 'aj-action--primary', () => sendReport(flow)));
  if (F.st === 'ok') bs.push(backBtn(flow));
  return bs;
}
function stepList(flow, F) {
  if (!F.steps.length) return null;
  const ol = node('ol', 'rssteps'); ol.id = 'rs-' + flow + '-steps'; ol.setAttribute('aria-live', 'polite');
  for (const x of F.steps) { const li = node('li', '', x.text); li.dataset.state = x.state; li.dataset.step = String(x.n); ol.append(li); }
  return ol;
}
function resultLines(F) {
  return String(F.text || '').split('\n').map((x) => x.trim()).filter(Boolean).filter((x) => !F.steps.some((y) => y.text === x));
}
function reportBox(flow) {      // the diagnosis sent from a card that gave up: its ticket, or why it was not sent
  const R = S.report;
  if (R.st === 'idle' || S.reportFrom !== flow) return null;
  const box = node('div', 'rsoffres'); box.id = 'rs-report-res'; box.dataset.state = R.st;
  if (R.st === 'run') box.append(bar('report', { ...R, step: 1 }, 1));
  if (R.st === 'ok') { const p = node('p', 'rsmsg rsticket'); fillText(p, R.text || tx('p585.report.sent')); box.append(p, node('p', 'rsnote', tx('p332.help.ticketNote'))); }
  if (R.st === 'fail') box.append(node('p', 'rsmsg rsfail', R.text || whyText(R.why) || tx('p585.report.fail')));
  return box;
}
function paintFix(flow) {
  const c = FIX[flow], F = S[flow];
  let card = el(c.id);
  if (!card) {
    card = node(flow === 'repair' || flow === 'deep_repair' || flow === 'rollback' ? 'div' : 'li', 'rscard' + (flow === 'repair' ? ' rsrepair' : '')); card.id = c.id;
    if (flow === 'repair') { const list = el('set-s-rescue')?.querySelector('.rscards'); if (!list) return; list.before(card); }
    if (flow === 'deep_repair') { const block = el('set-s-rescue'); if (!block) return; block.append(card); }
    if (flow === 'rollback') { const deep = el('rs-deep-repair'); if (!deep) return; deep.after(card); }
  }
  if (flow === 'rollback') { card.hidden = S.rollback.st === 'idle'; }
  if (flow === 'deep_repair') {
    const clear = el('rs-clear');
    if (clear && clear.nextElementSibling !== card) clear.after(card);
  }
  if (flow !== 'repair' && flow !== 'deep_repair' && flow !== 'rollback') {        // ladder order under ①: stop · restart Agent · restart service · new conversation
    const i = ORDER.indexOf(flow), prev = i === 0 ? el('rs-reset') : el(FIX[ORDER[i - 1]].id);
    if (prev && prev.nextElementSibling !== card) prev.after(card);
  }
  card.dataset.state = F.st;
  const title = node('h4', 'rstitle', tx(c.title));
  const note = node('p', 'rsnote', tx(c.note)); note.id = c.id + '-note';
  // @ix app.restart_session
  const goBtn = (label, cls) => { const b = btn(c.id + '-go', label, cls, () => go(flow)); b.disabled = anyRun(); b.setAttribute('aria-describedby', note.id); return b; };
  const parts = [title, note];
  if (F.fold && F.st === 'ok') { card.replaceChildren(title, ...folded(flow, F, logOf(F), () => clearFlow(flow)), acts(goBtn(tx(c.go), ''))); return; }
  if (F.st === 'run') { parts.push(bar(flow, F, STEPS[flow])); const l = stepList(flow, F); if (l) parts.push(l); }
  if (F.st === 'ok' || F.st === 'fail') {
    const first = F.st === 'ok' ? node('p', 'rsok', tx('p332.ok')) : node('p', 'rsmsg rsfail', whyText(F.why) || (flow === 'deep_repair' || flow === 'rollback' ? tx('p0304.fail') : P585_WHY.includes(F.why) ? tx('p585.why.' + F.why) : tx('p585.fail.' + flow)));
    first.id = c.id + '-msg';
    parts.push(headBar(flow, first, () => clearFlow(flow)));
    const l = stepList(flow, F); if (l) parts.push(l);
    for (const x of resultLines(F)) { const p = node('p', 'rsmsg rsres'); fillText(p, x); parts.push(p); }
    if (F.old) { const p = node('p', 'rsmsg rsold'); p.id = c.id + '-old'; fillText(p, tx('p332.oldSession', { id: F.old })); parts.push(p); }
    const rb = reportBox(flow); if (rb) parts.push(rb);
    parts.push(acts(...extras(flow, F), F.st === 'fail' ? goBtn(tx('p332.again'), '') : null));
    if (F.st === 'fail' && F.why === 'offline') parts.push(helperLine(c.id + '-helper'));
  }
  if (F.st === 'idle' && flow !== 'rollback') parts.push(acts(goBtn(tx(c.go), c.primary || (flow === 'restart' && estop.on) ? 'aj-action--primary' : '')));
  card.replaceChildren(...parts);
}
function paintClear() {
  const block = el('set-s-rescue');
  if (!block) return;
  let card = el('rs-clear');
  if (!card) { card = node('div', 'rscard'); card.id = 'rs-clear'; block.append(card); }
  const clear = btn('rs-clear-friend-asks', tx('p419.clearFriendAsks'), '', () => start('clear', 'clear_friend_asks'));
  clear.disabled = anyRun();
  const st = node('p', 'rsmsg', S.clear.st === 'idle' ? '' : S.clear.st === 'run' ? tx('p419.clearFriendAsks') : S.clear.text || whyText(S.clear.why));
  st.setAttribute('role', 'status');
  card.replaceChildren(node('h4', 'rstitle', tx('p419.clearFriendAsks')), acts(clear), st);
}
export async function reopenSession() { go('restart_session'); }
export function paint() {
  paintReset(); paintHelp(); assist.paintCard(el('rs-help-body')); assistTerminal.paintCard(el('rs-help-body')); paintAuth(); paintMode();
  for (const f of ['repair', ...ORDER]) paintFix(f);
  paintClear(); paintTakeover(); paintArk();
  for (const f of ['deep_repair', 'rollback']) paintFix(f);
  const block = el('set-s-rescue');
  if (block && !el('rs-detail-help')) block.append(helperLine('rs-detail-help'));
  const sup = el('rs-support-box');   // P400: 在线客服 (index.html) stays the block's last line, under the cards painted here
  if (block && sup && block.lastElementChild !== sup) block.append(sup);
  for (const f of changed) { try { f(); } catch { /* one painter never stops the rest */ } }
}

// ---------------------------------------------------------------- entry points (app.js / relay.js / settings.js)
/** ≡ 重置连接, `/reset`, the block's button: open the block and run ①. */
export function resetFromCommand() { H.openSettings(); start('reset'); }
/** The offline card's 「紧急修复」 and the rescue banner: open Settings at this block. */
export function openBlock() { H.openSettings(); refreshArk(); }
/** app.js: `{"t":"rescue","on",why,since,actions}` from the rescue watchdog (sent again for every other message). */
export function onRescue(m) {
  const on = m && m.on === true;
  const since = Number.isFinite(m && m.since) ? m.since : 0;
  if (on && S.rescue && S.rescue.on && !S.rescue.since && S.bannerHide === 'on') S.bannerHide = String(since || 'on');   // the ready's spell, now dated
  S.rescue = on ? { on, why: str(m.why, 40), since, actions: Array.isArray(m.actions) ? m.actions.slice(0, 12).map((x) => str(x, 24)) : [] } : null;
  paintMode();
}
/** app.js: a broadcast `reset_done` (another remote's run, or an answer that outlived its request). */
export function onDone(m) {
  const own = m && (m.flow === 'repair_cont' ? 'repair' : m.flow === 'deep_repair_cont' ? 'deep_repair' : m.flow === 'probe' ? FLOWS.find((f) => S[f].st === 'run' && S[f].cont === 'probe')
    : ['repair', 'stop_hard', 'new_chat', 'report', 'deep_repair', 'rollback'].includes(m.flow) ? m.flow : '');
  if (own) { if (S[own].st === 'run' && (S[own].dropped || S[own].cont)) finish(own, m); return; }
  const flow = m && (m.flow === 'end_turn' || m.flow === 'takeover') ? m.flow : m && m.flow === 'restart_session' ? 'restart_session' : m && /^ark_fix_[0-9a-f]{24}$/.test(m.flow || '') ? 'ark' : m && m.flow === 'clear_friend_asks' ? 'clear' : m && m.flow === 'restart' ? 'restart' : m && m.flow === 'help' ? 'help' : m && OFFERS.includes(m.flow) ? 'offer' : 'reset';
  if (flow === 'offer' && m.flow !== S.offer.name) return;
  if (flow === 'ark' && m.flow !== S.ark.name) return;
  if (S[flow].st === 'run' && S[flow].dropped) { if (flow === 'ark') onMsg(flow,m); else finish(flow,m); }
}
/** app.js onReady: rescue mode follows the ready message; a reset that was running when the link dropped and came back
 *  to a normal Agent J did its job (step 4 restarts the computer's side, which ends that very connection). */
export function onReady() {
  if (!peer.rescue) { S.rescue = null; }
  else if (!S.rescue) S.rescue = { on: true, why: '', since: 0, actions: [] };
  for (const flow of FLOWS) {
    const F = S[flow];
    if (F.st === 'run' && F.wait) { F.st = 'idle'; F.wait = false; start(flow, F.name); continue; }   // P393: tapped while reconnecting
    if (F.st === 'run' && F.cont && !peer.rescue) { if (!F.contSent) sendCont(flow); continue; }   // #585: back after the restart
    if (!['ark', 'end_turn', 'takeover', 'deep_repair', 'rollback'].includes(flow) && F.st === 'fail' && F.why === 'offline' && !peer.rescue) finish(flow, { ok: true, text: tx('p392.restored') });
    if (!['ark', 'deep_repair', 'rollback'].includes(flow) && F.st === 'run' && F.dropped && !peer.rescue) finish(flow, { ok: true, step: F.step, ticket: F.ticket, text: tx('p332.reset.back') });
    if ((F.st === 'ok' || F.st === 'fail') && !F.fold && !F.foldTimer) foldLater(flow);   // the link is back: ended results fold
  }
  refreshArk();
  paintMode();
  assist.paintCard(el('rs-help-body')); assistTerminal.paintCard(el('rs-help-body'));   // 0.26.9 #468: the step follows this host's `assist1` cap
}
/** P393 (app.js): the link has been unreachable for the full five seconds: a tap that was waiting for it says why now. */
export function onLinkDown() { for (const flow of FLOWS) if (S[flow].st === 'run' && S[flow].wait) finish(flow, { ok: false, why: 'offline' }); }
/** P393 (settings.js open): opening Settings again shows ended results as one line each; their log stays under 「详情」. */
export function foldEnded() { refreshArk();
  let any = false;
  for (const flow of FLOWS) { const F = S[flow]; if ((F.st === 'ok' || F.st === 'fail') && !F.fold) { clearTimeout(F.foldTimer); F.foldTimer = 0; F.fold = true; any = true; } }
  const sig = authSig(S.auth && S.auth.flow);
  if (sig && S.authFold !== sig) { S.authFold = sig; any = true; }
  if (any) paint();
}
export const inRescue = () => !!(S.rescue && S.rescue.on);
export function configure(hooks) {
  layoutDiag.configure();
  H = { ...H, ...hooks };
  authrescue.watch((v) => { const was = authSig(S.auth && S.auth.flow); S.auth = v; const now = authSig(v && v.flow); if (now !== was) S.authAt = Date.now(); if (v && v.flow && !now) S.authFold = S.authHide = ''; paintAuth(); });
  el('rescue-banner-go')?.addEventListener('click', openBlock);
  el('rescue-banner-x')?.addEventListener('click', hideBanner);
  registerLayer({ element: () => el('rescue-banner'), open: () => { const b = el('rescue-banner'); return !!b && !b.hidden; }, close: hideBanner,
    rank: 1, trap: false, preserveOutsideFocus: true });
  onLang(paint);
  paint();
}
// ---------------------------------------------------------------- 0.26.9 selfheal (CONTRACT §C, #461)
// 「接管卡住的会话」: signed `rescue_do {action:"takeover"}` — the host interrupts the turn, restarts the AI coding tool's
// background service if it is still busy after 10 s, resumes the same conversation and reconnects (reset_step / reset_done
// as for ①). The card is the first of 紧急修复 while a turn is stuck (the chat's queued notice says so), else under ①.
// 「强制结束这一轮」 (the chat's queued notice): signed `rescue_do {action:"end_turn"}` — only the interrupt step.
const changed = [];
/** relay.js: repaint the chat when an end_turn / takeover run moves on. */
export function onChange(fn) { changed.push(fn); }
/** relay.js: is a turn stuck right now (host `stuck`, or waited past the threshold)? Puts 「接管」 first. */
export function setStuck(on) { on = !!on; if (S.stuck === on) return; S.stuck = on; paintTakeover(); }
export const flowState = (flow) => (flow === 'end_turn' || flow === 'takeover' ? { st: S[flow].st, text: S[flow].text, why: S[flow].why, step: S[flow].step } : null);
export const whyLine = (why) => whyText(why);
export async function endTurn() {
  if (S.end_turn.st === 'run') return;
  if (!await confirmSheet(tx('p0269.queue.end'), tx('p0269.queue.endConfirm'), tx('p0269.queue.end'), true)) return;
  start('end_turn', 'end_turn');
}
/** The chat's end_turn result line was read: back to idle (the next stuck notice offers it again). */
export function clearEndTurn() { if (S.end_turn.st !== 'run') { clearTimeout(S.end_turn.foldTimer); S.end_turn = fresh(); } }
export async function takeover() {
  if (S.takeover.st === 'run') return;
  if (!await confirmSheet(tx('p0269.take.title'), tx('p0269.take.confirm'), tx('p0269.take.title'))) return;
  start('takeover', 'takeover');
}
function paintTakeover() {
  const reset = el('rs-reset');
  if (!reset) return;
  let card = el('rs-takeover');
  if (!card) { card = node('li', 'rscard'); card.id = 'rs-takeover'; }
  const F = S.takeover;
  // first while a turn is stuck; otherwise right under ① (and its 重启 Agent row)
  const anchor = S.stuck ? null : (el('rs-newchat') || el('rs-session') || reset);
  if (S.stuck) { if (reset.previousElementSibling !== card) reset.before(card); }
  else if (anchor.nextElementSibling !== card) anchor.after(card);
  card.dataset.state = F.st;
  card.toggleAttribute('data-hl', S.stuck && F.st === 'idle');
  const parts = [node('h4', 'rstitle', tx('p0269.take.title'))];
  if (S.stuck && F.st === 'idle') { const tag = node('p', 'rstag', tx('p0269.take.stuck')); tag.id = 'rs-takeover-tag'; parts.push(tag); }
  const note = node('p', 'rsnote', tx('p0269.take.note')); note.id = 'rs-takeover-note'; parts.push(note);
  const busy = FLOWS.some((f) => f !== 'takeover' && S[f].st === 'run');
  const go = (label, cls) => { const b = btn('rs-takeover-go', label, cls, takeover); b.disabled = busy; b.setAttribute('aria-describedby', note.id); return b; };
  if (F.fold && (F.st === 'ok' || F.st === 'fail')) { card.replaceChildren(parts[0], ...folded('takeover', F, logOf(F), () => clearFlow('takeover')), acts(go(tx('p0269.take.title'), ''))); return; }
  if (F.st === 'run') parts.push(bar('takeover', F, STEPS.takeover));
  if (F.st === 'ok') { const [first, ...rest] = okBlock('takeover', F); parts.push(headBar('takeover', first, () => clearFlow('takeover')), ...rest, acts(backBtn('takeover'))); }
  if (F.st === 'fail') {
    const known = whyText(F.why), lines = resultLines(F), real = !known && F.heard && lines.length ? lines.shift() : '';
    // #585: the host's real reason is the headline (never the generic 「没能接管」 above a different reason)
    const msg = node('p', 'rsmsg rsfail', known || real || (F.why === 'timeout' && !F.heard ? tx('p332.noAnswer') : tx('p0269.take.fail'))); msg.id = 'rs-takeover-msg';
    parts.push(headBar('takeover', msg, () => clearFlow('takeover')));
    for (const x of lines) { const p = node('p', 'rsmsg rsres'); fillText(p, x); parts.push(p); }
    parts.push(acts(...extras('takeover', F), go(tx('p332.again'), '')));   // #585: the real reason + the next step as a button
  }
  if (F.st === 'idle') parts.push(acts(go(tx('p0269.take.title'), S.stuck ? 'aj-action--primary' : '')));
  card.replaceChildren(...parts);
}

// tests
export const _debug = () => ({ reset: { ...S.reset, timer: !!S.reset.timer, foldTimer: !!S.reset.foldTimer }, help: { ...S.help, timer: !!S.help.timer, foldTimer: !!S.help.foldTimer }, offer: { ...S.offer, timer: !!S.offer.timer }, failedOnce: S.failedOnce, askFirst: S.askFirst, rescue: S.rescue, stuck: S.stuck, end_turn: { ...S.end_turn, timer: !!S.end_turn.timer }, takeover: { ...S.takeover, timer: !!S.takeover.timer } });
export function _reset() { arkOffer=null; arkDismissed=''; for (const f of FLOWS) { clearTimeout(S[f].timer); clearTimeout(S[f].foldTimer); if (S[f].r) done(S[f].r); S[f] = fresh(); } S.failedOnce = false; S.askFirst = false; S.stuck = false; S.authFold = S.authHide = ''; paint(); }
export function _foldNow() { for (const f of FLOWS) { const F = S[f]; if (F.foldTimer) { clearTimeout(F.foldTimer); F.foldTimer = 0; if (F.st === 'ok' || F.st === 'fail') F.fold = true; } } paint(); }


let arkOffer = null, arkDismissed = '';
export function onArkPlan(m) {
  const o = m && m.offer;
  arkOffer = o && /^ark_fix_[0-9a-f]{24}$/.test(o.action || '') && ['agent','coding'].includes(o.plan)
    && typeof o.model === 'string' && o.model.startsWith(o.plan === 'agent' ? 'ark-agent-plan/' : 'ark-coding-plan/') && /^(ark-agent-plan|ark-coding-plan)\/[A-Za-z0-9._:/@+-]{1,128}$/.test(o.model) ? o : null;
  const F = S.ark;
  if (F.st === 'run' && F.dropped && typeof F.expected === 'string') {
    if (m.current === F.expected) finish('ark',{ok:true,text:tx('p419.arkDone',{plan:F.expected.startsWith('ark-agent-plan/')?'Agent Plan':'Coding Plan'})});
    else if (['failed','rolled_back'].includes(m.outcome)) finish('ark',{ok:false,text:tx('p419.arkFailed')});
  }
  paintArk();
}
async function refreshArk() {
  if (!isReady() || peer.rescue) return;
  const m = await ask1({t:'ark_plan_get'}, 'ark_plan', 5000);
  if (m?.t === 'ark_plan') onArkPlan(m);
  if (S.ark.st === 'run' && S.ark.dropped) setTimeout(refreshArk,1500);
}
function paintArk() {
  const block = el('set-s-rescue'), anchor = el('rescue-banner');
  if (!block) return;
  const F = S.ark;
  let card = el('rs-ark-plan');
  if (!card && !arkOffer && F.st === 'idle') return;
  if (!card) { card = node('div','rscard'); card.id = 'rs-ark-plan'; block.append(card); }
  const label = arkOffer?.plan === 'coding' ? tx('p419.useCoding') : tx('p419.useAgent');
  const action=arkOffer?.action;
  const makeGo = id => { const b=btn(id,label,'',()=>start('ark',action)); b.disabled=!arkOffer || FLOWS.some(f=>S[f].st==='run'); return b; };
  const msg = F.st === 'idle' ? tx('p419.arkMismatch') : F.st === 'run' ? (F.text || tx('p419.arkWait')) : F.text || tx('p419.arkFailed');
  card.hidden = !arkOffer && F.st === 'idle';
  const heading=F.st==='ok'?tx('p419.arkDone',{plan:F.expected?.startsWith('ark-agent-plan/')?'Agent Plan':'Coding Plan'}):tx('p419.arkMismatch');
  const title=node('h4','rstitle',heading);
  card.replaceChildren(...(['ok','fail'].includes(F.st)?[headBar('ark',title,()=>clearFlow('ark'))]:[title]),...(msg===heading?[]:[node('p','rsmsg',msg)]),...(arkOffer?[node('p','rsnote',arkOffer.model)]:[]),...(arkOffer?[acts(makeGo('rs-ark-fix-go'))]:[]));
  let banner=el('ark-plan-banner');
  if (!banner && anchor) { banner=node('div','setup-banner'); banner.id='ark-plan-banner'; banner.setAttribute('role','status'); anchor.before(banner); }
  if (banner) {
    banner.hidden=(!arkOffer && F.st==='idle') || (arkOffer?.action===arkDismissed && F.st==='idle');
    const close=btn('ark-plan-dismiss','✕','setup-banner__x',()=>{arkDismissed=arkOffer?.action||'';if(['ok','fail'].includes(F.st))clearFlow('ark');banner.hidden=true;});
    close.setAttribute('aria-label',tx('ar.renewLater'));
    banner.replaceChildren(node('p','setup-banner__open',msg),...(arkOffer?[makeGo('ark-plan-fix-go')]:[]),close);
  }
}
