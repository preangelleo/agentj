// P170 · Settings → 邮件通道 (PROTOCOL §10.20): the owner's mailbox for Agent J — preset, address, allowlist, sender check,
// check interval; 「设置授权码」 opens the host's sealed secret card (the code never travels in these messages); 「测试连接」
// and 「立即检查」 run on the computer. Everything here is inside the Noise session; addresses are shown with textContent only.
import { mailServers } from './mail-servers.js';
import { t, lang, onLang } from './t.js';
import { renderTriage } from './mail-triage.js';
import { el, toast, notify, settingsSaid } from './ui.js';
import { signed } from './controls.js';
import { ask1, isReady, peer } from './session.js';
// 0.30.6: the select shows short names; the chosen level's full meaning sits on the line under it.
const AUTH_WHY = { standard: 'mail.authStd', strict: 'mail.authStrict', off: 'mail.authOff' };
function authWhy() { const n = el('mail-auth-why'), s = el('mail-auth'); if (n && s) n.textContent = t(AUTH_WHY[s.value] || AUTH_WHY.standard); }
onLang(authWhy);

let view = null;          // the host's last `mail_res.view`
let busy = false;
let trustWant = null;     // 0.30.6: the owner's tick while its signed save is open (render shows host truth otherwise)
let testedConnection = null;
const connectionKey = () => JSON.stringify([cfg().address, cfg().username, cfg().imap, cfg().smtp, cfg().auth]);
const ADDR = /^[A-Za-z0-9._%+'=-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$/;

// 0.30.7 (chairman 11-11): the panel line is the one place the sentence stays; a notice that only repeated it goes
function say(s) { el('mail-msg').textContent = s || ''; settingsSaid(s); }
const why = (w) => { const p = String(w || '').split(':'); const k = 'mail.why.' + (p[1] === 'self' ? 'self' : p[0]); const s = t(k); return s === k ? String(w || '?').slice(0, 60) : s; };

async function call(obj, ms = 20000) {
  if (!isReady()) { say(t('set.offline')); return null; }
  if (peer.mailuv1 && ['mail_set','mail_test'].includes(obj.t)) {
    try { obj = {...obj, authorization:await signed('host_write', {kind:obj.t, ...(obj.t === 'mail_set' ? {cfg:obj.cfg}: {})}, {})}; }
    catch (e) {
      const key = ({cancel:'mail.why.denied', no_passkey:'ask.passkeyRequired', offline:'set.offline', no_key:'set.offline'})[e.message] || 'elev.fa.cancel';
      say(t('mail.saveFail',{why:t(key)})); return null;
    }
  }
  const m = await ask1(obj, 'mail_res', ms);
  if (m.t !== 'mail_res') { say(m.t === 'timeout' ? t('mail.timeout') : t('set.offline')); return null; }
  if (m.view && typeof m.view === 'object') view = m.view;
  render(!!m.ok);
  return m;
}

export async function load() { say(t('mail.loading')); const m = await call({ t: 'mail_get' }); if (m) say(''); }

function cfg() { return (view && view.config) || {}; }
const imapHost = (c) => String(c.imap?.host || '').trim().toLowerCase().replace(/\.+$/, '');   // = mail._imap_host

/** 0.30.6: the signed patch for a trusted-server tick (want=true) or untick, or null when this state offers none. */
export function trustPatch(v, want) {
  const c = (v && v.config) || {}, s = (v && v.servid_status) || {};
  if (!want) return s.pinned === true ? { auth_servid: '' } : null;
  if (s.pinned === true || v?.servid_hint !== 'unpinned_known') return null;
  const host = imapHost(c);
  return { servid_optout: (Array.isArray(c.servid_optout) ? c.servid_optout : []).filter((h) => h !== host) };
}
function preset() { const id = el('mail-preset').value; return ((view && view.presets) || []).find((p) => p.id === id); }

function renderPresetNote() {
  const p = preset();
  el('mail-note').textContent = p ? p.note : '';
  el('mail-custom').open = !!(p && p.id === 'custom') || el('mail-custom').open;
}

function render(form = true) {
  if (!view) return;
  const c = cfg();
  if (form) {
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
  authWhy();
  renderPresetNote();
  }
  el('mail-enable-next').hidden = c.enabled || testedConnection !== connectionKey() || !c.address;
  el('mail-enable-next').disabled = busy;
  el('mail-key').textContent = t(view.has_password ? 'mail.passwordSet' : 'mail.key');
  // status
  const parts = [];
  parts.push(!c.address ? t('mail.stNone') : c.enabled ? t('mail.stOn', { a: c.address }) : t('mail.stOff', { a: c.address }));
  parts.push(view.has_password ? t('mail.stKey') : t('mail.stNoKey'));
  if (view.password_upgrade_required) parts.push(t('p503.mailUpgrade'));
  else if (view.password_state === 'unreadable') parts.push(t('p549.mailUnreadable'));   // P549: a lost key says why
  if (view.error) parts.push(t('mail.stErr', { why: why(view.error) }));
  else if (view.last_ok) parts.push(t('mail.stLast', { at: new Date(view.last_ok * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) }));
  el('mail-status').textContent = parts.join(' · ');
  const d = view.today || {};
  el('mail-today').textContent = t('mail.today', { ok: d.delivered || 0, out: d.not_allowed || 0, auth: d.auth || 0, auto: d.auto || 0, sent: d.reply_sent || 0 });
  // P206: `agentj mail expect` windows (the Agent waiting for one sign-up mail) — shown only once used today
  if (d.expect) el('mail-today').textContent += ' · ' + t('mail.todayExpect', { found: d.expect_found || 0, timeout: d.expect_timeout || 0, rejected: d.expect_rejected || 0 });
  renderTriage(view, save, lang() === 'en', busy, form);
  // sender-check sample from the last connection test
  const status = view.servid_status || {};
  const smp = status.sample || view.auth_sample;
  const checked = Number.isInteger(smp?.checked) && smp.checked >= 0 ? smp.checked : 0;
  const withResults = Number.isInteger(smp?.with_results) && smp.with_results >= 0 ? smp.with_results : 0;
  const detected = typeof status.detected === 'string' ? status.detected : null;
  el('mail-sample').textContent = !checked ? '' : withResults < checked
    ? t('p0304.mailSampleIncomplete', { n: withResults, m: checked })
    : t('p0304.mailSampleComplete', { n: withResults, m: checked });
  // A sampled id on a custom server can be sender-written. Never offer a one-tap pin
  // from mail content; known Gmail pins on the host, Outlook has no usable id.
  const pinned = status.pinned === true || (!view.servid_status && !!c.auth_servid);
  const value = status.value || c.auth_servid || '';
  const source = status.source === 'auto' ? t('p0304.mailSourceAuto') : t('p0304.mailSourceManual');
  el('mail-pinned').textContent = pinned
    ? t('p0304.mailPinnedSource', { id: value, source })
    : t('p0304.mailTrustedUnset');
  el('mail-detected').textContent = detected ? t('p0304.mailDetected', { id: detected }) : '';
  // 0.30.6: the box is the host's state; a tick/untick is a signed `mail_set` (host_write + Face ID), never a local flag.
  // Untick clears the pin (the host's servid_merge records the opt-out); tick on a known provider only drops this host from
  // servid_optout and the host pins its own table value. Custom / Outlook ids would come from mail, so they stay untickable.
  const box = el('mail-trusted-check');
  const known = !pinned && view.servid_hint === 'unpinned_known';
  const optedOut = known && (Array.isArray(c.servid_optout) ? c.servid_optout : []).includes(imapHost(c));
  const writable = !!view.servid_status && peer.mailuv1 === true;
  box.checked = trustWant ?? pinned;
  box.disabled = busy || !writable || !(pinned || known);
  el('mail-trusted-how').textContent = !writable ? '' : pinned ? t('p0306.mailTrustedClear')
    : known ? t(optedOut ? 'p0306.mailTrustedOptout' : 'p0306.mailTrustedPin')
    : view.servid_hint === 'unpinned_outlook' ? t('p0306.mailTrustedOutlook') : t('p0306.mailTrustedCustom');
  // Only the host's explicit auto-pin provenance on the exact Gmail IMAP host earns this note.
  const gmailAuto = pinned && status.source === 'auto' && imapHost(c) === 'imap.gmail.com';
  el('mail-gmail-pinned').hidden = !gmailAuto;
  el('mail-gmail-pinned').textContent = gmailAuto ? t('p0305.mailGmailPinned') : '';
  // toggle
  const tg = el('mail-on');
  tg.setAttribute('aria-checked', String(!!c.enabled));
  tg.disabled = busy || !c.address;
  // allowlist
  el('mail-allow').replaceChildren(...(c.allow || []).map((r) => {
    const li = document.createElement('li');
    const a = document.createElement('span'); a.textContent = r.addr + (r.name ? ' · ' + r.name : '');
    const role = document.createElement('select'); role.setAttribute('data-aj-owner', 'aj-channel'); role.className = 'aj-select mail-role';
    role.setAttribute('aria-label', t('p579.mailRole', {a: r.addr}));
    for (const value of ['owner', 'guest']) { const o = document.createElement('option'); o.value = value; o.textContent = t(value === 'guest' ? 'p579.chipGuest' : 'p579.chipOwner'); role.append(o); }
    role.value = r.role === 'guest' ? 'guest' : 'owner';
    role.disabled = busy || r.addr.startsWith('@');
    role.addEventListener('change', () => save({allow: (cfg().allow || []).map(x => x.addr === r.addr ? {...x, role: role.value} : x)}));
    const rm = document.createElement('button'); rm.setAttribute('data-aj-owner', 'aj-channel'); rm.type = 'button'; rm.className = 'setlink setdanger'; rm.textContent = t('mail.remove');
    rm.addEventListener('click', () => save({ allow: (cfg().allow || []).filter((x) => x.addr !== r.addr) }));
    li.append(a);
    if (r.subdomains) { const note = document.createElement('small'); note.setAttribute('data-aj-owner','aj-channel'); note.textContent = t('p567.subdomains'); a.append(document.createElement('br'), note); }
    li.append(rm, role);
    return li;
  }));
  el('mail-allow-empty').hidden = !!(c.allow && c.allow.length);
  // recently refused senders (never their words): one tap adds them
  const bl = (view.blocked || []).slice(-5).reverse();
  el('mail-blocked-h').hidden = !bl.length;
  el('mail-blocked').replaceChildren(...bl.map((b) => {
    const li = document.createElement('li');
    const a = document.createElement('span'); a.textContent = String(b.addr || '').slice(0, 120);
    const w = document.createElement('small'); w.setAttribute('data-aj-owner','aj-channel'); w.textContent = why(b.why);
    li.append(a, w);
    if (String(b.why).startsWith('not_allowed') && !(cfg().allow || []).some((x) => x.addr.toLowerCase() === String(b.addr).toLowerCase())) {
      const add = document.createElement('button'); add.setAttribute('data-aj-owner', 'aj-channel'); add.type = 'button'; add.className = 'setlink'; add.textContent = t('mail.addThis');
      add.addEventListener('click', () => save({ allow: [...(cfg().allow || []), { addr: b.addr, role: 'guest' }] }));
      li.append(add);
    }
    return li;
  }));
  for (const id of ['mail-save', 'mail-key', 'mail-test', 'mail-check']) el(id).disabled = busy;
}

async function save(patch, note) {
  busy = true; render(false); say(t('set.saving'));
  const m = await call({ t: 'mail_set', cfg: patch });
  busy = false;
  if (m) say(m.ok ? (note || t('set.saved')) : t('mail.saveFail', { why: why(m.why) }));
  render(!!(m && m.ok));
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
  el('mail-auth').addEventListener('change', () => { el('mail-auth-off').hidden = el('mail-auth').value !== 'off'; authWhy(); });
  authWhy();
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
    const c = cfg();
    const changed = Object.entries(f).some(([k, v]) => JSON.stringify(v) !== JSON.stringify(c[k]))
      || (f.username || f.address) !== (c.username || c.address);
    if (changed && !await save(f)) return;
    busy = true; render(false); say(t('mail.keyOpen'));
    const m = await call({ t: 'mail_key' }, 660000);
    testedConnection = m?.ok && m.verify === 'ok' ? connectionKey() : null;
    busy = false; render();
    if (m?.ok && m.verify === 'ok') notify({ kind: 'success', shape: 'circle', text: t('mail.passwordSaved'), ms: 2400 });
    // 0.30.7 N4b: a new task started between the confirmation and the save → its own sentence, not the generic failure
    const newTask = !m?.ok && { new_task: 'p0307.secret.newTask', new_task_during_validation: 'p0307.secret.newTaskValidated' }[m?.why];
    if (m) say(m.ok ? t(m.verify === 'fail' ? 'mail.testFail' : 'mail.testOk', {why: why(m.detail)}) : newTask ? t(newTask) : t('mail.keyFail', {why: why(m.why)}));
  });
  el('mail-test').addEventListener('click', async () => {
    busy = true; render(); say(t('mail.testing'));
    const m = await call({ t: 'mail_test' }, 90000);
    testedConnection = m?.ok ? connectionKey() : null;
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
  el('mail-enable-next').addEventListener('click', () => save({ enabled: true }));
  el('mail-trusted-check').addEventListener('change', async () => {
    const want = el('mail-trusted-check').checked, patch = busy ? null : trustPatch(view, want);
    if (!patch) { render(false); return; }
    trustWant = want;
    try { await save(patch); } finally { trustWant = null; render(false); }
  });
  el('mail-on').addEventListener('click', () => save({ enabled: !cfg().enabled }));
  el('mail-add-addr').addEventListener('input', () => {
    const domain = el('mail-add-addr').value.trim().startsWith('@');
    el('mail-add-role').disabled = domain;
    if (domain) el('mail-add-role').value = 'guest';
    el('mail-add-subdomains').disabled = !domain;
  });
  el('mail-add-subdomains').disabled = true;
  el('mail-add').addEventListener('click', () => {
    const a = el('mail-add-addr').value.trim();
    if (!(a.startsWith('@') ? ADDR.test('x' + a) : ADDR.test(a))) { say(t('mail.why.address')); el('mail-add-addr').focus(); return; }
    const domain = a.startsWith('@');
    const role = domain || el('mail-add-role').value === 'guest' ? 'guest' : 'owner';
    const rest = (cfg().allow || []).filter((x) => x.addr.toLowerCase() !== a.toLowerCase());
    save({ allow: [...rest, { addr: a, role, ...(domain ? {subdomains: el('mail-add-subdomains').checked} : {}) }] }).then((ok) => { if (ok) el('mail-add-addr').value = ''; });
  });
}

// for screens tests
export const _debug = () => ({ view, busy });
