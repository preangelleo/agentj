import { copyButton } from './copycode.js';
// P270: the pickup card's one live preview. DOM only; no secret in navigation state.
import { RelayMD } from './md.js';
import { mk } from './ui.js';
import { copyable } from './copycode.js';   // P292 (P290 merge): the plain-text value is tap-to-copy like every pre
import { t, onLang } from './t.js';
import { historyDoubleTap } from './history-corners.js';

// Register before relay.init installs its reader popstate listener. At the window
// target, registration order matters even for capture listeners.
let handlePop = null;
addEventListener('popstate', e => handlePop?.(e), true);

export function createSecretPreview(root, container, copy) {
  const bar = mk('div', 'sout-preview-bar');
  const back = mk('button', 'aj-btn'); back.id = 'sout-back'; back.type = 'button'; back.hidden = true;
  // P555: ⛶ is an icon — no border, inside the content box's top-right corner
  const expand = mk('button', 'ajicon sout-expand', '⛶'); expand.id = 'sout-expand'; expand.type = 'button'; expand.hidden = true;
  const fullCopy = mk('button', 'aj-btn'); fullCopy.id = 'sout-full-copy'; fullCopy.type = 'button'; fullCopy.hidden = true;
  const content = mk('div', 'sout-content'); content.id = 'sout-content';
  bar.append(back, fullCopy, expand); container.append(bar, content);
  let full = false, pushed = false, pendingPop = false;
  const labels = () => {
    back.textContent = t('error.back'); fullCopy.textContent = t('sout.copyFile');
    expand.setAttribute('aria-label', t('r.rd.aria')); expand.title = t('r.rd.aria');
  };
  labels(); onLang(labels);
  function leave(pop = false) {
    if (!full) return false;
    full = false; root.classList.remove('sout-full');
    back.hidden = fullCopy.hidden = true; expand.hidden = false;
    if (pushed && !pop) { pendingPop = true; history.back(); }
    pushed = false; expand.focus({preventScroll: true});
    return true;
  }
  function enter() {
    if (full || pendingPop || expand.hidden) return;
    full = true; root.classList.add('sout-full');
    back.hidden = fullCopy.hidden = false; expand.hidden = true;
    try { history.pushState({ajSecretPreview: 1}, ''); pushed = true; } catch { pushed = false; }
    back.focus({preventScroll: true});
  }
  handlePop = e => {
    if (!full && !pendingPop) return;
    e.stopImmediatePropagation(); pendingPop = false;
    if (full) leave(true);
  };
  back.addEventListener('click', () => leave()); expand.addEventListener('click', enter);
  fullCopy.addEventListener('click', copy);
  // No text selection suppression on the desktop: double click still selects a word.
  content.addEventListener('dblclick', enter);
  const tapSurface = mk('div');
  // Reuse the phone double-tap recogniser without its mouse/keyboard selection handlers.
  historyDoubleTap(tapSurface, enter);
  for (const type of ['pointerdown', 'pointerup', 'pointercancel', 'pointerleave']) {
    content.addEventListener(type, e => {
      if (e.target.closest('button,a') || e.pointerType === 'mouse') return;
      const forwarded = new PointerEvent(type, e);
      tapSurface.dispatchEvent(forwarded);
      if (forwarded.defaultPrevented && e.cancelable) e.preventDefault();
    });
  }
  content.addEventListener('click', async e => {
    const button = e.target.closest('button.copy');
    if (button) await copyButton(button.closest('.codeblock').querySelector('code').textContent, button);
  });
  return {
    close: () => leave(),
    reset() { leave(); content.replaceChildren(); content.className = 'sout-content'; expand.hidden = true; },
    show(text, markdown) {
      content.classList.toggle('words', markdown);
      if (markdown) {
        content.appendChild(RelayMD.toDOM(RelayMD.parse(text)));
        RelayMD.parse('');                 // discard the Markdown parser's transient source cache
      }
      else content.appendChild(copyable(mk('pre', 'mono', text), { variant: 'ext', btn: root.querySelector('#sout-copy') }));
      expand.hidden = false;
    },
    note(text) { content.appendChild(mk('p', 'elev-note', text)); }
  };
}
