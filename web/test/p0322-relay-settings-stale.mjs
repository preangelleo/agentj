// An actual delayed Noise reply cannot repopulate a closed key component.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {randomBytes} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,tap,sleep,shoot} from './browser.mjs';
const web=await startWebServer(),fake=await startFakeHost(),B=await launch(),out=process.env.OLARES_RUN_OUT||'reports/qa/p0322-relay-settings';mkdirSync(out,{recursive:true});
const names=['AGENTSRELAY_CLAUDE_KEY','AGENTSRELAY_OPENAI_KEY'],hex=()=>randomBytes(16).toString('hex');let delayed=null,reads=0;const calls=[];
function snapshot(r,swap){return{t:'keys',r,id:hex(),n:hex(),base:[{id:'agentsrelay',name:{en:'Agents Relay',zh:'Agents Relay'},rows:names.map((name,i)=>({type:'key',name,set:(i===0)!==swap}))}],third:[]};}
fake.st.onApp=async(c,m,send)=>{
 if(m.t==='keys_get'){reads++;if(reads===1)delayed={m,send};else await send(snapshot(m.r,false));return true;}
 if(m.t==='auth_get'){await send({t:'auth_res',r:m.r,ok:true,view:{harnesses:[],flow:null}});return true;}
 if(m.t==='acct_get'){await send({t:'acct_res',r:m.r,ok:true,view:{claude:{items:[]},codex:{items:[]}}});return true;}
 if(m.t==='keys_set'){calls.push(m.name);await send({t:'keys_res',r:m.r,ok:false,why:'passkey'});return true;}return false;
};
const p=await newPage(B,390,900,'dark',{touch:true,allow:[web.url,fake.relay+'/']});
async function click(sel){const point=await evaluate(p,`(()=>{const e=document.querySelector(${JSON.stringify(sel)});e.scrollIntoView({block:'center',behavior:'instant'});const r=e.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);await tap(p,point.x,point.y);}
const state=()=>evaluate(p,`[...document.querySelectorAll('#acct-relay-keys .kstate')].map(e=>e.dataset.on)`);
try{
 await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await evaluate(p,`for(const id of ['confirm-no','a2hs-ok','phone-env-dismiss']){const e=document.getElementById(id);if(e&&e.offsetParent)e.click();}`);
 await click('#setBtn');await click('#set-auth-nav');
 for(let i=0;i<100&&!delayed;i++)await sleep(20);assert.ok(delayed,'the original host request really waits');
 await click('#set-back');await click('#set-accts-nav');await waitFor(p,`document.querySelectorAll('#acct-relay-keys .krow').length===2`);assert.deepEqual(await state(),['1','0']);
 await delayed.send(snapshot(delayed.m.r,true));await sleep(150);assert.deepEqual(await state(),['1','0'],'the old opposite state cannot overwrite the current component');
 assert.equal(await evaluate(p,`document.getElementById('set-accts').hidden`),false);assert.equal(await evaluate(p,`document.getElementById('set-auth').hidden`),true);
 await click('#acct-relay-keys [data-name="AGENTSRELAY_OPENAI_KEY"] [data-op="set"]');for(let i=0;i<100&&!calls.length;i++)await sleep(20);assert.deepEqual(calls,[names[1]],'the current Codex row still requests its own sealed card');
 writeFileSync(`${out}/stale-settings.png`,await shoot(p));assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);
 console.log('PASS delayed real Noise list reply after auth close cannot overwrite backup keys; current action stays on its exact name');
}finally{writeFileSync(`${out}/stale-result.json`,JSON.stringify({reads,calls,physical:false},null,2));await p.dispose();await B.close();await fake.stop();await web.stop();}
