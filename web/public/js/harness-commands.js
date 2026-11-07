import DICT from '../i18n.js';
// Render only the current Host's checked command catalogue. Clicks use the
// existing phone slash channel; no terminal input or arbitrary shell strings.
export function renderHarnessCommands(box, menu, invoke, lang = 'zh') {
  box.replaceChildren();
  box.hidden = true;
  if (!menu || !Array.isArray(menu.native)) return;
  const rows = menu.native.filter(row => row && /^\/[A-Za-z0-9][A-Za-z0-9_:.\-]{0,62}$/.test(row.cmd)
    && ['run', 'agent', 'none'].includes(row.state)).slice(0, 120);
  if (!rows.length) return;
  const words = DICT[lang.startsWith('en') ? 'en' : 'zh'];
  const details = document.createElement('details');
  details.setAttribute('data-aj-owner','aj-rescue');details.className = 'rscard rscmds setfold';
  const title = document.createElement('summary');title.className='rstitle';title.setAttribute('data-aj-owner','aj-rescue');
  title.textContent = `${menu.kind || ''} · ${words['p0280.harness.title']}`;
  details.append(title);
  for (const row of rows) {
    const item = document.createElement('p');item.className='rscmd';item.setAttribute('data-aj-owner','aj-rescue');
    const button = document.createElement('button');
    button.type = 'button';button.setAttribute('data-aj-owner','aj-action');button.setAttribute('data-aj-action-context','dialog');button.className='aj-action';
    button.textContent = `${row.cmd} · ${words['p0280.harness.'+row.state]}`;
    button.disabled = row.state === 'none';
    button.addEventListener('click', () => { if (row.state !== 'none') invoke(row.cmd.slice(1)); });
    const note = document.createElement('small');note.setAttribute('data-aj-owner','aj-rescue');
    note.textContent = String(row.desc || '');
    item.append(button, note); details.append(item);
  }
  box.append(details); box.hidden = false;
}
