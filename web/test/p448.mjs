// P448: strict state contrast, trusted touch holds and selectable content. Local fake host only.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {generateKeyPairSync} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,shoot,sleep} from './browser.mjs';
const out='reports/qa/p448';mkdirSync(out,{recursive:true});
const before=process.env.P448_BEFORE==='1', checks=[],failures=[];
const web=await startWebServer(process.env.P448_PUBLIC_DIR?{publicDir:process.env.P448_PUBLIC_DIR}:{}),fake=await startFakeHost(),B=await launch();
// Actual compositing, including faded ancestors. No large-text, disabled or green-fill exemption.
const audit=`(()=>{
 const rgb=s=>{const a=s.match(/[\\d.]+/g).map(Number),k=s.startsWith('color(')?255:1;return [...a.slice(0,3).map(v=>v*k),a[3]??1]};
 const over=(a,b)=>a.slice(0,3).map((v,i)=>v*a[3]+b[i]*(1-a[3]));
 const L=c=>c.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);
 const bg=n=>{if(!n)return [250,249,245];const s=getComputedStyle(n),under=bg(n.parentElement),q=getComputedStyle(n,'::after'),back=q.content!=='none'&&q.position==='absolute'&&q.zIndex==='-1'?q.backgroundColor:s.backgroundColor,own=over(rgb(back),under),o=+s.opacity;return own.map((v,i)=>v*o+under[i]*(1-o));};
 return [...document.querySelectorAll('button,[role=button],a.aj-btn')].filter(n=>n.getClientRects().length&&!n.closest('[hidden]')).flatMap(n=>{
  const nodes=[n,...n.querySelectorAll('*')].filter(e=>e===n||[...e.childNodes].some(x=>x.nodeType===3&&x.textContent.trim()));
  return nodes.filter(e=>e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden'&&[...e.childNodes].some(t=>t.nodeType===3&&t.textContent.trim())&&parseFloat(getComputedStyle(e).fontSize)>0).map(e=>{let alpha=1;for(let a=e;a;a=a.parentElement)alpha*=+getComputedStyle(a).opacity;const ground=bg(e),f=rgb(getComputedStyle(e).color);f[3]*=alpha;const ink=over(f,ground),a=L(ink),b=L(ground);return {id:n.id||n.className,text:e.textContent.trim().slice(0,45),parent:n.parentElement.id+' '+n.parentElement.className,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05),fg:ink,bg:ground};});
 });
})()`;
try{
 for(const width of [390,1440])for(const theme of ['light','dark'])for(const lang of ['zh','en']){
  const tag=`${lang}-${theme}-${width}`;if(process.env.P448_ONLY&&tag!==process.env.P448_ONLY)continue;fake.reset();fake.setUp(true);
  const p=await newPage(B,width,width===390?844:900,theme,{touch:width===390,ua:width===390?(lang==='zh'?'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1':'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/130.0.0.0 Mobile Safari/537.36'):undefined,allow:[web.url,fake.relay+'/']});
  try{
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.documentElement.dataset.theme='${theme}'`);
   await fake.say('Selectable message.\n\n```text\ncopy this code\n```','agent');
   await waitFor(p,`document.getElementById('words').textContent.includes('Selectable')`);
   const {root}=await p.send('DOM.getDocument');
   await p.send('CSS.enable');
   const auditStates=async(surface)=>{
    await evaluate(p,`import('./js/ui.js').then(m=>m.toastOff())`);
    await evaluate(p,`for(const e of document.querySelectorAll('button,[role=button],a.aj-btn'))e.toggleAttribute('data-p448-audit',!!e.getClientRects().length&&!e.closest('[hidden]'))`);
    const {nodeIds}=await p.send('DOM.querySelectorAll',{nodeId:root.nodeId,selector:'[data-p448-audit]'});
    for(const state of ['normal','hover','active','focus','disabled']){
     if(state==='disabled')await evaluate(p,`window.p448Disabled=[...document.querySelectorAll('button')].map(e=>[e,e.disabled]);for(const [e]of window.p448Disabled)e.disabled=true`);
     await Promise.all(nodeIds.map(nodeId=>p.send('CSS.forcePseudoState',{nodeId,forcedPseudoClasses:state==='normal'||state==='disabled'?[]:state==='focus'?['focus','focus-visible']:[state]})));
     await sleep(60);
     const rows=await evaluate(p,audit);checks.push({tag,surface,state,rows});
     if(surface==='chat'){
      const glyph=await evaluate(p,`(()=>{const b=document.getElementById('webReportBtn');return {stroke:getComputedStyle(b.querySelector('g')).stroke,disc:getComputedStyle(b.querySelector('.disc')).fill}})()`);
      assert.equal(glyph.stroke,'rgb(44, 43, 38)',tag+' '+state+' P449 report glyph Noir');
      assert.equal(glyph.disc,'rgb(250, 249, 245)',tag+' '+state+' P449 report disc Lotion');
     }
     for(const r of rows)if(r.ratio<4.5)failures.push({tag,surface,state,...r});
     if(['sudo','settings','permission','menu'].includes(surface))writeFileSync(`${out}/${tag}-${surface}-${state}.png`,await shoot(p,''));
     if(state==='disabled')await evaluate(p,`for(const [e,d]of window.p448Disabled)e.disabled=d;delete window.p448Disabled`);
    }
    for(const nodeId of nodeIds)await p.send('CSS.forcePseudoState',{nodeId,forcedPseudoClasses:[]});
   };
   await evaluate(p,`document.getElementById('om').dataset.unread='1';document.querySelector('#omUnread .unb').textContent='2'`);
   await auditStates('chat');
   // P449 RESUME 6: exercise both busy tabs, selected/unselected, in all five control states.
   await evaluate(p,`document.getElementById('busyDlg').hidden=false`);
   for(const selected of ['busyTasksTab','busyWorkflowTab']){
    await evaluate(p,`for(const e of document.querySelectorAll('.busy-tabs button'))e.setAttribute('aria-selected',String(e.id==='${selected}'))`);
    await auditStates('busy-'+selected);
   }
   await evaluate(p,`document.getElementById('busyDlg').hidden=true`);
   // Real touch input, not dispatchEvent: all toolbar buttons must resist system selection.
   if(width===390){
    for(const id of ['webReportBtn','tDoc','tPhoto','slashBtn']){
     assert.equal(await evaluate(p,`getComputedStyle(document.getElementById('${id}')).userSelect`),'none');
     assert.equal(await evaluate(p,`(()=>{const e=new Event('contextmenu',{bubbles:true,cancelable:true});document.getElementById('${id}').dispatchEvent(e);return e.defaultPrevented})()`),true);
     await evaluate(p,`window.getSelection().removeAllRanges()`);
     const r=await evaluate(p,`(()=>{const r=document.getElementById('${id}').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
     await p.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{...r,radiusX:4,radiusY:4}]});await sleep(700);
     assert.equal(await evaluate(p,`String(window.getSelection())`),'',tag+' '+id+' hold selection');
     await p.send('Input.dispatchTouchEvent',{type:'touchCancel',touchPoints:[]});
     await evaluate(p,`document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))`);await sleep(300);
    }
   }
   await fake.say('Selectable message.\n\n```text\ncopy this code\n```','agent');await sleep(250);
   for(const selector of ['#words p','#words pre']){
    const selected=await evaluate(p,`(()=>{const e=document.querySelector('${selector}'),s=getSelection(),r=document.createRange();r.selectNodeContents(e);s.removeAllRanges();s.addRange(r);return {text:String(s),select:getComputedStyle(e).userSelect}})()`);
    assert.ok(selected.text.length);assert.notEqual(selected.select,'none');
   }
   assert.equal(await evaluate(p,`(()=>{const e=new Event('contextmenu',{bubbles:true,cancelable:true});document.querySelector('#words pre').dispatchEvent(e);return e.defaultPrevented})()`),false,'code keeps native menu');
   await evaluate(p,`getSelection().removeAllRanges()`);
   // Actual sudo inset: no password is entered, no command can execute.
   const id='a'.repeat(32),n='b'.repeat(32),epk=generateKeyPairSync('x25519').publicKey.export({format:'jwk'}).x;
   await fake.send({t:'elev',id,kind:'sudo',n,epk,ttl:600,tries:3,cmd:'systemctl status backup',why:'Check backup',effect:'Read status'});
   await waitFor(p,`document.getElementById('elev-allow')?.getClientRects().length>0`);
   assert.equal(await evaluate(p,`document.getElementById('reply-notices').contains(document.getElementById('elev-run'))`),true);
   await auditStates('sudo');
   await evaluate(p,`import('./js/elevate.js').then(m=>m.done({id:'${id}',result:'denied'}))`);
   await fake.ask({id:'c'.repeat(32),tool:'Bash',summary:'echo safe fixture',ttl:600,cat:['read']});
   await waitFor(p,`document.body.dataset.sheet==='1'`);await auditStates('permission');
   await fake.send({t:'ask_done',id:'c'.repeat(32),result:'denied'});
   await evaluate(p,`document.getElementById('setBtn').click()`);await waitFor(p,`!document.getElementById('settings').hidden`);await auditStates('settings');
   if(width===390){
    assert.equal(await evaluate(p,`getComputedStyle(document.getElementById('srchBtn')).userSelect`),'none','search native selection guard');
    const r=await evaluate(p,`(()=>{const r=document.getElementById('srchBtn').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
    await evaluate(p,`getSelection().removeAllRanges()`);
    await p.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[r]});await sleep(750);
    assert.equal(await evaluate(p,`String(getSelection())`),'',tag+' search hold');
    await p.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
    assert.equal(await evaluate(p,`document.getElementById('set-find').hidden`),true,'hold does not also tap search');
    await sleep(1000);
    await p.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[r]});await sleep(40);await p.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
    await waitFor(p,`!document.getElementById('set-find').hidden`);
   }

   await evaluate(p,`import('./js/settings.js').then(m=>m.close());import('./js/five-pages.js').then(m=>m.toggleMenu())`);await auditStates('menu');console.log('checked '+tag);
  }finally{await p.dispose()}
  if(before){writeFileSync(`${out}/before-checks.json`,JSON.stringify({checks,failures},null,2));console.log(JSON.stringify(failures.slice(0,60),null,2));process.exitCode=0;break;}
 }
}finally{await B.close();await fake.stop();await web.stop();}
writeFileSync(`${out}/${before?'before':'after'}-checks.json`,JSON.stringify({checks,failures},null,2)+'\n');
console.log(JSON.stringify({states:checks.length,failures:failures.length,samples:failures.slice(0,30)},null,2));
if(!before)assert.deepEqual(failures,[],'every button state >=4.5:1');
