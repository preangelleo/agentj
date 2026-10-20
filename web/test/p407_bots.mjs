// P407 Chromium behavior tests: real DOM, synthetic messages; no live model/relay/account.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFileSync,mkdirSync,writeFileSync} from 'node:fs';
import {parityRecorder} from '../../parity/lib.mjs';
import {launch,newPage,navigate,evaluate,waitFor,shoot} from './browser.mjs';
const chatSource=readFileSync(new URL('../../site/static/bots-assets/chat.js',import.meta.url),'utf8');
const embedSource=readFileSync(new URL('../../site/static/bots-assets/embed.js',import.meta.url),'utf8');
const keys=await crypto.subtle.generateKey('Ed25519',true,['sign','verify']);
let url;
const b64=b=>Buffer.from(b).toString('base64url');
const canonical=v=>v&&typeof v==='object'?(Array.isArray(v)?'['+v.map(canonical).join(',')+']':'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}'):JSON.stringify(v);
const server=createServer(async(req,res)=>{
 res.setHeader('content-type','text/html');
 if(req.url==='/chat.js'){
  res.setHeader('content-type','application/javascript');
  res.end(chatSource.split('// Parent only transfers')[0]+`\nseal=async obj=>JSON.stringify(obj);admitted=true;send.disabled=false;window.out=[];ws={send(data){out.push(JSON.parse(data));}};window.api={configure(c){handleReply({t:'config',...c});},ack(extra={}){handleReply({t:'reply',r:activeRequest,text:'answer',...extra});},flush,show,ready(){retryAt=0;sentAt=[];return flush();},draft(){return {pending,activeText,busy};}};`);return;
 }
 if(req.url==='/embed.js'){
  res.setHeader('content-type','application/javascript');res.end(embedSource.replace("origin='https://agentj.app'",`origin=${JSON.stringify(url)}`).replace('if(w.open_on_load===true)open();','window.widgetTest={button,panel,open,collapse,get frame(){return frame;}};if(w.open_on_load===true)open();'));return;
 }
 if(req.url==='/bots/test-business/manifest'){
  const manifest={bot:'a'.repeat(32),slug:'test-business',host:b64(await crypto.subtle.exportKey('raw',keys.publicKey)),expires:Math.floor(Date.now()/1000)+299,title:'Test shop',widget:{position:'br',accent:'#629887',open_on_load:false}};
  const sig=b64(await crypto.subtle.sign('Ed25519',keys.privateKey,new TextEncoder().encode('agentjarvis/bot-manifest-v1\n'+canonical(manifest))));res.setHeader('content-type','application/json');res.end(JSON.stringify({manifest,sig}));return;
 }
 if(req.url==='/launcher'){res.end('<!doctype html><meta name="viewport" content="width=device-width"><script src="/embed.js" data-bot="test-business" async></script>');return;}
 if(req.url?.startsWith('/bots/')){res.end('<!doctype html>Encrypted chat placeholder');return;}
 res.end(`<!doctype html><html data-parent-origin="${url}"><meta name="viewport" content="width=device-width"><p id="status"></p><div id="messages"></div><div id="quick"></div><div id="queue" hidden></div><button id="withdraw" hidden>Withdraw</button><form id="chat"><textarea id="message"></textarea><button id="send">Send</button></form><button id="human">Human</button><button id="identity">Identity</button><input id="order"><input id="email"><script type="module" src="/chat.js"></script></html>`);
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));url='http://127.0.0.1:'+server.address().port;
const rec=parityRecorder('web/test/p407_bots.mjs');let passed=false;
const browser=await launch(),out=new URL('../../../reports/qa/p407/browser/',import.meta.url);mkdirSync(out,{recursive:true});let checks=0;
try{
 const p=await newPage(browser,390,844,'light',{allow:[url]});await navigate(p,url);await waitFor(p,'window.api');
 await evaluate(p,`api.configure({handoff_email:true,limits:{max_chars:20,per_minute:2},widget:{queue_merge:true,quick_replies:['Warranty']},welcome:'Welcome to shop',link_domains:['example.com']})`);
 const submit=t=>evaluate(p,`document.getElementById('message').value=${JSON.stringify(t)};document.getElementById('send').click()`);
 await submit('first');await waitFor(p,'out.length===1');await submit('one');await submit('two');
 assert.deepEqual(await evaluate(p,'api.draft().pending'),['one','two']);assert.equal(await evaluate(p,"document.getElementById('queue').hidden"),false);checks++;
 await evaluate(p,'api.ack()');await waitFor(p,'out.length===2');assert.equal(await evaluate(p,'out[1].text'),'one\n\ntwo');checks++;
 await submit('later');await evaluate(p,'api.ack()');await waitFor(p,"document.getElementById('queue').textContent.includes('s')");assert.equal(await evaluate(p,'out.length'),2);checks++;
 await evaluate(p,"document.getElementById('withdraw').click()");assert.equal(await evaluate(p,"document.getElementById('message').value"),'later');assert.equal(await evaluate(p,'api.draft().pending.length'),0);checks++;
 await submit('123456789012345678901');assert.equal(await evaluate(p,'out.length'),2);assert.match(await evaluate(p,"document.getElementById('status').textContent"),/too long/);checks++;
 await evaluate(p,"api.show('AI','<img src=x onerror=alert(1)> [good](https://example.com/doc) [bad](https://evil.example/doc) ```sh\\nsudo rm file\\n```')");
 assert.equal(await evaluate(p,"document.querySelectorAll('#messages img').length"),0);assert.equal(await evaluate(p,"document.querySelectorAll('#messages a').length"),1);assert.equal(await evaluate(p,"document.querySelector('#messages a').hostname"),'example.com');assert.equal(await evaluate(p,"document.querySelectorAll('#messages details pre').length"),1);checks++;
 writeFileSync(new URL('queue-390.png',out),await shoot(p));
 // A server-side minute refusal requeues the same text, then merges with later text.
 await evaluate(p,"document.getElementById('message').value='again';api.ready()");await submit('again');await evaluate(p,'api.ready()');await waitFor(p,'out.length===3');await evaluate(p,"api.ack({status:'queued',retry_after:60})");await submit('more');assert.deepEqual(await evaluate(p,'api.draft().pending'),['again','more']);checks++;
 await evaluate(p,'api.ready()');await waitFor(p,'out.length===4');assert.equal(await evaluate(p,'out[3].text'),'again\n\nmore');checks++;
 await evaluate(p,'api.ack()');
 await evaluate(p,`document.getElementById('email').value='visitor@example.com';Array.from(document.querySelectorAll('button')).find(b=>b.textContent.startsWith('Request email')).click()`);
 await waitFor(p,`out.some(o=>o.t==='email_contact')`);assert.equal(await evaluate(p,`out.find(o=>o.t==='email_contact').email`),'visitor@example.com');checks++;
 await evaluate(p,'api.ack()');await evaluate(p,`document.querySelector('[aria-label="Email code / 邮箱验证码"]').value='123456';Array.from(document.querySelectorAll('button')).find(b=>b.textContent.startsWith('Verify email')).click()`);
 await waitFor(p,`out.some(o=>o.t==='email_verify')`);assert.equal(await evaluate(p,`out.find(o=>o.t==='email_verify').code`),'123456');checks++;
 assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);
 for(const width of [390,1440]){
  const page=await newPage(browser,width,900,'light',{allow:[url]});await navigate(page,url+'/launcher');await waitFor(page,'window.widgetTest');await evaluate(page,'widgetTest.button.click()');
  const size=await evaluate(page,'({w:widgetTest.panel.getBoundingClientRect().width,h:widgetTest.panel.getBoundingClientRect().height,src:widgetTest.frame.src,expanded:widgetTest.button.getAttribute("aria-expanded")})');assert.ok(size.w<=width);assert.equal(size.expanded,'true');assert.equal(size.src,url+'/bots/test-business');if(width===1440)assert.ok(Math.abs(size.h/size.w-16/9)<0.01);checks++;writeFileSync(new URL('launcher-open-'+width+'.png',out),await shoot(page));
  await evaluate(page,"widgetTest.frame.dataset.persist='yes';widgetTest.collapse();widgetTest.open()");assert.equal(await evaluate(page,'widgetTest.frame.dataset.persist'),'yes');checks++;
  await evaluate(page,'widgetTest.button.focus()');await page.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});await waitFor(page,'widgetTest.panel.hidden');checks++;
  writeFileSync(new URL('launcher-'+width+'.png',out),await shoot(page));assert.deepEqual(page.problems,[]);assert.deepEqual(page.offsite,[]);
 }
 writeFileSync(new URL('results.json',out),JSON.stringify({passed:checks,scope:'Local Chromium DOM; synthetic queue/metadata; crypto tested separately by p82-public-browser'},null,2));console.log('P407 browser PASS '+checks);passed=true;
}finally{rec.record('P407 browser',passed);rec.write();await browser.close();await new Promise(r=>server.close(r));}
