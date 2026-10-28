// P537: real Chromium, isolated fake host. Baseline/current zh/en phone+desktop captures.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate as ev,navigate,waitFor,waitState,shoot,key,sleep} from './browser.mjs';
const before=process.argv.includes('before'),out=resolve(process.env.P537_OUT || 'reports/qa/p537',before?'before':'after');mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0,...(before?{publicDir:'/tmp/p537-baseline/web/public'}:{})});
const fake=await startFakeHost(),B=await launch();
const click=(p,id)=>ev(p,`document.getElementById(${JSON.stringify(id)}).click()`);
const shot=async(p,n)=>writeFileSync(out+'/'+n+'.png',await shoot(p,''));
let checks=0;const ok=(v,m)=>{assert.ok(v,m);checks++;};
try{
 for(const lang of ['zh','en'])for(const [w,h] of [[390,844],[1440,1000]]){
  fake.reset();fake.st.extraCaps=['heartbeat_prompt'];
  fake.st.prefs={appearance:{language:lang,theme:'light'},heartbeat:{enabled:true,interval_minutes:30,daily_at:''}};
  let text=lang==='zh'?'# 我的心跳提示\n\n保留我的个人巡检安排。':'# My heartbeat prompt\n\nKeep my personal review notes.',sha='original',writes=[];
  const factory=lang==='zh'?'# 出厂心跳提示\n\nA. 定时任务日志\nB. 好友请求\nC. 分身处理记录':'# Factory heartbeat prompt\n\nA. Scheduled job logs\nB. Friend requests\nC. Avatar handling records';
  fake.st.onApp=async(c,m,send)=>{
   if(m.t==='heartbeat_get'){await send({t:'heartbeat_state',r:m.r,ok:true,text,sha,default:factory,update_available:!text.includes('A.')});return true;}
   if(m.t==='heartbeat_write'){writes.push(m);if(m.text==='reject'){await send({t:'ctl_res',r:m.r,ok:false,why:'invalid'});return true;}
    if(m.sha!==sha){await send({t:'ctl_res',r:m.r,ok:false,why:'changed'});return true;}
    text=m.op==='merge'?text+'\n'+factory:m.text;sha='changed';await send({t:'ctl_res',r:m.r,ok:true});return true;}
  };
  const p=await newPage(B,w,h,'light',{touch:w<500,allow:[web.url,fake.relay+'/']});
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await ev(p,`document.getElementById('phone-env-dismiss')?.click()`);await click(p,'setBtn');
  await ev(p,`document.getElementById('set-hb-row').scrollIntoView({block:'center'})`);await sleep(300);
  await shot(p,lang+'-'+w+'-heartbeat');
  if(before){await ev(p,`import('./js/settings.js').then(m=>m.close())`);await ev(p,`document.getElementById('input').value='Draft / 草稿';document.getElementById('tFull').click()`);await shot(p,lang+'-'+w+'-composer');await p.close();continue;}
  await waitFor(p,`!document.getElementById('set-hb-update').hidden`);
  ok(await ev(p,`(()=>{const a=document.getElementById('set-hb-prompt').getBoundingClientRect(),b=document.getElementById('set-hb-merge').getBoundingClientRect();return a.left>=0&&b.left>=0&&a.bottom<=b.top&&a.width<=innerWidth;})()`),'prompt actions never overlap or leave viewport');
  await click(p,'set-hb-prompt');await waitFor(p,`!document.getElementById('fsEd').hidden`);
  ok(await ev(p,`document.getElementById('fsText').value.includes(${JSON.stringify(lang==='zh'?'个人':'personal')})`),'read custom prompt');
  await shot(p,lang+'-'+w+'-editor');
  await ev(p,`document.getElementById('fsText').value='cancelled draft';document.getElementById('fsText').dispatchEvent(new Event('input'))`);
  await key(p,'Escape');ok(writes.length===0,'Esc cancels without write');
  await click(p,'set-hb-prompt');await waitFor(p,`!document.getElementById('fsEd').hidden`);
  await click(p,'fsClear');ok(await ev(p,`document.getElementById('fsText').value.includes('A.')`),'restore defaults changes draft');ok(writes.length===0,'restore waits for Save');
  await click(p,'fsSlash');ok(await ev(p,`document.getElementById('fsText').readOnly`),'preview is readonly');await click(p,'fsSlash');
  await ev(p,`document.getElementById('fsText').value='reject'`);await click(p,'fsSend');await sleep(200);ok(await ev(p,`!document.getElementById('fsEd').hidden && document.getElementById('fsText').value==='reject'`),'save failure retains draft');
  await ev(p,`document.getElementById('fsText').value='Saved / 已保存'`);await click(p,'fsSend');await waitFor(p,`document.getElementById('fsEd').hidden`);ok(text==='Saved / 已保存','save new prompt');
  ok(writes.every(m=>m.sig&&m.n&&m.ts),'all writes have device signatures');
  await click(p,'set-hb-merge');await waitFor(p,`document.getElementById('set-hb-update').hidden`);ok(text.startsWith('Saved / 已保存')&&text.includes('A.'),'one-tap merge retains custom text');
  ok(await ev(p,`document.querySelectorAll('#settings textarea').length===0`),'settings contains no inline multiline textarea');
  // Ordinary settings text goes through the same editor, with no composer contamination.
  await ev(p,`document.getElementById('input').value='keep composer';document.getElementById('mail-addr').click()`);
  await waitFor(p,`!document.getElementById('fsEd').hidden`);await ev(p,`document.getElementById('fsText').value='owner@example.invalid'`);await click(p,'fsSend');
  ok(await ev(p,`document.getElementById('mail-addr').value==='owner@example.invalid'&&document.getElementById('input').value==='keep composer'`),'shared editor updates settings and preserves composer');
  await ev(p,`import('./js/settings.js').then(m=>m.close())`);await click(p,'tFull');
  ok(await ev(p,`document.getElementById('fsText').value==='keep composer'&&!document.getElementById('fsSlash').hidden`),'composer original actions restored');
  await shot(p,lang+'-'+w+'-composer');await key(p,'Escape');
  // All four corners are caller-configurable; one shared DOM/keyboard owner.
  await ev(p,`import('./js/text-editor.js').then(m=>{window.p537Corners=[];const corner=k=>({label:k,icon:k,action:({setValue})=>{window.p537Corners.push(k);setValue(k)}});m.open({value:'custom',corners:{topLeft:corner('A'),topRight:corner('B'),bottomLeft:corner('C'),bottomRight:corner('D')}})})`);
  for(const id of ['fsExit','fsClear','fsSlash','fsSend'])await click(p,id);
  ok(await ev(p,`window.p537Corners.join('')==='ABCD'&&document.getElementById('fsText').value==='D'`),'four corner labels, icons and actions configurable');await key(p,'Escape');
  await ev(p,`(()=>{const field=document.createElement('textarea');field.id='p537-dynamic';field.value=${JSON.stringify('first\nsecond')};document.getElementById('settings').append(field)})()`);
  await waitFor(p,`document.getElementById('p537-dynamic').type==='hidden'`);
  ok(await ev(p,`document.getElementById('p537-dynamic').value===${JSON.stringify('first\nsecond')}&&document.querySelectorAll('#settings textarea').length===0`),'dynamic settings multiline field preserves newlines behind shared editor');
  await ev(p,`import('./js/text-editor.js').then(m=>{window.p537Pending=new Promise(r=>window.p537Resolve=r);m.open({value:'old draft',save:()=>window.p537Pending})})`);
  await click(p,'fsSend');
  await ev(p,`import('./js/fsedit.js').then(m=>m.forget()).then(()=>import('./js/text-editor.js')).then(m=>m.open({value:'new draft'}))`);
  ok(await ev(p,`!document.getElementById('fsSend').disabled`),'a new editor is usable after disconnect during save');
  await ev(p,`window.p537Resolve(true)`);await sleep(50);
  ok(await ev(p,`!document.getElementById('fsEd').hidden&&document.getElementById('fsText').value==='new draft'`),'old save completion cannot close the newer editor');
  await p.close();
 }
 console.log(`P537 ${before?'baseline screenshots':checks+' browser checks PASS'} → ${out}`);
}finally{await B.close();await fake.stop();await web.stop();}
