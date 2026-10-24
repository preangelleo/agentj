// P499: compare the shipped 0.25.5a1 base and candidate at identical viewports/locales.
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,writeFileSync,rmSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,shoot} from './browser.mjs';
const out='reports/qa/p499/compare',tmp=mkdtempSync(join(tmpdir(),'p499-base-'));mkdirSync(out,{recursive:true});
execFileSync('tar',['-x','-C',tmp],{input:execFileSync('git',['archive','d3ca5aed3','web/public'],{maxBuffer:32*1024*1024})});
const B=await launch(),fake=await startFakeHost(),rows=[];
try{
 for(const side of ['base','candidate']){
 const web=await startWebServer(side==='base'?{publicDir:join(tmp,'web/public')}:{});
 try{for(const width of [390,1440])for(const lang of ['zh','en'])for(const theme of ['light','dark']){
 fake.reset();fake.setUp(true);const p=await newPage(B,width,width===390?844:1000,theme,{allow:[web.url,fake.relay+'/']});
 try{
 await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.documentElement.dataset.theme='${theme}'`);
 await evaluate(p,`document.fonts.ready`);
 for(const screen of ['chat','settings']){
 if(screen==='settings'){await evaluate(p,`import('./js/settings.js').then(m=>m.open())`);await waitFor(p,`!document.getElementById('settings').hidden`);}
 const r=await evaluate(p,`(()=>{const e=document.getElementById('${screen}'==='settings'?'settings':'words');return {overflow:e.scrollWidth>e.clientWidth+1,localNav:!!document.getElementById('set-local-files-nav')}})()`);
 assert.equal(r.overflow,false);assert.equal(r.localNav,side==='candidate');
 writeFileSync(`${out}/${side}-${screen}-${lang}-${theme}-${width}.png`,await shoot(p));rows.push({side,screen,lang,theme,width,...r});
 }
 }finally{await p.dispose();}
 }}finally{await web.stop();}
 }
}finally{await fake.stop();await B.close();rmSync(tmp,{recursive:true,force:true});}
writeFileSync(out+'/comparison.json',JSON.stringify({base:'d3ca5aed3 / 0.25.5a1',rows},null,2));console.log('PASS 32 baseline/candidate screen captures, no overflow; new Local file access entry only in candidate');
