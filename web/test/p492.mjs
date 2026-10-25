import assert from 'node:assert/strict';
import {parityRecorder} from '../../parity/lib.mjs';
const rec=parityRecorder('web/test/p492.mjs');
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const out=process.env.P492_OUT||'reports/qa/p492';mkdirSync(out,{recursive:true});
const web=await startWebServer(process.env.P492_BASE ? {publicDir:process.env.P492_BASE} : {}),fake=await startFakeHost(),B=await launch();
const url='https://claude.com/cai/oauth/authorize?state=p492-fixture';
const id='a'.repeat(32), auth={kind:'claude',url,code:'',expires_at:Math.floor(Date.now()/1000)+580,round:2,rounds:3};
try {for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const width of [390,1280]){
 fake.reset();let view={harnesses:[],flow:null,dot:false}, checks=0;
 fake.st.onApp=async(c,m,send)=>{if(m.t==='auth_get'){++checks;await send({t:'auth_res',r:m.r,ok:true,view});return true;}return false;};
 const p=await newPage(B,width,844,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
 await p.send('Emulation.setEmulatedMedia',{features:[{name:'prefers-color-scheme',value:theme},{name:'prefers-reduced-motion',value:'reduce'}]});
 const save=async name=>writeFileSync(`${out}/${lang}-${theme}-${width}-${name}.png`,await shoot(p,''));
 try{
 await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.documentElement.dataset.theme='${theme}'`);
 await sleep(700);await save('chat');
 for(const state of ['idle','working','waiting']){await fake.send({t:'status',s:state,agent:'claude',name:'Agent J'});await sleep(700);await save('chat-'+state);}
 if(process.env.P492_CHAT_ONLY) continue;
 await evaluate(p,`import('./js/settings.js').then(s=>s.openAuth())`);await waitFor(p,`!document.getElementById('set-auth').hidden`);
 const row={kind:'claude',label:'Claude Code',installed:true,current:true,state:'checking',routes:[]};
 view={...view,harnesses:[row]};await fake.send({t:'auth_rescue',view});
 await waitFor(p,`!!document.querySelector('#ar-list .archip--checking')`);
 assert.equal(await evaluate(p,`document.querySelectorAll('#ar-list .arhealth').length`),0);await save('checking');
 view={...view,harnesses:[{...row,state:'ok',health:{at:Math.floor(Date.now()/1000),ok:true}}]};await fake.send({t:'auth_rescue',view});
 await waitFor(p,`!!document.querySelector('#ar-list .arhealth')`);
 const before=checks;await evaluate(p,`document.querySelector('[data-kind=claude] button').click()`);await sleep(200);assert.ok(checks>before,'Check now requests a live check');await save('health-current');
 // Both rescue routes use their own copy; Face ID alone must not select sandbox copy.
 for(const [i,provider] of ['Claude','Codex'].entries()){
 const approvalId=(i?'d':'c').repeat(32);
 await fake.send({t:'ask',id:approvalId,tool:'AuthRescue',summary:lang==='zh'?`重新登录 ${provider}`:`Sign in to ${provider} again`,ttl:600,passkey_required:true});
 await sleep(700);
 assert.equal(await evaluate(p,`document.getElementById('settings').hidden`),false,'rescue confirmation must retain Settings');
 assert.ok(await evaluate(p,`(()=>{const n=document.getElementById('sheet'),r=n.getBoundingClientRect();return r.height>0&&n.contains(document.elementFromPoint(r.x+20,r.y+20))})()`),'confirmation is above Settings');
 await save(i?'confirm-codex':'confirm');
 assert.equal(await evaluate(p,`document.getElementById('sheetTitle').textContent`),lang==='zh'?'重新登录 AI':'Sign in to AI again','rescue title must describe sign-in');
 assert.equal(await evaluate(p,`document.getElementById('apprAllowLbl').textContent`),lang==='zh'?'开始重新登录':'Start sign-in','rescue approval starts sign-in');
 assert.equal(await evaluate(p,`document.getElementById('askTags').textContent`),lang==='zh'?'需要本人确认':'Your confirmation is required','rescue tag must describe owner confirmation');
 assert.doesNotMatch(await evaluate(p,`document.getElementById('sheet').textContent`),/Sandbox unavailable|Run this launch without isolation|沙箱不可用|本次不隔离运行/,'rescue sheet must contain no sandbox copy');
 await fake.send({t:'ask_done',id:approvalId,result:'allow'});
 await evaluate(p,`document.getElementById('sheetClose').click()`);
 }
 view={...view,flow:{...auth,stage:'url',route:'setup_token'},dot:true};await fake.send({t:'auth_rescue',view});
 await fake.send({t:'elev',id,kind:'secret',name:'CLAUDE_AUTH_CODE',purpose:lang==='zh'?'Claude 官方授权页提供的授权码，只在这张卡里粘贴':'The code from the official Claude authorization page; paste only into this card',dest:lang==='zh'?'本机 Agent J 服务 env 文件（0600）':'Local Agent J service env file (0600)',verify:'claude -p (live)',n:'b'.repeat(32),epk:'A'.repeat(43),ttl:580,auth});
 await waitFor(p,`!!document.querySelector('#elev-auth a')`);
 assert.equal(await evaluate(p,`document.querySelector('#elev-auth a').href`),url);
 assert.match(await evaluate(p,`document.getElementById('elev-auth').textContent`),/2\/3/);
 assert.equal(await evaluate(p,`document.querySelector('#elev-auth a').target`),'_blank');
 assert.match(await evaluate(p,`document.querySelector('#elev-auth a').rel`),/noreferrer/);
 assert.ok(await evaluate(p,`document.querySelectorAll('#elev-auth button').length`));
 await evaluate(p,`document.querySelector('#elev-auth button').click()`);
 await waitFor(p,`navigator.clipboard.readText().then(value=>value===${JSON.stringify(url)})`);
 await save('claude-card');
 await evaluate(p,`document.getElementById('elev-hide').click()`);
 assert.equal(await evaluate(p,`document.getElementById('settings').hidden`),false);
 assert.equal(await evaluate(p,`document.getElementById('set-auth').hidden`),false);
 await evaluate(p,`document.querySelector('#ar-flow > button').click()`);
 await waitFor(p,`!document.getElementById('elev').hidden`);
 await fake.send({t:'elev_done',id,result:'saved',verify:'ok'});
 assert.equal(await evaluate(p,`document.getElementById('settings').hidden`),false);
 for(const stage of ['verifying','done','failed']){view={...view,flow:{kind:'claude',stage,saved:true}};await fake.send({t:'auth_rescue',view});await sleep(80);await save(stage);}
 view={...view,harnesses:[...view.harnesses,{kind:'codex',label:'Codex',installed:true,state:'ok',routes:[]},{kind:'kimi',label:'Kimi Code',installed:true,state:'ok',routes:[]}]};
 await fake.send({t:'auth_rescue',view});await waitFor(p,`document.querySelectorAll('#ar-list .arrow').length===3`);
 await evaluate(p,`document.getElementById('ar-list').scrollIntoView({block:'end'})`);
 view={...view,flow:{kind:'codex',stage:'device',url:'https://auth.openai.com/codex/device',code:'ABCD-1234',expires_at:auth.expires_at,round:1,rounds:1}};
 await fake.send({t:'auth_rescue',view});await waitFor(p,`document.querySelector('#ar-flow .arcode')?.textContent==='ABCD-1234'`);
 await waitFor(p,`(()=>{const r=document.querySelector('#ar-flow .arcode').getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight})()`);await save('codex-device');
 assert.equal(await evaluate(p,`document.getElementById('settings').hidden`),false);
 assert.ok(await evaluate(p,`document.documentElement.scrollWidth<=innerWidth`),'no horizontal overflow');
 // Existing sandbox approval keeps its own wording.
 await fake.send({t:'ask',id:'e'.repeat(32),tool:'SandboxOnce',summary:'fixture sandbox launch',ttl:600,passkey_required:true});
 await sleep(700);
 assert.equal(await evaluate(p,`document.getElementById('sheetTitle').textContent`),lang==='zh'?'沙箱不可用':'Sandbox unavailable');
 assert.equal(await evaluate(p,`document.getElementById('apprAllowLbl').textContent`),lang==='zh'?'本次不隔离运行':'Run this launch without isolation');
 await fake.send({t:'ask_done',id:'e'.repeat(32),result:'allow'});
 await evaluate(p,`document.getElementById('sheetClose').click()`);
 assert.deepEqual(p.problems,[]);console.log(`PASS ${lang} ${theme} ${width}`);
 }finally{await p.dispose();}
}
if(!process.env.P492_CHAT_ONLY){rec.record('P492 rescue cards retain Settings and show host authorization',true);console.log(rec.write());}
}finally{await B.close();await fake.stop();await web.stop();}
