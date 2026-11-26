// A presentation of paired-host history. Text never becomes markup or a command.
import {t} from './t.js';
import {SVG} from './icons.js';
import {clearEndTurn, endTurn, onChange, flowState, whyLine} from './rescue.js';
const el = id => document.getElementById(id);
// 0378: removing queued messages (one row / 「清空」). `dropper` is null until the page wires it; a row is offered for
// removal only when relay.js marked it (`drop`) — a computer without `queuedrop1` and the running turn never are.
let dropper = null, dropFailed = () => {}, repaint = () => {};
const dropping = new Set();          // page ids with a queue_drop in flight, 'all' for 「清空」
function crossIcon() {
  const svg = document.createElementNS(SVG, 'svg');
  for (const [k, v] of [['viewBox', '0 0 24 24'], ['width', '20'], ['height', '20'], ['fill', 'none'], ['stroke', 'currentColor'],
    ['stroke-width', '1.8'], ['stroke-linecap', 'round'], ['aria-hidden', 'true']]) svg.setAttribute(k, v);
  const path = document.createElementNS(SVG, 'path');
  path.setAttribute('d', 'M7 7l10 10M17 7L7 17');
  svg.append(path);
  return svg;
}
async function drop(what) {
  const key = what === 'all' ? 'all' : what[0];
  if (!dropper || dropping.has(key) || dropping.has('all')) return;
  dropping.add(key); repaint();
  let r;
  try { r = await dropper(what); } catch { r = {ok: false}; }
  dropping.delete(key);
  // The rows leave with the computer's own history update. An answer that names this row as skipped, or none at all,
  // gives the control back and says so.
  const refused = !r?.ok || (what === 'all' ? !r.turns.length && r.skipped.length > 0 : r.skipped.includes(key));
  if (refused) dropFailed();
  repaint();
}
export function paintOwnerQueue(rows, live, stuck) {
  const btn = el('ownerQueueBtn'), dialog = el('ownerQueueDialog');
  btn.hidden = !rows.length;
  if (!rows.length && dialog.open) dialog.close();
  btn.querySelector('.queueCount').textContent = String(rows.length);
  btn.title = t('p0319.count', {n:rows.length});
  btn.setAttribute('aria-label', btn.title);
  if (!dialog.open) {
    el('ownerQueueList').replaceChildren();
    return;
  }
  el('ownerQueueStop').disabled = !live || flowState('end_turn').st === 'run';
  const wait = el('ownerQueueWait');
  wait.hidden = !rows[0].wait;
  wait.textContent = rows[0].wait || '';
  el('ownerQueueTitle').textContent = t('p0319.count', {n:rows.length});
  el('ownerQueueWarning').hidden = !stuck;
  const offered = dropper ? rows.filter(r => r.drop === true) : [];
  const clear = el('ownerQueueClear');
  clear.hidden = !offered.length;
  clear.disabled = dropping.size > 0;
  el('ownerQueueList').replaceChildren(...rows.map(({source, time, text, turn, drop: can}) => {
    const row = document.createElement('li'), head = document.createElement('header');
    const channel = document.createElement('span'), when = document.createElement('time');
    channel.textContent = source; when.textContent = time;
    head.append(channel, when);
    const summary = document.createElement('p'); summary.textContent = text;
    row.append(head, summary);
    if (dropper && can === true) {
      const b = document.createElement('button');
      b.type = 'button'; b.dataset.queueDrop = String(turn);
      b.setAttribute('data-aj-owner', 'aj-reply-action');
      b.title = t('p0319.drop'); b.setAttribute('aria-label', b.title);
      b.disabled = dropping.has(turn) || dropping.has('all');
      b.append(crossIcon());
      row.append(b);
    }
    return row;
  }));
}
// Return the signed operation's host receipt to the remote even after its queue clears.
let awaitingReceipt = false;
export async function stopQueuedTurn(signed, fallback) {
  if (!signed) { fallback(); return; }
  if (flowState('end_turn').st === 'run') return;
  clearEndTurn();
  awaitingReceipt = true;
  await endTurn();
  if (flowState('end_turn').st === 'idle') awaitingReceipt = false;
}
/** `queueDrop(ids | 'all')` → the computer's answer; absent = the two removal controls never show. */
export function initOwnerQueue(refresh, stop, report, queueDrop = null) {
  const btn = el('ownerQueueBtn'), dialog = el('ownerQueueDialog');
  dropper = queueDrop; repaint = refresh; dropFailed = () => report(t('p0319.dropFail'));
  // 「清空」 only empties the queue: it never ends the running turn (that stays 「关闭上一条任务」).
  el('ownerQueueClear').addEventListener('click', () => drop('all'));
  el('ownerQueueList').addEventListener('click', e => {
    const b = e.target.closest?.('button[data-queue-drop]');
    if (b && !b.disabled) drop([Number(b.dataset.queueDrop)]);
  });
  onChange(() => {
    if (!awaitingReceipt) return;
    const f = flowState('end_turn');
    if (!['ok','fail'].includes(f.st)) return;
    awaitingReceipt = false;
    report(f.st === 'ok' ? f.text || t('p0269.queue.ended') : whyLine(f.why) || t('p0269.queue.endFail'));
  });
  btn.addEventListener('click', () => {
    dialog.showModal();
    btn.setAttribute('aria-expanded', 'true');
    refresh();
  });
  el('ownerQueueClose').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => {
    btn.setAttribute('aria-expanded', 'false');
    el('ownerQueueList').replaceChildren();
  });
  dialog.addEventListener('click', e => {
    if (e.target !== dialog) return;
    const r = dialog.getBoundingClientRect();
    if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) dialog.close();
  });
  el('ownerQueueStop').addEventListener('click', () => {
    dialog.close();
    stop();
  });
}
