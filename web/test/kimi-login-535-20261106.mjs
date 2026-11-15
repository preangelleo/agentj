// Standalone recovery: deadline/Kimi link-only only; no API-key path or real accounts.
// #535 real client + local encrypted fake host. No customer account or secret is loaded.
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const whyByLang=JSON.parse(execFileSync('python3',['-c',"import ast,json; m=ast.parse(open('host/agentj/auth_rescue.py').read()); d=next(ast.literal_eval(n.value) for n in m.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='WHY' for t in n.targets)); print(json.dumps({k:v['no_code_kimi'] for k,v in d.items()}))"],{encoding:'utf8'}));
const before=false, out=process.env.P535_OUT||process.env.OLARES_RUN_OUT||'reports/qa/535-kimi/web';
mkdirSync(out,{recursive:true});
let checks=0;
const web=await startWebServer(process.env.P535_PUBLIC ? {publicDir:process.env.P535_PUBLIC} : {}),fake=await startFakeHost(),B=await launch();
const view={dot:true,flow:null,harnesses:['claude','codex','opencode','kimi'].map(kind=>({kind,label:{claude:'Claude Code',codex:'Codex',opencode:'OpenCode',kimi:'Kimi Code'}[kind],installed:true,current:kind==='kimi',state:'checking',key_name:{kimi:'KIMI_API_KEY',claude:'ANTHROPIC_API_KEY',codex:'OPENAI_API_KEY',opencode:'OPENAI_API_KEY'}[kind],key_set:false,routes:['api_key']}))};
fake.st.onApp=async(c,m,send)=>{if(m.t!=='auth_get')return false;await send({t:'auth_res',r:m.r,ok:true,view});return true;};
console.log(`preview=${web.url} source=${process.env.P535_PUBLIC || process.cwd()} (local fake host)`);
try{
for(const lang of ['zh','en'])for(const width of [390,1440])for(const theme of ['dark','light']){
 const p=await newPage(B,width,1000,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
 try{
 await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await evaluate(p,"if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click();document.getElementById('setBtn').click()");
 await waitFor(p,"!document.getElementById('settings').hidden");await evaluate(p,"document.getElementById('set-auth-nav').click()");
 await waitFor(p,"document.querySelectorAll('#ar-list .archip').length===4");
 if(!before){await evaluate(p,'{const now=Date.now;Date.now=()=>now()+91000;}');await fake.send({t:'auth_rescue',view});
 await waitFor(p,"[...document.querySelectorAll('#ar-list .archip')].every(x=>!x.textContent.includes('…'))");
 assert.equal(await evaluate(p,"[...document.querySelectorAll('#ar-list .setrow')].filter(row=>row.querySelector('button')).length"),4);
 }
 await evaluate(p,"document.getElementById('ar-list').scrollIntoView({block:'start'})");await sleep(120);
 const name=`535-Kimi登录救援-${before?'改前':'改后'}-${lang}-${width}-${theme}.png`;
 writeFileSync(resolve(out,name),await shoot(p,''));
 await evaluate(p,"document.querySelector('#ar-list [data-kind=kimi]').scrollIntoView({block:'center'})");
 writeFileSync(resolve(out,name.replace('.png','-Kimi行.png')),await shoot(p,''));
 const why=before ? 'Kimi Code did not print a device code (kimi too old, or kimi.com / kimi.ai unreachable)' : whyByLang[lang];
 // #456 current contract: ended flows disappear; the harness row retains the host failure.
 await fake.send({t:'auth_rescue',view:{...view,harnesses:view.harnesses.map(h=>h.kind==='kimi'?{...h,last:{result:'failed',why}}:h),flow:{kind:'kimi',route:'device',stage:'failed',why}}});
 await waitFor(p,`document.querySelector('#ar-list [data-kind=kimi]').textContent.includes(${JSON.stringify(why)}) && document.getElementById('ar-flow').hidden`);checks++;
 await evaluate(p,"document.querySelector('#ar-list [data-kind=kimi]').scrollIntoView({block:'center'})");
 writeFileSync(resolve(out,name.replace('.png','-Kimi错误说明.png')),await shoot(p,''));
 if(!before){
 await fake.send({t:'auth_rescue',view:{...view,flow:{kind:'kimi',route:'device',stage:'device',url:'https://www.kimi.com/code/authorize',code:'',expires_at:Math.floor(Date.now()/1000)+600}}});
 await waitFor(p,"!!document.querySelector('#ar-flow a.arlink')");
 assert.equal(await evaluate(p,"document.querySelectorAll('#ar-flow .arcode,#ar-flow input,#ar-flow textarea').length"),0);checks++;
 assert.equal(await evaluate(p,"document.getElementById('ar-flow').textContent.includes('ChatGPT')"),false,'Kimi flow must not instruct ChatGPT login');checks++;
 await evaluate(p,"document.getElementById('ar-flow').scrollIntoView({block:'start'})");
 writeFileSync(resolve(out,name.replace('.png','-Kimi授权链接.png')),await shoot(p,''));
 }
 // Current selfheal.AGENTS deliberately has no Kimi chat login. Settings repair must not open that route.
 const loginCount=fake.log.filter(m=>m.t==='auth_rescue').length;
 await evaluate(p,"import('./js/loginsheet.js').then(m=>m.open('kimi'))");
 assert.equal(await evaluate(p,"!document.getElementById('lgs') || document.getElementById('lgs').hidden"),true);checks++;
 assert.equal(fake.log.filter(m=>m.t==='auth_rescue').length,loginCount,'unsupported Kimi chat sends nothing');checks++;
 assert.equal(fake.log.some(m=>m.t==='auth_start'&&m.route==='api_key'),false,'old PrimaryKey extension not enabled');checks++;
 assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);checks+=2;console.log('PASS '+name);
 }catch(error){writeFileSync(resolve(out,`FAIL-${lang}-${width}-${theme}.png`),await shoot(p,''));throw error;}finally{await p.dispose();}
}
console.log('P535 standalone browser PASS '+checks+' checks; no PrimaryKey extension');
}finally{await B.close();await fake.stop();await web.stop();}
