// 0.30.78: display receipts for ordinary questions and pending v2 approvals. Decisions use their existing signed paths.
import { gen, sendApp, peer } from './session.js';
import { S } from './snap.js';
import { canSign } from './api.js';
import { renderedApproval } from './approval541.js';
import { canonicalJson, textProblem } from '../proto/wire.js';

const ID = /^[0-9a-f]{32}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const MAX_PENDING = 64;
const POLL_MS = 125;
const probes = new Map();
let timer = 0;

const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);

function matchesQuestion(message, card) {
  if (message.t !== 'question' || !card || card.kind !== 'question' || card.state !== 'open' ||
      !Array.isArray(message.qs) || message.qs.length !== card.raw.length ||
      !Number.isInteger(message.ttl) || message.ttl <= 0 || message.ttl > 3600 ||
      message.text_max !== 2000 || card.textMax !== 2000) return false;
  // Require the ordinary normalized §10.7 shape. The terminal screen header belongs to its own path.
  return message.qs.every((question, index) => {
    const shown = card.raw[index];
    return record(question) && Object.keys(question).length === 4 && !textProblem(question.q, 1000) && question.q.trim() &&
      !textProblem(question.h, 64) && !question.h.includes('\n') && typeof question.m === 'boolean' &&
      question.q === shown.q && question.h === shown.h && question.m === shown.m &&
      Array.isArray(question.o) && question.o.length === shown.o.length &&
      question.o.every((option, i) => record(option) && Object.keys(option).length === 2 && !textProblem(option.l, 200) && option.l.trim() &&
        !textProblem(option.d, 500) && option.l === shown.o[i].l && option.d === shown.o[i].d);
  }) && new Set(message.qs.map(question => question.q)).size === message.qs.length;
}

function matchesAsk(message, card) {
  if (!card || card.kind !== 'permission' || card.state !== 'open' || card.touched ||
      message.approval_v !== 2 || card.approval_v !== 2 || card.approval_invalid ||
      message.review_state !== 'pending' || card.review_state !== 'pending' ||
      !Number.isSafeInteger(message.revision) || message.revision < 1 || message.revision !== card.revision ||
      typeof message.envelope_sha256 !== 'string' || !DIGEST.test(message.envelope_sha256) ||
      message.envelope_sha256 !== card.envelope_sha256 || message.tool !== card.tool ||
      message.summary !== card.summary || (message.passkey_required === true) !== card.passkey_required ||
      !record(message.envelope) || !record(card.envelope)) return false;
  try {
    return canonicalJson(message.envelope) === canonicalJson(card.envelope);
  } catch {
    return false;
  }
}

const key = (kind, id) => kind + ':' + id;

function live(probe) {
  if (probe.kind === 'special') {
    return canSign() && gen() === probe.generation && probes.get(key(probe.kind, probe.id)) === probe &&
      performance.now() < probe.expires && probe.current();
  }
  const ask = probe.kind === 'ask';
  return canSign() && gen() === probe.generation && probes.get(key(probe.kind, probe.id)) === probe &&
    (ask ? S.asks : S.qs).get(probe.id) === probe.card && probe.card.state === 'open' &&
    (!ask || (probe.card.review_state === 'pending' && !probe.card.touched)) &&
    Date.now() < probe.card.deadline && performance.now() < probe.expires;
}

export function visible(element, rounded = false) {
  if (!element || element.closest('[hidden],[inert],[aria-hidden="true"]')) return false;
  for (let current = element; current; current = current.parentElement) {
    const style = getComputedStyle(current);
    if (style.visibility !== 'visible' || Number(style.opacity) <= 0) return false;
  }
  const bounds = element.getBoundingClientRect();
  const view = window.visualViewport;
  const left = view?.offsetLeft ?? 0, top = view?.offsetTop ?? 0;
  if (bounds.width < 2 || bounds.height < 2 || bounds.left < left || bounds.top < top ||
      bounds.right > left + (view?.width ?? innerWidth) || bounds.bottom > top + (view?.height ?? innerHeight)) return false;
  // Noir notices can obscure the sheet even while letting input through to it.
  for (const notice of document.querySelectorAll('.toast[data-aj-owner="aj-notice"]')) {
    const cover = notice.getBoundingClientRect();
    if (cover.width && cover.height && cover.left < bounds.right && cover.right > bounds.left &&
        cover.top < bounds.bottom && cover.bottom > bounds.top) {
      const style = getComputedStyle(notice);
      if (style.visibility === 'visible' && Number(style.opacity) > 0) return false;
    }
  }
  const samples = rounded ? [[.18, .5], [.82, .5], [.5, .5], [.5, .18], [.5, .82]]
    : [[.03, .03], [.97, .03], [.5, .5], [.03, .97], [.97, .97]];
  return samples.every(([x, y]) =>
    element.contains(document.elementFromPoint(bounds.left + x * bounds.width, bounds.top + y * bounds.height)));
}

