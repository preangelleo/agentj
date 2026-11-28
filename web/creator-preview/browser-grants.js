// The paired host owns all credentialed site actions. The UI only shows and
// signs the host's exact short-lived offer.
import { ask1 } from './session.js';
import { signedWrite, signedWriteFa } from './controls.js';
import { faceIdStep } from './settings.js';
import { lang, locale } from './t.js';
import { mk, pageCopy } from './ui.js';

const rule = {
  'bilibili': {
    origin: 'https://member.bilibili.com',
    name: ['B 站创作中心', 'Bilibili creator center'],
    account: ['B 站 UID', 'Bilibili UID'],
    pattern: /^[1-9][0-9]{0,15}$/,
    action: 'archive_status',
    label: ['读取稿件状态', 'Read submission status'],
  },
  'youtube-studio': {
    origin: 'https://studio.youtube.com',
    name: ['YouTube 工作室', 'YouTube Studio'],
    account: ['YouTube 频道 ID', 'YouTube channel ID'],
    pattern: /^UC[A-Za-z0-9_-]{22}$/,
    action: 'channel_status',
    label: ['核对已登录频道', 'Check the signed-in channel'],
  },
};
const copy = (zh, en) => lang() === 'en' ? en : zh;
const first = (xs) => copy(...xs);
const id = (v) => typeof v === 'string' && /^[a-f0-9]{32}$/.test(v);
const expiry = (v) => new Intl.DateTimeFormat(locale(), {dateStyle: 'medium', timeStyle: 'short'}).format(v * 1000);

function valid(row) {
  const s = Object.hasOwn(rule, row?.site) && rule[row.site];
  return s && row.v === 1 && id(row.id) && row.origin === s.origin &&
    typeof row.account === 'string' && s.pattern.test(row.account) &&
    row.backend === 'ego-lite' && row.profile === 'Agent J' && /^[a-f0-9]{64}$/.test(row.host_binding) &&
    row.actions?.length === 1 && row.actions[0] === s.action &&
    Number.isSafeInteger(row.expires) && row.expires * 1000 > Date.now() && row.expires * 1000 <= Date.now() + 481 * 60_000;
}

function fact(row) {
  return [row.origin, first(rule[row.site].account) + ' ' + row.account, first(rule[row.site].label),
    copy('失效于 ', 'Expires ') + expiry(row.expires)].join('\n');
}

function button(label, click) {
  const b = mk('button', 'aj-action aj-action--primary', label, 'aj-action');
  b.type = 'button';
  b.addEventListener('click', click);
  return b;
}

function option(select, value, label) {
  const node = mk('option', '', label);
  node.value = value;
  select.append(node);
}

function field(label, input) {
  const wrap = mk('label', 'browser-grant-field');
  wrap.append(mk('span', '', label), input);
  return wrap;
}

const errors = {
  account_unverified: ['专用资料里无法核实这个账号。请先登录并确认账号。', 'The dedicated profile could not verify this account. Sign in and confirm the account.'],
  browser_unavailable: ['请保持 Mac 上的 Ego Lite 与 Agent J 专用资料已连接。', 'Connect the Agent J profile and keep Ego Lite open on the Mac.'],
  changed: ['授权已失效。请重新核对站点和账号。', 'This request expired. Check the site and account again.'],
  busy: ['专用浏览器正在核对另一项操作。', 'The dedicated browser is checking another request.'],
  no_passkey: ['请先在当前遥控器设置通行密钥。', 'Set up a passkey on this remote first.'],
};
const failure = (why) => first(errors[why] || ['主机未确认此操作，请查看当前授权。', 'The host has not confirmed the change. Check current grants.']);

