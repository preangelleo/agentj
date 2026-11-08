// 0.28.6: every visually adjacent Settings card keeps the shared control gap.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import * as H from './browser.mjs';
const phase=process.env.MARGIN_PHASE||'after';
const out=resolve(process.env.MARGIN_OUT||'reports/qa/0286-settings-margin');mkdirSync(out,{recursive:true});
const web=await startWebServer(process.env.MARGIN_PUBLIC_DIR?{publicDir:resolve(process.env.MARGIN_PUBLIC_DIR)}:{}),fake=await startFakeHost(),B=await H.launch(),rows=[];
let failures=0;
import {SETTINGS_CARD_GAPS as sweep} from './settings-card-spacing.mjs';
async function capture(p,name,clip){const {data}=await p.send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,...(clip?{clip}: {})});writeFileSync(`${out}/${name}.png`,Buffer.from(data,'base64'));}
try{for(const width of [390,1440])for(const theme of ['light','dark']){
fake.reset();fake.st.prefs={appearance:{language:'zh',theme},voice:{speak_replies:false},agent:{session_mode:'shared'}};
const p=await H.newPage(B,width,width===390?844:900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
try{await H.navigate(p,fake.newPairing(web.url+'?lang=zh'));await H.waitState(p,'awaiting-approval');await fake.approve();await H.waitState(p,'ready');await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
await fake.send(theme==='light'?{t:'theme',v:1,id:'margin-light',colors:{menu:{background:'#faf9f5',panel:'#f0eee6',text:'#141414',accent:'#141414',line:'#e3dacc'}}}:{t:'theme',v:1,factory:'robots'});
await H.waitFor(p,`getComputedStyle(document.documentElement).getPropertyValue('--menu-bg').trim()==='${theme==='light'?'#faf9f5':'#2c2b26'}'`);
await H.evaluate(p,`(async()=>{document.getElementById('a2hs-ok')?.click();document.getElementById('phone-env-dismiss')?.click();document.getElementById('setBtn').click();await document.fonts.ready})()`);
await H.waitFor(p,`document.getElementById('rs-clear')?.getClientRects().length>0`);
await H.sleep(250);
const metrics=await H.evaluate(p,sweep);const renderedMenu=await H.evaluate(p,`({background:getComputedStyle(document.getElementById('settings')).backgroundColor,text:getComputedStyle(document.getElementById('set-s-rescue')).color})`);rows.push({width,theme,renderedMenu,...metrics});
if(phase==='after'){await H.evaluate(p,`document.getElementById('rs-clear').style.marginTop='-4px'`);const negative=await H.evaluate(p,sweep);assert.ok(negative.violations.some(x=>x.from==='layout-diag-card'&&x.to==='rs-clear'&&x.gap<0),'negative control: overlapping cards fail');await H.evaluate(p,`document.getElementById('rs-clear').style.removeProperty('margin-top')`);}
await H.evaluate(p,`document.getElementById('layout-diag-card').scrollIntoView({block:'center'})`);await H.sleep(150);
const clip=await H.evaluate(p,`(()=>{const a=document.getElementById('layout-diag-card').getBoundingClientRect(),b=document.getElementById('rs-clear').getBoundingClientRect();const x=Math.max(0,a.left-8),y=Math.max(0,a.top-8);return{x,y,width:Math.min(innerWidth-x,a.width+16),height:Math.min(innerHeight-y,b.bottom-y+8),scale:1}})()`);
await capture(p,`${phase}-${width}-${theme}-repair`,clip);
// Fit the scrollable Settings page into a tall viewport, preserving its real width/styles.
const height=await H.evaluate(p,`document.getElementById('set-main').scrollHeight+document.querySelector('.sethead').getBoundingClientRect().height+100`);
await p.send('Emulation.setDeviceMetricsOverride',{width,height:Math.ceil(height),deviceScaleFactor:1,mobile:width<500});
await H.evaluate(p,`document.getElementById('set-main').scrollTop=0`);await H.sleep(200);await capture(p,`${phase}-${width}-${theme}-settings-full`);
try{assert.ok(metrics.floor>=12);assert.equal(metrics.violations.length,0,JSON.stringify(metrics.violations));assert.ok(metrics.pairs.some(x=>x.from==='layout-diag-card'&&x.to==='rs-clear'),'diagnostic/friend cards measured');}catch(e){failures++;console.error(`${width} ${theme}: ${e.message}`)}
}finally{await p.dispose()}}
}finally{writeFileSync(`${out}/${phase}-results.json`,JSON.stringify({phase,scope:'isolated Chromium, real Noise paired fakehost; desktop/phone viewport simulations, not physical Mac Safari',rows,failures},null,2));await B.close();await fake.stop();await web.stop()}
console.log(JSON.stringify({phase,failures,out}));process.exitCode=failures?1:0;
