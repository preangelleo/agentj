// P567: actual paired page, both browser engines, before/after visual matrix and signed fallback persistence.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const pw=createRequire(import.meta.url)(process.env.AJ_PLAYWRIGHT_MODULE||'/tmp/aj-webmcp-playwright/node_modules/playwright');
const out=resolve(process.env.P567_OUT||'reports/qa/p567/screens');mkdirSync(out,{recursive:true});
const after=await startWebServer(),before=process.env.P567_BEFORE?await startWebServer({publicDir:resolve(process.env.P567_BEFORE)}):null,fake=await startFakeHost();
const results=[];
try{
 for(const engine of (process.env.P567_ENGINES||'chromium').split(',')){
  const b=await pw[engine].launch(engine==='chromium'?{executablePath:process.env.CHROMIUM||'/usr/bin/chromium',args:['--no-sandbox']}:{});
  try{for(const lang of ['zh','en'])for(const [width,theme] of [[390,'light'],[390,'dark'],[1440,'light']])for(const [phase,web] of [...(before?[['before',before]]:[]),['after',after]]){
   fake.reset();fake.setUp(true);fake.st.runCmds=['doctor','reconnect','status','service_status','rescue_status','update_check',...['claude','codex','opencode','kimi','qwen','dsh'].map(h=>'switch_'+h)];
   fake.st.prefs={appearance:{language:lang,theme},ui:{forward_destination:'share'}};fake.st.forwardSync=true;
   const page=await b.newPage({viewport:{width,height:width===390?844:1000},colorScheme:theme});const errors=[];page.on('pageerror',e=>errors.push(String(e)));
   try{
    // WebKit's Linux audio backend is absent in this fixture; no gesture needs to initialize audio.
    await page.addInitScript(()=>{window.Audio=undefined;window.AudioContext=undefined;window.webkitAudioContext=undefined;});
    await page.goto(fake.newPairing(web.url+'?lang='+lang));
    await page.waitForFunction(()=>document.body.dataset.conn==='approval'||document.body.dataset.phase==='awaiting-approval'||document.getElementById('pair-code')?.textContent, null,{timeout:1000}).catch(()=>{});
    await fake.approve();
    await page.waitForFunction(async()=>{const m=await import('./js/session.js');return m.isReady()},{timeout:15000});
    await page.evaluate(async()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();await import('./js/settings.js').then(m=>m.open());});
    await page.waitForFunction(()=>!document.getElementById('settings').hidden&&document.querySelectorAll('#rs-cmd-list li').length>0);
    const shot=async name=>{await page.evaluate(async()=>{await document.fonts.ready;await import('./js/ui.js').then(m=>m.toastOff());});await page.waitForTimeout(350);await page.screenshot({path:resolve(out,`${engine}-${lang}-${width}-${theme}-${phase}-${name}.png`)});};
    const scroll=async id=>{await page.locator('#'+id).evaluate(e=>e.scrollIntoView({block:'start'}));await page.waitForTimeout(100);};
    await scroll('rs-auth');await shot('terminal-collapsed');
    if(phase==='after'){
     assert.equal(await page.locator('#rs-cmds').getAttribute('open'),null);
     assert.equal(await page.locator('#rs-cmds-h').innerText(),lang==='zh'?'终端指令':'Terminal Commands');
     await page.locator('#rs-cmds summary').click();
     const rows=await page.locator('#rs-cmd-list li').allTextContents();assert.equal(rows.length,12);
     for(const h of ['claude','codex','opencode','kimi','qwen','dsh'])assert.ok(rows.some(x=>x.includes('agentj agent switch '+h+' --force')));
     if(lang==='en')assert.ok(rows.every(x=>!/[\u3400-\u9fff]/.test(x)));
     const layout=await page.locator('#rs-cmd-list li').evaluateAll(items=>items.map(li=>{
      const main=li.querySelector('.rscmdmain'),meta=li.querySelector('.rscmdmeta'),go=li.querySelector('.rscmdgo'),term=li.querySelector('.rscmdterm');
      const r=li.getBoundingClientRect(),a=main.getBoundingClientRect(),m=meta.getBoundingClientRect();
      return {full:Math.abs(a.width-r.width)<2,below:m.top>=a.bottom,icons:[go,term].every(b=>b.textContent===''&&b.querySelector('svg')&&b.title&&b.getAttribute('aria-label')),grey:term.getAttribute('aria-disabled')==='true',size:getComputedStyle(go.querySelector('svg')).width};
     }));
     assert.ok(layout.every(x=>x.full&&x.below&&x.icons&&x.grey&&x.size==='21px'),JSON.stringify(layout));
     const blocked=page.locator('#rs-cmd-term-doctor');await blocked.click({force:true});
     assert.ok((await page.locator('#toast').textContent()).length>10);
     // Long hold is a hint, and must not execute a command.
     const go=page.locator('#rs-cmd-doctor');await go.scrollIntoViewIfNeeded();const gr=await go.boundingBox();
     await page.mouse.move(gr.x+gr.width/2,gr.y+gr.height/2);await page.mouse.down();await page.waitForTimeout(650);await page.mouse.up();
     assert.ok((await page.locator('#toast').textContent()).includes('agentj doctor'));
     assert.equal(await page.locator('#rs-cmd-head').count(),0);
     // Enabled terminal icons only prefill; the route still owns actual execution.
     const prefilled=await page.evaluate(async()=>{
       const c=await import('./js/cmdrun.js'),h=await import('./js/termhook.js');let lines=[];
       c.configure({openTerminal:x=>lines.push(x)});h.render({terminal:{on:true,os:true,me:true,web_ok:true,kind:'desktop'}});c.paint();
       for(const b of document.querySelectorAll('.rscmdterm'))b.click();
       h.render({});c.paint();return lines;
     });
     assert.equal(prefilled.length,12);assert.equal(prefilled.filter(x=>x.endsWith('--force')).length,6);

     await fake.send({t:'cmds_state',names:['doctor','switch_kimi','unknown']});await page.waitForFunction(()=>document.querySelectorAll('#rs-cmd-list li').length===2);
     await fake.send({t:'cmds_state',names:fake.st.runCmds});await page.waitForFunction(()=>document.querySelectorAll('#rs-cmd-list li').length===12);
    }
    if(phase==='before'&&await page.locator('details#rs-cmds').count())await page.locator('#rs-cmds').evaluate(e=>{e.open=true;});
    await scroll('rs-cmds');await shot('terminal-expanded');
    if(await page.locator('[data-cmd="switch_claude"]').count()){await page.locator('[data-cmd="switch_claude"]').evaluate(e=>e.scrollIntoView({block:'start'}));await shot('terminal-switches-top');await page.locator('[data-cmd="switch_qwen"]').evaluate(e=>e.scrollIntoView({block:'start'}));await shot('terminal-switches-bottom');}
    await page.locator('#set-mail-nav').evaluate(e=>e.click());await page.waitForFunction(()=>!document.getElementById('set-mail').hidden);
    await scroll(phase==='after'?'mail-allow-card':'mail-allow');await shot('allow-collapsed');
    if(phase==='after'){
     assert.equal(await page.locator('#mail-allow-card').getAttribute('open'),null);await page.locator('#mail-allow-card summary').click();
     await page.locator('#mail-add-addr').fill('@Anthropic.COM');assert.equal(await page.locator('#mail-add-role').inputValue(),'guest');assert.equal(await page.locator('#mail-add-role').isDisabled(),true);
     await page.locator('#mail-add-subdomains').check();await page.locator('#mail-add').click();await page.waitForFunction(()=>document.getElementById('mail-allow').textContent.includes('@Anthropic.COM'));
     const row=fake.st.mail.config.allow.at(-1);assert.equal(row.role,'guest');assert.equal(row.subdomains,true);
     await page.evaluate(()=>document.getElementById('set-back').click());
     // An actual signed write must save share after unbinding, and reloading must not send another reset.
     await fake.send({t:'wechat_state',bound:true,can_forward:false,pending:0});
     await page.waitForFunction(async()=>{const m=await import('./js/session.js');return m.peer.wxbound===true;});
     assert.equal(await page.evaluate(async()=>{const m=await import('./js/forward-choice.js');return m.select('wx');}),true);
     await page.waitForFunction(()=>import('./js/forward-choice.js').then(m=>m.destination()==='wx'));
     for(let i=0;i<1000&&fake.st.prefs.ui.forward_destination!=='wx';i++)await page.waitForTimeout(20);
     assert.equal(fake.st.prefs.ui.forward_destination,'wx', JSON.stringify({meta:await page.evaluate(async()=>({note:document.getElementById('set-fwd-note')?.dataset.i18n,key:!!(await import('./js/store.js').then(m=>m.signKey())),peer:(await import('./js/session.js')).peer.wxbound})),sets:fake.st.forwardSets, toast:await page.locator('#toast').textContent()}));
     await fake.send({t:'wechat_state',bound:false,can_forward:false,pending:0});
     await page.waitForFunction(()=>import('./js/forward-choice.js').then(m=>m.destination()==='share'));
     for(let i=0;i<50&&fake.st.prefs.ui.forward_destination!=='share';i++)await page.waitForTimeout(20);
     assert.equal(fake.st.prefs.ui.forward_destination,'share');assert.ok(fake.st.forwardSets.some(x=>x.dest==='share'));
     await page.reload();await page.waitForFunction(()=>import('./js/session.js').then(m=>m.isReady()));
     assert.equal(await page.evaluate(()=>import('./js/forward-choice.js').then(m=>m.destination())),'share');
     await page.evaluate(async()=>{await import('./js/settings.js').then(m=>m.open());document.getElementById('set-mail-nav').click();});
     await page.waitForFunction(()=>!document.getElementById('set-mail').hidden);await page.locator('#mail-allow-card').evaluate(e=>{e.open=true;e.scrollIntoView({block:'start'});});
    }
    await shot('allow-expanded');
    assert.deepEqual(errors,[]);results.push({engine,lang,width,theme,phase,ok:true});console.log('PASS',engine,lang,width,theme,phase);
   }finally{await page.close();}
  }}finally{await b.close();}
 }
 writeFileSync(resolve(out,'results.json'),JSON.stringify(results,null,2)+'\n');
}finally{await fake.stop();await after.stop();if(before)await before.stop();}
