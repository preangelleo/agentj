// P210 「连上你的Agent J 主机」: the unpaired page is a set of numbered, mutually exclusive method cards (任选一种).
// The visible cards are numbered here (no reverse-pairing code in this browser → the rest become 方法一 / 方法二), the copy
// icons copy the command next to them, and 「以前配过？」 shows only together with its Face ID button.
import { RelayMD } from './md.js';
import { t, onLang } from './t.js';
import { el, toast } from './ui.js';

export function renumber() {
  let n = 0;
  for (const c of document.querySelectorAll('#pair-view .pm')) c.querySelector('.pm__no').textContent = c.hidden ? '' : t('pair.no' + (++n));
}
const past = () => { el('pm-past').hidden = el('pk-restore').hidden; };

/** scan(): the in-page pairing scanner (both 「扫二维码」 buttons: the terminal's code and the account page's code are #p= links). */
export function init(scan) {
  for (const b of document.querySelectorAll('[data-pair-scan]')) b.addEventListener('click', () => scan());   // @ix app.pair_scan
  for (const b of document.querySelectorAll('[data-pm-copy]')) b.addEventListener('click', async () => {   // @ix app.pair_copy
    const ok = await RelayMD.copyText(el(b.dataset.pmCopy).textContent);
    toast(t(ok ? 'pair.copied' : 'r.copy.fail'), 2400);
  });
  const watch = new MutationObserver(() => { renumber(); past(); });
  for (const e of [...document.querySelectorAll('#pair-view .pm'), el('pk-restore')]) watch.observe(e, { attributes: true, attributeFilter: ['hidden'] });
  onLang(renumber);
  renumber();
  past();
}
