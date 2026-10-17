// P366 fixture: a host's `hook_list_res` (made-up names, ids and hook.agentj.app URLs — no account, no secret: a URL-token
// hook carries the host's literal placeholder) and a fake host handler for hook_list / hook_set that keeps the state.
export const TOKEN_TAIL = '/<令牌/token>';
const id = (c) => c.repeat(22);
export const HID = { once: id('A'), stripe: id('B'), alert: id('C'), form: id('D'), used: id('E'), old: id('F') };
export function sample(now = Math.floor(Date.now() / 1000)) {
  const H = (o) => ({ name: 'x', preset: 'hmac', life: 'once', expires: null, target: 'main', auth: 'hmac', ip_allow: [], enabled: true, note: '',
    state: 'active', ended: null, last: null, count: 0, url: `https://hook.agentj.app/hb/${o.id}`, created: { by: 'main', at: now - 600 }, approved: null, ...o });
  return {
    t: 'hook_list_res', ok: true, up: true, quota: { used: 3, day_max: 1000, hooks_max: 20 },
    hooks: [
      H({ id: HID.once, name: '视频-123', preset: 'callback', life: 'once', expires: now + 3600, created: { by: 'main', at: now - 60 }, e2e: true }),
      H({ id: HID.stripe, name: 'Stripe 收款', preset: 'stripe', life: 'resident', auth: 'stripe', e2e: false, last: { at: now - 120, result: 'ok' }, count: 2, created: { by: 'main', at: now - 9000 }, approved: { device: 'd', at: now - 8000 } }),
      H({ id: HID.alert, name: '线上告警', preset: 'alert', life: 'resident', auth: 'bearer', state: 'pending', target: 'owner' }),
      H({ id: HID.form, name: '表单', preset: 'form', life: 'ttl', auth: 'token', expires: now + 86000, enabled: false, target: 'order:o42', url: `https://hook.agentj.app/hb/${HID.form}${TOKEN_TAIL}` }),
      H({ id: HID.used, name: '昨天的回调', state: 'used', ended: now - 3000, last: { at: now - 3000, result: 'ok' }, count: 1 }),
      H({ id: HID.old, name: '很久以前', state: 'revoked', ended: now - 90000 }),
      { id: 'short', name: 'bad id' }, null,
    ],
    recent: [
      { id: 'd' + '1'.repeat(12), hook: HID.used, at: now - 3000.5, method: 'POST', bytes: 120, result: 'ok', triage: { v: 'continue', why: '', at: now } },
      { id: 'd' + '2'.repeat(12), hook: HID.stripe, at: now - 120.25, method: 'POST', bytes: 300, result: 'ok', triage: { v: 'ask', why: '退款', at: now } },
      { id: 'd' + '3'.repeat(12), hook: id('Z'), at: now - 60, method: 'POST', bytes: 10, result: 'ok', triage: null },
      { id: 'nope', hook: HID.stripe, at: now, result: 'ok' },
    ],
  };
}
/** st.onApp for fakehost: hook_list → the current list; hook_set pause / resume / revoke changes it (as hook_service does). */
export function hooksHandler(st, { empty = false, calls = [] } = {}) {
  const m = sample();
  if (empty) { m.hooks = []; m.recent = []; m.quota.used = 0; }
  const prev = st.onApp;
  st.onApp = async (c, x, send) => {
    if (x.t === 'hook_list') { calls.push({ t: x.t }); await send({ ...m, r: x.r }); return true; }
    if (x.t === 'hook_set') {
      calls.push({ t: x.t, id: x.id, op: x.op });
      const h = m.hooks.find((y) => y && y.id === x.id);
      if (!h || !['pause', 'resume', 'revoke'].includes(x.op)) { await send({ t: 'hook_list_res', r: x.r, ok: false, why: 'shape' }); return true; }
      if (x.op === 'revoke') Object.assign(h, { state: 'revoked', ended: Math.floor(Date.now() / 1000) });
      else h.enabled = x.op === 'resume';
      await send({ ...m, r: x.r });
      return true;
    }
    return prev ? prev(c, x, send) : false;
  };
  return { calls, model: m };
}
