// Native private top-layer failure must refuse entry; an ordinary textarea is never a secret fallback.
import assert from'node:assert/strict';import{mkdirSync,writeFileSync}from'node:fs';import{startWebServer}from'./serve.mjs';import{startFakeHost}from'./fakehost.mjs';import{launch,newPage,navigate,waitState,evaluate}from'./browser.mjs';
const out=process.env.OLARES_RUN_OUT||'reports/qa/secret-editor-failclosed';mkdirSync(out,{recursive:true});const web=await startWebServer(),fake=await startFakeHost(),B=await launch();let p,checks=0;const rows=[];
try{p=await newPage(B,390,900,'dark',{allow:[web.url,fake.relay+'/']});await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 for(const mode of['unsupported','throws']){
  const result=await evaluate(p,`(async()=>{const m=await import('./js/fsedit.js'),proto=HTMLDialogElement.prototype,old=proto.showModal,canary='synthetic-unaccepted-value';const input=document.getElementById('input');input.value='ordinary draft';proto.showModal=${mode==='unsupported'?'undefined':"function(){throw new DOMException('','InvalidStateError')}"};try{const opened=m.openText({secret:true,value:canary});return{refused:opened===false,hidden:document.getElementById('fsEd').hidden,ordinary:document.getElementById('fsText').tagName==='TEXTAREA'&&input.value==='ordinary draft',clean:![...document.querySelectorAll('input,textarea')].some(x=>x.value.includes(canary))&&!document.querySelector('.fs-secret-shell')&&!document.querySelector('.fs-secret-context')&&!document.body.dataset.fs};}finally{proto.showModal=old;m.forget();}})()`);
  assert.deepEqual(result,{refused:true,hidden:true,ordinary:true,clean:true});checks+=4;rows.push({mode,...result});
 }
 writeFileSync(out+'/results.json',JSON.stringify({checks,rows},null,2));console.log('secret editor failclosed '+checks+' PASS');
}finally{await p?.dispose();await B.close();await fake.stop();await web.stop();}
