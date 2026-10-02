// agentjarvis host-local Agent admin page (A3.2 contract §4). Login: the one-time token arrives in the URL fragment (#t=),
// is wiped from the address bar at once and swapped (POST /api/session) for a session secret kept only in this tab's
// sessionStorage and sent as `Authorization: Bearer` — no cookie anywhere. Same-origin JSON API; every value from
// the API goes into text nodes (textContent) — never parsed as HTML. The only non-text sink is the QR <img src>, and only for a
// data:image/svg+xml URI that the host itself rendered (segno). Device labels are self-chosen by the device: shown as untrusted.
'use strict';

const $ = (id) => document.getElementById(id);
const LIMIT_DEFAULT = 5;
const STARTERS = ['贾维斯一号', 'Wren', '市场部 Agent', '内容制作部 Agent'];
const DENY_TEXT = {
  code_mismatch: '安全码不一致，已拒绝。', denied: '已拒绝。', timeout: '120 秒内没有确认，已拒绝。', abandoned: '已取消。',
  bad_handshake: '握手失败。', hs_timeout: '手机没有完成握手，已拒绝。', device_limit: '已达 5 台上限，没有批准：先解绑一台。',
  no_pending_device: '没有在等批准的设备。', gone: '手机断开了。', replaced: '已被另一个配对（终端 jarvis pair 或另一个页面）取代。',
  serve_gone: 'jarvis serve 断开了。', relay_down: '中继没连上，稍后再试。', cancelled: '已取消。',
  pass_locked: '批准口令连续输错，已暂时锁定，请稍后再试。',
  pass_not_set: '还没有设置批准口令：先在这台电脑的终端运行 `jarvis passphrase set`。',
};
const ERR_TEXT = {
  serve_not_running: 'jarvis serve 没在运行：先在另一个终端运行 `jarvis serve`，再添加遥控器。',
  relay_down: '中继没连上（jarvis serve 正在重连），稍后再试。', unbind_first: '已达上限：先解绑一台遥控器。',
  no_pending_device: '没有在等批准的设备。', unknown_device: '没有这台设备（可能已经解绑了）。', serve_gone: 'jarvis serve 断开了。',
  passphrase_required: '还要输入批准口令。',
};

const SESSION_KEY = 'aj_admin_session';
let session = null;
let state = null;
let timer = null;
let busy = false;

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = String(text);
  return n;
}

function show(id, on) { $(id).hidden = !on; }

function say(text, kind) {
  const m = $('msg');
  m.textContent = text || '';
  m.dataset.kind = kind || 'ok';
  m.hidden = !text;
}

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
  if (!ts) return '未知时间';
  const d = new Date(ts * 1000);
  const p = (x) => String(x).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function deviceRow(d, label, onClick) {
  const li = el('li');
  li.dataset.device = d.id;
  const dot = el('span', d.online ? 'dot on' : 'dot');
  const text = el('div', 'dtext');
  const name = el('span', 'dname', d.name || '（没有名字）');
  name.title = d.name || '';
  text.append(name, el('span', 'meta', `${d.online ? '在线' : '离线'} · 配对于 ${when(d.paired_at)}`), el('span', 'meta mono', d.id));
  const b = el('button', 'btn btn-small btn-danger', label);
  b.type = 'button';
  b.addEventListener('click', () => onClick(d));
  li.append(dot, text, b);
  return li;
}

async function revoke(d) {
  if (!window.confirm(`解绑「${d.name || d.id}」？它会立即断开，再用要重新扫码配对。`)) return;
  const r = await api('POST', `/api/devices/${encodeURIComponent(d.id)}/revoke`, {});
  say(r.status === 200 ? `已解绑「${d.name || d.id}」。` : (ERR_TEXT[r.data.error] || '没解绑成功。'), r.status === 200 ? 'ok' : 'error');
  refresh();
}

async function unbindForPairing(d) {
  if (!window.confirm(`解绑「${d.name || d.id}」，给新设备腾出位置？它会立即断开。`)) return;
  const r = await api('POST', '/api/pair/unbind', { device: d.id });
  if (r.status !== 200 || !r.data.ok) say(ERR_TEXT[r.data.error] || '没解绑成功（它可能已经被解绑了）。', 'error');
  else say(`已解绑「${d.name || d.id}」。现在看着手机，输入手机上的安全码。`);
  refresh();
}

function renderDevices(s) {
  const limit = s.limit || LIMIT_DEFAULT;
  $('dev-count').textContent = `${s.devices.length} / ${limit}`;
  const list = $('dev-list');
  list.replaceChildren(...s.devices.map((d) => deviceRow(d, '解绑', revoke)));
  show('dev-empty', s.devices.length === 0);
}

