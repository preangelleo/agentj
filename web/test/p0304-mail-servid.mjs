#!/usr/bin/env node
// A4 read-only pin projection through the paired real web client, with synthetic host views.
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,shoot} from './browser.mjs';
const dir=resolve('reports/qa/p0304/mail-servid');mkdirSync(dir,{recursive:true});
const cases=[
  {name:'gmail-pinned',host:'imap.gmail.com',pin:'mx.google.com',detected:'mx.google.com',source:'auto',sample:{checked:5,with_results:5},checked:true},
  {name:'outlook-unpinned',host:'outlook.office365.com',pin:'',detected:null,source:null,sample:null,checked:false},
  {name:'custom-untrusted',host:'mail.example.test',pin:'',detected:'sender.example.test',source:null,sample:{checked:5,with_results:2},checked:false},
  {name:'manual-pinned',host:'mail.example.test',pin:'mail.example.test',detected:'sender.example.test',source:'manual',sample:{checked:5,with_results:5},checked:true},
];
let web,fake,B;
try {
  web=await startWebServer();fake=await startFakeHost();B=await launch();
  for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const width of [390,1440])for(const item of cases){
    fake.reset();fake.st.prefs={appearance:{language:lang,theme},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
    fake.st.mail={config:{enabled:false,preset:'custom',address:'',username:'',imap:{host:item.host,port:993,security:'ssl'},smtp:{host:'',port:465,security:'ssl'},poll_minutes:5,auth:'standard',auth_servid:item.pin,allow:[]},has_password:false,today:{},blocked:[],presets:[{id:'custom',name:'Custom',imap:{host:'',port:993,security:'ssl'},smtp:{host:'',port:465,security:'ssl'},domains:[],note:''}],servid_status:{pinned:item.checked,value:item.pin,source:item.source,detected:item.detected,sample:item.sample},auth_sample:null};
    const p=await newPage(B,width,900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
    try {
      await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
      await fake.send(theme==='light'?{t:'theme',v:1,id:'qa-light',colors:{menu:{background:'#faf9f5',panel:'#f0eee6',text:'#141414',accent:'#141414',line:'#e3dacc'}}}:{t:'theme',v:1,factory:'robots'});
      await waitFor(p,`getComputedStyle(document.documentElement).getPropertyValue('--menu-bg').trim()==='${theme==='light'?'#faf9f5':'#2c2b26'}'`);
      await evaluate(p,`(async()=>{for(const id of ['phone-env-dismiss','a2hs-ok'])document.getElementById(id)?.click();await document.fonts.ready;document.getElementById('setBtn').click()})()`);
      await waitFor(p,`!document.getElementById('settings').hidden`);
      // Switch theme through the real Settings button, not a dataset override (QA: light/dark shots were identical).
      await evaluate(p,`document.querySelector('[data-set-theme=${JSON.stringify(theme)}]').click()`);
      await waitFor(p,`document.documentElement.dataset.theme===${JSON.stringify(theme)}`);
      await waitFor(p,`!document.getElementById('toast').classList.contains('on')`,8000);
      const setboxBg=await evaluate(p,`getComputedStyle(document.querySelector('#settings .setbox')).backgroundColor`);
      assert.equal(setboxBg,theme==='light'?'rgb(250, 249, 245)':'rgb(44, 43, 38)',`settings background ${theme}`);
      await evaluate(p,`document.getElementById('set-mail-nav').click()`);
      await waitFor(p,`!document.getElementById('set-mail').hidden && document.getElementById('mail-pinned').textContent.length>0`);
      const result=await evaluate(p,`(()=>({checked:document.getElementById('mail-trusted-check').checked,disabled:document.getElementById('mail-trusted-check').disabled,pinned:document.getElementById('mail-pinned').textContent,detected:document.getElementById('mail-detected').textContent,sample:document.getElementById('mail-sample').textContent,pin:!!document.getElementById('mail-pin'),overflow:document.documentElement.scrollWidth>innerWidth+1}))()`);
      assert.equal(result.checked,item.checked);assert.equal(result.disabled,true);assert.equal(result.pin,false);assert.equal(result.overflow,false);
      assert.equal(result.detected.includes(item.detected||'\0'),!!item.detected);
      if(item.checked){assert.ok(result.pinned.includes(item.pin));assert.ok(result.pinned.includes(item.source==='auto'?(lang==='zh'?'自动':'automatically'):(lang==='zh'?'手动':'manually')));}
      if(item.name==='custom-untrusted'){assert.ok(result.sample.includes(lang==='zh'?'不完整':'incomplete'));assert.ok(result.detected.includes(lang==='zh'?'发件人':'sender'));}
      if(item.name==='outlook-unpinned')assert.equal(result.sample,'');
      await evaluate(p,`document.getElementById('mail-trusted-status').scrollIntoView({block:'center'})`);
      writeFileSync(resolve(dir,`${width}-${theme}-${lang}-${item.name}.png`),await shoot(p,undefined));
      assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);
      console.log(`${width}-${theme}-${lang}-${item.name} PASS`);
    }finally{await p.dispose();}
  }
}finally{await B?.close();await fake?.stop();await web?.stop();}
