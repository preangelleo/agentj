import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,shoot} from './browser.mjs';
const phase=process.env.PAIR_PHASE||'after',out=`reports/qa/p0285-pair-actions/${phase}`;
mkdirSync(out,{recursive:true});
const web=await startWebServer(process.env.PAIR_PUBLIC?{publicDir:process.env.PAIR_PUBLIC}:{}),fake=await startFakeHost(),browser=await launch(),rows=[];
try {
 for(const width of [390,768,1440])for(const lang of ['zh','en'])for(const theme of ['dark','light']) {
  fake.reset();fake.st.prefs={appearance:{language:lang}};
  fake.st.remotes={items:[],limit:10,can_add:true,link:'https://example.invalid/synthetic-pairing',qr:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4+eMrAAXUAueiCJL+AAAAAElFTkSuQmCC'};
  const page=await newPage(browser,width,1000,theme,{allow:[web.url,fake.relay+'/']});
  try {
   await navigate(page,fake.newPairing(web.url+'?lang='+lang));await waitState(page,'awaiting-approval');await fake.approve();await waitState(page,'ready');
   await evaluate(page,`import('./js/settings.js').then(m=>m.open('remotes',true))`);
   await waitFor(page,`!document.getElementById('rm-copy').hidden`);
   await evaluate(page,`document.fonts.ready.then(()=>{document.getElementById('a2hs-ok')?.click();document.getElementById('phone-env-dismiss')?.click();document.getElementById('rm-addbox').scrollIntoView({block:'center'});})`);
   const geometry=await evaluate(page,`(()=>{
    const rect=n=>{const r=n.getBoundingClientRect();return{x:r.x,y:r.y,w:r.width,h:r.height,right:r.right,bottom:r.bottom};};
    const card=document.getElementById('rm-addbox'),group=document.getElementById('rm-addacts'),copy=document.getElementById('rm-copy'),cancel=document.getElementById('rm-cancel');
    const a=rect(copy),b=rect(cancel),c=rect(card),label=rect(copy.querySelector('span')),steps=rect(card.querySelector('.rmsteps'));
    const hit=n=>{const r=n.getBoundingClientRect();return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.closest('button')===n;};
    return{card:c,copy:a,cancel:b,label,steps,group:rect(group),border:getComputedStyle(copy).borderTopWidth,radius:getComputedStyle(copy).borderRadius,icon:!!copy.querySelector('svg'),hit:hit(copy)&&hit(cancel),overflow:document.documentElement.scrollWidth>innerWidth};
   })()`);
   const issues=[];for(const [name,r]of[['copy',geometry.copy],['cancel',geometry.cancel]]){if(r.w<44||r.h<44)issues.push(name+' target');if(r.x<geometry.card.x||r.right>geometry.card.right+1||r.bottom>geometry.card.bottom+1)issues.push(name+' outside card');}
   if(geometry.label.x<geometry.copy.x||geometry.label.right>geometry.copy.right+1)issues.push('label outside button');
   if(geometry.copy.y<geometry.steps.bottom-1)issues.push('above instructions');
   if(Math.abs(geometry.copy.h-geometry.cancel.h)>1)issues.push('unequal heights');
   if(parseFloat(geometry.border)<1)issues.push('missing outline');
   if(!geometry.hit)issues.push('button hit obstructed');if(geometry.overflow)issues.push('page overflow');
   if(width===390&&geometry.cancel.y<geometry.copy.bottom-1)issues.push('narrow actions not stacked');
   if(width===1440&&Math.abs(geometry.copy.y-geometry.cancel.y)>1)issues.push('wide actions not inline');
   const file=`${out}/${width}-${lang}-${theme}.png`;writeFileSync(file,await shoot(page));
   // Physical pointer input must reach the existing copy and cancel handlers.
   await evaluate(page,`import('./js/md.js').then(m=>{window.__pairCopyCount=0;m.RelayMD.copyText=async text=>{if(text==='https://example.invalid/synthetic-pairing')window.__pairCopyCount++;return true;};})`);
   const click=async r=>{await page.send('Input.dispatchMouseEvent',{type:'mousePressed',x:r.x+r.w/2,y:r.y+r.h/2,button:'left',clickCount:1});await page.send('Input.dispatchMouseEvent',{type:'mouseReleased',x:r.x+r.w/2,y:r.y+r.h/2,button:'left',clickCount:1});};
   await click(geometry.copy);await waitFor(page,'window.__pairCopyCount===1');const cancelNow=await evaluate(page,`(()=>{const n=document.getElementById('rm-cancel');n.scrollIntoView({block:'center'});const r=n.getBoundingClientRect();return{x:r.x,y:r.y,w:r.width,h:r.height};})()`);await click(cancelNow);await waitFor(page,`document.getElementById('rm-addbox').hidden`);
   rows.push({width,lang,theme,...geometry,issues,file,copyClicks:1,cancelClosed:true});
   console.log(phase,width,lang,theme,issues.length?issues.join(', '):'PASS');
  }finally{await page.dispose();}
 }
}finally{writeFileSync(`${out}/measurements.json`,JSON.stringify({base:'76d37df',phase,fixture:'isolated Noise fakehost; no real pairing QR or account metadata',rows},null,2));await browser.close();await fake.stop();await web.stop();}
if(phase!=='before')assert.equal(rows.flatMap(r=>r.issues).length,0,'pair actions must fit, stack, and remain hittable');
