// Independent R2 acceptance: actual shipped public bytes versus the candidate.
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdtempSync,mkdirSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {startWebServer,PUBLIC_DIR} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,key,shoot} from './browser.mjs';
const fixtureNow=Date.now();
const baseline='70c8cf3e5', source=execFileSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).trim(),candidate=process.env.AJ_0335_R2_CANDIDATE||source;
const out=join(process.env.OLARES_RUN_OUT||'reports/qa/p0335-r2','shots-r2');mkdirSync(out,{recursive:true});
const temp=mkdtempSync(join(tmpdir(),'aj-0335-r2-'));
execFileSync('tar',['-xf','-','-C',temp],{input:execFileSync('git',['archive',baseline,'web/public'],{maxBuffer:50*1024*1024})});
let candidateDir=PUBLIC_DIR;
if(candidate!==source){candidateDir=join(temp,'candidate');mkdirSync(candidateDir);execFileSync('tar',['-xf','-','-C',candidateDir],{input:execFileSync('git',['archive',candidate,'web/public'],{maxBuffer:50*1024*1024})});candidateDir=join(candidateDir,'web/public');}
const old=await startWebServer({publicDir:join(temp,'web/public')}), fresh=await startWebServer({publicDir:candidateDir});
const fake=await startFakeHost({visualSeed:'0335-r2-public'}), B=await launch(), rows=[];
// Repaint the full surface before both captures: incremental rounded-corner
// rasterisation otherwise differs by3 RGB pixels after an unchanged-node repaint.
async function stableShot(p){await evaluate(p,`(async()=>{const s=document.body.style.visibility;document.body.style.visibility='hidden';await new Promise(requestAnimationFrame);document.body.style.visibility=s;await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame)})()`);return shoot(p);}
async function click(p,selector){const pos=await evaluate(p,`(()=>{const e=document.querySelector(${JSON.stringify(selector)});e.scrollIntoView({block:'center'});const r=e.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);for(const type of ['mousePressed','mouseReleased'])await p.send('Input.dispatchMouseEvent',{type,button:'left',clickCount:1,...pos});}
async function dom(p){return evaluate(p,`(()=>{const path=e=>e.id?'#'+e.id:e.parentElement?path(e.parentElement)+'>'+e.tagName.toLowerCase()+':nth-child('+([...e.parentElement.children].indexOf(e)+1)+')':e.tagName;return [...document.querySelectorAll('body *')].filter(e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none'&&r.bottom>0&&r.top<innerHeight&&!e.closest('template')}).map(e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return {path:path(e),tag:e.tagName,role:e.getAttribute('role'),text:e.children.length?null:e.textContent.trim(),rect:[r.x,r.y,r.width,r.height].map(x=>Math.round(x*100)/100),style:Object.fromEntries(['display','position','fontFamily','fontSize','fontWeight','lineHeight','color','backgroundColor','padding','margin','borderRadius','gap','justifyContent','alignItems'].map(k=>[k,s[k]]))}})})()`);}
async function pixels(p,a,b){return evaluate(p,`(async()=>{const load=src=>new Promise((resolve,reject)=>{const i=new Image();i.onload=()=>resolve(i);i.onerror=reject;i.src=src});const [a,b]=await Promise.all([load('data:image/png;base64,${a.toString('base64')}'),load('data:image/png;base64,${b.toString('base64')}')]);const c=document.createElement('canvas');c.width=a.width;c.height=a.height;const x=c.getContext('2d');x.drawImage(a,0,0);const aa=x.getImageData(0,0,c.width,c.height);x.clearRect(0,0,c.width,c.height);x.drawImage(b,0,0);const bb=x.getImageData(0,0,c.width,c.height),d=x.createImageData(c.width,c.height);let count=0,minX=c.width,minY=c.height,maxX=-1,maxY=-1;for(let i=0;i<aa.data.length;i+=4){const different=[0,1,2,3].some(j=>aa.data[i+j]!==bb.data[i+j]);if(different){count++;const px=(i/4)%c.width,py=Math.floor(i/4/c.width);minX=Math.min(minX,px);maxX=Math.max(maxX,px);minY=Math.min(minY,py);maxY=Math.max(maxY,py)}d.data.set(different?[255,0,80,255]:[bb.data[i],bb.data[i+1],bb.data[i+2],60],i)}x.putImageData(d,0,0);return{count,bounds:count?[minX,minY,maxX,maxY]:null,width:c.width,height:c.height,png:c.toDataURL('image/png').split(',')[1]}})()`);}
try{
 for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  const id=`${lang}-${theme}-${width}`, captures=[];
  for(const [label,web]of [['v03021',old],['candidate',fresh]]){
   fake.reset();fake.st.prefs={appearance:{language:lang,theme}};fake.st.menu={source:'file',items:[{cmd:'/owner-command',desc:'Owner command'}],skills:[]};
   fake.st.turns=[{id:1,ts:fixtureNow-60000,src:{k:'phone',text:'Owner asks for summary'},reply:{text:'The final main Agent reply'},end:'done'}];fake.st.nextId=2;
   const p=await newPage(B,width,900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
   try{
    await p.send('Page.addScriptToEvaluateOnNewDocument',{source:`Date.now=()=>${fixtureNow};`});
    await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await waitFor(p,`document.getElementById('words').textContent.includes('The final main Agent reply')`);
    await evaluate(p,`document.fonts.ready`);await evaluate(p,`(()=>{const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s)})()`);
    // Capture DOM after the same settled repaint used by the pixel evidence.
    const png=await stableShot(p),snap=await dom(p);writeFileSync(join(out,`${label}-${id}-main.png`),png);writeFileSync(join(out,`${label}-${id}-dom.json`),JSON.stringify(snap,null,2));captures.push({label,snap,png});
    if(label==='candidate'&&process.env.AJ_0335_R2_INTERACTIONS!=='0'){
     assert.equal(await evaluate(p,`document.getElementById('commandsOpen')`),null);
     await fake.addTurn({k:'auto',text:'Background fixture'},'Automation result');await fake.addTurn({k:'sys',text:'System fixture'},'System result');await fake.addTurn({k:'agent',via:'hook',text:'Webhook fixture'},'Webhook failure','failed',{activity_failure:true});
     await waitFor(p,`document.getElementById('pg').textContent==='1 / 1'`);
     assert.equal(await evaluate(p,`document.getElementById('words').textContent.trim()`),'The final main Agent reply');
     const afterArrival=await stableShot(p),arrivalDiff=await pixels(p,png,afterArrival);
     writeFileSync(join(out,`candidate-${id}-after-background-main.png`),afterArrival);
     writeFileSync(join(out,`arrival-diff-${id}.png`),Buffer.from(arrivalDiff.png,'base64'));delete arrivalDiff.png;
     writeFileSync(join(out,`${id}-arrival-comparison.json`),JSON.stringify({pixels:arrivalDiff,domBefore:snap,domAfter:await dom(p)},null,2));
     assert.equal(arrivalDiff.count,0,'Silent background arrivals must not change any main pixel');assert.deepEqual(await dom(p),snap,'Silent arrivals must not add main DOM elements');
     await click(p,'#slashBtn');await waitFor(p,`!document.getElementById('menu').hidden&&document.getElementById('backgroundCommandEntry')`);
     assert.match(await evaluate(p,`document.getElementById('backgroundCommandEntry').textContent`),new RegExp(lang==='zh'?'后台动态':'Background activity'));
     writeFileSync(join(out,`candidate-${id}-commands.png`),await shoot(p));await click(p,'#backgroundCommandEntry');await waitFor(p,`!document.getElementById('silent').hidden`);
     assert.equal(await evaluate(p,`document.querySelectorAll('#silentList details').length`),3);
     writeFileSync(join(out,`candidate-${id}-background.png`),await shoot(p));await key(p,'Escape');
    }
    assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);
   }finally{await p.dispose()}
  }
  const [a,b]=captures, comparePage=await newPage(B,width,900,theme);
  let pix;try{pix=await pixels(comparePage,a.png,b.png)}finally{await comparePage.dispose()}
  writeFileSync(join(out,`diff-${id}.png`),Buffer.from(pix.png,'base64'));delete pix.png;
  const amap=new Map(a.snap.map(x=>[x.path,x])),bmap=new Map(b.snap.map(x=>[x.path,x]));
  const added=b.snap.filter(x=>!amap.has(x.path)),removed=a.snap.filter(x=>!bmap.has(x.path)),changed=b.snap.filter(x=>amap.has(x.path)&&JSON.stringify(amap.get(x.path))!==JSON.stringify(x)).map(x=>({path:x.path,before:amap.get(x.path),after:x}));
  const row={id,baseline,candidate,pixels:pix,added,removed,changed,mask:[],pass:pix.count===0&&added.length===0&&removed.length===0&&changed.length===0};rows.push(row);writeFileSync(join(out,`${id}-comparison.json`),JSON.stringify(row,null,2));console.log(id,JSON.stringify({pixels:pix.count,added:added.length,removed:removed.length,changed:changed.length}));
 }
 writeFileSync(join(out,'comparison.json'),JSON.stringify({baseline,candidate,fixture:'paired synthetic owner turn; fixed Date.now; animations/transitions/caret disabled identically; no pixel or DOM masks',rows},null,2));
 assert.ok(rows.every(x=>x.pass),'Main layout must match shipped 0.30.21 exactly; inspect comparison.json');console.log('p0335_r2_layout PASS');
}finally{await B.close();await fake.stop();await old.stop();await fresh.stop();rmSync(temp,{recursive:true,force:true})}
