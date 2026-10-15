// P264: local settings-search index, contextual shortcuts, scan and explicit-send boundary.
import assert from 'node:assert/strict';
import {writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate,navigate,waitState,waitFor,key} from './browser.mjs';
let web, fake, B;
const out=new URL('../../../reports/qa/p264/',import.meta.url);let checks=0;
try {
 web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'});fake=await startFakeHost();B=await launch();
 const p=await newPage(B,393,852,'dark',{allow:[web.url,fake.relay+'/'],touch:true});
 await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 const ev=s=>evaluate(p,s),click=id=>ev(`document.getElementById('${id}').click()`);
 await click('setBtn');
 await ev(`document.getElementById('setBtn').dispatchEvent(new PointerEvent('contextmenu',{bubbles:true,cancelable:true,button:2,pointerType:'mouse'}))`);
 assert.ok(await ev(`!document.getElementById('confirm').hidden && document.getElementById('confirm').textContent.includes('长按') && document.getElementById('confirm-yes').textContent==='知道了'`));checks++;await click('confirm-yes');
 await ev(`document.getElementById('setBtn').dispatchEvent(new PointerEvent('contextmenu',{bubbles:true,cancelable:true,pointerType:'touch'}))`);assert.ok(await ev(`document.getElementById('confirm').hidden`));checks++;
 const found=await ev(`(async()=>{const s=await import('./js/settings.js');return {title:s.searchItems('好友').map(n=>n.id),description:s.searchItems('对外接待客人').map(n=>n.id),keyword:s.searchItems('customer service').map(n=>n.id),private:s.searchItems('MY PRIVATE PASSWORD').length};})()`);
 assert.ok(found.title.includes('open-friends'));assert.ok(found.description.includes('open-bots'));assert.ok(found.keyword.includes('open-bots'));assert.equal(found.private,0);checks+=4;
 await click('srchBtn');await ev(`document.getElementById('set-find-input').value='NO SUCH SETTING';document.getElementById('set-find-input').dispatchEvent(new Event('input'))`);
 assert.ok(await ev(`document.getElementById('set-find-results').textContent.includes('没找到')`));checks++;
 await click('scanBtn');await waitFor(p,`document.getElementById('settings').hidden && document.body.dataset.view==='scan'`);checks++;
 await click('scan-back');await waitFor(p,`document.body.dataset.view==='chat'`);
 await key(p,'k',{ctrl:true,code:'KeyK'});await waitFor(p,`!document.getElementById('srch').hidden`);checks++;
 await click('srchClose');await click('setBtn');await click('open-friends');await click('assist-friends');
 assert.equal(fake.log.filter(m=>m.t==='say').length,0);checks++;
 await click('send');
 for(let n=0;n<100&&!fake.log.some(m=>m.t==='say');n++)await new Promise(r=>setTimeout(r,30));
 const sent=fake.log.find(m=>m.t==='say');assert.ok(sent?.text.startsWith('/agentj-friends'));assert.ok(sent.text.includes('来自设置页按钮'));checks+=2;
 assert.equal(p.problems.length,0,p.problems.join('\n'));await p.dispose();
 writeFileSync(new URL('extra-result.json',out),JSON.stringify({ok:true,checks},null,2)+'\n');console.log('P264 extra PASS',checks);
} finally {await Promise.allSettled([B?.close(),fake?.stop(),web?.stop()]);}
