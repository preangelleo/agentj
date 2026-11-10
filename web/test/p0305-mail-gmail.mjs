#!/usr/bin/env node
// Gmail auto-pin note, rendered by the real paired client against synthetic host views (no mailuv1 → display-only box;
// the 0.30.6 signed tick/untick is p0306-mail-servid-toggle.mjs).
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch, newPage, navigate, waitState, waitFor, evaluate, shoot} from './browser.mjs';
const dir = resolve('reports/qa/p0305/mail-gmail');
mkdirSync(dir, {recursive:true});
const cases = [
  {name:'before-manual-gmail', host:'imap.gmail.com', source:'manual', pinned:true},
  {name:'after-auto-gmail', host:'imap.gmail.com', source:'auto', pinned:true},
  {name:'auto-custom', host:'mail.example.test', source:'auto', pinned:true},
  {name:'gmail-unpinned', host:'imap.gmail.com', source:null, pinned:false},
];
let web, fake, browser;
try {
  web = await startWebServer(); fake = await startFakeHost(); browser = await launch();
  for (const lang of ['zh','en']) for (const theme of ['light','dark']) for (const width of [390,1440]) for (const item of cases) {
    fake.reset();
    fake.st.prefs = {appearance:{language:lang,theme},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
    fake.st.mail = {config:{enabled:false,preset:'custom',address:'',username:'',imap:{host:item.host,port:993,security:'ssl'},smtp:{host:'',port:465,security:'ssl'},poll_minutes:5,auth:'standard',auth_servid:item.pinned?'mx.google.com':'',allow:[]},has_password:false,today:{},blocked:[],presets:[{id:'custom',name:'Custom',imap:{host:'',port:993,security:'ssl'},smtp:{host:'',port:465,security:'ssl'},domains:[],note:''}],servid_status:{pinned:item.pinned,value:item.pinned?'mx.google.com':'',source:item.source,detected:null,sample:null}};
    const page = await newPage(browser,width,900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
    try {
      await navigate(page,fake.newPairing(web.url+'?lang='+lang)); await waitState(page,'awaiting-approval'); await fake.approve(); await waitState(page,'ready');
      await fake.send(theme==='light'?{t:'theme',v:1,id:'qa-light',colors:{menu:{background:'#faf9f5',panel:'#f0eee6',text:'#141414',accent:'#141414',line:'#e3dacc'}}}:{t:'theme',v:1,factory:'robots'});
      await waitFor(page,`getComputedStyle(document.documentElement).getPropertyValue('--menu-bg').trim()==='${theme==='light'?'#faf9f5':'#2c2b26'}'`);
      await evaluate(page,`(async()=>{for(const id of ['phone-env-dismiss','a2hs-ok'])document.getElementById(id)?.click();await document.fonts.ready;document.getElementById('setBtn').click()})()`);
      await waitFor(page,`!document.getElementById('settings').hidden`);
      await evaluate(page,`document.querySelector('[data-set-theme=${JSON.stringify(theme)}]').click()`);
      await waitFor(page,`document.documentElement.dataset.theme===${JSON.stringify(theme)}`);
      await waitFor(page,`!document.getElementById('toast').classList.contains('on')`,8000);
      await evaluate(page,`document.getElementById('set-mail-nav').click()`);
      await waitFor(page,`!document.getElementById('set-mail').hidden && document.getElementById('mail-pinned').textContent.length>0`);
      const result = await evaluate(page,`(()=>{const check=document.getElementById('mail-trusted-check'),note=document.getElementById('mail-gmail-pinned');return {checked:check.checked,disabled:check.disabled,noteHidden:note.hidden,note:note.textContent,pinned:document.getElementById('mail-pinned').textContent,overflow:document.documentElement.scrollWidth>innerWidth+1}})()`);
      const expected = item.name==='after-auto-gmail';
      assert.equal(result.checked,item.pinned); assert.equal(result.disabled,true);
      assert.equal(result.noteHidden,!expected); assert.equal(result.note,expected?(lang==='zh'?'Gmail 已自动固定':'Gmail check server pinned automatically'):'');
      assert.equal(result.overflow,false); assert.ok(result.pinned);
      await evaluate(page,`document.getElementById('mail-trusted-status').scrollIntoView({block:'center'})`);
      writeFileSync(resolve(dir,`${width}-${theme}-${lang}-${item.name}.png`),await shoot(page,undefined));
      assert.deepEqual(page.problems,[]); assert.deepEqual(page.offsite,[]);
      console.log(`${width}-${theme}-${lang}-${item.name} PASS`);
    } finally { await page.dispose(); }
  }
} finally { await browser?.close(); await fake?.stop(); await web?.stop(); }
