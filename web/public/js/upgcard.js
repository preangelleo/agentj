// P203 (PROTOCOL §29.2): the upgrade confirmation card. Before a manual upgrade — ≡ /update, Settings 「升级Agent J 主机上的 Agent J」,
// or the Agent running `agentj update apply` for the owner — the computer lists what a restart now would interrupt (a turn
// in progress, queued messages, cards waiting, scheduled / background tasks, friend sessions). Nothing → upgrade at once,
// one tap. Something → this card; 「现在升级」 is this phone's own signed /update, 「取消」 closes it on every remote.
import { ask1, isReady, peer, sendApp } from './session.js';
import { t } from './t.js';
import { el, toast, confirmSheet, confirmOpen, closeConfirm } from './ui.js';

export const KINDS = ['turn', 'shared_turn', 'queue', 'ask', 'question', 'secret', 'card', 'friend_card', 'friend', 'task',
  'task_queue', 'bots', 'bg', 'task_soon'];
const ID = /^[0-9a-f]{32}$/;
let H = { upgrade: async () => false };
let card = null;          // the computer's open card {id, items, to, deadline}
let showing = null;       // the id of the card on screen
export function configure(h) { H = { ...H, ...h }; }

export function lines(items) {
  return (Array.isArray(items) ? items : []).filter((x) => x && KINDS.includes(x.k) && Number.isInteger(x.n) && x.n > 0)
    .slice(0, KINDS.length).map((x) => t('upg.k.' + x.k, { n: x.n }));
}
async function ask(items, to) {
  const box = el('confirm');
  box.dataset.kind = 'upg';
  const body = t('upg.lead') + '\n' + lines(items).map((l) => '· ' + l).join('\n');
  try { return await confirmSheet(to ? t('upg.titleTo', { to }) : t('upg.title'), body, t('upg.yes'), false, t('upg.no')); }
  finally { delete box.dataset.kind; }
}

/** ≡ /update and Settings: ask the computer first. Nothing in the way → the signed /update at once. An older computer (no
 *  upg1) or no answer → the old one-step /update (the computer still waits until it is idle before restarting). */
export async function start() {
  if (!isReady()) return false;
  if (!peer.upg1) return H.upgrade();
  const m = await ask1({ t: 'upg_check' }, 'upg_impact', 8000);
  if (m.t !== 'upg_impact') return H.upgrade();
  if (!lines(m.items).length) return H.upgrade();
  if (!(await ask(m.items, m.newer && typeof m.latest === 'string' ? m.latest.slice(0, 32) : ''))) return false;
  return H.upgrade();
}

/** host → `upg_card` (the Agent's `agentj update apply` found things a restart would interrupt; re-sent after ready). */
export async function add(m) {
  if (!m || !ID.test(m.id || '')) return;
  const ttl = Number.isInteger(m.ttl) && m.ttl >= 0 && m.ttl <= 600 ? m.ttl : 600;
  card = { id: m.id, items: Array.isArray(m.items) ? m.items : [], to: typeof m.to === 'string' ? m.to.slice(0, 32) : '',
    deadline: Date.now() + ttl * 1000 };
  if (showing === m.id) return;
  for (let i = 0; confirmOpen() && i < 600; i++) {            // another question is on screen: this card waits its turn
    await new Promise((r) => setTimeout(r, 1000));
    if (!card || card.id !== m.id) return;
  }
  if (!card || card.id !== m.id || showing === m.id) return;
  showing = m.id;
  const yes = await ask(card.items, card.to);
  if (showing !== m.id) return;                               // closed by upg_card_done / a newer card
  showing = null;
  if (!card || card.id !== m.id) return;
  card = null;
  if (yes) { if (await H.upgrade()) toast(t('upg.started'), 2600); }
  else sendApp({ t: 'upg_card_off', id: m.id }).catch(() => {});
}
/** host → `upg_card_done` (started by a remote's /update · cancelled · expired · replaced). */
export function done(m) {
  if (!m || !card || card.id !== m.id) return;
  card = null;
  if (showing === m.id) { showing = null; closeConfirm(); }
  if (m.result === 'expired') toast(t('upg.expired'), 3200);
}
export const ids = () => (card ? [card.id] : []);
export function prune(gone) { if (card && gone(card.id)) done({ id: card.id, result: card.deadline <= Date.now() ? 'expired' : 'gone' }); }
export function clear() { if (showing) { showing = null; closeConfirm(); } card = null; }
export const _debug = () => ({ card: card && { id: card.id, items: card.items }, showing });
