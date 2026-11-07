// C19: views inside the existing busy detail. All actions return to the ONE main
// Agent; this module has no transport, authority, approval or agent selector.
import { mk as nativeMk } from './ui.js';

function mk(tag,cls,text){const action=cls?.split(/\s+/).includes('aj-action');return nativeMk(tag,cls,text,action?'aj-action':'aj-busy',action?'dialog':undefined);}

const ACTIONS = ['send', 'stop', 'resume'];
export function originBreadcrumb(origin) {
  const el = mk('p', 'order-origin');
  el.setAttribute('aria-label', 'origin');
  // Only the host's structured projection is accepted by the integration caller.
  el.textContent = Array.isArray(origin) ? origin.filter(x => typeof x === 'string').join(' › ') : '';
  return el;
}
export function orderId(item) {
  const candidate = item.order_id || item.parent || (typeof item.id === 'string' && item.id.startsWith('order:') ? item.id.slice(6).split(':')[0] : '');
  return typeof candidate === 'string' && /^o\d{8}-\d{6}-[0-9a-f]{4}$/.test(candidate) ? candidate : '';
}
export function orderedBusy(items) {
  const groups = groupOrders(items), seen = new Set(), out = [];
  for (const item of items) {
    const id = orderId(item);
    if (!id) out.push(item);
    else if (!seen.has(id)) { seen.add(id); out.push(...groups.get(id)); }
  }
  return out;
}
export function groupOrders(items) {
  const groups = new Map();
  for (const item of items) {
    const id = orderId(item);
    if (typeof id !== 'string' || !/^o\d{8}-\d{6}-[0-9a-f]{4}$/.test(id)) continue;
    if (!groups.has(id)) groups.set(id, []);
    groups.get(id).push(item);
  }
  return groups;
}
export function orderCard(order, { t, toMain, input, stopped = false }) {
  const card = mk('article', 'setcard order-card');
  card.dataset.order = order.id;
  card.append(mk('h3', '', order.title || order.id), originBreadcrumb(order.origin));
  const head = order.head || {};
  for (const [key, value] of [['done', head.done], ['ready', head.ready], ['needs', head.needs], ['verdict', head.verdict]]) {
    if (value !== undefined && value !== null) card.append(mk('p', '', `${t('p53.' + key)}: ${String(value)}`));
  }
  const status = mk('p', 'order-status'); status.setAttribute('role', 'status');
  const actions = mk('div', 'order-actions');
  for (const action of ACTIONS) {
    const button = mk('button', 'aj-action', t('p53.' + action));
    button.type = 'button';
    button.disabled = stopped || (action === 'resume' ? !['stopped', 'failed'].includes(order.state) : order.state === 'done');
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        const text = action === 'send' ? await input(t('p53.send')) : '';
        if (action === 'send' && (typeof text !== 'string' || !text.trim())) return;
        if (await toMain({ action, id: order.id, ...(text ? { text } : {}) }) === false) throw new Error("refused");
        status.textContent = t('p53.sent');
      } catch { status.textContent = t('p53.failed'); }
      finally { button.disabled = stopped; }
    });
    actions.append(button);
  }
  card.append(actions, status);
  for (const gate of order.gates || []) card.append(gateCard(order.id, gate, { t, toMain, stopped }));
  return card;
}
export function gateCard(id, gate, { t, toMain, stopped = false }) {
  const card = mk('section', 'setcard order-gate');
  card.append(mk('h4', '', gate.question), mk('p', '', `${t('p53.default')}: ${gate.default}`));
  if (gate.deadline) card.append(mk('p', '', String(gate.deadline)));
  const status = mk('p', 'order-gate-status'); status.setAttribute('role', 'status');
  card.append(status);
  // These are reversible preference answers, never substitutes for signed approval cards.
  for (const option of gate.options || []) {
    const b = mk('button', 'aj-action', option); b.type = 'button'; b.disabled = stopped;
    b.addEventListener('click', async () => {
      b.disabled = true;
      try {
        if (await toMain({ action: 'send', id, ...(gate.id ? { gate: gate.id, answer: option } : {}), text: `${gate.question}\n${option}` }) === false) throw new Error('refused');
        status.textContent = t('p53.sent');
      } catch { status.textContent = t('p53.failed'); }
      finally { b.disabled = stopped; }
    });
    card.append(b);
  }
  return card;
}
