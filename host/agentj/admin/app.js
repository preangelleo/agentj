// Agent J — the host-local Agent settings page (`agentj admin`, A3.2 contract §4). Login: the one-time token arrives in
// the URL fragment (#t=), is wiped from the address bar at once and swapped (POST /api/session) for a session secret kept only
// in this tab's sessionStorage and sent as `Authorization: Bearer` — no cookie anywhere. Same-origin JSON API; every value from
// the API goes into text nodes (textContent) — never parsed as HTML. The only non-text sink is the QR <img src>, and only for a
// data:image/svg+xml URI that the host itself rendered (segno). Device labels are self-chosen by the device: shown as untrusted.
// Words: i18n/admin.{zh,en}.json (same origin, shipped in the wheel) through window.AJLang (brand/lang.js); `code` in a string
// becomes a <code> text node. Language choice: AJLang (localStorage "aj.lang" of this origin, or ?lang=).
'use strict';

const $ = (id) => document.getElementById(id);
const LIMIT_DEFAULT = 5;
const DICT = { zh: {}, en: {} };
const DENY = new Set(['code_mismatch', 'denied', 'timeout', 'abandoned', 'bad_handshake', 'hs_timeout', 'device_limit',
  'no_pending_device', 'gone', 'replaced', 'serve_gone', 'relay_down', 'cancelled', 'pass_locked', 'pass_not_set']);
const ERRS = new Set(['serve_not_running', 'serve_busy', 'pair_failed', 'relay_down', 'unbind_first', 'no_pending_device',
  'unknown_device', 'serve_gone', 'passphrase_required', 'bad_code']);
const NAME_ERRS = new Set(['name_required', 'bad_name', 'name_taken', 'unreachable', 'not_bound', 'rate_limited', 'failed']);

const SESSION_KEY = 'aj_admin_session';
let session = null;
let state = null;
let timer = null;
let busy = false;
let lastMsg = null;          // [key, vars, kind] of the page message, re-rendered on a language switch
let lastNameMsg = null;

// ------------------------------------------------------------------ words
const t = (key, vars) => (window.AJLang ? window.AJLang.t(DICT, key, vars) : key);

