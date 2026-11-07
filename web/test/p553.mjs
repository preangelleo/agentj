// #553 real client + paired local fake host: width, resize, content and resource scope.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,evaluate,waitFor,sleep,shoot,key} from './browser.mjs';
const out=process.env.P553_OUT || new URL('../../../reports/qa/load-modal-553-20261106/web/',import.meta.url).pathname;
mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch();
const results=[];
try {
for(const lang of ['zh','en']) for(const scheme of ['light','dark']) for(const w of [320,390,1440]){
 fake.reset(); const p=await newPage(B,w,w<500?844:900,scheme,{allow:[web.url,fake.relay+'/'],touch:w<500});
 try{
 await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('confirm-no')?.click();document.getElementById('a2hs-ok')?.click();`);
 await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
 fake.st.res={cpu:.2,mem:.4,gpu:{pct:.3,name:'NVIDIA GeForce RTX 3070 Ti Laptop GPU'},disk:{busy:.02,free:.85,free_gb:1700,total_gb:2000,scope:'workdir'},net:{rx:1234000,tx:250000}};
 await evaluate(p,`document.getElementById('loadHit').click()`);
 await waitFor(p,`document.querySelector('#resList [data-k=disk] small').textContent.includes('2000')`); await sleep(250);
 const a=await evaluate(p,`(()=>{const q=s=>document.querySelector(s),sq=q('#loadDlg .sq'),r=sq.getBoundingClientRect(),om=q('#om'),deck=q('#deck'),ds=getComputedStyle(deck);const expected=!om.hidden?om.getBoundingClientRect().width:deck.clientWidth-parseFloat(ds.paddingLeft)-parseFloat(ds.paddingRight)-24;return {width:r.width,expected,inside:r.left>=0&&r.right<=innerWidth,overflow:sq.scrollWidth>sq.clientWidth,gpu:q('[data-k=gpu] small').textContent,disk:q('[data-k=disk] small').textContent,button:q('#loadClose').className,buttonHeight:q('#loadClose').getBoundingClientRect().height,ellipsis:getComputedStyle(q('[data-k=gpu] small')).textOverflow};})()`);
 assert.ok(Math.abs(a.width-Math.min(a.expected,960))<=1,JSON.stringify(a));assert.equal(a.inside,true);assert.equal(a.overflow,false);assert.ok(a.buttonHeight>=44);assert.match(a.button,/aj-btn--primary/);assert.equal(a.ellipsis,'clip');assert.match(a.disk,lang==='zh'?/工作目录所在盘/:/Working directory disk/);assert.match(a.disk,/2000/);
 writeFileSync(out+lang+'-'+w+'-'+scheme+'.png',await shoot(p,''));
 await key(p,'Escape');await waitFor(p,`document.getElementById('loadDlg').hidden`);
 results.push({lang,scheme,w,...a});
 }finally{await p.dispose();}
}
writeFileSync(out+'results.json',JSON.stringify(results,null,2));console.log('P553 12 viewport/theme/language cases PASS');
}finally{await B.close();await fake.stop();await web.stop();}
