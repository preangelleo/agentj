#!/usr/bin/env node
// Isolated mail-triage viewport captures; no account, real mailbox or shared CDP profile.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,sleep} from './browser.mjs';

const out=resolve('.jarvis/out/0304-settings-ui/after');
mkdirSync(out,{recursive:true});
const rows=[];
let web,fake,B;
try {
  web=await startWebServer();fake=await startFakeHost();B=await launch();
  for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const width of [390,1440]) {
    const tag=`${width}-${theme}-${lang}`;
    fake.reset();
    // The fixture contains only synthetic mail status, never a real mailbox or address.
    fake.st.mail={config:{enabled:false,preset:'',address:'',username:'',imap:{host:'',port:993,security:'ssl'},smtp:{host:'',port:465,security:'ssl'},poll_minutes:5,auth:'standard',auth_servid:'',allow:[],reply_per_hour:20,triage:{preset:'allowlist_only',provider:'',model:'',usd_per_day:0,usd_per_call:0.01,every_minutes:30,per_hour:30}},today:{},blocked:[],triage:{total:0,review:0,archived:0,items:[]},presets:[{id:'qq',name:'QQ',imap:{host:'imap.qq.com',port:993,security:'ssl'},smtp:{host:'smtp.qq.com',port:465,security:'ssl'},domains:['qq.com'],note:''},{id:'custom',name:'Custom',imap:{host:'',port:993,security:'ssl'},smtp:{host:'',port:465,security:'ssl'},domains:[],note:''}]};
    fake.st.prefs={appearance:{language:lang,theme},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
    const p=await newPage(B,width,900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
    const row={tag,shots:[],checks:[],problems:[]};rows.push(row);
    try {
      await navigate(p,fake.newPairing(web.url+'?lang='+lang));
      await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
      await fake.send(theme==='light'?{t:'theme',v:1,id:'qa-light',colors:{menu:{background:'#faf9f5',panel:'#f0eee6',text:'#141414',accent:'#141414',line:'#e3dacc'}}}:{t:'theme',v:1,factory:'robots'});
      await waitFor(p,`getComputedStyle(document.documentElement).getPropertyValue('--menu-bg').trim()==='${theme==='light'?'#faf9f5':'#2c2b26'}'`);
      await evaluate(p,`(async()=>{for(const id of ['phone-env-dismiss','a2hs-ok'])document.getElementById(id)?.click();await document.fonts.ready;document.getElementById('setBtn').click()})()`);
      await waitFor(p,`!document.getElementById('settings').hidden`);
      // Use the actual Settings theme button, not a screenshot-only CSS override.
      await evaluate(p,`document.querySelector('[data-set-theme=${JSON.stringify(theme)}]').click()`);
      await waitFor(p,`document.documentElement.dataset.theme===${JSON.stringify(theme)}`);
      await waitFor(p,`!document.getElementById('toast').classList.contains('on')`,8000);
      await evaluate(p,`document.getElementById('set-mail-nav').click()`);
      await waitFor(p,`!document.getElementById('set-mail').hidden && !!document.getElementById('mail-triage')`);
      for(const state of ['collapsed','expanded']) {
        const open=state==='expanded';
        const info=await evaluate(p,`(()=>{const box=document.getElementById('mail-triage'),adv=document.getElementById('mail-triage-advanced'),scroll=document.getElementById('set-mail');box.open=true;adv.open=${open};document.getElementById('mail-triage-results').open=false;box.scrollIntoView({block:'start'});return {theme:document.documentElement.dataset.theme,menuBg:getComputedStyle(document.documentElement).getPropertyValue('--menu-bg').trim(),bodyBg:getComputedStyle(document.querySelector('#settings .setbox')).backgroundColor,advanced:adv.open,triage:box.open,scrollWidth:document.documentElement.scrollWidth,scrollHeight:scroll.scrollHeight,viewportHeight:scroll.clientHeight,triageTop:box.getBoundingClientRect().top,triageBottom:box.getBoundingClientRect().bottom,selectedTheme:document.querySelector('[data-set-theme=${JSON.stringify(theme)}]').getAttribute('aria-pressed'),language:document.documentElement.lang}})()`);
        await sleep(120);
        assert.equal(info.advanced,open);assert.equal(info.triage,true);assert.equal(info.theme,theme);assert.equal(info.selectedTheme,'true');assert.ok(info.triageBottom>0);assert.ok(info.scrollWidth<=width+1,`horizontal overflow ${tag}`);
        const path=`${out}/${tag}-mail-triage-${state}.png`;
        const {data}=await p.send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});writeFileSync(path,Buffer.from(data,'base64'));
        row.shots.push(path);row.checks.push(info);
        // If expanded card exceeds the viewport, capture its lower part too rather than silently cropping.
        if(info.triageBottom>900){await evaluate(p,`document.getElementById('set-mail').scrollTop+=${Math.ceil(info.triageBottom-850)}`);await sleep(100);const more=`${out}/${tag}-mail-triage-${state}-bottom.png`;const image=await p.send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});writeFileSync(more,Buffer.from(image.data,'base64'));row.shots.push(more);}
      }
      row.problems.push(...p.problems.map(x=>'browser '+x),...p.offsite.map(x=>'offsite '+x));
      console.log(tag,row.problems.length?'RED '+JSON.stringify(row.problems):'PASS');
    }catch(e){row.problems.push(String(e));console.error(tag,'RED',String(e));}finally{await p.dispose();}
  }
}finally{try{writeFileSync(`${out}/triage-browser.json`,JSON.stringify({source:'current candidate, isolated fake host',rows},null,2)+'\n');}finally{await B?.close();await fake?.stop();await web?.stop();}}
assert.ok(rows.every(x=>x.checks.length===2 && x.problems.length===0), 'mail triage screenshot scenarios red');
