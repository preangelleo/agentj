// P474 actual Chromium + isolated Noise peer, baseline/candidate screenshots. No customer data.
import assert from 'node:assert/strict';
import {parityRecorder} from '../../parity/lib.mjs';
import {mkdirSync,writeFileSync,mkdtempSync,rmSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate as ev,shoot,sleep,key} from './browser.mjs';
const out=resolve(process.env.P474_OUT||'reports/qa/p474');mkdirSync(out,{recursive:true});
const baseline=mkdtempSync(join(tmpdir(),'p474-baseline-'));
execFileSync('tar',['-x','-C',baseline],{input:execFileSync('git',['archive',process.env.P474_BASE||'d6545fb98','web/public'],{maxBuffer:64*1024*1024})});
const before=await startWebServer({publicDir:join(baseline,'web/public')}),after=await startWebServer(process.env.P474_PUBLIC_DIR?{publicDir:resolve(process.env.P474_PUBLIC_DIR)}:{}),fake=await startFakeHost(),B=await launch();
const screenshots=new Map(),comparisons=[],mutations=[];let checks=0;
const check=(value,label)=>{assert.ok(value,label);checks++;};
const content='  香港 / Hong Kong\n\nRoom 201 · **literal markdown**  \n';
try {
 for(const width of [390,1280])for(const language of ['zh','en'])for(const scheme of ['light','dark'])for(const [stage,web] of [['before',before],['after',after]]) {
  fake.reset();fake.st.caps=['jcards1'];fake.st.prefs={appearance:{language,theme:scheme},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
  let notes=stage==='before'?[{id:'n1',text:'2026-10-22 '+content,editable:true},{id:'n2',text:'2026-10-21 No title',editable:true}]:[{id:'n1',title:'香港地址 / Hong Kong address',text:content,date:'2026-10-22',editable:true},{id:'n2',title:'',text:'No title',date:'2026-10-21',editable:true}];
  fake.st.onApp=async(c,m,send)=>{
   const reply=async(t,v)=>{await send({t,r:m.r,...v});return true;};
   if(m.t==='jcard_list')return reply('jcard_items',{cards:['notes','todo','automation'].map(id=>({id,name:{zh:id==='notes'?'记事本':id,en:id},on:true,official:true,accent:'windstorm'}))});
   if(m.t==='jcard_get')return reply('jcard_get',{ok:true,id:m.id,noteFields:stage==='after'&&m.id==='notes',template:m.id==='todo'?'checklist':m.id,entries:notes,items:m.id==='todo'?[{id:'t1',text:'A task',done:false}]:[{id:'a1',name:'Local automation',description:'Only a fixture',source:'agentj'}],actions:[],revision:'rev'});
   if(m.t==='jcard_note'){
    mutations.push(m);
    if(m.op==='edit')notes=notes.map(it=>it.id===m.entry?{...it,...m}:it);
    if(m.op==='add')notes.unshift({id:'new',title:m.title||'',text:m.text,date:'2026-10-22'});
    if(m.op==='delete')notes=notes.filter(it=>it.id!==m.entry);
    return reply('jcard_note',{ok:true,id:m.id});
   }
   return false;
  };
  const p=await newPage(B,width,900,scheme,{allow:[web.url,fake.relay+'/'],touch:width<600,permissions:['clipboardReadWrite','clipboardSanitizedWrite']});
  const tag=`${width}-${language}-${scheme}`;
  try {
   await navigate(p,fake.newPairing(web.url+(language==='en'?'?lang=en':'')));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await ev(p,"document.getElementById('phone-env-dismiss')?.click();if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click()");
   await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});await sleep(2000);
   check(await ev(p,`document.documentElement.dataset.theme===${JSON.stringify(scheme)}`),'actual application theme');
   await ev(p,"import('./js/jcards.js').then(m=>m.refresh())");
   await ev(p,"(()=>{const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s)})()");
   for(const id of ['workflows','skills','memory','agentj','todo','automation','chat','notes']) {
    await ev(p,`import('./js/five-pages.js').then(m=>m.openTarget('${id}'))`);
    if(['notes','todo','automation'].includes(id))await waitFor(p,`document.querySelector('.jc-body[data-card="${id}"]')`);
    await sleep(150);await ev(p,'document.fonts.ready');
    await ev(p,'document.getAnimations().forEach(a=>a.cancel())');
    const bytes=await shoot(p),name=`${stage}-${id}-${tag}.png`;writeFileSync(join(out,name),bytes);
    if(id!=='notes'){
     const k=`${id}-${tag}`;
     if(stage==='before')screenshots.set(k,bytes);else {const same=bytes.equals(screenshots.get(k));comparisons.push({id,tag,equal:same});}
    }
   }
   if(stage==='after') {
    check(await ev(p,"document.querySelectorAll('.jc-note-title').length===1"),'empty title omitted');
    check(await ev(p,"document.querySelector('.jc-note-title').textContent==='香港地址 / Hong Kong address'"),'title separate');
    check(await ev(p,"document.querySelector('.jc-note-date').textContent==='2026-10-22'"),'date separate');
    check(await ev(p,"(()=>{const e=document.querySelector('.jc-entry'),a=e.querySelector('.jc-entry-actions').getBoundingClientRect(),d=e.querySelector('.jc-note-date').getBoundingClientRect(),r=e.getBoundingClientRect();return a.top>=d.bottom&&Math.abs(a.right-r.right)<20})()"),'actions bottom right');
    // Write with the actual Clipboard API, then read its actual value (no clipboard stub).
    await ev(p,"document.querySelector('[data-note-op=copy]').click()");await sleep(100);
    assert.equal(await ev(p,'navigator.clipboard.readText()'),content);checks++;
    await ev(p,"document.querySelectorAll('[data-note-op=copy]')[1].click()");await sleep(50);
    assert.equal(await ev(p,'navigator.clipboard.readText()'),'No title');checks++;
    await ev(p,"document.querySelector('[data-note-op=edit]').click()");await waitFor(p,"document.querySelector('.jc-dialog[open] input')");
    assert.equal(await ev(p,"document.querySelector('.jc-dialog input').value"),'香港地址 / Hong Kong address');checks++;
    assert.equal(await ev(p,"document.querySelector('.jc-dialog textarea').value"),content);checks++;
    writeFileSync(join(out,`after-edit-${tag}.png`),await shoot(p));
    await ev(p,"document.querySelector('.jc-dialog input').value='';document.querySelector('.jc-dialog textarea').value='  changed\\nbody  '");
    await key(p,'Enter',{code:'Enter',ctrl:true});await waitFor(p,"!document.querySelector('.jc-dialog')");
    assert.equal(mutations.at(-1).title,'');assert.equal(mutations.at(-1).date,'2026-10-22');assert.equal(mutations.at(-1).text,'  changed\nbody  ');checks+=3;
    await waitFor(p,"document.querySelector('.jc-entry .fp-prose')?.textContent.includes('changed') && !!document.querySelector('[data-act=add]')");
    await ev(p,"document.querySelector('[data-act=add]').click()");await waitFor(p,"document.querySelector('.jc-dialog input')");
    await ev(p,"document.querySelector('.jc-dialog textarea').value='Only content'");await key(p,'Enter',{code:'Enter',ctrl:true});await waitFor(p,"!document.querySelector('.jc-dialog')");
    assert.equal(mutations.at(-1).text,'Only content');assert.equal(mutations.at(-1).title,'');checks+=2;
   }
   check(p.problems.length===0,p.problems.join('\n'));
  } finally {await p.dispose();}
 }
 writeFileSync(join(out,'browser.json'),JSON.stringify({checks,comparisons,physical:false},null,2));
 check(comparisons.every(x=>x.equal),'all non-Notebook screenshots byte-identical');
 if(!process.env.P474_PUBLIC_DIR){const rec=parityRecorder('web/test/p474.mjs');rec.record('P474 content-only clipboard and scoped layout',true);rec.write();}
 console.log(`P474 PASS: ${checks} assertions, ${comparisons.length} unchanged-page screenshots`);
} finally {await B.close();await fake.stop();await before.stop();await after.stop();rmSync(baseline,{recursive:true,force:true});}
