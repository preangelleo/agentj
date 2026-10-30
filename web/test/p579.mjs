// P579: real paired client, allowlist roles, processed-mail switch and selectable internal links.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const {chromium}=await import(process.env.AJ_PLAYWRIGHT_MODULE||'/tmp/aj-webmcp-playwright/node_modules/playwright/index.mjs');
const out=resolve(process.env.P579_OUT||'reports/qa/p579');mkdirSync(out,{recursive:true});
const phase=process.env.P579_PHASE||'after',capture=process.env.P579_CAPTURE==='1';
const web=await startWebServer(process.env.P579_PUBLIC?{publicDir:resolve(process.env.P579_PUBLIC)}:{}),fake=await startFakeHost();
const browser=await chromium.launch({executablePath:process.env.CHROMIUM||'/usr/bin/chromium'});
let failures=[];const check=(name,fn)=>{try{fn();console.log('PASS',name);}catch(e){failures.push(name+': '+e.message);console.error('FAIL',name,e.message);}};
try{
 for(const lang of ['zh','en'])for(const width of [390,1440])for(const theme of ['light','dark']){
  fake.reset();fake.st.mail=null;fake.st.autoReply=false;fake.st.prefs={appearance:{language:lang,theme}};
  const page=await browser.newPage({viewport:{width,height:width===390?844:1000},colorScheme:theme});
  await page.context().grantPermissions(['clipboard-read','clipboard-write'],{origin:web.url.replace(/\/$/,'')});
  try{
   await page.goto(fake.newPairing(web.url+'?lang='+lang));await page.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await page.waitForFunction(()=>window.__ajState==='ready');
   await page.evaluate(()=>{document.querySelector('#phone-env-dismiss')?.click();document.querySelector('#a2hs-ok')?.click();});
   await page.evaluate(async()=>{await import('./js/settings.js').then(m=>m.open());document.getElementById('set-mail-nav').click();});
   await page.waitForFunction(()=>!document.getElementById('set-mail').hidden&&document.getElementById('mail-blocked').children.length);
   await page.locator('#mail-allow-card').evaluate(e=>{e.open=true;e.scrollIntoView({block:'start'});});
   await page.locator('#mail-blocked button').click();await page.waitForFunction(()=>document.getElementById('mail-allow').children.length);
   check('#395 recommended sender is guest '+lang,()=>assert.equal(fake.st.mail.config.allow[0].role,'guest'));
   const role=page.locator('#mail-allow select');const awaitCount=await role.count(); // count separately below to retain independent old-code failures
   check('#395 compact role picker '+lang,()=>assert.equal(awaitCount,1));
   if(awaitCount){await role.selectOption('owner');await page.waitForFunction(()=>document.querySelector('#mail-allow select').value==='owner');await role.selectOption('guest');await page.waitForFunction(()=>document.querySelector('#mail-allow select').value==='guest');assert.equal(fake.st.mail.config.allow[0].role,'guest');}
   await page.locator('#mail-add-addr').fill('new@example.org');
   const geometry=await page.evaluate(()=>{const role=document.querySelector('#mail-allow select'),a=document.querySelector('#mail-allow li>span'),add=document.querySelector('#mail-add-role'),input=document.querySelector('#mail-add-addr');const rr=role.getBoundingClientRect(),ar=a.getBoundingClientRect(),nr=add.getBoundingClientRect(),ir=input.getBoundingClientRect();return {same:Math.abs(rr.y+rr.height/2-ar.y-ar.height/2)<2,right:rr.x>ar.x,compact:rr.width<=70,newSame:Math.abs(nr.y-ir.y)<2,newRight:nr.x>ir.x,tap:rr.height>=44};});
   check('compact role geometry '+lang+' '+width,()=>assert.ok(Object.values(geometry).every(Boolean),JSON.stringify(geometry)));
   if(capture){writeFileSync(resolve(out,`${phase}-${lang}-${theme}-${width}-theme.json`),JSON.stringify(await page.evaluate(()=>({theme:document.documentElement.dataset.theme,settingsBackground:getComputedStyle(document.getElementById('settings')).backgroundColor,roleColor:getComputedStyle(document.getElementById('mail-add-role')).color})),null,2));await page.locator('#mail-add-addr').evaluate(e=>e.scrollIntoView({block:'center'}));await page.screenshot({path:resolve(out,`${phase}-${lang}-${theme}-${width}-chip.png`)});await role.focus();await page.keyboard.press('Space');await page.screenshot({path:resolve(out,`${phase}-${lang}-${theme}-${width}-menu.png`)});await page.keyboard.press('Escape');}
   const sw=page.locator('#mail-mark-read');const exists=await sw.count();check('#397 switch exists '+lang,()=>assert.equal(exists,1));
   if(exists){assert.equal(await sw.getAttribute('aria-checked'),'true');await sw.click();await page.waitForFunction(()=>document.getElementById('mail-mark-read').getAttribute('aria-checked')==='false');assert.equal(fake.st.mail.config.mark_processed_read,false);await sw.click();}
   if(capture){await page.locator(exists?'#mail-mark-read':'#mail-poll').evaluate(e=>e.scrollIntoView({block:'center'}));await page.screenshot({path:resolve(out,`${phase}-${lang}-${theme}-${width}-mark-read.png`)});await page.locator('#mail-allow-card').evaluate(e=>e.scrollIntoView({block:'center'}));await page.screenshot({path:resolve(out,`${phase}-${lang}-${theme}-${width}-mail.png`)});}
   await page.evaluate(async()=>await import('./js/settings.js').then(m=>m.close()));
   const text=lang==='zh'?'应用市场里的 Agent J 今天可用。':'The Agent J in the app store is ready.';
   await fake.addTurn({k:'phone',dev:'other',text:'question'},text);await page.waitForFunction(t=>document.querySelector('#words').textContent.includes(t),text);
   // Internal links are actual buttons emitted by five-pages; mirror those DOM semantics for each selectable chip class.
   for(const cls of ['fp-link-card','fp-link-person','fp-link-doc','fp-link-skill','fp-link-memory']){
    const selection=await page.evaluate(([text,cls])=>{const p=document.querySelector('#words p');p.replaceChildren(document.createTextNode(text.slice(0,text.indexOf('Agent J'))));const b=document.createElement('button');b.className='fp-link '+cls;b.textContent='Agent J';p.append(b,document.createTextNode(text.slice(text.indexOf('Agent J')+7)));const range=document.createRange();range.selectNodeContents(p);getSelection().removeAllRanges();getSelection().addRange(range);return getSelection().toString();},[text,cls]);
    check('#407 native selection '+cls+' '+lang,()=>assert.equal(selection,text));
   }
   if(capture)await page.screenshot({path:resolve(out,`${phase}-${lang}-${theme}-${width}-links.png`)});
   await page.evaluate(()=>{document.dispatchEvent(new Event('selectionchange'));});await page.waitForTimeout(650);
   const quote=await page.evaluate(async text=>({actual:document.getElementById('qbText').textContent,want:(await import('./js/t.js')).t('r.quote.wrap',{text})}),text);check('#407 quote excerpt '+lang,()=>assert.equal(quote.actual,quote.want));
   // The reader must support starting and ending inside a link's label too.
   await page.evaluate(async()=>{getSelection().removeAllRanges();await import('./js/relay.js').then(m=>m.remoteReader(true));});await page.waitForTimeout(700);
   const readerText=await page.evaluate(()=>{const p=document.querySelector('#rdWords p');p.replaceChildren();const b=document.createElement('button');b.className='fp-link fp-link-workflow';b.textContent='Agent J';p.append(b);const r=document.createRange();r.selectNodeContents(b);getSelection().removeAllRanges();getSelection().addRange(r);document.dispatchEvent(new Event('selectionchange'));return getSelection().toString();});
   check('#407 reader selection '+lang,()=>assert.equal(readerText,'Agent J'));await page.waitForTimeout(650);
   const quoted=await page.locator('#qbar .qw').allTextContents();check('#407 reader quote '+lang,()=>assert.ok(quoted.includes('Agent J')));
   await page.keyboard.press(process.platform==='darwin'?'Meta+c':'Control+c');const clip=await page.evaluate(()=>navigator.clipboard.readText());check('#407 reader copy '+lang,()=>assert.equal(clip,'Agent J'));
   await page.evaluate(async()=>await import('./js/relay.js').then(m=>m.remoteReader(false)));



  }finally{await page.close();}
 }
 writeFileSync(resolve(out,phase+'-web-results.json'),JSON.stringify({failures},null,2)+'\n');
 if(failures.length)process.exitCode=1;
}finally{await browser.close();await fake.stop();await web.stop();}
