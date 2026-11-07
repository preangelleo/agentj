// #375: only settled replies to a host-verified owner source can offer terminal drafts.
// A code fence is data: never execute it. Host term_prefill checks the empty shell editor again.
import {t} from './t.js';
import {enter, termInfo} from './termhook.js';
export function ownReply(p) {
  const s = p?.source;
  return Number.isInteger(p?.id) && p.end !== 'open' && !!s && ['phone','host'].includes(s.k)
    && !s.quote && !s.via && !s.forward && !s.external && !s.friend && !p.assist && !p.external
    && (!p.role || p.role === 'assistant');
}
export function command(text, lang) {
  if (!/^(?:sh|bash|zsh|shell|console|terminal)$/i.test(lang || '')) return null;
  const s = String(text || '').replace(/\n$/, '');
  return s.trim() && s.length <= 4096 && !/[\x00-\x1f\x7f-\x9f\u2028\u2029]/.test(s) ? s : null;
}
export function dangerous(s) {
  // Visual warning only. The host remains authoritative when Enter is pressed.
  return /(?:^|[\s;&|(\x60])["']?(?:\/[^\s;&|"'()]*\/)?(?:sudo|doas|rm|rmdir|shred|mkfs\w*|dd|chmod|chown)["']?(?:\s|$)|\b(?:curl|wget)\b[^\n]*\|\s*(?:\/[^\s|]*\/)?(?:ba|z|da)?sh\b/.test(s);
}
export function paint(root, page) {
  for (const b of root.querySelectorAll('.term-command')) b.remove();
  if (!ownReply(page) || termInfo()?.prefill !== true) return;
  for (const pre of root.querySelectorAll('.codeblock pre[data-lang]')) {
    // The reply root is itself a blockquote. Only descendants count as quoted material.
    const quote = pre.closest('blockquote,details');
    if (quote && quote !== root) continue;
    const cmd = command(pre.querySelector('code')?.textContent, pre.dataset.lang);
    if (!cmd) continue;
    const b = document.createElement('button'); b.setAttribute('data-aj-owner', 'aj-terminal'); b.type = 'button'; b.className = 'term-command';
    const risk = dangerous(cmd); b.classList.toggle('term-command--danger', risk);
    b.textContent = t('terminal.execute'); b.title = t(risk ? 'terminal.danger' : 'terminal.draft');
    b.setAttribute('aria-label', b.textContent + ' · ' + b.title);
    b.addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); enter(cmd); });
    pre.parentElement.append(b);
  }
}
