// P478: owner-tapped, signed folder scopes. No filesystem paths leave Noise.
import { t } from './t.js';
import { el, mk, confirmSheet, toast } from './ui.js';
import { ask1 } from './session.js';
import { signedWrite, whyText } from './controls.js';

export async function load() {
  const box = el('local-files-list');
  box.replaceChildren(mk('p', 'small', t('p478.loading'), 'aj-file'));
  const m = await ask1({ t: 'files_get' }, 'files_state');
  box.replaceChildren();
  if (m?.t !== 'files_state') { box.append(mk('p', 'small', t('p478.offline'), 'aj-file')); return; }
  box.append(mk('p', 'small', t('p478.fence'), 'aj-file'));
  if (m.workdir) box.append(mk('p', 'small', t('p478.work') + ' ' + m.workdir, 'aj-file'));
  if (m.mac) {
    box.append(mk('p', 'small', t('p478.tcc'), 'aj-file'));
    for (const name of ['Downloads', 'Desktop', 'Documents']) {
      const value = m.tcc?.folders?.[name];
      box.append(mk('p', 'small', name + ': ' + t(value === 'readable' ? 'p478.readable' : 'p478.unknown'), 'aj-file'));
    }
    box.append(mk('p', 'small', 'Finder: ' + t(m.tcc?.automation === 'available' ? 'p478.readable' : 'p478.unknown'), 'aj-file'));
  }
  for (const g of m.grants || []) {
    const row = mk('div', 'local-files-row');
    const text = mk('span', 'small local-files-path', g.path + ' · ' + (g.expires ? new Date(g.expires * 1000).toLocaleString() : t('p478.permanent')), 'aj-file');
    const button = mk('button', 'aj-action', t('p478.remove'), 'aj-action', 'dialog'); button.type = 'button';
    button.addEventListener('click', async () => { await write('remove', g.path, 0); });
    row.append(text, button); box.append(row);
  }
}
async function write(op, path, expires) {
  const target = { op, path, expires };
  const res = await signedWrite('files_write', target, { t: 'files_write', ...target });
  if (!res.ok) { toast(t('p478.failed') + ' ' + whyText(res.why)); return; }
  await load();
}
export function configure() {
  el('local-files-add').addEventListener('click', async () => {
    const path = el('local-files-path').value.trim();
    const duration = el('local-files-duration').value;
    if (!path || !await confirmSheet(t('p478.nav'), path + '\n' + t(duration === 'hour' ? 'p478.hour' : 'p478.permanent'), t('p478.allow'))) return;
    await write('add', path, duration === 'hour' ? Math.floor(Date.now() / 1000) + 3600 : 0);
  });
}
