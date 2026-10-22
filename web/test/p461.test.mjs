import {test} from 'node:test';
import assert from 'node:assert/strict';
globalThis.window={};
const {notifyFormat,circleStatus}=await import('../public/js/ui.js');
test('P461 circle accepts one status, never a phrase, punctuation, a counter or mixed scripts',()=>{
 for(const text of ['已发送','已复制','保存成功','失败','Sent','Copied','Saved','Failed','Forwarded']){
  assert.equal(circleStatus(text),true,text);assert.equal(notifyFormat({text}).shape,'circle');
 }
 for(const text of ['已保存到电脑','Saved successfully','Copy failed','Sent.','已发送 2 条','已存到J','第一条\n最后条','Sent\nagain','1234','', '🙂','a-b', '已发送。']){
  assert.equal(circleStatus(text),false,text);
  for(const glance of [false,true])assert.equal(notifyFormat({shape:'circle',text,ms:2200,glance}).shape,'square',text);
 }
});
test('P461 fallback preserves duration/kind and does not route A to reply; structured B remains rect',()=>{
 for(const kind of ['success','info','error','warn']){
  const f=notifyFormat({kind,shape:'circle',text:'A detailed reason to try again.',ms:2600});
  assert.equal(f.kind,kind);assert.equal(f.ms,2600);assert.equal(f.shape,'square');
 }
 assert.equal(notifyFormat({shape:'circle',text:'Long explanation',ms:0}).ms,0);
 for(const extra of [{actions:[{label:'Undo',fn(){}}]},{title:'Title'},{media:{nodeType:1}}])
  assert.equal(notifyFormat({text:'Saved',shape:'circle',...extra}).shape,'rect');
 assert.equal(notifyFormat({text:'Sent',shape:'rect'}).shape,'rect');
 assert.equal(notifyFormat({kind:'error',text:'Long reason'}).shape,'rect');
});
