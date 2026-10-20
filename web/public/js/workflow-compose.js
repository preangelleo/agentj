import {focusDraft, touchComposer} from './composer-focus.js';
// P244: editable text with atomic, non-editable workflow mentions. The existing textarea remains the plain-text
// transport / draft / ASR boundary. No markup is sent, stored, copied or pasted.
const CMD_RE=/^[/\uFF0F][^\s/\uFF0F]+(?=\s)/;
export function composeMentions(input, getMentions) {
 const editor=document.createElement('div');editor.id='workflow-compose';editor.contentEditable='true';editor.hidden=true;
 editor.setAttribute('role','textbox');editor.setAttribute('aria-label',input.getAttribute('placeholder')||'Message');
 editor.setAttribute('aria-multiline','true');input.after(editor);
 let last='',frame=0,composing=false;
 const plain=n=>n.nodeType===3?n.textContent:n.nodeType!==1?'':n.dataset?.mention||n.tagName==='BR'&&'\n'||[...n.childNodes].map(plain).join('')+(n.tagName==='DIV'&&n!==editor?'\n':'');
 const value=()=>[...editor.childNodes].map(plain).join('');
 function caret(){const sel=getSelection();if(!sel?.rangeCount||!editor.contains(sel.anchorNode))return input.value.length;const r=sel.getRangeAt(0).cloneRange();r.selectNodeContents(editor);r.setEnd(sel.anchorNode,sel.anchorOffset);return [...r.cloneContents().childNodes].map(plain).join('').length;}
 function sync(){last=input.value=value();const at=caret();input.setSelectionRange(at,at);input.dispatchEvent(new Event('input',{bubbles:true}));}
 // P406 #163: the caret where the field has it (after an inserted token and its space), not always at the end
 function place(at){if(document.activeElement!==editor)return;let n=at;const r=document.createRange();for(const c of editor.childNodes){const len=c.nodeType===3?c.textContent.length:plain(c).length;if(c.nodeType===3&&n<=len){r.setStart(c,n);r.collapse(true);const sel=getSelection();sel.removeAllRanges();sel.addRange(r);return;}n-=len;if(n<=0){r.setStartAfter(c);r.collapse(true);const sel=getSelection();sel.removeAllRanges();sel.addRange(r);return;}}tail();}
 function tail(){const r=document.createRange();r.selectNodeContents(editor);r.collapse(false);const sel=getSelection();sel.removeAllRanges();sel.addRange(r);}
 function paint(focus=false){
  // P406 #163: the field and this editor are one composer. A focus already in it moves with the text (touch too: the
  // keyboard is up, nothing new opens); only a composer that had no focus stays unfocused on touch (P375)
  const inside=document.activeElement===input||document.activeElement===editor, give=n=>{if(inside)n.focus();else focusDraft(n);};
  const text=input.value, entries=getMentions().filter(w=>text.includes(w.text));
  // P302 (owner: 「slash command 放在文本输入框没有给颜色……Peek-a-Blue」): a finished leading /command (followed by a space)
  // is a chip too — Peek-a-Blue ground, Noir text; still plain text in the field, the host reads it as before
  const cmd=CMD_RE.exec(text)?.[0]||'';
  if(!entries.length&&!cmd){const wasEditing=!editor.hidden;editor.hidden=true;input.classList.remove('workflow-backed');last=text;if(focus&&wasEditing){const at=input.selectionEnd;give(input);input.setSelectionRange(at,at);}return;}
  last=text;editor.replaceChildren();let at=0;
  if(cmd){const chip=document.createElement('span');chip.className='workflow-token cmd-token';chip.contentEditable='false';chip.dataset.mention=cmd;chip.textContent=cmd;editor.append(chip);at=cmd.length;}
  while(at<text.length){const w=entries.find(w=>text.startsWith(w.text,at));if(w){const pill=document.createElement('span');pill.className='workflow-token';pill.contentEditable='false';pill.dataset.mention=w.text;pill.textContent=w.label;pill.title=w.text;editor.append(pill);at+=w.text.length;}else{let end=at+1;while(end<text.length&&!entries.some(w=>text.startsWith(w.text,end)))end++;editor.append(document.createTextNode(text.slice(at,end)));at=end;}}
  editor.hidden=false;input.classList.add('workflow-backed');if(focus&&(inside||!touchComposer())){const at=input.selectionEnd;give(editor);place(at);}
  if(!frame)frame=requestAnimationFrame(watch);
 }
 function watch(){frame=0;if(editor.hidden)return;if(!composing&&input.value!==last)paint(document.activeElement===editor);if(!editor.hidden)frame=requestAnimationFrame(watch);}
 input.addEventListener('input',()=>{if(input.value!==last)paint(document.activeElement===input);});
 editor.addEventListener('input',()=>{if(!composing)sync();});
 editor.addEventListener('compositionstart',()=>{composing=true;input.dispatchEvent(new CompositionEvent('compositionstart'));});
 editor.addEventListener('compositionend',()=>{composing=false;sync();input.dispatchEvent(new CompositionEvent('compositionend'));});
 editor.addEventListener('keydown',e=>{e.stopPropagation();if(composing||e.isComposing)return;input.setSelectionRange(caret(),caret());const forwarded=new KeyboardEvent('keydown',{key:e.key,code:e.code,shiftKey:e.shiftKey,ctrlKey:e.ctrlKey,altKey:e.altKey,metaKey:e.metaKey,bubbles:true,cancelable:true});if(!input.dispatchEvent(forwarded))e.preventDefault();});
 editor.addEventListener('paste',e=>{e.stopPropagation();e.preventDefault();input.setSelectionRange(caret(),caret());const forwarded=new ClipboardEvent('paste',{clipboardData:e.clipboardData,bubbles:true,cancelable:true});if(!input.dispatchEvent(forwarded))return;const text=e.clipboardData.getData('text/plain');const sel=getSelection();if(!sel?.rangeCount)return;const r=sel.getRangeAt(0);r.deleteContents();const n=document.createTextNode(text);r.insertNode(n);r.setStartAfter(n);r.collapse(true);sel.removeAllRanges();sel.addRange(r);sync();});
 editor.addEventListener('cut',e=>{const sel=getSelection();if(!sel?.rangeCount)return;e.preventDefault();const r=sel.getRangeAt(0);e.clipboardData.setData('text/plain',[...r.cloneContents().childNodes].map(plain).join(''));r.deleteContents();sync();});
 editor.addEventListener('copy',e=>{const sel=getSelection();if(!sel?.rangeCount)return;e.preventDefault();e.clipboardData.setData('text/plain',[...sel.getRangeAt(0).cloneContents().childNodes].map(plain).join(''));});
 return {paint, clear:()=>{editor.hidden=true;input.classList.remove('workflow-backed');last='';cancelAnimationFrame(frame);frame=0;}};
}