/** Put `text` into `node` as text nodes; `backticked` parts become <code> elements (still text only, never HTML). */
function rich(node, text) {
  const parts = String(text).split(/`([^`]+)`/);
  node.replaceChildren(...parts.map((p, i) => {
    if (i % 2 === 0) return document.createTextNode(p);
    const c = document.createElement('code');
    c.textContent = p;
    return c;
  }));
}

function applyStatic() {
  for (const n of document.querySelectorAll('[data-i18n]')) rich(n, t(n.dataset.i18n));
  for (const n of document.querySelectorAll('[data-i18n-attr]')) {
    for (const pair of n.dataset.i18nAttr.split(',')) {
      const [attr, key] = pair.split(':').map((s) => s.trim());
      if (attr && key) n.setAttribute(attr, t(key));
    }
  }
  const desc = document.querySelector('meta[name="description"]');
  if (desc) desc.setAttribute('content', t('page.description'));
  for (const a of document.querySelectorAll('footer a[target="_blank"]')) {
    a.href = window.AJLang ? window.AJLang.link(a.getAttribute('href').replace(/[?#].*$/, '')) : a.href;
  }
}

async function loadDicts() {
  const get = async (lang) => {
    try {
      const r = await fetch(`/i18n/admin.${lang}.json`, { credentials: 'omit', cache: 'no-store' });
      const d = r.ok ? await r.json() : {};
      return d && typeof d === 'object' ? d : {};
    } catch { return {}; }
  };
  [DICT.zh, DICT.en] = await Promise.all([get('zh'), get('en')]);
}

// ------------------------------------------------------------------ helpers
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = String(text);
  return n;
}

function show(id, on) { $(id).hidden = !on; }

function sayNode(node, msg) {
  if (!msg) { node.hidden = true; node.replaceChildren(); return; }
  const [key, vars, kind] = msg;
  rich(node, t(key, vars));
  node.className = kind === 'error' ? 'aj-callout aj-callout--danger adm-msg' : 'aj-callout adm-msg';
  node.hidden = false;
}

function say(key, vars, kind) {
  lastMsg = key ? [key, vars || null, kind || 'ok'] : null;
  sayNode($('msg'), lastMsg);
}

function nameMsg(key, vars, kind) {
  lastNameMsg = key ? [key, vars || null, kind || 'ok'] : null;
  sayNode($('name-msg'), lastNameMsg);
}

const errKey = (code, fallback) => (ERRS.has(code) ? `err.${code}` : fallback);

async function api(method, path, body) {
  const opt = { method, credentials: 'omit', cache: 'no-store', headers: {} };
  if (session) opt.headers.Authorization = `Bearer ${session}`;
  if (method !== 'GET') {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body || {});
  }
  let r;
  try { r = await fetch(path, opt); } catch { return { status: 0, data: {} }; }
  let data = {};
  try { data = await r.json(); } catch { data = {}; }
  return { status: r.status, data: data && typeof data === 'object' ? data : {} };
}

function when(ts) {
  if (!ts) return null;
  const d = new Date(ts * 1000);
  const p = (x) => String(x).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

const label = (d) => d.name || d.id;

// ------------------------------------------------------------------ devices
function deviceRow(d, buttonKey, onClick) {
  const li = el('li', 'adm-dev');
  li.dataset.device = d.id;
  const dot = el('span', d.online ? 'aj-dot' : 'aj-dot aj-dot--off');
  dot.setAttribute('aria-hidden', 'true');
  const text = el('div', 'adm-dev__text');
  const name = el('span', 'adm-dev__name', d.name || t('devices.noname'));
  name.title = d.name || '';
  const at = when(d.paired_at);
  const meta = el('span', 'adm-dev__meta');
  meta.append(el('span', '', `${t(d.online ? 'devices.online' : 'devices.offline')} ·`),
    el('span', '', at ? t('devices.paired_at', { when: at }) : t('devices.unknown_time')));
  text.append(name, meta, el('span', 'adm-dev__id', d.id));
  const b = el('button', 'aj-btn aj-btn--sm adm-unbind', t(buttonKey));
  b.type = 'button';
  b.setAttribute('aria-label', `${t(buttonKey)} · ${d.name || d.id}`);
  b.addEventListener('click', () => onClick(d));
  li.append(dot, text, b);
  return li;
}

async function revoke(d) {
  if (!window.confirm(t('devices.confirm_unbind', { name: label(d) }))) return;
  const r = await api('POST', `/api/devices/${encodeURIComponent(d.id)}/revoke`, {});
  if (r.status === 200) say('devices.unbound', { name: label(d) });
  else say(errKey(r.data.error, 'devices.unbind_failed'), null, 'error');
  refresh();
}

async function unbindForPairing(d) {
  if (!window.confirm(t('pair.confirm_unbind', { name: label(d) }))) return;
  const r = await api('POST', '/api/pair/unbind', { device: d.id });
  if (r.status !== 200 || !r.data.ok) say(errKey(r.data.error, 'pair.unbind_failed'), null, 'error');
  else say('pair.unbound_next', { name: label(d) });
  refresh();
}

function renderDevices(s) {
  const limit = s.limit || LIMIT_DEFAULT;
  const n = s.devices.length;
  $('dev-count').textContent = `${n} / ${limit}`;
  $('dev-count-wrap').setAttribute('aria-label', t('devices.count_label', { n, limit }));
  $('dev-count-wrap').setAttribute('role', 'img');
  const meter = $('dev-meter');
  meter.replaceChildren(...Array.from({ length: limit }, (_, i) => el('span', i < n ? 'adm-meter__slot is-on' : 'adm-meter__slot')));
  show('dev-full', n >= limit);
  $('dev-list').replaceChildren(...s.devices.map((d) => deviceRow(d, 'devices.unbind', revoke)));
  show('dev-empty', n === 0);
}

// ------------------------------------------------------------------ pairing
function renderPairing(s) {
  const p = s.pairing;
  const phase = p ? p.phase : null;
  show('pair-idle', !phase || ['approved', 'denied', 'expired'].includes(phase));
  show('pair-waiting', phase === 'waiting');
  show('pair-pending', phase === 'pending');
  show('pair-full', phase === 'full');
  const res = $('pair-result');
  if (phase === 'approved') {
    rich(res, t('pair.approved', { name: (p.device && p.device.name) || t('pair.new_device') }));
    res.className = 'aj-callout';
  } else if (phase === 'denied') {
    rich(res, t(DENY.has(p.reason) ? `deny.${p.reason}` : 'deny.denied'));
    res.className = 'aj-callout aj-callout--danger';
  } else if (phase === 'expired') {
    rich(res, t('pair.expired'));
    res.className = 'aj-callout aj-callout--danger';
  }
  res.hidden = !['approved', 'denied', 'expired'].includes(phase);
  if (phase === 'waiting') {
    const img = $('pair-qr');
    if (typeof p.qr_svg === 'string' && p.qr_svg.startsWith('data:image/svg+xml') && img.getAttribute('src') !== p.qr_svg) img.src = p.qr_svg;
    $('pair-link').textContent = typeof p.link === 'string' ? p.link : '';
    const left = Math.max(0, Math.round((p.expires || 0) - Date.now() / 1000));
    const leftText = left >= 60 ? t('pair.left_min', { n: Math.ceil(left / 60) }) : t('pair.left_sec', { n: left });
    rich($('pair-scan'), t('pair.scan', { left: leftText }));
  } else {
    $('pair-qr').removeAttribute('src');
    $('pair-link').textContent = '';
    setLinkBox(false);
  }
  if (phase === 'pending') {
    rich($('pair-device-lead'), t('pair.pending_lead', { name: (p.device && p.device.name) || t('pair.unnamed_device') }));
    rich($('pair-code-where'), t('pair.code_where', { n: String(p.deadline_in ?? '') }));
    const wrong = $('pair-pass-wrong');
    wrong.hidden = typeof p.pass_wrong !== 'number';
    if (typeof p.pass_wrong === 'number') rich(wrong, t('pair.pass_wrong', { n: p.pass_wrong }));
    else wrong.replaceChildren();
    show('pair-pass-unset', s.passphrase_set === false);
  } else {
    $('pair-code').value = '';
    $('pair-pass').value = '';
    $('pair-approve').disabled = true;
  }
  if (phase === 'full') {
    rich($('pair-full-lead'), t('pair.full_lead', { name: (p.device && p.device.name) || t('pair.unnamed_device') }));
    rich($('pair-full-warn'), t('pair.full_warn', { limit: s.limit || LIMIT_DEFAULT }));
    $('pair-full-list').replaceChildren(...s.devices.map((d) => deviceRow(d, 'pair.full_unbind', unbindForPairing)));
  }
  return phase === 'waiting' || phase === 'pending' || phase === 'full';
}

function setLinkBox(open) {
  show('pair-link-box', open);
  const b = $('pair-show-link');
  b.setAttribute('aria-expanded', open ? 'true' : 'false');
  rich(b, t(open ? 'pair.hide_link' : 'pair.show_link'));
}

// ------------------------------------------------------------------ whole page
function render(s) {
  state = s;
  // While a code is being typed (pending / full), no control-plane text (Agent name, company name) sits on the page:
  // a compromised company dashboard must not be able to put a fake "code" next to the code field (review A32-04).
  const coding = !!(s.pairing && ['pending', 'full'].includes(s.pairing.phase));
  const name = coding ? null : s.agent_name;
  $('agent-name').textContent = coding ? t('hero.adding') : (name || t('hero.unnamed'));
  $('top-name').textContent = coding ? t('header.adding') : (name || 'Agent J');   // unnamed → the product name
  $('top-name').title = coding ? '' : (name || '');
  $('agent-sub').textContent = s.machine ? t('hero.sub', { machine: s.machine }) : t('hero.sub_here');
  document.title = coding || !name ? t('page.title') : t('page.title_named', { name });
  show('name-card', !coding);
  const running = !!s.serve.running;
  const relayUp = running && !!s.serve.relay_up;
  $('serve-state').textContent = t(running ? 'conn.program_on' : 'conn.program_off');
  $('serve-dot').className = running ? 'aj-dot' : 'aj-dot aj-dot--off';
  $('relay-state').textContent = t(!running ? 'conn.relay_na' : (relayUp ? 'conn.relay_on' : 'conn.relay_off'));
  $('relay-dot').className = relayUp ? 'aj-dot' : (running ? 'aj-dot aj-dot--warn' : 'aj-dot aj-dot--off');
  const tenant = s.dashboard && s.dashboard.tenant;
  $('dash-state').textContent = !s.dashboard.linked ? t('conn.company_none')
    : (coding || !tenant ? t('conn.company_hidden') : t('conn.company_linked', { slug: tenant.slug, name: tenant.name }));
  show('serve-hint', !running);
  renderDevices(s);
  const active = renderPairing(s);
  $('pair-start').disabled = !running;
  show('pair-need-program', !running);
  const ru = $('remote-unbind');
  if (document.activeElement !== ru) ru.checked = !!s.remote_unbind;
  rich($('remote-unbind-note'), t(s.remote_unbind ? 'remote.on' : 'remote.off'));
  $('foot-version').textContent = t('footer.version', { version: s.version });
  $('foot-channel').textContent = t('footer.channel', { channel: s.channel });
  return active;
}

async function refresh() {
  clearTimeout(timer);
  const r = await api('GET', '/api/state');
  let active = false;
  if (r.status === 200) active = render(r.data);
  else if (r.status === 404) { loggedOut(); return; } else say('session.unreachable', null, 'error');
  timer = setTimeout(refresh, active ? 1000 : 4000);
}

// ------------------------------------------------------------------ name
function chip(text, onClick, quiet) {
  const b = el('button', quiet ? 'adm-chip adm-chip--quiet' : 'adm-chip', text);
  b.type = 'button';
  b.addEventListener('click', onClick);
  return b;
}

let suggestions = [];

function renderChips() {
  const pick = (x) => () => { $('name-input').value = x; $('name-input').focus(); };
  const chips = [1, 2, 3, 4].map((i) => chip(t(`name.starter_${i}`), () => pick(t(`name.starter_${i}`))()));
  chips.push(chip(t('name.own'), () => { $('name-input').value = ''; $('name-input').focus(); }, true));
  $('name-chips').replaceChildren(...chips);
  const sug = $('name-suggestions');
  sug.replaceChildren(...suggestions.map((x) => chip(x, pick(x))));
  sug.hidden = suggestions.length === 0;
}

async function saveName() {
  if (busy) return;
  const v = $('name-input').value;
  busy = true;
  $('name-save').disabled = true;
  const r = await api('POST', '/api/name', { name: v });
  busy = false;
  $('name-save').disabled = false;
  if (r.status === 200 && r.data.ok) {
    nameMsg('name.saved', { name: r.data.name });
    $('name-input').value = '';
    suggestions = [];
    renderChips();
    refresh();
    return;
  }
  nameMsg(NAME_ERRS.has(r.data.error) ? `name.err.${r.data.error}` : 'name.failed', null, 'error');
  suggestions = Array.isArray(r.data.suggestions) ? r.data.suggestions.filter((x) => typeof x === 'string') : [];
  renderChips();
}

function setupName() {
  renderChips();
  $('name-save').addEventListener('click', saveName);
  $('name-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') saveName(); });
}

// ------------------------------------------------------------------ pairing controls
function setupPairing() {
  $('pair-start').addEventListener('click', async () => {
    say(null);
    const r = await api('POST', '/api/pair/start', {});
    if (r.status !== 200) say(errKey(r.data.error, 'pair.start_failed'), null, 'error');
    refresh();
  });
  const cancel = async () => { await api('POST', '/api/pair/cancel', {}); say('pair.cancelled'); refresh(); };
  $('pair-cancel').addEventListener('click', cancel);
  $('pair-full-deny').addEventListener('click', cancel);
  $('pair-show-link').addEventListener('click', () => { setLinkBox($('pair-link-box').hidden); });
  const code = $('pair-code');
  const pass = $('pair-pass');
  const ready = () => code.value.length === 6 && pass.value.length > 0;
  const sync = () => { $('pair-approve').disabled = !ready(); };
  code.addEventListener('input', () => {
    code.value = code.value.replace(/[^0-9]/g, '').slice(0, 6);
    sync();
  });
  pass.addEventListener('input', sync);
  const decide = async (value) => {
    $('pair-approve').disabled = true;
    const body = value ? { code: value, passphrase: pass.value } : { code: '' };
    pass.value = '';                       // never kept in the page longer than one try
    const r = await api('POST', '/api/pair/code', body);
    if (r.status !== 200) say(errKey(r.data.error, 'pair.submit_failed'), null, 'error');
    refresh();
  };
  $('pair-approve').addEventListener('click', () => { if (ready()) decide(code.value); });
  for (const f of [code, pass]) f.addEventListener('keydown', (e) => { if (e.key === 'Enter' && ready()) decide(code.value); });
  $('pair-deny').addEventListener('click', () => decide(''));
}

function setupRemote() {
  $('remote-unbind').addEventListener('change', async (e) => {
    const r = await api('POST', '/api/remote-unbind', { on: e.target.checked });
    if (r.status !== 200) say('remote.failed', null, 'error');
    refresh();
  });
}

// ------------------------------------------------------------------ session
function loggedOut(key) {
  clearTimeout(timer);
  session = null;
  state = null;
  try { sessionStorage.removeItem(SESSION_KEY); } catch { /* storage off: nothing kept anyway */ }
  show('app', false);
  show('logout', false);
  $('top-name').textContent = '';
  document.title = t('page.title');
  say(key || 'session.logged_out', null, 'error');
}

/** The token is in the fragment, which never reaches any server; read it and wipe it from the address bar and history
 *  before anything else runs (the ?lang= query, if any, stays). → the token or null. */
function takeToken() {
  const m = /^#t=([A-Za-z0-9_-]{43})$/.exec(location.hash);
  if (location.hash) history.replaceState(null, '', location.pathname + location.search);
  return m ? m[1] : null;
}

async function login(token) {
  try { session = sessionStorage.getItem(SESSION_KEY); } catch { session = null; }
  if (token) {
    session = null;
    const r = await api('POST', '/api/session', { token });
    if (r.status !== 200 || typeof r.data.session !== 'string') { loggedOut('session.link_used'); return false; }
    session = r.data.session;
    try { sessionStorage.setItem(SESSION_KEY, session); } catch { /* keep it in memory only */ }
  }
  if (!session) { loggedOut(); return false; }
  show('app', true);
  show('logout', true);
  return true;
}

async function logout() {
  await api('POST', '/api/session/end', {});
  loggedOut('session.bye');
}

function relabel() {                       // language switched: every visible word again, no new request
  applyStatic();
  renderChips();
  sayNode($('msg'), lastMsg);
  sayNode($('name-msg'), lastNameMsg);
  if (state) render(state);
  else document.title = t('page.title');
}

document.addEventListener('DOMContentLoaded', async () => {
  const token = takeToken();
  await loadDicts();
  applyStatic();
  document.title = t('page.title');
  document.body.classList.remove('adm-loading');
  if (window.AJLang) window.AJLang.onChange(relabel);
  setupName();
  setupPairing();
  setupRemote();
  $('logout').addEventListener('click', logout);
  if (await login(token)) refresh();
});
