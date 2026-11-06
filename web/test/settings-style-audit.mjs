// #517 deterministic DOM lint. Runs in Chromium; no model judgement or production access.
export function auditSettings(root = document.getElementById('settings')) {
  const errors = [], controls = [], headings = [];
  const visible = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
  const ident = e => e.id || e.outerHTML.slice(0,160);
  const buttonComponents = {
    'aj-btn': () => true,
    'act': e => !!e.closest('.rmbar.rscmdacts'),
    'bulb': e => !!e.closest('#set-actions'),
    'cc-btn': e => !!e.closest('.cc') || !!e._ccFor,
    'text-editor-field': e => e.classList.contains('aj-input') && !!e.previousElementSibling?.matches('input[data-text-field]'), 'setnav': () => true, 'menuicon': () => true,
    'setx': e => !!e.closest('.sethead'), 'setcopy': e => e.hasAttribute('data-copy') || !!e.closest('.setcmd'),
    'setlink': () => true, 'tgl': e => e.getAttribute('role') === 'switch',
    'setswitch': e => e.getAttribute('role') === 'switch',
    'tw-sw': e => !!e.closest('.twoway') && e.getAttribute('role') === 'switch',
    'tw-lab': e => !!e.closest('.twoway'), 'setsegment': e => !!e.closest('.segrow'),
    'setstep': e => !!e.closest('.fontstep,.sfxvol'), 'setsidebtn': e => !!e.closest('.setside'),
    'cueplay': e => e.hasAttribute('data-cue-play'), 'tschip': e => !!e.closest('.toolsort'),
    'ctm-main': e => !!e.closest('.ctmpage'), 'ctm-allow': e => !!e.closest('.ctmpage'),
  };
  for (const e of root.querySelectorAll('select,input,textarea,button')) {
    let ok = false;
    if (e.localName === 'button') ok = Object.entries(buttonComponents).some(([c, structure]) => e.classList.contains(c) && structure(e));
    else if (e.localName === 'select') ok = e.classList.contains('aj-select');
    else if (e.localName === 'textarea') ok = e.classList.contains('aj-textarea');
    else if (['checkbox','radio'].includes(e.type)) ok = e.classList.contains('aj-check-input');
    else if (e.type === 'hidden') ok = e.dataset.textField === '1' && e.classList.contains('aj-textarea');
    else if (e.type === 'range') ok = e.classList.contains('aj-range');
    else if (e.type === 'file') ok = e.classList.contains('aj-file');
    else ok = e.classList.contains('aj-input') || (e.classList.contains('rmcode') && !!e.closest('.rmadd'));
    if (!ok) errors.push(`shared-component: ${ident(e)}`);
    const cs = getComputedStyle(e);
    if (visible(e) && e.matches('.aj-input,.aj-select,.aj-textarea,.aj-btn') && parseFloat(cs.borderRadius) <= 0) errors.push(`square-control: ${ident(e)}`);
    controls.push({id:ident(e),component:e.className,visible:visible(e),radius:cs.borderRadius});
  }
  for (const h of root.querySelectorAll('h3,h4,.setg')) {
    if (!h.classList.contains('setg') && !(h.classList.contains('rstitle') && h.closest('.rscard'))) errors.push(`section-component: ${ident(h)}`);
    if (!visible(h)) continue;
    const card = h.closest('.rscard,.setcard') || root;
    const hr = h.getBoundingClientRect();
    const probe = document.createElement('span'); probe.style.cssText='position:absolute;visibility:hidden;width:var(--space-section);height:0'; h.append(probe);
    const token = probe.getBoundingClientRect().width; probe.remove();
    if (!Number.isFinite(token) || token < 24) errors.push(`section-token: ${ident(h)} ${token}px < 24px`);
    const seams = [...card.querySelectorAll('*')].filter(e => visible(e) && !e.contains(h) && (e.compareDocumentPosition(h) & Node.DOCUMENT_POSITION_FOLLOWING) && getComputedStyle(e).borderBottomStyle === 'dashed').map(e => e.getBoundingClientRect().bottom);
    const gap = seams.length ? hr.top - seams.at(-1) : null;
    if (gap !== null && gap + 0.001 < token) errors.push(`section-seam: ${ident(h)} ${gap}px < ${token}px`);
    headings.push({id:ident(h),gap,minimum:token});
  }
  return {errors, controls, headings};
}
