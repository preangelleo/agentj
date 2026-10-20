// P393 #137 · real Chromium + real Noise to the local fake host. The top-left logo robot wears the colour of the state the
// page shows: five states (idle, working, waiting, question, offline), light and dark — the robot's body pixel in a real
// screenshot equals the page's state colour. A theme that changes a state colour without its own logo gets the robot in that
// colour; a theme's own logo is shown as it is (never repainted). Shots → reports/qa/p393/logo/.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,cpSync,mkdtempSync,rmSync,readFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {i18nModule} from '../build.mjs';
import {parityRecorder} from '../../parity/lib.mjs';
import {startWebServer,PUBLIC_DIR} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate as ev,navigate,waitState,waitFor,sleep,shoot} from './browser.mjs';
const out=fileURLToPath(new URL('../../../reports/qa/p393/logo/',import.meta.url));mkdirSync(out,{recursive:true});
const tmp=mkdtempSync(join(tmpdir(),'p393-logo-'));cpSync(PUBLIC_DIR,tmp,{recursive:true});writeFileSync(join(tmp,'i18n.js'),i18nModule());
const rec=parityRecorder('web/test/p393_logo.mjs');let passed=false;
const web=await startWebServer({publicDir:tmp}),fake=await startFakeHost(),B=await launch(),results=[];
const hex=c=>'#'+c.map(v=>v.toString(16).padStart(2,'0')).join('');
const css=c=>{const m=c.match(/^color\(srgb ([^)]+)\)$/);return hex((m?m[1]:c.match(/\(([^)]+)\)/)[1]).split(/[\s,]+/).slice(0,3).map(v=>Math.round(Number(v)*(m?255:1))));};
// the robot's chest, in the 40 px orb: the template's body colour sits at (20, 12) of 40 (a 256 px source's (128, 80))
const robot=async(p,name)=>{const r=await ev(p,`(()=>{const b=document.getElementById('orb').getBoundingClientRect();return {x:Math.round(b.left+b.width*0.5),y:Math.round(b.top+b.height*0.31)};})()`);
 const f=join(out,name+'.png');writeFileSync(f,await shoot(p));
 return execFileSync('magick',[f,'-format',`%[hex:p{${r.x},${r.y}}]`,'info:'],{encoding:'utf8'}).trim().slice(0,6).toLowerCase().replace(/^/,'#');};
// image scaling / colour management moves a channel by a few units: within 6 of the target, and nearer it than any other state
const rgbOf=h=>[1,3,5].map(i=>parseInt(h.slice(i,i+2),16)),dist=(a,b)=>Math.max(...rgbOf(a).map((v,i)=>Math.abs(v-rgbOf(b)[i])));
const near=(px,want,msg)=>assert.ok(dist(px,want)<=6,msg);
const STATES=[['idle',{t:'status',s:'idle'}],['working',{t:'status',s:'working'}],['waiting',{t:'status',s:'waiting',kind:'permission'}],
 ['question',{t:'status',s:'waiting',kind:'question'}],['offline',{t:'status',s:'unknown'}]];
const page=async scheme=>{const p=await newPage(B,390,844,scheme,{allow:[web.url,fake.relay+'/'],touch:true});
 await navigate(p,fake.newPairing(web.url+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await ev(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('input').blur()`);return p;};
const state=p=>ev(p,`({v:document.getElementById('orb').dataset.v,src:document.getElementById('orb').getAttribute('src'),bg:getComputedStyle(document.body).getPropertyValue('--state').trim(),
 page:getComputedStyle(document.body).backgroundColor})`);
try{
 for(const scheme of ['light','dark']){
  fake.reset();const p=await page(scheme);
  for(const [name,msg] of STATES){
   await fake.send(msg);await waitFor(p,`document.getElementById('orb').dataset.v===${JSON.stringify(name)}`);await sleep(150);
   const s=await state(p),px=await robot(p,`${scheme}-${name}`);
   near(px,s.bg,`#137 ${scheme}/${name}: robot ${px} vs state colour ${s.bg} `+JSON.stringify(s));
   assert.ok(!s.src.startsWith('blob:'),'official colours use the official files');
   results.push({scheme,state:name,robot:px,state_colour:s.bg,page:css(s.page),src:s.src});
  }
  // the background reads body[data-status]; so does the logo — even when the status arrives without a render (a lagging copy)
  await ev(p,`document.body.dataset.status='working'`);await sleep(150);
  assert.equal((await state(p)).v,'working','#137 the logo follows the attribute the background follows');
  await fake.send({t:'status',s:'idle'});await sleep(150);
  // a theme that changes state colours only: the official robot in the theme's colours
  const COL={idle:'#3a6ea5',working:'#1f4e79',waiting:'#b8860b',question:'#6a5acd',offline:'#555555'};
  await fake.send({t:'theme',v:1,id:'p393-colours',factory:null,rev:'c1',name:{zh:'色',en:'Colours'},colors:{state:COL}});
  for(const [name,msg] of STATES){
   await fake.send(msg);await waitFor(p,`document.getElementById('orb').dataset.v===${JSON.stringify(name)}&&document.getElementById('orb').getAttribute('src').startsWith('blob:')`);await sleep(150);
   const s=await state(p),px=await robot(p,`${scheme}-theme-${name}`);
   near(px,COL[name],`#137 theme ${scheme}/${name}: robot ${px}, want ${COL[name]}`);
   results.push({scheme,state:name,theme:'colours',robot:px,page:css(s.page)});
  }
  // a theme with its own logo: shown as it is, never repainted
  const own=readFileSync(new URL('../public/brand/img/status/logo-question.png',import.meta.url)).toString('base64');
  await fake.send({t:'theme',v:1,id:'p393-own',factory:null,rev:'c2',name:{zh:'自',en:'Own'},colors:{state:COL},logo:{png:own}});
  await fake.send({t:'status',s:'working'});await waitFor(p,`document.getElementById('orb').dataset.v==='working'`);await sleep(250);
  near(await robot(p,`${scheme}-own-logo`),'#827dbd','#137 a theme logo is the owner\'s picture, untouched');
  await fake.send({t:'theme',v:1,id:'robots',factory:'robots',rev:'robots'});await sleep(150);
  console.log(`P393 logo ${scheme} PASS`);await p.dispose();
 }
 writeFileSync(join(out,'result.json'),JSON.stringify({ok:true,results},null,1)+'\n');
 passed=true;console.log('P393 #137 the logo robot wears the state colour PASS');
}catch(e){console.error(e);process.exitCode=1;}finally{rec.record('p393-logo',passed);rec.write();await B.close();await fake.stop();await web.stop();rmSync(tmp,{recursive:true,force:true});}
