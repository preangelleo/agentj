// Actual settings DOM. No write or UV request is made; state is a synthetic paired Host.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,evaluate,waitState,waitFor,shoot,sleep,key} from './browser.mjs';
const before=!!process.env.P0280_SETTINGS_PUBLIC,out=`reports/qa/p0280-settings/${before?'before':'after'}/`;mkdirSync(out,{recursive:true});
const web=await startWebServer(before?{publicDir:process.env.P0280_SETTINGS_PUBLIC}:{}),fake=await startFakeHost(),B=await launch(),results=[];
try{for(const width of [390,834,1440])for(const theme of ['light','dark']){
 const lang=theme==='light'?'zh':'en';fake.reset();fake.st.prefs={appearance:{language:lang,theme}};
 fake.st.menu={kind:'codex',source:'synthetic',native:[{cmd:'/review',state:'run',desc:'Synthetic supported command'},{cmd:'/mcp',state:'agent',desc:'Synthetic delegated command'},{cmd:'/permissions',state:'none',desc:'Synthetic unavailable command'}],items:[],skills:[],cmds:[]};
 const writes=[];fake.st.onApp=async(c,m,send)=>{
  if(m.t==='owner_hosts_get'){await send({t:'owner_hosts_state',r:m.r,valid:true,rev:'synthetic',scope:{channel:fake.channel,host:'synthetic',seat:'synthetic'},rows:[],candidates:[]});return true;}
  if(m.t==='owner_hosts_set'){writes.push(m);return true;}return false;
 };
 const p=await newPage(B,width,900,theme,{allow:[web.url,fake.relay+'/']});
 try{
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await evaluate(p,`document.getElementById('phone-env-dismiss')?.click()`);
  assert.equal(await evaluate(p,`document.getElementById('owner-hosts').hidden`),true,'old Host has no entry');
  await fake.send({t:'preferences',value:fake.st.prefs,host:{version:'0.28.0',owner_hosts:1,settable:[]}});
  await waitFor(p,`!document.getElementById('owner-hosts').hidden`);await evaluate(p,`document.getElementById('setBtn').click()`);await waitFor(p,`!document.getElementById('settings').hidden`);await sleep(200);
  await evaluate(p,`document.getElementById('owner-hosts-add').click();document.getElementById('owner-hosts').scrollIntoView({block:'start'})`);await sleep(100);
  const geometry=await evaluate(p,`(()=>{const root=document.getElementById('owner-hosts'),r=root.getBoundingClientRect();return {display:getComputedStyle(root).display,columns:getComputedStyle(root).gridTemplateColumns,left:r.left,right:r.right,width:r.width,fields:[...root.querySelectorAll('input,button')].filter(n=>n.getClientRects().length).map(n=>{const b=n.getBoundingClientRect();return {id:n.id,w:b.width,h:b.height,left:b.left,right:b.right}})}})()`);
  if(!before){assert.ok(geometry.left>=0&&geometry.right<=width);for(const f of geometry.fields){assert.ok(f.left>=geometry.left-1&&f.right<=geometry.right+1,f.id+' stays inside owner section');assert.ok(f.w>=44&&f.h>=44,f.id+' target >=44');}}
  writeFileSync(out+`${width}-${theme}-owner.png`,await shoot(p));
  await evaluate(p,`document.querySelector('[data-set-jump="set-s-rescue"]')?.click();document.getElementById('harness-commands').scrollIntoView({block:'center'});document.querySelector('#harness-commands summary')?.click()`);await sleep(100);
  await waitFor(p,`document.querySelectorAll('#harness-commands button').length===3`);
  const commands=await evaluate(p,`[...document.querySelectorAll('#harness-commands button')].map(b=>({text:b.textContent,disabled:b.disabled,h:b.getBoundingClientRect().height}))`);
  if(!before)for(const c of commands)assert.ok(c.h>=44);
  assert.equal(commands.at(-1).disabled,true);assert.equal(writes.length,0);writeFileSync(out+`${width}-${theme}-commands.png`,await shoot(p));
  results.push({width,theme,lang,geometry,commands});console.log('PASS',width,theme);
  await key(p,'Escape');
 }finally{await p.dispose();}
}}finally{writeFileSync(out+'results.json',JSON.stringify(results,null,2));await B.close();await fake.stop();await web.stop();}