function displayed(probe) {
  if (probe.kind === 'special') {
    return !document.hidden && document.visibilityState === 'visible' && document.hasFocus() && probe.onScreen();
  }
  if (document.hidden || document.visibilityState !== 'visible' || !document.hasFocus() ||
      document.body.dataset.sheet !== '1' || (probe.kind === 'question' && document.body.dataset.view !== 'chat')) return false;
  const sheet = document.getElementById('sheet');
  if (!sheet || sheet.dataset.card !== [probe.card.kind, probe.id, probe.card.at].join('|')) return false;
  if (probe.kind === 'ask') {
    const title = sheet.querySelector('#sheetTitle'), action = sheet.querySelector('#action541');
    return sheet.dataset.approvalV === '2' && renderedApproval(probe.card) &&
      !!title?.textContent.trim() && !!action?.textContent.trim() &&
      visible(title) && visible(action) && visible(sheet.querySelector('#apprDeny'), true);
  }
  const qs = sheet.querySelector('#qs');
  const heading = probe.card.raw.length === 1 ? sheet.querySelector('#sheetTitle') : qs?.querySelector('h3');
  const choice = qs?.querySelector('button.opt[data-q="0"][data-n="1"]');
  const label = choice?.querySelector('.t > span');
  const question = probe.card.raw[0];
  return heading?.textContent.endsWith(question.q) && label?.textContent === question.o[0].l &&
    visible(heading) && visible(choice);
}

function report(probe, phase) {
  probe.sending = true;
  const observe = () => {
    if (!live(probe) || (probe.kind === 'ask' && probe.card.envelope_verified !== true) ||
        (phase === 'visible' && !displayed(probe))) return false;
    probe.attempts[phase]++;
    return true;
  };
  sendApp({ t: 'card_receipt', kind: probe.kind, id: probe.id, n: probe.n, phase }, probe.generation, null, observe)
    .then(() => { if (phase === 'received') probe.received = true; })
    .catch(() => {})
    .finally(() => { probe.sending = false; schedule(); });
}

function afterPaint(probe) {
  probe.paint = requestAnimationFrame(() => {
    probe.paint = requestAnimationFrame(() => {
      probe.paint = 0;
      if (live(probe) && displayed(probe)) report(probe, 'visible');
      else schedule();
    });
  });
}

function poll() {
  timer = 0;
  let waiting = false;
  for (const probe of probes.values()) {
    if (!live(probe)) { drop(probe.kind, probe.id); continue; }
    if (probe.sending || probe.paint) continue;
    if (probe.kind === 'ask' && probe.card.envelope_verified !== true) { waiting = true; continue; }
    if (probe.attempts.received < probe.limit) report(probe, 'received');
    else if (probe.received && probe.attempts.visible < probe.limit) {
      if (displayed(probe)) afterPaint(probe);
      else waiting = true;
    }
  }
  if (waiting) schedule();
}

function schedule() {
  if (!timer && probes.size) timer = setTimeout(poll, POLL_MS);
}

/** Observe an authenticated card only after the existing snapshot accepted or recognized it. */
export function observe(message, card, newCard) {
  const generation = gen(), { t: kind, id, delivery: offer } = message;
  const ask = kind === 'ask';
  const supported = ask ? peer.cardAskReceipt1 : kind === 'question' && peer.cardReceipt1;
  if (!supported || !canSign() || !generation || typeof id !== 'string' || !ID.test(id) || !record(offer) ||
      Object.keys(offer).length !== 2 || typeof offer.n !== 'string' || !ID.test(offer.n) ||
      !Number.isSafeInteger(message.ttl) || !Number.isInteger(offer.ttl) || offer.ttl <= 0 || offer.ttl > 60 ||
      offer.ttl > message.ttl || !(ask ? matchesAsk(message, card) : matchesQuestion(message, card))) return;
  install(message, generation, { kind, card, newCard });
}

/** Observe specialized presenters without creating another approval action or card. */
export function observeSpecial(message, current, onScreen) {
  const offer = message.delivery, generation = gen();
  if (!peer.cardSpecialReceipt1 || !generation || !canSign() || typeof message.id !== 'string' ||
      !ID.test(message.id) || !record(offer) || Object.keys(offer).length !== 2 ||
      typeof offer.n !== 'string' || !ID.test(offer.n) || !Number.isSafeInteger(message.ttl) ||
      !Number.isInteger(offer.ttl) || offer.ttl <= 0 || offer.ttl > 60 || offer.ttl > message.ttl) return;
  install(message, generation, { kind: 'special', current, onScreen, newCard: true });
}

function install(message, generation, { kind, card, current, onScreen, newCard }) {
  const { id, delivery: offer } = message;
  for (const probe of probes.values()) if (!live(probe)) drop(probe.kind, probe.id);
  const probeKey = key(kind, id);
  const existing = probes.get(probeKey);
  if (existing) {
    // A matching reoffer opens a second attempt during the original receipt window.
    if (existing.n === offer.n && message.ttl <= existing.ttl && offer.ttl <= existing.offerTTL &&
        existing.card === card && existing.generation === generation) existing.limit = 2;
  } else if (newCard && probes.size < MAX_PENDING) {
    probes.set(probeKey, { kind, id, n: offer.n, generation, card, current, onScreen,
      ttl: message.ttl, offerTTL: offer.ttl, expires: performance.now() + offer.ttl * 1000, limit: 1, received: false,
      attempts: { received: 0, visible: 0 }, sending: false, paint: 0,
      expiryTimer: setTimeout(() => drop(kind, id), offer.ttl * 1000) });
  }
  schedule();
}

export function drop(kind, id) {
  const probeKey = key(kind, id), probe = probes.get(probeKey);
  if (!probe) return;
  if (probe.paint) cancelAnimationFrame(probe.paint);
  clearTimeout(probe.expiryTimer);
  probes.delete(probeKey);
}

export function clear() {
  clearTimeout(timer);
  timer = 0;
  for (const probe of probes.values()) drop(probe.kind, probe.id);
}
