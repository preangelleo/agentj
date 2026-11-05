// P460: historical sheet geometry and unchanged P458 execution cards, isolated encrypted fixtures.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,mkdtempSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const out='reports/qa/p460';mkdirSync(out,{recursive:true});
const ref=mkdtempSync(tmpdir()+'/p460-ref-');
for(const [name,rev] of [['original','3ca92f9b4^'],['live','e96cc19ae']]){
 mkdirSync(ref+'/'+name);execFileSync('tar',['-x','-C',ref+'/'+name],{input:execFileSync('git',['archive',rev,'web/public'],{maxBuffer:40e6})});
}
const B=await launch(),fake=await startFakeHost(),results=[],failures=[];
try{
 for(const lang of ['zh','en'])for(const theme of ['light','dark']){
  const key=lang+'-'+theme, measured={};
  for(const source of ['original','live','after']){
   const web=await startWebServer(source==='after'?{}:{publicDir:ref+'/'+source+'/web/public'});
   fake.reset();fake.setUp(true);const p=await newPage(B,390,844,theme,{touch:true,allow:[web.url,fake.relay+'/']});
   try{
    await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
    await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.documentElement.dataset.theme='${theme}'`);
    await fake.addTurn({k:'phone',text:'Check backup'},'Checking backup.');await waitFor(p,`document.getElementById('pg').textContent==='1 / 1'`);
    measured[source]={};
    const capture=async(name,id)=>{
     await sleep(650);
     // Screenshot the transition endpoint on every reference, independent of headless frame scheduling.
     await p.send('Page.bringToFront');
     await evaluate(p,`(()=>{const n=document.getElementById('${id}');getComputedStyle(n).transform;n.getAnimations().forEach(a=>a.finish());})()`);
     await waitFor(p,`new DOMMatrixReadOnly(getComputedStyle(document.getElementById('${id}')).transform).isIdentity`);
     measured[source][name]=await evaluate(p,`(()=>{const n=document.getElementById('${id}'),r=n.getBoundingClientRect(),c=getComputedStyle(n);return {x:r.x,y:r.y,w:r.width,h:r.height,bg:c.backgroundColor,color:c.color,radius:c.borderRadius,position:c.position,inReply:!!n.closest('#reply-notices')}})()`);
     writeFileSync(out+'/'+key+'-'+source+'-'+name+'.png',await shoot(p));
    };
    if(source!=='live')for(const [i,name,count] of [[1,'two',2],[2,'four',4],[3,'long',2]]){
     const id=String(i).repeat(32),zh=lang==='zh';
     const desc=name==='long'?(zh?'运行时保持电脑唤醒，合盖后仍继续工作。请根据电源和使用情况选择合适的设置。':'Keep the computer awake while working, including when the lid is closed. Choose the setting that suits your power and working conditions. ').repeat(5):'';
     await fake.send({t:'status',s:'waiting',kind:'question',agent:'codex',name:'Agent J'});
     await fake.send({t:'question',id,qs:[{q:zh?'防休眠':'Keep awake',h:'',m:false,o:Array.from({length:count},(_,j)=>({l:zh?['是，合盖也不睡','是，但合盖照常睡','仅使用电源时','关闭'][j]:['Yes, even with lid closed','Yes, sleep with lid closed','Only on external power','Off'][j],d:desc}))}],ttl:180});
     await waitFor(p,`document.body.dataset.sheet==='1'&&document.querySelectorAll('#qs .opt').length===${count}`);
     assert.equal(await evaluate(p,`!!document.querySelector('#reply-notices .sheet')`),false);
     await evaluate(p,`import('./js/ui.js').then(m=>m.toastOff())`);
     await capture(name,'sheet');
     if(name==='long'){
      assert.ok(await evaluate(p,`!!document.querySelector('.qmore:not([hidden])')`));
      await evaluate(p,`document.querySelector('.qmore:not([hidden])').click()`);
      assert.ok(await evaluate(p,`!!document.querySelector('#qs .d.open')`));
     }else{
      await evaluate(p,`document.querySelector('#qs .opt').click();document.getElementById('askSend').click()`);
      await waitFor(p,`document.getElementById('askSend').disabled`);
     }
     await fake.send({t:'question_done',id,result:'cancelled'});await evaluate(p,`document.getElementById('sheetClose').click()`);
    }
    if(source!=='original'){
     await fake.send({t:'status',s:'idle',agent:'codex',name:'Agent J'});
     await evaluate(p,`import('./js/ui.js').then(m=>m.executionToast('Command completed',60000))`);await capture('terminal','toast');
     await evaluate(p,`import('./js/ui.js').then(m=>m.toastOff())`);
     await fake.send({t:'elev',id:'a'.repeat(32),kind:'sudo',n:'b'.repeat(32),epk:'c'.repeat(43),ttl:120,tries:3,cmd:'systemctl status backup',why:'Check backup',effect:'Read status',state:'running',phase:'started',elapsed:2,timeout:600});
     await waitFor(p,`document.getElementById('elev-run')?.hidden===false`);await capture('sudo','elev-run');
    }
   }finally{await p.dispose();await web.stop();}
  }
  for(const name of ['two','four','long','terminal','sudo']){
   const baseline=['terminal','sudo'].includes(name)?'live':'original';const a=measured[baseline][name],b=measured.after[name];
   // Geometry includes placement, dimensions, palette and the clipping owner.
   const properties=['x','y','w','h','bg','color','radius','position','inReply'];
   const differences=properties.filter(k=>typeof a[k]==='number'?Math.abs(a[k]-b[k])>1:a[k]!==b[k]);
   const row={key,name,baseline,before:a,after:b,differences,pass:!differences.length};results.push(row);if(!row.pass)failures.push(row);
  }
 }
}finally{await B.close();await fake.stop();}
const combined=JSON.parse(await import('node:fs').then(m=>m.readFileSync('web/public/version.json','utf8'))).combined;
writeFileSync(out+'/results.json',JSON.stringify({combined,results,failures},null,2)+'\n');
assert.deepEqual(failures,[]);console.log('PASS P460: 20 historical sheet / P458 B-card comparisons; selections and expand work');