export function panel() {
  const section = mk('section', 'browser-grants');
  section.id = 'setup-browser-grants';
  const state = mk('p', 'setup-note');
  state.setAttribute('role', 'status');
  const list = mk('ul', 'browser-grant-list');
  const site = mk('select'), account = mk('input'), minutes = mk('select');
  const hint = mk('span', '');
  const fields = mk('div', 'browser-grant-fields');
  let busy = false;
  account.maxLength = 24;
  account.autocomplete = 'off';
  account.spellcheck = false;
  for (const [key, s] of Object.entries(rule)) option(site, key, first(s.name));
  for (const [value, label] of [[15, copy('15 分钟', '15 minutes')], [60, copy('1 小时', '1 hour')], [480, copy('8 小时', '8 hours')]]) {
    option(minutes, value, label);
  }
  const accountField = mk('label', 'browser-grant-field');
  accountField.append(hint, account);
  function changed() {
    hint.textContent = first(rule[site.value].account);
    account.inputMode = site.value === 'bilibili' ? 'numeric' : 'text';
  }
  site.addEventListener('change', () => { account.value = ''; changed(); });
  changed();

  function pending(on) {
    busy = on;
    for (const el of section.querySelectorAll('input, select, button')) el.disabled = on;
  }
  async function refresh() {
    const result = await ask1({t: 'browser_grants'}, 'ctl_res', 10000);
    if (!section.isConnected) return;
    if (!result.ok || result.action !== 'browser_grants') {
      state.textContent = failure(result.why);
      fields.hidden = true;
      list.replaceChildren();
      return;
    }
    fields.hidden = !result.available;
    if (!result.available) state.textContent = failure('browser_unavailable');
    const grants = Array.isArray(result.grants) ? result.grants.filter(valid).slice(0, 2) : [];
    list.replaceChildren(...grants.map(row => {
      const item = mk('li', 'browser-grant-row');
      item.append(pageCopy(mk('p', 'browser-grant-fact', fact(row), 'aj-setup')));
      item.append(button(copy('收回权限', 'Revoke access'), async () => {
        if (busy) return;
        pending(true);
        const res = await signedWrite('browser_grant_revoke', {id: row.id}, {t: 'browser_grant_revoke', id: row.id});
        state.textContent = res.ok ? copy('已收回权限。', 'Access revoked.') : failure(res.why);
        await refresh();
        pending(false);
      }));
      return item;
    }));
  }

  const authorize = button(copy('核对并授权', 'Check and authorize'), async () => {
    const key = site.value, expected = account.value.trim(), lease = Number(minutes.value);
    if (busy || !rule[key].pattern.test(expected)) {
      if (!busy) state.textContent = copy('请输入有效的账号标识。', 'Enter a valid account ID.');
      return;
    }
    pending(true);
    state.textContent = copy('正在核对已登录的账号…', 'Checking the signed-in account…');
    const offer = await ask1({t: 'browser_grant_prepare', site: key, account: expected, minutes: lease}, 'ctl_res', 35000);
    if (!section.isConnected) return;
    const target = offer.target;
    if (!offer.ok || offer.action !== 'browser_grant_prepare' || !valid(target) ||
        target.site !== key || target.account !== expected || target.expires * 1000 > Date.now() + (lease * 60 + 30) * 1000) {
      state.textContent = failure(offer.why);
      pending(false);
      return;
    }
    const res = await signedWriteFa('browser_grant', target, {t: 'browser_grant', target}, async (challenge) => {
      const sheet = document.getElementById('confirm');
      sheet.dataset.browserGrant = '1';
      try {
        return await faceIdStep(challenge, {titleText: copy('授权专用浏览器', 'Authorize the dedicated browser'), textText: fact(target), showSaving: false});
      } finally {
        delete sheet.dataset.browserGrant;
      }
    });
    state.textContent = res.ok ? copy('已授权。可从这台遥控器的主对话查询。', 'Authorized. Use this remote’s main chat to ask for a check.')
      : res.why === 'cancel' ? copy('已取消。', 'Cancelled.') : failure(res.why);
    await refresh();
    pending(false);
  });
  fields.append(field(copy('站点', 'Site'), site), accountField, field(copy('有效期', 'Duration'), minutes), authorize);
  section.append(mk('h4', 'setup-group', copy('按站点授权', 'Website access'), 'aj-setup'),
    pageCopy(mk('p', 'setup-note', copy('暂可授权读取。上传、改稿和发布请在主机浏览器完成。',
      'Authorize read access. Uploads, edits and publishing stay in the browser on the host.'), 'aj-setup')), list, fields, state);
  void refresh();
  return section;
}
