import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,shoot} from './browser.mjs';
const out='reports/qa/p478';mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
const checks=[],calls=[];
try {
 for(const width of [390,1440])for(const theme of ['light','dark'])for(const lang of ['zh','en']) {
  fake.reset();fake.setUp(true);let grants=[];
  const handler=fake.st.onApp=async(c,m,send)=>{
   if(m.t==='files_get') {await send({t:'files_state',r:m.r,workdir:'/workspace/example/coding',mac:true,grants,
     tcc:{folders:{Downloads:'readable',Desktop:'blocked_or_unknown',Documents:'readable'},automation:'available'}});return true;}
   if(m.t==='files_write') {
    const target={op:m.op,path:m.path,expires:m.expires};const ok=await fake.verifyControl(c,'files_write',m,target);
    calls.push({...target,ok});assert.ok(ok,'real paired-device signature');
    grants=m.op==='add'?[...grants.filter(g=>g.path!==m.path),{path:m.path,expires:m.expires}]:grants.filter(g=>g.path!==m.path);
    await send({t:'ctl_res',r:m.r,ok});return true;
   }
   return false;
  };
  const p=await newPage(B,width,width===390?844:1000,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
  try {
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.documentElement.dataset.theme='${theme}';import('./js/settings.js').then(m=>m.open())`);
   await waitFor(p,`!document.getElementById('settings').hidden`);
   await evaluate(p,`document.getElementById('set-local-files-nav').click()`);
   await waitFor(p,`!document.getElementById('set-local-files').hidden&&document.getElementById('local-files-list').textContent.includes('/workspace/example/coding')`);
   // Offline/older hosts return an envelope, not null: never paint a false empty scope state.
   fake.st.onApp=async(c,m,send)=>{if(m.t==='files_get'){await send({t:'offline',r:m.r});return true;}return false;};
   await evaluate(p,`import('./js/local-files.js').then(m=>m.load())`);
   await waitFor(p,`document.getElementById('local-files-list').textContent.includes(${JSON.stringify(lang==='zh'?"Agent J 主机未连接，请连接后重试。":"Agent J host offline. Connect and try again.")})`);
   assert.equal(await evaluate(p,`document.querySelectorAll('#local-files-list button').length`),0);
   fake.st.onApp=handler;
   await evaluate(p,`import('./js/local-files.js').then(m=>m.load())`);
   await waitFor(p,`document.getElementById('local-files-list').textContent.includes('Downloads')`);
   const text=await evaluate(p,`document.getElementById('local-files-list').textContent`);
   assert.ok(text.includes('Downloads'));assert.ok(text.includes('Finder'));
   for(const duration of ['hour','always']) {
    await evaluate(p,`document.getElementById('local-files-path').value='/workspace/example/Downloads';document.getElementById('local-files-duration').value='${duration}';document.getElementById('local-files-add').click()`);
    await waitFor(p,`!document.getElementById('confirm').hidden`);
    assert.ok((await evaluate(p,`document.getElementById('confirm-text').textContent`)).includes('/workspace/example/Downloads'));
    if(duration==='hour') { // cancelling a folder addition cannot send a signed write
     const before=calls.length;await evaluate(p,`document.getElementById('confirm-no').click()`);assert.equal(calls.length,before);
     await evaluate(p,`document.getElementById('local-files-add').click()`);await waitFor(p,`!document.getElementById('confirm').hidden`);
    }
    await evaluate(p,`document.getElementById('confirm-yes').click()`);
    await waitFor(p,`document.querySelectorAll('#local-files-list button').length===1`);
    assert.equal(grants.length,1);assert.equal(grants[0].expires===0,duration==='always');
    if(duration==='always')break;
    await evaluate(p,`document.querySelector('#local-files-list button').click()`);
    await waitFor(p,`document.querySelectorAll('#local-files-list button').length===0`);assert.equal(grants.length,0);
   }
   const geom=await evaluate(p,`(()=>{const e=document.getElementById('set-local-files');return {overflow:e.scrollWidth>e.clientWidth+1,button:document.getElementById('local-files-add').getBoundingClientRect().height}})()`);
   assert.equal(geom.overflow,false);assert.ok(geom.button>=44,JSON.stringify(geom));
   writeFileSync(`${out}/${lang}-${theme}-${width}.png`, await shoot(p,`${out}/${lang}-${theme}-${width}.png`));
   await evaluate(p,`document.getElementById('set-back').click()`);
   await waitFor(p,`document.getElementById('set-local-files').hidden`);
   checks.push({width,theme,lang,ok:true});
  }finally{await p.dispose();}
 }
}finally{await B.close();await fake.stop();await web.stop();}
writeFileSync(`${out}/phone.json`,JSON.stringify({checks,calls},null,2));
console.log('P478 phone: '+checks.length+' locale/theme/viewport cases, signed add/remove, cancel, expiry, cached TCC, no overflow');
