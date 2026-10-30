#!/usr/bin/env node
// P585: owner channels keep the original input and response together. Local Noise fakehost + real Chromium.
// BEFORE captures the old task + trailing sys-notice projection reported in #409, on an unmodified base public/.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {join,resolve} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const before=process.env.P585_BEFORE;
const history=process.env.P585_HISTORY_FIXTURE?JSON.parse(readFileSync(process.env.P585_HISTORY_FIXTURE,'utf8')):null;
const out=resolve(process.env.P585_PHONE_OUT || 'reports/qa/p585-web');
mkdirSync(out,{recursive:true});
const web=await startWebServer(before?{publicDir:resolve(before)}:{}),fake=await startFakeHost(),B=await launch();
const results=[];
const text={zh:{input:'请检查备份结果，并告诉我是否需要处理。',reply:'备份已经完成，没有失败项目，不需要额外处理。',agent:'来自 Agent · Warden AWS',email:'邮件 · 主人',wechat:'微信'},en:{input:'Please check the backup and tell me whether anything needs attention.',reply:'The backup completed with no failed items. No further action is needed.',agent:'From Agent · Warden AWS',email:'Email · Owner',wechat:'WeChat'}};
const sourceLabel=(lang,kind,src,fallback)=>kind==='email' ? (lang==='zh'?'邮件':'Email')+' · '+(src.name || '') : kind==='wechat' ? (src.name || 'WeChat') : kind==='same-owner' ? (lang==='zh'?'来自 Agent · ':'From Agent · ')+(src.name || '') : fallback;
const get=(p,id)=>evaluate(p,`document.getElementById(${JSON.stringify(id)}).textContent`);
try{
 for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  fake.reset();fake.st.prefSet=true;fake.st.prefs={appearance:{language:lang,theme:'system'}};
  const p=await newPage(B,width,844,theme,{touch:true,allow:[web.url,fake.relay+'/']});
  const s=text[lang],tag=`${lang}-${theme}-${width}`;
  try{
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click()`);
   await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
   if(before){
    await fake.addTurn({k:'task',name:'Warden AWS',text:'Warden AWS\n'+s.input},'');
    await fake.addTurn({k:'sys',text:''},lang==='zh'?'好友任务已进入主对话：Warden AWS':'Friend task entered the main conversation: Warden AWS');
    await waitFor(p,"document.getElementById('pg').textContent==='2 / 2'");
    assert.equal(await evaluate(p,"document.getElementById('om').dataset.header"),'1');
    await sleep(250);writeFileSync(join(out,`${tag}-same-owner.png`),await shoot(p,''));continue;
   }
   const cases=[['same-owner',{k:'task',via:'same-owner',name:'Warden AWS'},s.agent],['email',{k:'email',name:lang==='zh'?'主人':'Owner'},s.email],['wechat',{k:'wechat',name:s.wechat},s.wechat],['telegram',{k:'telegram',name:'Telegram'},'Telegram']];
   for(const [i,[kind,src,label]] of cases.entries()){
    // An open turn is updated in place. No trailing notice may cover its input/reply.
    const actual=history?.[lang]?.find(row=>row.kind===kind);
    if(history)assert.ok(actual,`host fixture includes ${lang} ${kind}`);
    const input=actual?.src.text ?? s.input,answer=actual?.reply.text ?? s.reply;
    const turn=await fake.addTurn(actual?.src ?? {...src,text:s.input},'','open');
    await fake.updateTurn(turn.id,{reply:actual?.reply ?? {text:s.reply},end:actual?.end ?? 'done'});
    await waitFor(p,`document.getElementById('pg').textContent==='${i+1} / ${i+1}' && document.getElementById('words').textContent.includes(${JSON.stringify(answer)})`);
    assert.equal(await get(p,'omText'),input,`${tag} ${kind}: original input`);
    assert.equal(await get(p,'omLabel'),sourceLabel(lang,kind,actual?.src ?? src,label),`${tag} ${kind}: source label`);
    assert.equal((await get(p,'words')).trim(),answer,`${tag} ${kind}: same-card response`);
    assert.equal(await evaluate(p,"document.getElementById('om').dataset.header"),'0');
    assert.equal(await evaluate(p,'document.documentElement.scrollWidth>innerWidth'),false,'no horizontal overflow');
    await sleep(250);writeFileSync(join(out,`${tag}-${kind}.png`),await shoot(p,''));
   }
   // Full reconnect: host history must preserve source/original and link the same response, not create duplicate pages.
   await navigate(p,web.url+'?lang='+lang);await waitState(p,'ready');
   await waitFor(p,"document.getElementById('pg').textContent==='4 / 4'");
   for(let i=3;i>=0;i--){
    const [kind,src,label]=cases[i],actual=history?.[lang]?.find(row=>row.kind===kind);
    assert.equal(await get(p,'omLabel'),sourceLabel(lang,kind,actual?.src ?? src,label),`${tag} restored ${kind} source`);
    assert.equal(await get(p,'omText'),actual?.src.text ?? s.input,`${tag} restored ${kind} original`);
    assert.equal((await get(p,'words')).trim(),actual?.reply.text ?? s.reply,`${tag} restored ${kind} response`);
    if(i) {await evaluate(p,"document.getElementById('pgPrev').click()");await waitFor(p,`document.getElementById('pg').textContent==='${i} / 4'`);}
   }
   assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);
   results.push({tag,channels:4,reconnect:true,result:'pass'});console.log('PASS P585 '+tag);
  }finally{await p.dispose();}
 }
 writeFileSync(join(out,'results.json'),JSON.stringify({mode:before?'before':'after',fixture:process.env.P585_HISTORY_FIXTURE || 'wire-contract',results},null,2)+'\n');
}finally{await B.close();await fake.stop();await web.stop();}
