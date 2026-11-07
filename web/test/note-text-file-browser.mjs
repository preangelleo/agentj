import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate as ev,key,shoot,sleep} from './browser.mjs';
const out='reports/qa/167-370-393';mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0}),fake=await startFakeHost(),B=await launch();let checks=0;
const ok=(v,m)=>{assert.ok(v,m);checks++;};
try {
 fake.reset();fake.st.caps=['jcards1'];const mutations=[];
 fake.st.onApp=async(c,m,send)=>{
  if(m.t==='say')throw new Error('model send forbidden');
  if(m.t==='jcard_get'){await send({t:m.t,r:m.r,ok:true,id:'notes',noteFields:true});return true;}
  if(m.t==='jcard_note'){mutations.push(m);await send({t:m.t,r:m.r,ok:true,item:{id:'new'}});return true;}return false;
 };
 const p=await newPage(B,390,844,'light',{allow:[web.url,fake.relay+'/']});
 await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await ev(p,`window.noteNav=[];import('./js/jcards.js').then(m=>{m.directAdd('notes',{nav:x=>noteNav.push(x)});})`);
 await waitFor(p,`!!document.querySelector('.jc-dialog[open]')`);
 const select=async(expr,paste=false)=>ev(p,`(()=>{const d=new DataTransfer();d.items.add(${expr});const picker=document.querySelector('.jc-dialog input[type=file]');${paste?"document.querySelector('.jc-dialog textarea').dispatchEvent(new ClipboardEvent('paste',{clipboardData:d,bubbles:true,cancelable:true}))":"picker.files=d.files;picker.dispatchEvent(new Event('change'))"};})()`);
 await select(`new File(['Hello\\n董事长'],'memo.txt',{type:'text/plain'})`);
 await waitFor(p,`document.querySelector('.jc-dialog textarea').value==='Hello\\n董事长'`);ok(true,'picker reads UTF8');
 for(const expr of [`new File(['bad'],'bad.txt',{type:'text/html'})`,`new File(['x'.repeat(16385)],'large.txt')`,`new File([new Uint8Array([255])],'bad.txt')`,`new File(['sk-'+ 'a'.repeat(24)],'key.txt')`]){
  await select(expr,true);await sleep(60);ok(await ev(p,`document.querySelector('.jc-dialog textarea').value==='Hello\\n董事长'`),'refused file keeps draft');
 }
 await select(`new File(['\\nPasted'],'paste.txt',{type:'text/plain'})`,true);await waitFor(p,`document.querySelector('.jc-dialog textarea').value.endsWith('Pasted')`);
 await key(p,'Enter',{code:'Enter',ctrl:true});await waitFor(p,`!document.querySelector('.jc-dialog')`);
 ok(mutations.length===1&&mutations[0].text==='Hello\n董事长\nPasted','single Host mutation');ok(await ev(p,`noteNav[0]==='card/notes#latest'`),'latest navigation');ok(fake.st.says.length===0,'zero model sends');
 await p.close();
 const before=execFileSync('git',['show','HEAD:agentjarvis/site/static/site.css'],{encoding:'utf8'}),after=readFileSync('agentjarvis/site/static/site.css','utf8');
 const computed=[];
 for(const theme of ['light','dark'])for(const width of [390,1440])for(const [phase,css] of [['before',before],['after',after]]){
  const q=await newPage(B,width,900,theme,{allow:[web.url]});await navigate(q,web.url);
  await ev(q,`document.documentElement.dataset.theme=${JSON.stringify(theme)};document.head.querySelectorAll('link[rel=stylesheet]').forEach(x=>x.remove());document.body.innerHTML='<main style="padding:24px"><p class="site-wr__say">Existing workflow quotation</p><section class="workflow-prompt"><blockquote><p>Describe your workflow\\nKeep this prompt</p></blockquote></section><dl class="workflow-stack"><div><dt>Existing tools</dt><dd>Workflow stack description</dd></div></dl></main>';const s=document.createElement('style');s.textContent=${JSON.stringify(':root{--r-md:12px;--accent:#d97757;--sp-4:16px}html{background:#faf9f6;color:#333}html[data-theme=dark]{background:#272726;color:#faf9f6}'+css)};document.head.append(s)`);
  const styles=await ev(q,`[...document.querySelectorAll('.site-wr__say,.workflow-prompt blockquote,.workflow-stack>div')].map(x=>{const s=getComputedStyle(x);return {border:s.borderTopWidth,left:s.borderLeftWidth,radius:s.borderTopLeftRadius,color:s.borderLeftColor}})`);
  if(phase==='after')ok(styles.every(s=>parseFloat(s.border)>0&&parseFloat(s.radius)>0),'rounded full borders');
  computed.push({theme,width,phase,styles});writeFileSync(`${out}/${phase}-${theme}-${width}.png`,await shoot(q));await q.close();
 }
 writeFileSync(out+'/computed.json',JSON.stringify(computed,null,2));console.log(`${checks} checks PASS; 8 component fixture screenshots; zero model sends`);
}finally{await B.close();await fake.stop();await web.stop();}
