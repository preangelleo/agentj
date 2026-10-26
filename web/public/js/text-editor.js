// P537: shared full-screen text editor; settings retain their existing save/validation paths.
export {openText as open} from './fsedit.js';
import {openText} from './fsedit.js';
import {t} from './t.js';
export function field(input, title) {
  if (input.dataset.textField) return input;
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
      save:value=>{input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));paint();}});
  };
  let button=null;
  const paint=()=>{if(button)button.textContent=(input.value || t('p537.edit')).slice(0,180);};
  if (input.type==='hidden') { button=document.createElement('button');button.type='button';button.className='aj-input text-editor-field';button.setAttribute('aria-label',title||t('p537.edit'));input.after(button);button.addEventListener('click',launch);paint(); }
  else { input.addEventListener('click',launch);input.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();launch();}}); }
  return input;
}
export function watchSettings(root) {
  const scan=()=>{for(const input of root.querySelectorAll('textarea,input:not([type]),input[type=text],input[type=email],input[type=tel]')) {
    if(input.inputMode==='numeric'||input.autocomplete==='one-time-code'||input.closest('[data-screenshot-private],[data-secret]')||input.autocomplete.includes('password'))continue;
    field(input);
  }};
  scan();new MutationObserver(scan).observe(root,{childList:true,subtree:true});
}