function renderPairing(s) {
  const p = s.pairing;
  const phase = p ? p.phase : null;
  show('pair-idle', !phase || ['approved', 'denied', 'expired'].includes(phase));
  show('pair-waiting', phase === 'waiting');
  show('pair-pending', phase === 'pending');
  show('pair-full', phase === 'full');
  const res = $('pair-result');
  if (phase === 'approved') {
    res.textContent = `✓ 已批准「${(p.device && p.device.name) || '新设备'}」。它现在是这台主机的遥控器。`;
    res.dataset.kind = 'ok';
  } else if (phase === 'denied') {
    res.textContent = '✗ ' + (DENY_TEXT[p.reason] || '已拒绝。');
    res.dataset.kind = 'error';
  } else if (phase === 'expired') {
    res.textContent = '✗ 二维码已过期（5 分钟），请重新添加。';
    res.dataset.kind = 'error';
  }
  res.hidden = !['approved', 'denied', 'expired'].includes(phase);
  if (phase === 'waiting') {
    const img = $('pair-qr');
    if (typeof p.qr_svg === 'string' && p.qr_svg.startsWith('data:image/svg+xml') && img.getAttribute('src') !== p.qr_svg) img.src = p.qr_svg;
    $('pair-link').textContent = typeof p.link === 'string' ? p.link : '';
    const left = Math.max(0, Math.round((p.expires || 0) - Date.now() / 1000));
    $('pair-expires').textContent = left >= 60 ? `${Math.ceil(left / 60)} 分钟内有效` : `${left} 秒内有效`;
  } else {
    $('pair-qr').removeAttribute('src');
    $('pair-link').textContent = '';
    show('pair-link-box', false);
  }
  if (phase === 'pending') {
    $('pair-device-name').textContent = (p.device && p.device.name) || '未命名设备';
    $('pair-deadline').textContent = String(p.deadline_in ?? '');
    const wrong = $('pair-pass-wrong');
    wrong.hidden = typeof p.pass_wrong !== 'number';
    wrong.textContent = typeof p.pass_wrong === 'number' ? `✗ 批准口令不对（再错 ${p.pass_wrong} 次会锁定）。设备还在等，请再输一次。` : '';
    show('pair-pass-unset', s.passphrase_set === false);
  } else {
    $('pair-code').value = '';
    $('pair-pass').value = '';
    $('pair-approve').disabled = true;
  }
  if (phase === 'full') {
    $('pair-full-name').textContent = (p.device && p.device.name) || '未命名设备';
    $('pair-full-list').replaceChildren(...s.devices.map((d) => deviceRow(d, '解绑这台', unbindForPairing)));
  }
  return phase === 'waiting' || phase === 'pending' || phase === 'full';
}

function render(s) {
  state = s;
  // While a code is being typed (pending / full), no control-plane text (Agent name, company name) sits on the page:
  // a compromised Dashboard must not be able to put a fake "code" next to the code field (review A32-04).
  const coding = !!(s.pairing && ['pending', 'full'].includes(s.pairing.phase));
  const name = coding ? null : s.agent_name;
  $('agent-name').textContent = coding ? '正在添加遥控器' : (name || '还没起名的 Agent');
  $('agent-sub').textContent = `1 个席位 · 运行在 ${s.machine || '这台主机'} 上`;
  $('top-host').textContent = s.machine || '';
  document.title = coding ? 'Agent 管理页' : `${name || 'Agent'} · Agent 管理页`;
  show('name-card', !coding);
  const sv = $('serve-state');
  sv.textContent = s.serve.running ? '运行中' : '没在运行';
  sv.className = s.serve.running ? 'ok' : 'bad';
  const rl = $('relay-state');
  rl.textContent = !s.serve.running ? '—（serve 没在运行）' : (s.serve.relay_up ? '已连接' : '断开，正在重连');
  rl.className = s.serve.running && s.serve.relay_up ? 'ok' : 'bad';
  $('dash-state').textContent = !s.dashboard.linked ? '未绑定 Dashboard（只改本机）'
    : (coding ? '已绑定（配对时不显示公司名）' : `已添加到公司账号 ${s.dashboard.tenant.slug}（${s.dashboard.tenant.name}）`);
  show('serve-hint', !s.serve.running);
  renderDevices(s);
  const active = renderPairing(s);
  $('pair-start').disabled = !s.serve.running;
  const ru = $('remote-unbind');
  if (document.activeElement !== ru) ru.checked = !!s.remote_unbind;
  $('remote-unbind-note').textContent = s.remote_unbind
    ? '开：Dashboard 的所有者可以请求本机解绑一台遥控器；本机核对它在准许名单上、每小时最多执行 3 次，执行与拒绝都记进 host.log。只能让设备失去访问，不能加设备或批准设备。'
    : '关：本机拒绝 Dashboard 的一切解绑请求；解绑只能在这里或用 `jarvis revoke`。';
  $('foot-version').textContent = `agentjarvis 主机端 ${s.version}`;
  $('foot-channel').textContent = s.channel;
  return active;
}

