// Unknown mailbox: collect non-secret server details before opening the sealed card.
import { mk } from './ui.js';
import { t } from './t.js';
import { registerLayer } from './layers.js';
let active = null;
export function mailServers(cfg) {
  if (active) return Promise.resolve(null);
  return new Promise((resolve) => {
    const root = mk('div', 'ajmodal dlgcard elev'); root.id = 'mail-servers';
    root.setAttribute('role', 'dialog'); root.setAttribute('aria-modal', 'true'); root.setAttribute('aria-labelledby', 'mail-servers-title');
    const form = mk('form', 'box');
    const title = mk('h2', '', t('mail.serverTitle')); title.id = 'mail-servers-title';
    form.append(title, mk('p', 'setnote', t('mail.serverNote')));
    const controls = {};
    for (const kind of ['imap', 'smtp']) {
      for (const field of ['host', 'port', 'security']) {
        const label = mk('label', 'mailf'); label.append(mk('span', '', kind.toUpperCase() + ' · ' + t('mail.' + field)));
        const input = field === 'security' ? mk('select', 'aj-select') : mk('input', 'aj-input');
        input.id = 'mail-dialog-' + kind + '-' + field;
        if (field === 'security') for (const v of ['ssl', 'starttls']) { const o = mk('option', '', v.toUpperCase()); o.value = v; input.append(o); }
        else { input.type = field === 'port' ? 'number' : 'text'; input.required = true; if (field === 'port') { input.min = '1'; input.max = '65535'; } }
        input.value = cfg[kind]?.[field] || (field === 'port' ? kind === 'imap' ? 993 : 465 : field === 'security' ? 'ssl' : '');
        controls[kind + '_' + field] = input; label.append(input); form.append(label);
      }
    }
    const save = mk('button', 'aj-btn aj-btn--primary', t('mail.serverContinue')); save.type = 'submit';
    const cancel = mk('button', 'aj-btn aj-btn--quiet', t('mail.serverCancel')); cancel.type = 'button';
    const finish = (value) => { active = null; root.remove(); resolve(value); };
    cancel.onclick = () => finish(null);
    form.onsubmit = (e) => { e.preventDefault(); const out = {}; for (const k of ['imap', 'smtp']) out[k] = {host: controls[k + '_host'].value.trim(), port: Number(controls[k + '_port'].value), security: controls[k + '_security'].value}; finish(out); };
    form.append(save, cancel); root.append(form); document.body.append(root); active = root;
    controls.imap_host.focus();
  });
}
registerLayer({element: () => active, open: () => !!active, rank: 54,
  close: () => active?.querySelector('button[type=button]').click(), fallback: () => document.getElementById('mail-key')});
