// P206B: paired mail settings reuse mail_set's exact signed target; results remain external data.
// Local bilingual copy is source, not a generated i18n/release artifact.
const COPY = {
  zh: {title:'白名单外邮件分诊', note:'默认关闭。分诊仅整理外部来信，草稿需人工查看；不会自动回复或交给主人主模型。归档仅记录本地状态，原邮件保留。',
    preset:'分诊方式', allowlist_only:'只收白名单（关闭）', rules:'仅免费规则', assistant:'指定模型分诊', support:'指定客服模型分诊',
    provider:'独立模型配置 ID', model:'指定模型', usd_per_day:'每日估算预算（美元）', usd_per_call:'每次调用预留估算费用（美元）',
    every_minutes:'检查间隔（分钟）', per_hour:'每小时调用上限', save:'保存分诊设置',
    status:'共 {total} 封 · 待查看 {review} · 本地归档 {archived}', external:'外部邮件 · 未受信任', draft:'建议草稿（未发送）', error:'分诊暂停',
    spend:'累计预留估算费用', empty:'暂无分诊记录'},
  en: {title:'Mail outside the allowlist', note:'Off by default. Triage sorts external mail and suggests drafts for review. It never replies automatically or passes mail to the owner’s main model. Archive is local only; originals stay in the mailbox.',
    preset:'Triage mode', allowlist_only:'Allowlist only (off)', rules:'Free rules only', assistant:'Designated model', support:'Designated support model',
    provider:'Separate model profile ID', model:'Designated model', usd_per_day:'Daily estimated budget (USD)', usd_per_call:'Estimated reservation per call (USD)',
    every_minutes:'Check interval (minutes)', per_hour:'Calls per hour', save:'Save triage settings',
    status:'{total} messages · {review} for review · {archived} locally archived', external:'External email · untrusted', draft:'Suggested draft (not sent)', error:'Triage paused',
    spend:'Estimated cost reserved', empty:'No triage records yet'}
};
const node = (tag, text) => { const e = document.createElement(tag);e.setAttribute('data-aj-owner',tag==='button'?'aj-action':'aj-channel');if(tag==='button')e.setAttribute('data-aj-action-context','dialog'); if (text !== undefined) e.textContent = text; return e; };

export function renderTriage(view, save, en = false, busy = false, form = true) {
  const anchor = document.getElementById('mail-today');
  if (!anchor || !view?.config?.triage) return; // older hosts have no second lane
  const copy = COPY[en ? 'en' : 'zh'];
  let box = document.getElementById('mail-triage');
  if (!box) {
    box = node('details'); box.id = 'mail-triage';
    const summary = node('summary', copy.title);
    const note = node('p', copy.note);
    const fields = node('div'); fields.id = 'mail-triage-fields';
    for (const key of ['preset','provider','model','usd_per_day','usd_per_call','every_minutes','per_hour']) {
      const label = node('label', copy[key]);
      const input = node(key === 'preset' ? 'select' : 'input');
      input.id = 'mail-triage-' + key;
      if (key === 'preset') {
        for (const mode of ['allowlist_only','rules','assistant','support']) {
          const option = node('option', copy[mode]); option.value = mode; input.append(option);
        }
      } else {
        input.type = ['provider','model'].includes(key) ? 'text' : 'number';
        if (input.type === 'text') { input.autocomplete = 'off'; input.maxLength = key === 'model' ? 120 : 64; }
        else {
          input.min = key === 'usd_per_day' ? '0' : key === 'usd_per_call' ? '0.0001' : key === 'every_minutes' ? '10' : '1';
          input.max = key.startsWith('usd_') ? '20' : key === 'every_minutes' ? '360' : '200';
          input.step = key.startsWith('usd_') ? '0.0001' : '1';
        }
      }
      label.append(input); fields.append(label, node('br'));
    }
    const button = node('button', copy.save); button.type = 'button'; button.className = 'aj-action'; button.id = 'mail-triage-save';
    const status = node('p'); status.id = 'mail-triage-status'; status.setAttribute('role', 'status');
    const list = node('ul'); list.id = 'mail-triage-items';
    box.append(summary, note, fields, button, status, list); anchor.after(box);
  }
  // Always bind the save button to the newest host view, including subsequent mailbox/config changes.
  const button = document.getElementById('mail-triage-save');
  button.onclick = () => {
    const triage = {...view.config.triage};
    for (const key of ['preset','provider','model','usd_per_day','usd_per_call','every_minutes','per_hour']) {
      const value = document.getElementById('mail-triage-' + key).value;
      triage[key] = ['preset','provider','model'].includes(key) ? value.trim() : Number(value);
    }
    save({triage});
  };
  button.disabled = busy;
  const fields = document.getElementById('mail-triage-fields');
  for (const input of fields.querySelectorAll('input,select')) {
    input.disabled = busy;
    const key = input.id.slice('mail-triage-'.length);
    if (form && document.activeElement !== input) input.value = String(view.config.triage[key] ?? '');
  }
  const data = view.triage || {};
  const text = copy.status.replace(/\{(\w+)\}/g, (_, key) => String(Number(data[key]) || 0));
  document.getElementById('mail-triage-status').textContent = text +
    (data.error ? ' · ' + copy.error : '') + ' · ' + copy.spend + ': $' + (Number(data.spend?.usd) || 0).toFixed(4);
  document.getElementById('mail-triage-items').replaceChildren(...(data.items || []).slice(-100).reverse().map(row => {
    const li = node('li');
    li.append(node('strong', copy.external), node('p', String(row.subject || '').slice(0, 200)),
      node('p', String(row.cat || '') + ' · ' + String(row.status || '')),
      node('p', String(row.summary || '').slice(0, 400)));
    if (row.draft) li.append(node('strong', copy.draft), node('p', String(row.draft).slice(0, 4000)));
    return li;
  }));
}
