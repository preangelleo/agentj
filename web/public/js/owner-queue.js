// A presentation of paired-host history. Text never becomes markup or a command.
import {t} from './t.js';
import {clearEndTurn, endTurn, onChange, flowState, whyLine} from './rescue.js';
const el = id => document.getElementById(id);
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
  el('ownerQueueList').replaceChildren(...rows.map(({source, time, text}) => {
    const row = document.createElement('li'), head = document.createElement('header');
    const channel = document.createElement('span'), when = document.createElement('time');
    channel.textContent = source; when.textContent = time;
    head.append(channel, when);
    const summary = document.createElement('p'); summary.textContent = text;
    row.append(head, summary);
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
export function initOwnerQueue(refresh, stop, report) {
  const btn = el('ownerQueueBtn'), dialog = el('ownerQueueDialog');
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
