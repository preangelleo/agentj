// P256: both Settings and the Agent use the same sealed card and this renderer.
// Provider text and links come from the host's SIGNED purpose field, not a second table.
import { t } from './t.js';
const OFFICIAL = new Set(['agentj.app', 'myaccount.google.com', 'service.mail.qq.com', 'help.mail.163.com',
  'account.apple.com', 'support.apple.com', 'login.yahoo.com', 'help.yahoo.com', 'account.live.com',
  'support.microsoft.com', 'help.aliyun.com', 'open.work.weixin.qq.com']);
export function paintMailPassword(c, label, input, rows) {
  input.placeholder = '';
  if (c.kind !== 'secret' || c.name !== 'AGENTJ_MAIL_PASSWORD' || !c.purpose.includes('\n')) return;
  const lines = c.purpose.split('\n');
  label.textContent = lines[0];
  input.placeholder = 'abcd efgh ijkl mnop';
  rows.replaceChildren();
  const dt = document.createElement('dt'), dd = document.createElement('dd');
  dt.textContent = t('mail.addr'); dd.textContent = lines[1] || ''; rows.append(dt, dd);
  const help = document.createElement('dd');
  help.textContent = lines.slice(2).filter(line => !/^https:/.test(line) && !/^(IMAP|SMTP):/.test(line)).join('\n');
  rows.append(help);
  let official = false;
  for (const raw of c.purpose.match(/https:\/\/[^\s]+/g) || []) {
    let u; try { u = new URL(raw); } catch { continue; }
    if (!OFFICIAL.has(u.hostname) || u.username || u.password) continue;
    const dd = document.createElement('dd'), a = document.createElement('a');
    if (!official) {
      const dt = document.createElement('dt'); dt.textContent = t('mail.official'); rows.append(dt); official = true;
    }
    a.href = u.href; a.target = '_blank'; a.rel = 'noopener noreferrer';
    a.textContent = u.hostname === 'agentj.app' ? t('mail.gmailHelp') : u.hostname === 'myaccount.google.com' ? t('mail.gmailOpen') : u.hostname;
    dd.append(a); rows.append(dd);
  }
  const serverText = lines.filter(line => /^(IMAP|SMTP):/.test(line)).join('\n');
  if (!serverText) return true;
  const details = document.createElement('details'), summary = document.createElement('summary'), servers = document.createElement('p');
  summary.textContent = t('mail.adv'); servers.textContent = serverText;
  details.append(summary, servers); const serverRow = document.createElement('dd'); serverRow.append(details); rows.append(serverRow);
  return true;

}
