// 0.28.6 fix1: source titles remain one line; full labels belong to the expanded input.
import assert from 'node:assert/strict';
import {mkdirSync, readFileSync, writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import * as H from './browser.mjs';
const phase=process.env.FIX1_PHASE||'after',out=resolve(process.env.FIX1_OUT||`reports/qa/0286/fix1-web/${phase}`);mkdirSync(out,{recursive:true});
const event=JSON.parse(readFileSync('reports/qa/0286/qa/host-input-after.json'));
const long='月度自动化检查 Monthly automation verification '.repeat(6);
const sources=[['hook',event.turn.src],['mail',{k:'email',name:long}],['wechat',{k:'wechat',name:long}],['telegram',{k:'agent',input_channel:'telegram',name:'Telegram · '+long}],['api',{k:'agent',via:'api',name:long}],['automation',{k:'auto',name:long}],['system',{k:'sys'}],['background',{k:'agent',via:'background_done'}],['hook-empty',{k:'agent',via:'hook',name:long}]];
const web=await startWebServer(),fake=await startFakeHost(),B=await H.launch(),rows=[];let failures=0;
try{for(const lang of (process.env.FIX1_SINGLE?['zh']:['zh','en']))for(const width of (process.env.FIX1_SINGLE?[390]:[390,1440]))for(const theme of (process.env.FIX1_SINGLE?['light']:['light','dark'])){
 fake.reset();fake.st.prefs={appearance:{language:lang,theme:'system'},voice:{speak_replies:false}};
 const p=await H.newPage(B,width,width===390?844:900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
 try{await H.navigate(p,fake.newPairing(web.url+'?lang='+lang));await H.waitState(p,'awaiting-approval');await fake.approve();await H.waitState(p,'ready');await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});await H.evaluate(p,`document.getElementById('a2hs-ok')?.click();document.getElementById('phone-env-dismiss')?.click()`);
 for(const [kind,source]of sources){const text=kind==='hook'?source.text:kind==='hook-empty'?'':'Input original '+kind;const turn=await fake.addTurn({...source,text},event.turn.reply.text+(kind==='hook'?'':' '+kind),'done');await fake.updateTurn(turn.id,{ts:event.turn.ts});await H.waitFor(p,kind==='hook-empty'?`document.getElementById('om').dataset.header==='1'&&document.getElementById('omLabel').textContent.includes(${JSON.stringify(long.slice(0,64))})`:`document.getElementById('omText').textContent===${JSON.stringify(text)}`);
 for(const expanded of [false,true]){if(expanded){assert.equal(await H.evaluate(p,`getComputedStyle(document.getElementById('omMore')).visibility`),'visible');await H.evaluate(p,`document.getElementById('omMore').click()`);}await H.evaluate(p,'document.fonts.ready');
 const got=await H.evaluate(p,`(()=>{const l=document.getElementById('omLabel'),t=document.getElementById('omTime'),pg=document.getElementById('pg'),f=l.parentElement,c=getComputedStyle(l),r=l.getBoundingClientRect(),tr=t.getBoundingClientRect(),pr=pg.getBoundingClientRect(),d=document.getElementById('omSourceDetail');return{label:l.textContent,height:r.height,lineHeight:parseFloat(c.lineHeight),whiteSpace:c.whiteSpace,textOverflow:c.textOverflow,scrollWidth:l.scrollWidth,width:r.width,page:pg.textContent,time:t.textContent,timeWidth:tr.width,labelRight:r.right,timeLeft:tr.left,sourceRight:f.getBoundingClientRect().right,pageLeft:pr.left,headerHeight:document.querySelector('.omhd').getBoundingClientRect().height,expanded:document.getElementById('om').classList.contains('open'),detail:d?.textContent,detailVisible:!!d&&getComputedStyle(d).display!=='none',overflow:document.documentElement.scrollWidth>innerWidth}})()`);
 let error;try{assert.equal(got.whiteSpace,'nowrap');assert.ok(Math.abs(got.height-got.lineHeight)<1,'title height equals one line');assert.equal(got.textOverflow,'ellipsis');assert.equal(got.headerHeight,44);assert.match(got.page,/^[1-9]\d* \/ [1-9]\d*$/);assert.ok(got.timeWidth>0);assert.ok(got.labelRight<=got.timeLeft+1);assert.ok(got.sourceRight<=got.pageLeft+1);assert.equal(got.expanded,expanded);assert.equal(got.detail,got.label);assert.equal(got.detailVisible,expanded);assert.equal(got.overflow,false);}catch(e){failures++;error=e.message;}
 rows.push({lang,viewportWidth:width,theme,kind,expanded,...got,pass:!error,error});
 if(kind==='hook'||width===390&&lang==='zh'&&theme==='light')writeFileSync(`${out}/qa-fix1-${phase}-${width}-${lang}-${theme}-${kind}-${expanded?'expanded':'folded'}.png`,await H.shoot(p));
 }
 }
 }finally{await p.dispose();}
}}finally{writeFileSync(out+'/results.json',JSON.stringify({phase,scope:'isolated Chromium and real Noise fake host; same recorded Host HookService source event; no physical phone',rows,failures},null,2));await B.close();await fake.stop();await web.stop();}
console.log(JSON.stringify({phase,cases:rows.length,failures,out}));process.exitCode=failures?1:0;

if(!failures)console.log('0286 fix1 existing title geometry and expanded full label across eight configurations PASS');
