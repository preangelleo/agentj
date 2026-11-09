import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,evaluate,sleep,shoot} from './browser.mjs';
const ev=(p,s)=>evaluate(p,s.includes('await ')?'(async()=>{'+s+'})()':s);
const out=process.env.P0287_OUT||fileURLToPath(new URL('../../../.jarvis/out/0287-batch2',import.meta.url));mkdirSync(out,{recursive:true});
const phase=process.env.P0287_PHASE||'after',web=await startWebServer(process.env.P0287_PUBLIC?{publicDir:process.env.P0287_PUBLIC}:{}),fake=await startFakeHost(),B=await launch(),rows=[];
try{for(const width of [390,1440])for(const theme of ['light','dark']){
const p=await newPage(B,width,900,theme,{allow:[web.url,fake.relay+'/']});
try{
await navigate(p,fake.newPairing(web.url+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await ev(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('input').blur()`);
await ev(p,`document.getElementById('setBtn').click();const k=await import('./js/pkpeer.js');k.configure({supported:async()=>true,ask:async()=>({t:'pk_peer_res',ok:true,mode:'peer',id:'Abcdefghijklmnopqrstuv',code:'482731',exp:Date.now()/1000+300,online:1})});await k.startPeerSetup()`);await sleep(300);
let m=await ev(p,`(()=>{let n=document.querySelector('#pkp'),b=n.querySelector('.box'),r=b.getBoundingClientRect(),c=getComputedStyle(b);return {system:n.dataset.popupSystem,bg:c.backgroundColor,ink:c.color,center:r.y+r.height/2,visible:!n.hidden&&getComputedStyle(n).visibility!=='hidden',code:n.querySelector('.pkp-code-v')?.textContent,topOwned:!!document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.closest('#pkp')}})()`);rows.push({width,theme,scenario:'settings-reset',...m});if(phase==='after'){assert.equal(m.visible,true);assert.equal(m.topOwned,true);assert.equal(m.code,'482731');assert.equal(m.system,'center');assert.equal(m.bg,'rgb(44, 43, 38)');}writeFileSync(out+'/'+phase+'-'+width+'-'+theme+'-settings-reset.png',await shoot(p));
await ev(p,`document.getElementById('pkp-cancel').click();document.getElementById('setBtn').click()`);
if(width===390)for(const state of ['idle','working','waiting','question','unknown'])for(const known of [false,true]){
await ev(p,`document.body.dataset.status=${JSON.stringify(state==='question'?'waiting':state)};document.body.dataset.kind=${JSON.stringify(state==='question'?'question':'')};document.body.dataset.conn='on';const w=document.getElementById('water');w.dataset.known=${JSON.stringify(known?'1':'0')};w.style.setProperty('--lvl',${JSON.stringify(known?'.65':'0')});const u=await import('./js/ui.js');u.confirmSheet('确认本次操作','请查看完整详情后再决定。','确认',false,undefined,'','grow');`);await sleep(250);
m=await ev(p,`(()=>{let w=document.getElementById('water'),q=document.getElementById('m5h'),b=document.querySelector('#confirm .box');return {waterTop:w.getBoundingClientRect().top,quotaTop:q.getBoundingClientRect().top,waterDisplay:getComputedStyle(w).display,bg:getComputedStyle(b).backgroundColor,wet:document.getElementById('confirm').style.getPropertyValue('--popup-water')}})()`);rows.push({width,theme,state,known,...m});if(phase==='after'){assert.notEqual(m.waterDisplay,'none');assert.ok(m.waterTop<=m.quotaTop+1);assert.ok(m.wet);}writeFileSync(out+'/'+phase+'-'+width+'-'+theme+'-'+state+'-'+(known?'history':'no-history')+'.png',await shoot(p));await ev(p,`(await import('./js/ui.js')).closeConfirm()`);
}
await ev(p,`document.body.dataset.status='working';const w=document.getElementById('water');w.dataset.known='1';w.style.setProperty('--lvl','0');w.dataset.compacting='1';w.style.animationDelay='-14s';`);await sleep(200);m=await ev(p,`({top:water.getBoundingClientRect().top,quota:m5h.getBoundingClientRect().top})`);rows.push({width,theme,scenario:'compact-min',...m});if(phase==='after')assert.ok(m.top<=m.quota+1);writeFileSync(out+'/'+phase+'-'+width+'-'+theme+'-compact-min.png',await shoot(p));
}finally{await p.dispose();}}
}finally{await B.close();await fake.stop();await web.stop();writeFileSync(out+'/'+phase+'-measurements.json',JSON.stringify(rows,null,2));}
console.log(phase+' '+rows.length+' visual scenarios PASS');
