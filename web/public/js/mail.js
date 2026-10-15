// P170 · Settings → 邮件通道 (PROTOCOL §10.20): the owner's mailbox for Agent J — preset, address, allowlist, sender check,
// check interval; 「设置授权码」 opens the host's sealed secret card (the code never travels in these messages); 「测试连接」
// and 「立即检查」 run on the computer. Everything here is inside the Noise session; addresses are shown with textContent only.
import { mailServers } from './mail-servers.js';
import { t } from './t.js';
import { el, toast } from './ui.js';
import { ask1, isReady } from './session.js';

let view = null;          // the host's last `mail_res.view`
let busy = false;
const ADDR = /^[A-Za-z0-9._%+'=-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$/;

function say(s) { el('mail-msg').textContent = s || ''; }
const why = (w) => { const p = String(w || '').split(':'); const k = 'mail.why.' + (p[1] === 'self' ? 'self' : p[0]); const s = t(k); return s === k ? String(w || '?').slice(0, 60) : s; };

async function call(obj, ms = 20000) {
  if (!isReady()) { say(t('set.offline')); return null; }
  const m = await ask1(obj, 'mail_res', ms);
  if (m.t !== 'mail_res') { say(m.t === 'timeout' ? t('mail.timeout') : t('set.offline')); return null; }
  if (m.view && typeof m.view === 'object') view = m.view;
  render();
  return m;
}

export async function load() { say(t('mail.loading')); const m = await call({ t: 'mail_get' }); if (m) say(''); }

function cfg() { return (view && view.config) || {}; }
function preset() { const id = el('mail-preset').value; return ((view && view.presets) || []).find((p) => p.id === id); }

function renderPresetNote() {
  const p = preset();
  el('mail-note').textContent = p ? p.note : '';
  el('mail-custom').open = !!(p && p.id === 'custom') || el('mail-custom').open;
}

function render() {
  if (!view) return;
  const c = cfg();
  const sel = el('mail-preset');
  if (!sel.options.length) {
    for (const p of view.presets || []) { const o = document.createElement('option'); o.value = p.id; o.textContent = p.name; sel.append(o); }
  }
  if (document.activeElement !== sel) sel.value = c.preset || 'qq';
  for (const [id, v] of [['mail-addr', c.address], ['mail-user', c.username], ['mail-imap-host', c.imap && c.imap.host],
    ['mail-imap-port', c.imap && c.imap.port], ['mail-smtp-host', c.smtp && c.smtp.host], ['mail-smtp-port', c.smtp && c.smtp.port]]) {
    if (document.activeElement !== el(id)) el(id).value = v == null ? '' : String(v);
  }
  el('mail-imap-sec').value = (c.imap && c.imap.security) || 'ssl';
  el('mail-smtp-sec').value = (c.smtp && c.smtp.security) || 'ssl';
  el('mail-poll').value = String(c.poll_minutes || 5);
  el('mail-auth').value = c.auth || 'standard';
  el('mail-auth-off').hidden = c.auth !== 'off';
  renderPresetNote();
  el('mail-key').textContent = t(view.has_password ? 'mail.passwordSet' : 'mail.key');
  // status
  const parts = [];
  parts.push(!c.address ? t('mail.stNone') : c.enabled ? t('mail.stOn', { a: c.address }) : t('mail.stOff', { a: c.address }));
  parts.push(view.has_password ? t('mail.stKey') : t('mail.stNoKey'));
  if (view.error) parts.push(t('mail.stErr', { why: why(view.error) }));
  else if (view.last_ok) parts.push(t('mail.stLast', { at: new Date(view.last_ok * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) }));
  el('mail-status').textContent = parts.join(' · ');
  const d = view.today || {};
  el('mail-today').textContent = t('mail.today', { ok: d.delivered || 0, out: d.not_allowed || 0, auth: d.auth || 0, auto: d.auto || 0, sent: d.reply_sent || 0 });
  // P206: `agentj mail expect` windows (the Agent waiting for one sign-up mail) — shown only once used today
  if (d.expect) el('mail-today').textContent += ' · ' + t('mail.todayExpect', { found: d.expect_found || 0, timeout: d.expect_timeout || 0, rejected: d.expect_rejected || 0 });
  // sender-check sample from the last connection test
  const smp = view.auth_sample;
  el('mail-sample').textContent = !smp || !smp.checked ? '' : smp.with_results
    ? t('mail.sampleYes', { n: smp.with_results, m: smp.checked, id: smp.servid || '—' }) : t('mail.sampleNo', { m: smp.checked });
  el('mail-pin').hidden = !(smp && smp.servid && smp.servid !== c.auth_servid);
  el('mail-pinned').textContent = c.auth_servid ? t('mail.pinned', { id: c.auth_servid }) : '';
  // toggle
  const tg = el('mail-on');
  tg.setAttribute('aria-checked', String(!!c.enabled));
  tg.disabled = busy || !c.address;
  // allowlist
  el('mail-allow').replaceChildren(...(c.allow || []).map((r) => {
    const li = document.createElement('li');
    const a = document.createElement('span'); a.textContent = r.addr + (r.name ? ' · ' + r.name : '');
    const role = document.createElement('small'); role.textContent = t(r.role === 'guest' ? 'mail.roleGuest' : 'mail.roleOwner');
    const rm = document.createElement('button'); rm.type = 'button'; rm.className = 'setlink setdanger'; rm.textContent = t('mail.remove');
    rm.addEventListener('click', () => save({ allow: (cfg().allow || []).filter((x) => x.addr !== r.addr) }));
    li.append(a, role, rm);
    return li;
  }));
  el('mail-allow-empty').hidden = !!(c.allow && c.allow.length);
  // recently refused senders (never their words): one tap adds them
  const bl = (view.blocked || []).slice(-5).reverse();
  el('mail-blocked-h').hidden = !bl.length;
  el('mail-blocked').replaceChildren(...bl.map((b) => {
    const li = document.createElement('li');
    const a = document.createElement('span'); a.textContent = String(b.addr || '').slice(0, 120);
    const w = document.createElement('small'); w.textContent = why(b.why);
    li.append(a, w);
    if (String(b.why).startsWith('not_allowed') && !(cfg().allow || []).some((x) => x.addr.toLowerCase() === String(b.addr).toLowerCase())) {
      const add = document.createElement('button'); add.type = 'button'; add.className = 'setlink'; add.textContent = t('mail.addThis');
      add.addEventListener('click', () => save({ allow: [...(cfg().allow || []), { addr: b.addr, role: 'owner' }] }));
      li.append(add);
    }
    return li;
  }));
  for (const id of ['mail-save', 'mail-key', 'mail-test', 'mail-check']) el(id).disabled = busy;
}

async function save(patch, note) {
  busy = true; render(); say(t('set.saving'));
  const m = await call({ t: 'mail_set', cfg: patch });
  busy = false;
  if (m) say(m.ok ? (note || t('set.saved')) : t('mail.saveFail', { why: why(m.why) }));
  render();
  return !!(m && m.ok);
}

function formCfg() {
  const n = (id) => Number(el(id).value) || 0;
  const out = { preset: el('mail-preset').value, address: el('mail-addr').value.trim(), poll_minutes: Number(el('mail-poll').value),
    auth: el('mail-auth').value };
  if (out.preset === 'custom') {
    out.imap = { host: el('mail-imap-host').value.trim(), port: n('mail-imap-port'), security: el('mail-imap-sec').value };
    out.smtp = { host: el('mail-smtp-host').value.trim(), port: n('mail-smtp-port'), security: el('mail-smtp-sec').value };
  }
  const u = el('mail-user').value.trim();
  if (u) out.username = u;
  return out;
}

export function configure() {
  el('mail-preset').addEventListener('change', () => {
    renderPresetNote();
    const p = preset();
    if (p && p.id !== 'custom') {
      el('mail-imap-host').value = p.imap.host; el('mail-imap-port').value = p.imap.port; el('mail-imap-sec').value = p.imap.security;
      el('mail-smtp-host').value = p.smtp.host; el('mail-smtp-port').value = p.smtp.port; el('mail-smtp-sec').value = p.smtp.security;
    }
  });
  el('mail-addr').addEventListener('change', () => {
    const dom = el('mail-addr').value.trim().split('@')[1] || '';
    const hit = ((view && view.presets) || []).find((p) => (p.domains || []).includes(dom.toLowerCase()));
    const prior = (cfg().address || '').split('@')[1]?.toLowerCase();
    el('mail-preset').value = hit ? hit.id : dom.toLowerCase() === prior ? (cfg().preset || 'custom') : 'custom';
    if (!hit && dom.toLowerCase() !== prior) {
      // A different unknown domain must not silently inherit the old provider's servers.
      el('mail-imap-host').value = ''; el('mail-smtp-host').value = '';
      el('mail-imap-port').value = '993'; el('mail-smtp-port').value = '465';
    }
    if (el('mail-user').value === cfg().address) el('mail-user').value = '';
    el('mail-preset').dispatchEvent(new Event('change'));
  });
  el('mail-auth').addEventListener('change', () => { el('mail-auth-off').hidden = el('mail-auth').value !== 'off'; });
  el('mail-save').addEventListener('click', () => {
    const f = formCfg();
    if (!ADDR.test(f.address)) { say(t('mail.why.address')); el('mail-addr').focus(); return; }
    save(f, view && view.has_password ? t('set.saved') : t('mail.savedNext'));
  });
  el('mail-key').addEventListener('click', async () => {
    if (busy) return;
    const f = formCfg();
    if (!ADDR.test(f.address)) { say(t('mail.why.address')); el('mail-addr').focus(); return; }
    if (f.preset === 'custom') {
      const servers = await mailServers(f);
      if (!servers) return;
      Object.assign(f, servers);
    }
    if (!await save(f)) return;
    busy = true; render(); say(t('mail.keyOpen'));
    const m = await call({ t: 'mail_key' }, 660000);
    busy = false; render();
    if (m?.ok) { toast(t('mail.passwordSaved')); el('toast').classList.add('mail-saved'); }
    if (m) say(m.ok ? t(m.verify === 'fail' ? 'mail.testFail' : 'mail.testOk', {why: why(m.detail)}) : t('mail.keyFail', {why: why(m.why)}));
  });
  el('mail-test').addEventListener('click', async () => {
    busy = true; render(); say(t('mail.testing'));
    const m = await call({ t: 'mail_test' }, 90000);
    busy = false; render();
    if (m) say(m.ok ? t('mail.testOk') : t('mail.testFail', { why: why(m.why) }));
  });
  el('mail-check').addEventListener('click', async () => {
    busy = true; render(); say(t('mail.checking'));
    const m = await call({ t: 'mail_check' }, 300000);
    busy = false; render();
    const c = m && m.check;
    if (m) say(m.ok ? (c && c.baseline ? t('mail.checkBase') : t('mail.checkOk', { n: (c && c.delivered) || 0, m: (c && c.new) || 0 }))
      : t('mail.testFail', { why: why(m.why) }));
  });
  el('mail-on').addEventListener('click', () => save({ enabled: !cfg().enabled }));
  el('mail-pin').addEventListener('click', () => { const s = view && view.auth_sample; if (s && s.servid) save({ auth_servid: s.servid }); });
  el('mail-add').addEventListener('click', () => {
    const a = el('mail-add-addr').value.trim();
    if (!ADDR.test(a)) { say(t('mail.why.address')); el('mail-add-addr').focus(); return; }
    const role = el('mail-add-role').value === 'guest' ? 'guest' : 'owner';
    const rest = (cfg().allow || []).filter((x) => x.addr.toLowerCase() !== a.toLowerCase());
    save({ allow: [...rest, { addr: a, role }] }).then((ok) => { if (ok) el('mail-add-addr').value = ''; });
  });
}

// for screens tests
export const _debug = () => ({ view, busy });
