import {lang} from './t.js';
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
import { signed } from './controls.js';
import { t, fillText, onLang } from './t.js';
import { el, mk, confirmSheet } from './ui.js';
import * as authrescue from './authrescue.js';
import { registerLayer } from './layers.js';

export const HELPER_CMD = 'curl -fsSL https://agentj.app/rescue | sh';
export const STEPS = { reset: 4, help: 3, offer: 1, restart: 1, restart_session: 1, clear: 1, ark: 1 };          // §33: the reset ladder has four steps, the help flow three
export const FIRST_MS = 30000;                       // no answer at all this long → the host is stuck or too old
export const IDLE_MS = { reset: 120000, help: 240000, offer: 240000, restart: 120000, restart_session: 120000, clear: 120000, ark: 240000 };   // then: silence this long between two messages = no answer
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
  offers: [], used: [], name: '', advice: false, fold: false, foldTimer: 0, wait: false, at: 0 });
const S = { reset: fresh(), help: fresh(), offer: fresh(), restart: fresh(), restart_session: fresh(), clear: fresh(), ark: fresh(), failedOnce: false, askFirst: false, rescue: null, auth: null, authFold: '', authHide: '', bannerHide: '' };
const FLOWS = ['reset', 'help', 'offer', 'restart', 'restart_session', 'clear', 'ark'];

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
  if (flow === 'offer') return tx('p332.reset.start');
  if (flow === 'reset') return tx('p332.reset.s' + Math.min(4, Math.max(1, n)));
  return tx('p332.help.s' + Math.min(3, Math.max(1, n)));
}
function finish(flow, m) {
  const F = S[flow];
  takeOffers(flow, m);
  clearTimeout(F.timer); F.timer = 0;
  if (F.r) { done(F.r); F.r = null; }
  const ok = m.ok === true;
  if (Number.isInteger(m.step) && m.step > 0 && m.step < 20) F.step = m.step;
  const tk = str(m.ticket, 64);
  if (TICKET.test(tk)) F.ticket = tk;
  const old = str(m.old_session, 80);
  F.old = ok && OLD.test(old) ? old : '';
  F.why = ok ? '' : String(m.why || 'failed');
  F.text = str(m.text, flow === 'help' ? 1600 : 400);
  const d = str(m.detail, 600);
  if (d) F.details.push(d);
  F.st = ok ? 'ok' : 'fail'; F.wait = false; F.at = Date.now();
  if (ok && isReady()) foldLater(flow);
  if (flow === 'help' && F.text && (m.advice === true || ADVICE.test(F.text))) F.advice = true;
  // ① did not bring the link back (a real failure, or a computer that never answered) → ② is the next thing to try
  if (!ok && flow === 'reset' && !['offline', 'no_key', 'shape', 'stale', 'bad_signature', 'replay', 'busy'].includes(F.why)) S.failedOnce = true;
  paint();
}