async function refresh() {
  clearTimeout(timer);
  const r = await api('GET', '/api/state');
  let active = false;
  if (r.status === 200) active = render(r.data);
  else if (r.status === 404) { loggedOut(); return; } else say('暂时连不上 jarvis admin。', 'error');
  timer = setTimeout(refresh, active ? 1000 : 4000);
}

function chip(text, onClick, quiet) {
  const b = el('button', quiet ? 'chip quiet' : 'chip', text);
  b.type = 'button';
  b.addEventListener('click', onClick);
  return b;
}

function nameMsg(text, kind) {
  const m = $('name-msg');
  m.textContent = text || '';
  m.dataset.kind = kind || 'ok';
  m.hidden = !text;
}

async function saveName() {
  if (busy) return;
  const v = $('name-input').value;
  busy = true;
  $('name-save').disabled = true;
  const r = await api('POST', '/api/name', { name: v });
  busy = false;
  $('name-save').disabled = false;
  const sug = $('name-suggestions');
  if (r.status === 200 && r.data.ok) {
    nameMsg(`✓ Agent 名改为「${r.data.name}」。`);
    $('name-input').value = '';
    sug.hidden = true;
    refresh();
    return;
  }
  nameMsg(r.data.message || '名字没改。', 'error');
  const list = Array.isArray(r.data.suggestions) ? r.data.suggestions.filter((x) => typeof x === 'string') : [];
  sug.replaceChildren(...list.map((x) => chip(x, () => { $('name-input').value = x; $('name-input').focus(); })));
  sug.hidden = list.length === 0;
}

function setupName() {
  const chips = STARTERS.map((x) => chip(x, () => { $('name-input').value = x; $('name-input').focus(); }));
  chips.push(chip('或者自己起一个', () => { $('name-input').value = ''; $('name-input').focus(); }, true));
  $('name-chips').replaceChildren(...chips);
  $('name-save').addEventListener('click', saveName);
  $('name-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') saveName(); });
}

function setupPairing() {
  $('pair-start').addEventListener('click', async () => {
    say('');
    const r = await api('POST', '/api/pair/start', {});
    if (r.status !== 200) say(ERR_TEXT[r.data.error] || '没法开始配对。', 'error');
    refresh();
  });
  const cancel = async () => { await api('POST', '/api/pair/cancel', {}); say('已取消配对。'); refresh(); };
  $('pair-cancel').addEventListener('click', cancel);
  $('pair-full-deny').addEventListener('click', cancel);
  $('pair-show-link').addEventListener('click', () => { show('pair-link-box', $('pair-link-box').hidden); });
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
    if (r.status !== 200) say(ERR_TEXT[r.data.error] || '没有提交成功。', 'error');
    refresh();
  };
  $('pair-approve').addEventListener('click', () => { if (ready()) decide(code.value); });
  for (const f of [code, pass]) f.addEventListener('keydown', (e) => { if (e.key === 'Enter' && ready()) decide(code.value); });
  $('pair-deny').addEventListener('click', () => decide(''));
}

function setupRemote() {
  $('remote-unbind').addEventListener('change', async (e) => {
    const r = await api('POST', '/api/remote-unbind', { on: e.target.checked });
    if (r.status !== 200) say('没改成功。', 'error');
    refresh();
  });
}

function loggedOut(text) {
  clearTimeout(timer);
  session = null;
  try { sessionStorage.removeItem(SESSION_KEY); } catch { /* storage off: nothing kept anyway */ }
  show('app', false);
  document.title = 'Agent 管理页';
  say(text || '这个页面没有登录或登录已失效：在主机终端里运行 `jarvis admin`（或在它的终端里按回车）拿一个新链接。', 'error');
}

async function login() {
  // The token is in the fragment, which never reaches any server; wipe it from the address bar and history first.
  const m = /^#t=([A-Za-z0-9_-]{43})$/.exec(location.hash);
  if (location.hash) history.replaceState(null, '', location.pathname);
  try { session = sessionStorage.getItem(SESSION_KEY); } catch { session = null; }
  if (m) {
    session = null;
    const r = await api('POST', '/api/session', { token: m[1] });
    if (r.status !== 200 || typeof r.data.session !== 'string') { loggedOut('这个链接已经用过或过期了：在主机终端里按回车拿一个新链接。'); return false; }
    session = r.data.session;
    try { sessionStorage.setItem(SESSION_KEY, session); } catch { /* keep it in memory only */ }
  }
  if (!session) { loggedOut(); return false; }
  show('app', true);
  return true;
}

async function logout() {
  await api('POST', '/api/session/end', {});
  loggedOut('已退出。要再打开，在主机终端里按回车拿一个新链接。');
}

document.addEventListener('DOMContentLoaded', async () => {
  setupName();
  setupPairing();
  setupRemote();
  $('logout').addEventListener('click', logout);
  if (await login()) refresh();
});
