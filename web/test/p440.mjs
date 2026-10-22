// P440: identical before/after fixture, local encrypted fake host; no real sudo / credentials.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {generateKeyPairSync} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot} from './browser.mjs';
const before=process.env.P440_BEFORE, out=`reports/qa/p440/${before?'before':'after'}`;
mkdirSync(out,{recursive:true});
const web=await startWebServer(before?{publicDir:before}:{}), fake=await startFakeHost(), B=await launch(), checks=[];
try {
 for(const width of [390,1440]) for(const theme of ['light','dark']) for(const lang of ['zh','en']) {
  fake.reset(); fake.setUp(true);
  const p=await newPage(B,width,width===390?844:900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
  const tag=`${lang}-${theme}-${width}`;
  try {
   await navigate(p,fake.newPairing(web.url+'?lang='+lang)); await waitState(p,'awaiting-approval'); await fake.approve(); await waitState(p,'ready');
   await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click();document.documentElement.dataset.theme='${theme}'`);
   const dev=await evaluate(p,`import('./js/api.js').then(m=>m.myDeviceId())`);
   await fake.addTurn({k:'phone',dev,text:lang==='zh'?'检查备份任务':'Check the backup task'},lang==='zh'?'正在检查备份任务。执行状态和输出会显示在下方。':'Checking the backup task. Execution status and output appear below.');
   await waitFor(p,`document.getElementById('pg').textContent==='1 / 1'`);
   const screenshot=async(name)=>writeFileSync(`${out}/${tag}-${name}.png`,await shoot(p,''));
   const geometry=async(id)=>evaluate(p,`(()=>{const n=document.getElementById('${id}'),r=n.getBoundingClientRect(),main=document.querySelector('.rm main'),m=main.getBoundingClientRect(),cs=getComputedStyle(main),slot=document.getElementById('reply-notices'),rm=document.getElementById('rm').getBoundingClientRect(),bar=document.getElementById('rmbar').getBoundingClientRect(),om=document.getElementById('om').getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,w:r.width,h:r.height,expectedLeft:m.left+parseFloat(cs.paddingLeft),expectedRight:m.right-parseFloat(cs.paddingRight),inside:slot?.contains(n),bg:getComputedStyle(n).backgroundColor,radius:getComputedStyle(n).borderRadius,barTop:bar.top,rmTop:rm.top,rmBottom:rm.bottom,omBottom:om.bottom,viewport:innerHeight};})()`);
   const check=async(id,name)=>{const g=await geometry(id);if(!before){assert.equal(g.inside,true,`${tag} ${name} inside reply`);assert.ok(Math.abs(g.left-g.expectedLeft)<1.5,JSON.stringify(g));assert.ok(Math.abs(g.right-g.expectedRight)<1.5,JSON.stringify(g));assert.ok(g.bottom<=g.barTop+1,`${name} above footer ${JSON.stringify(g)}`);assert.ok(g.top>=g.rmTop,`${name} within reply`);assert.ok(g.h>0);assert.ok(g.rmBottom<=g.viewport);assert.equal(g.bg,'rgb(44, 43, 38)');}checks.push({tag,name,...g});};
   const id='a'.repeat(32), n='b'.repeat(32), epk=generateKeyPairSync('x25519').publicKey.export({format:'jwk'}).x;
   await fake.send({t:'elev',id,kind:'sudo',n,epk,ttl:120,tries:3,cmd:'systemctl status backup',why:'Check backup status',effect:'Read status',state:'running',phase:'started',elapsed:2,timeout:600});
   await waitFor(p,`document.getElementById('elev-run')?.hidden===false`);
   await check('elev-run','collapsed');await screenshot('sudo-collapsed');
   await evaluate(p,`document.getElementById('elev-run-main').click()`);
   await fake.send({t:'elev_out',id,seq:1,text:'Backup checked\n'+('Safe fixture output\n'.repeat(25))});
   await waitFor(p,`document.getElementById('elev-run-panel').hidden===false`);
   await check('elev-run','expanded');await screenshot('sudo-expanded');
   await fake.send({t:'elev_done',id,result:'done',code:0,tail:'Backup checked\nAll files present\n'});
   await waitFor(p,`document.getElementById('elev-run').dataset.state==='done'`);
   await check('elev-run','done');await screenshot('sudo-done');
   await evaluate(p,`document.getElementById('elev-run-x').click();import('./js/ui.js').then(m=>m.toastOff())`);
   await fake.send({t:'auto',tool:'Bash',summary:'systemctl status backup',by:'jev'});
   // Invoke the real handler too: old hosts use differing message names; this is a UI fixture.
   await evaluate(p,`import('./js/controls.js').then(m=>m.showAuto({tool:'Bash',summary:'systemctl status backup',by:'jev'}))`);
   await waitFor(p,`document.getElementById('toast').classList.contains('on')`);
   if(!before)await check('toast','auto-approval');await screenshot('auto-approval');
   await evaluate(p,`import('./js/ui.js').then(m=>m.notify({kind:'confirm',text:'Command completed',actions:[{label:'Undo',fn(){}}],ms:60000}))`);
   if(!before)await check('toast','action-result');await screenshot('action-result');
   await evaluate(p,`import('./js/ui.js').then(m=>m.notify({kind:'success',shape:'circle',text:'${lang==='zh'?'已复制':'Copied'}',ms:60000}))`);
   if(!before){const g=await geometry('toast');assert.ok(Math.abs(g.w-g.h)<1);assert.ok(Math.abs((g.top+g.bottom)/2-g.viewport/2)<1);}
   await screenshot('copied-circle');
   if(!before){
    await evaluate(p,`import('./js/ui.js').then(m=>m.toastOff())`);
    for(const nid of ['setup-banner','ar-renew','qr-banner','rescue-banner','shared-status','version-later','estop-banner','grant-bar','share-notice']){
     await evaluate(p,`(()=>{const n=document.getElementById('${nid}');n.hidden=false;if(!n.textContent.trim())n.textContent='System status';})()`);
     await check(nid,nid);await screenshot(nid);
     await evaluate(p,`document.getElementById('${nid}').hidden=true`);
    }
    await evaluate(p,`import('./js/elevate.js').then(m=>m.grants([{g:'c'.repeat(16),exe:'systemctl',left:120}]))`);
    await check('elev-grant','sudo-grant');await screenshot('sudo-grant');
    // Actual Settings page: long errors/actions remain visible inside the page, never behind it.
    await evaluate(p, `document.getElementById('setBtn').click()`);
    await waitFor(p, `!document.getElementById('settings').hidden`);
    await evaluate(p, `import('./js/ui.js').then(m=>m.notify({kind:'error',text:'A local result requiring attention',ms:60000}))`);
    assert.equal(await evaluate(p, `document.getElementById('settings').contains(document.getElementById('toast'))`),true);
    assert.ok(await evaluate(p, `(()=>{const r=document.getElementById('toast').getBoundingClientRect();return r.height>0&&r.top>=0&&r.bottom<=innerHeight})()`));
    await screenshot('settings-result');
    await evaluate(p, `import('./js/settings.js').then(m=>m.close());import('./js/friends.js').then(m=>m.open())`);
    await evaluate(p, `import('./js/ui.js').then(m=>m.notify({kind:'error',text:'A friend page result requiring attention',ms:60000}))`);
    assert.equal(await evaluate(p, `document.getElementById('friends-view').contains(document.getElementById('toast'))`),true);
    assert.ok(await evaluate(p, `(()=>{const r=document.getElementById('toast').getBoundingClientRect();return r.height>0&&r.top>=0&&r.bottom<=innerHeight})()`));
    await screenshot('friends-result');
    await evaluate(p, `import('./js/controls.js').then(m=>m.openPanel('chat'));import('./js/ui.js').then(m=>m.toastOff())`);
    await evaluate(p, `import('./js/relay.js').then(m=>m.remoteReader(true))`);
    await waitFor(p, `document.body.dataset.reader==='1'`);
    for(const selector of ['html','#satband','#sabband']) assert.equal(await evaluate(p, `getComputedStyle(document.querySelector('${selector}')).backgroundColor`),'rgb(20, 20, 20)',`${tag} reader safe area ${selector}`);
    await screenshot('reader-safe-areas');
    await evaluate(p, `import('./js/settings.js').then(m=>m.open())`);
    const askId='c'.repeat(32);
    await fake.ask({t:'ask',id:askId,tool:'Bash',summary:'echo safe-fixture',ttl:120,cat:['read']});
    await waitFor(p, `document.body.dataset.sheet==='1'&&document.getElementById('settings').hidden&&document.body.dataset.reader!=='1'`);
    await check('sheet','permission-inset');
    assert.equal(await evaluate(p, `getComputedStyle(document.getElementById('deck')).filter`),'none');
    await screenshot('permission-inset');
    await evaluate(p, `(()=>{const s=document.getElementById('sheet'),b=document.getElementById('apprAllow').getBoundingClientRect(),r=s.getBoundingClientRect();s.scrollTop+=Math.max(0,b.bottom-r.bottom+8)})()`);
    assert.ok(await evaluate(p, `(()=>{const b=document.getElementById('apprAllow').getBoundingClientRect(),s=document.getElementById('sheet').getBoundingClientRect();return b.height>0&&b.top>=s.top&&b.bottom<=s.bottom+1})()`),`${tag} approval action reachable in card`);
    await screenshot('permission-actions');

    await fake.send({t:'ask_done',id:askId,result:'denied'});



   }
  }finally{await p.dispose();}
 }
 if(!before){
  const p=await newPage(B,390,844,'light',{allow:[web.url]});
  try{
   await navigate(p,web.url+'?from=share&lang=en');
   await waitFor(p,`!document.getElementById('share-notice').hidden`);
   assert.ok(await evaluate(p,`(()=>{const n=document.getElementById('share-notice'),r=n.getBoundingClientRect();return document.getElementById('pair-view').contains(n)&&r.height>0&&r.top>=0&&r.bottom<=innerHeight})()`));
   writeFileSync(out+'/unpaired-share.png',await shoot(p,''));
  }finally{await p.dispose();}
 }
}finally{await B.close();await fake.stop();await web.stop();}
writeFileSync(out+'/checks.json',JSON.stringify({passed:checks.length,checks},null,2)+'\n');
console.log(`PASS P440 ${before?'before':'after'}: ${checks.length} measured placements`);
