// P581 (#312/#407/#581): select words in a reply card → the quote excerpt and the clipboard keep every visible word,
// including the #580 reply tag (button.rtag, user-select:none by its owner rule), internal links (button.fp-link),
// external links, inline code and reply-option buttons. Real page + fake host, Chromium.
//   AJ_PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node web/test/p581-quote-link.mjs
import assert from 'node:assert/strict';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const {chromium}=await import(process.env.AJ_PLAYWRIGHT_MODULE||'/tmp/aj-webmcp-playwright/node_modules/playwright/index.mjs');
const web=await startWebServer(),fake=await startFakeHost();
const browser=await chromium.launch({executablePath:process.env.CHROMIUM||'/usr/bin/chromium'});
const failures=[];const check=(name,fn)=>{try{fn();console.log('PASS',name);}catch(e){failures.push(name+': '+e.message);console.error('FAIL',name,e.message);}};
const WANT='见（#580）和 Agent J 的官网 agentj.app，运行 agentj seat，再点 好的 继续。';
try{
 fake.reset();fake.st.autoReply=false;fake.st.prefs={appearance:{language:'zh',theme:'light'}};
 const page=await browser.newPage({viewport:{width:390,height:844}});
 await page.context().grantPermissions(['clipboard-read','clipboard-write'],{origin:web.url.replace(/\/$/,'')});
 await page.goto(fake.newPairing(web.url+'?lang=zh'));await page.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await page.waitForFunction(()=>window.__ajState==='ready');
 await page.evaluate(()=>{document.querySelector('#phone-env-dismiss')?.click();document.querySelector('#a2hs-ok')?.click();});
 // The real renderer turns #580 into button.rtag, [..](https) into <a>, `..` into <code>.
 await fake.addTurn({k:'phone',dev:'other',text:'question'},'见（#580）和 Agent J 的官网 [agentj.app](https://agentj.app)，运行 `agentj seat`，再点 好的 继续。');
 await page.waitForFunction(()=>document.querySelector('#words .rtag'));
 const dom=await page.evaluate(()=>{
  const p=document.querySelector('#words .rtag').closest('p');
  // Make sure each kind of node is there in the real DOM shape: an internal link and a reply-option button.
  if(!p.querySelector('button.fp-link')){const w=document.createTreeWalker(p,NodeFilter.SHOW_TEXT);for(let n;(n=w.nextNode());){const i=n.data.indexOf('Agent J');if(i<0)continue;const r=n.splitText(i);r.splitText(7);const b=document.createElement('button');b.className='fp-link fp-link-card';b.textContent='Agent J';r.replaceWith(b);break;}}
  if(!p.querySelector('button.rbtn')){const w=document.createTreeWalker(p,NodeFilter.SHOW_TEXT);for(let n;(n=w.nextNode());){const i=n.data.indexOf('好的');if(i<0)continue;const r=n.splitText(i);r.splitText(2);const b=document.createElement('button');b.setAttribute('data-aj-owner','aj-reply-action');b.type='button';b.className='rbtn';b.textContent='好的';r.replaceWith(b);break;}}
  return {tag:!!p.querySelector('button.rtag'),tagSel:getComputedStyle(p.querySelector('button.rtag')).userSelect,link:!!p.querySelector('button.fp-link'),a:!!p.querySelector('a[href]'),code:!!p.querySelector('code'),rbtn:!!p.querySelector('button.rbtn')};
 });
 check('DOM has rtag/fp-link/a/code/rbtn',()=>assert.ok(dom.tag&&dom.link&&dom.a&&dom.code&&dom.rbtn,JSON.stringify(dom)));
 // Select the whole paragraph the way a finger/mouse would, then let the select-to-quote idle timer fire.
 await page.evaluate(()=>{const p=document.querySelector('#words .rtag').closest('p');const r=document.createRange();r.selectNodeContents(p);getSelection().removeAllRanges();getSelection().addRange(r);document.dispatchEvent(new Event('selectionchange'));});
 await page.waitForTimeout(700);
 const q=await page.evaluate(async want=>({bar:document.getElementById('qbText')?.textContent||'',barWant:(await import('./js/t.js')).t('r.quote.wrap',{text:want}),strip:[...document.querySelectorAll('#qbar .qw')].map(e=>e.textContent)}),WANT);
 check('quote excerpt keeps #580 / Agent J / link / code / button',()=>assert.equal(q.bar,q.barWant,JSON.stringify(q)));
 check('no empty brackets in quote',()=>assert.ok(!/（\s*）/.test(q.bar+q.strip.join('')),JSON.stringify(q)));
 const auto=await page.evaluate(()=>navigator.clipboard.readText());
 check('auto copy on selection = full visible words',()=>assert.equal(auto,WANT));
 // Native copy (Ctrl+C / the phone's Copy) of a fresh selection, before the quote timer moves focus to the composer.
 await page.evaluate(()=>{getSelection().removeAllRanges();document.activeElement?.blur?.();});await page.waitForTimeout(100);
 await page.evaluate(()=>navigator.clipboard.writeText(''));
 await page.evaluate(()=>{const p=document.querySelector('#words .rtag').closest('p');const r=document.createRange();r.selectNodeContents(p);getSelection().removeAllRanges();getSelection().addRange(r);});
 await page.keyboard.press('Control+c');
 const clip=await page.evaluate(()=>navigator.clipboard.readText());
 check('Ctrl+C copy = full visible words',()=>assert.equal(clip,WANT));
 // Native copy has no quote-excerpt limit: preserve the full long user selection.
 await page.evaluate(()=>{getSelection().removeAllRanges();document.activeElement?.blur?.();document.querySelector('#words').textContent='Q'.repeat(2100);const r=document.createRange();r.selectNodeContents(document.querySelector('#words'));getSelection().addRange(r);});
 await page.keyboard.press('Control+c');
 const longcopy=await page.evaluate(()=>navigator.clipboard.readText());
 check('native copy keeps all 2100 selected characters',()=>assert.equal(longcopy,'Q'.repeat(2100)));
 await page.close();
}finally{await browser.close();await fake.stop();await web.stop();}
if(failures.length){console.error(failures.length+' failure(s)');process.exitCode=1;}else console.log('ALL PASS');
