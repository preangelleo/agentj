import {test} from 'node:test';
import assert from 'node:assert/strict';
globalThis.window={};
const {notifyFormat,circleStatus}=await import('../public/js/ui.js');
test('circle candidates have no character threshold; actual font/layout decides in notify',()=>{
 for(const text of ['中文'.repeat(20),'M'.repeat(14),'i'.repeat(14),'é'.repeat(30),'🙂']) assert.equal(circleStatus(text),true);
 for(const text of ['', 'First\nLast', 'First\u2028Last']) { assert.equal(circleStatus(text),false);assert.equal(notifyFormat({text}).shape,'square'); }
});
test('P461 fallback preserves duration/kind and does not route A to reply; structured B remains rect',()=>{
 for(const kind of ['success','info','error','warn']){
  const f=notifyFormat({kind,shape:'circle',text:'A detailed reason to try again.',ms:2600});
  assert.equal(f.kind,kind);assert.equal(f.ms,2600);assert.equal(f.shape,'circle');
 }
 assert.equal(notifyFormat({shape:'circle',text:'Long explanation',ms:0}).ms,0);
 for(const extra of [{actions:[{label:'Undo',fn(){}}]},{title:'Title'},{media:{nodeType:1}}])
  assert.equal(notifyFormat({text:'Saved',shape:'circle',...extra}).shape,'rect');
 assert.equal(notifyFormat({text:'Sent',shape:'rect'}).shape,'rect');
 assert.equal(notifyFormat({kind:'error',text:'Long reason'}).shape,'rect');
});