// ---------------------------------------------------------------- painting
const node = (tag, cls, text) => mk(tag, cls, text);
function btn(id, label, cls, fn) {
  const b = node('button', 'aj-btn ' + (cls || ''), label);
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
  const a = node('a', 'hint__link', tx('p375.help'));
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
const backBtn = (flow) => btn('rs-' + flow + '-chat', tx('p332.backChat'), 'aj-btn--primary', () => H.toChat());

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
  if (F.st === 'idle') parts.push(acts(btn('rs-reset-go', tx('p332.reset.go'), 'aj-btn--primary', () => start('reset'))));
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
      acts(btn('rs-help-first', tx('p332.help.tryFirst'), 'aj-btn--primary', () => { S.askFirst = false; paint(); start('reset'); el('rs-reset')?.scrollIntoView({ block: 'nearest' }); }),
        btn('rs-help-anyway', tx('p332.help.anyway'), '', () => start('help'))));
    parts.push(box);
  } else if (F.st === 'idle') parts.push(acts(btn('rs-help-go', tx('p332.help.go'), S.failedOnce ? 'aj-btn--primary' : '', askHelp)));
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
function paintSession() {
  const reset = el('rs-reset');
  if (!reset) return;
  let card = el('rs-session');
  if (!card) { card = node('li', 'rscard'); card.id = 'rs-session'; reset.after(card); }
  const F = S.restart_session;
  // @ix app.restart_session
  const go = btn('rs-session-go', tx('p435.session'), '', async () => {
    if (await confirmSheet(tx('p435.session'), tx('p435.sessionConfirm'), tx('p435.session'))) start('restart_session', 'restart_session');
  });
  go.disabled = FLOWS.some(f => S[f].st === 'run');
  const status = node('p', 'rsmsg', F.text || ''); status.setAttribute('role', 'status');
  card.replaceChildren(node('h4', 'rstitle', tx('p435.session')), node('p', 'rsnote', tx('p435.sessionNote')), acts(go), status);
}
function paintRestart() {
  const block = el('set-s-rescue');
  if (!block) return;
  let card = el('rs-restart');
  if (!card) { card = node('div', 'rscard'); card.id = 'rs-restart'; block.append(card); }
  const F = S.restart;
  const go = btn('rs-restart-go', tx('p385.restart.go'), '', () => start('restart', 'restart'));
  go.disabled = FLOWS.some(flow => S[flow].st === 'run');
  const note = node('p', 'rsnote', tx('p385.restart.note')); note.id = 'rs-restart-note';
  go.setAttribute('aria-describedby', note.id);
  const status = node('p', 'rsmsg', F.st === 'idle' || F.fold ? '' : F.st === 'run' ? tx('p385.restart.wait') : F.st === 'ok' ? tx('p332.reset.back') : whyText(F.why) || tx('p332.noAnswer'));
  status.setAttribute('role', 'status');
  card.dataset.state = F.st;
  const clear = btn('rs-clear-friend-asks', tx('p419.clearFriendAsks'), '', () => start('clear', 'clear_friend_asks'));
  clear.disabled = go.disabled;
  const clearStatus = node('p', 'rsmsg', S.clear.st === 'idle' ? '' : S.clear.st === 'run' ? tx('p419.clearFriendAsks') : S.clear.text || whyText(S.clear.why));
  clearStatus.setAttribute('role', 'status');
  card.replaceChildren(node('h4', 'rstitle', tx('p385.restart.go')), node('small', 'rsnote', tx('p438.recommended')), note, acts(go, clear), status, clearStatus);
}
export function paint() {
  paintReset(); paintHelp(); paintAuth(); paintMode(); paintRestart(); paintSession(); paintArk();
  const block = el('set-s-rescue');
  if (block && !el('rs-detail-help')) block.append(helperLine('rs-detail-help'));
  const sup = el('rs-support-box');   // P400: 在线客服 (index.html) stays the block's last line, under the cards painted here
  if (block && sup && block.lastElementChild !== sup) block.append(sup);
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
  const flow = m && m.flow === 'restart_session' ? 'restart_session' : m && /^ark_fix_[0-9a-f]{24}$/.test(m.flow || '') ? 'ark' : m && m.flow === 'clear_friend_asks' ? 'clear' : m && m.flow === 'restart' ? 'restart' : m && m.flow === 'help' ? 'help' : m && OFFERS.includes(m.flow) ? 'offer' : 'reset';
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
    if (flow !== 'ark' && F.st === 'fail' && F.why === 'offline' && !peer.rescue) finish(flow, { ok: true, text: tx('p392.restored') });
    if (flow !== 'ark' && F.st === 'run' && F.dropped && !peer.rescue) finish(flow, { ok: true, step: F.step, ticket: F.ticket, text: tx('p332.reset.back') });
    if ((F.st === 'ok' || F.st === 'fail') && !F.fold && !F.foldTimer) foldLater(flow);   // the link is back: ended results fold
  }
  refreshArk();
  paintMode();
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
  H = { ...H, ...hooks };
  authrescue.watch((v) => { const was = authSig(S.auth && S.auth.flow); S.auth = v; const now = authSig(v && v.flow); if (now !== was) S.authAt = Date.now(); if (v && v.flow && !now) S.authFold = S.authHide = ''; paintAuth(); });
  el('rescue-banner-go')?.addEventListener('click', openBlock);
  el('rescue-banner-x')?.addEventListener('click', hideBanner);
  registerLayer({ element: () => el('rescue-banner'), open: () => { const b = el('rescue-banner'); return !!b && !b.hidden; }, close: hideBanner,
    rank: 1, trap: false, preserveOutsideFocus: true });
  onLang(paint);
  paint();
}
// tests
export const _debug = () => ({ reset: { ...S.reset, timer: !!S.reset.timer, foldTimer: !!S.reset.foldTimer }, help: { ...S.help, timer: !!S.help.timer, foldTimer: !!S.help.foldTimer }, offer: { ...S.offer, timer: !!S.offer.timer }, failedOnce: S.failedOnce, askFirst: S.askFirst, rescue: S.rescue });
export function _reset() { arkOffer=null; arkDismissed=''; for (const f of FLOWS) { clearTimeout(S[f].timer); clearTimeout(S[f].foldTimer); if (S[f].r) done(S[f].r); S[f] = fresh(); } S.failedOnce = false; S.askFirst = false; S.authFold = S.authHide = ''; paint(); }
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
