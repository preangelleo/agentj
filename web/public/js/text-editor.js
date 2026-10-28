// P537: shared full-screen text editor; settings retain their existing save/validation paths.
// P561 追加 13 (#338, Leo 10-28: 「给遥控器改个名字，都需要给我全屏吗？……点击下边的确认并不是保存，是回到上一个页面，然后还要再点保存」):
// only multi-line text goes full screen. A single-line field (a remote's name, an e-mail address, a host…) is edited where it
// stands, and Enter / its own Save saves in one step. Where the editor does open, ✓ is that field's Save.
// P563 追加 3 (贾维斯审查 10-28): ✓ is Save every time multi-line text goes full screen — not only when the field is the only
// thing its form saves. The field's form is submitted (its own submit handler = the page's Save, with the other fields as they
// stand), so there is never a second tap on the page's Save. A field outside any form has nothing else to save: ✓ writes it back.
export {openText as open} from './fsedit.js';
import {openText} from './fsedit.js';
import {t} from './t.js';
/** true for text that may run to many lines (a textarea, or a hidden value a caller marks multi-line) — the only kind that goes full screen */
export const multiline = (input) => input.tagName === 'TEXTAREA' || (input.tagName === 'INPUT' && input.type === 'hidden');
/** the form ✓ saves: the field's own form, whatever else it holds (P563: no longer only a form the field is all of) */
export const saveForm = (input) => input.form || null;
export function field(input, title) {
  if (input.dataset.textField || !multiline(input)) return input;
  if (input.tagName === 'TEXTAREA') {
    const old = input; input = document.createElement('input');
    for (const a of old.attributes) if (!['rows','type'].includes(a.name)) input.setAttribute(a.name,a.value);
    input.type='hidden'; input.value=old.value; old.replaceWith(input);
  }
  input.dataset.textField='1'; input.readOnly=true;
  const launch = () => {
    if (input.disabled) return;
    openText({title:title || input.getAttribute('aria-label') || input.closest('label')?.querySelector('span')?.textContent || input.placeholder || t('p412.aria'),
      trigger:button || input, value:input.value, maxLength:input.maxLength>0?input.maxLength:16000,
      save:value=>{input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));paint();
        const f=saveForm(input);if(f)queueMicrotask(()=>f.requestSubmit());}});
  };
  let button=null;
  const paint=()=>{if(button)button.textContent=(input.value || t('p537.edit')).slice(0,180);};
  button=document.createElement('button');button.type='button';button.className='aj-input text-editor-field';button.setAttribute('aria-label',title||t('p537.edit'));input.after(button);button.addEventListener('click',launch);paint();
  return input;
}
export function watchSettings(root) {
  const scan=()=>{for(const input of root.querySelectorAll('textarea')) {   // P561: single-line inputs stay inline
    if(input.inputMode==='numeric'||input.autocomplete==='one-time-code'||input.closest('[data-screenshot-private],[data-secret]')||input.autocomplete.includes('password'))continue;
    field(input);
  }};
  scan();new MutationObserver(scan).observe(root,{childList:true,subtree:true});
}
