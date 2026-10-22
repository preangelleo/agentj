// P424 #127 · real Chromium + real Noise to the local fake host. An external link in the Agent's reply keeps Peachy Feeling
// where it reads (dark theme, every state but waiting) and takes Bisque #fee2c5 from the palette where pure Peachy is too
// faint (the light theme; the waiting card, which is the Peachy hue itself) — never a mixed, off-palette colour. Five states
// × light/dark × 390 / 1440; the measured contrast lands in reports/qa/p424/xlink/<tag>/result.json.
// P424_TAG=before P424_PUBLIC=<old public/> = the same shots of the code before the fix (no assertions).
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,cpSync,mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {i18nModule} from '../build.mjs';
import {parityRecorder} from '../../parity/lib.mjs';
import {startWebServer,PUBLIC_DIR} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate as ev,navigate,waitState,waitFor,sleep,shoot} from './browser.mjs';
const TAG=process.env.P424_TAG||'after',BEFORE=TAG==='before';
const out=fileURLToPath(new URL(`../../../reports/qa/p424/xlink/${TAG}/`,import.meta.url));mkdirSync(out,{recursive:true});
const tmp=mkdtempSync(join(tmpdir(),'p424-xlink-'));cpSync(process.env.P424_PUBLIC||PUBLIC_DIR,tmp,{recursive:true});writeFileSync(join(tmp,'i18n.js'),i18nModule());
const rec=parityRecorder('web/test/p424_xlink.mjs');let passed=false;
const web=await startWebServer({publicDir:tmp}),fake=await startFakeHost(),B=await launch(),results=[];
const REPLY={zh:'资料在这里：[打开 Apple 开发者账号页](https://developer.apple.com/account)，看完告诉我。',
 en:'Here it is: [Open the Apple developer account page](https://developer.apple.com/account) — tell me when you have read it.'};
// The closed palette (brand/palette.css = the Leo palette): the link's colour must be one of these, exactly.
const PEACHY='217,119,87',LOTION='250,249,245';
const lum=([r,g,b])=>{const f=v=>(v/=255)<=.04045?v/12.92:((v+.055)/1.055)**2.4;return .2126*f(r)+.7152*f(g)+.0722*f(b);};
const ratio=(a,b)=>{const[x,y]=[lum(a),lum(b)].sort((p,q)=>q-p);return (x+.05)/(y+.05);};
const nums=s=>{const m=s.match(/[\d.]+/g).map(Number);if(/^color\(srgb/.test(s))return m.slice(0,3).map(v=>v*255);return m.slice(0,3);};
async function pair(w,scheme,lang){
 fake.reset();await fake.addTurn({k:'phone',dev:'X',name:'Remote',text:lang==='zh'?'开发者账号页在哪？':'Where is the developer page?'},REPLY[lang],'done');
 const p=await newPage(B,w,900,scheme,{allow:[web.url,fake.relay+'/'],touch:w<500});
 await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await waitFor(p,`!!document.querySelector('#words a[href^="https://developer.apple.com"]')`);
 await ev(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('input').blur()`);return p;
}
try{
 for(const w of [390,1440])for(const scheme of ['light','dark']){
  const lang=w===390?'zh':'en',p=await pair(w,scheme,lang),link=[];
  for(const status of ['idle','working','waiting','question','unknown']){
   await ev(p,`document.body.dataset.status=${JSON.stringify(status==='question'?'waiting':status)};document.body.dataset.kind=${JSON.stringify(status==='question'?'question':'')}`);await sleep(60);
   const c=await ev(p,`(()=>{const a=document.querySelector('#words a[href^="https://developer.apple.com"]'),s=getComputedStyle(a);
     return {color:s.color,deco:s.textDecorationColor,line:s.textDecorationLine,bg:getComputedStyle(document.querySelector('.rm')).backgroundColor};})()`);
   const got=nums(c.color).map(Math.round).join(),cr=+ratio(nums(c.color),nums(c.bg)).toFixed(2);
   link.push({status,color:got,bg:nums(c.bg).map(Math.round).join(),contrast:cr});
   if(!BEFORE){
    const want=LOTION; // P477: all reply-body text, including external links, is Lotion.
    assert.equal(got,want,`${w}/${scheme}/${status}: ${c.color} — P477 reply-body Lotion`);
    assert.match(c.line,/underline/);assert.equal(c.color,c.deco,'the underline is the link colour');
    assert.ok(cr>=3,`${w}/${scheme}/${status} contrast ${cr}`);
   }
   writeFileSync(join(out,`reply-${w}-${scheme}-${status}.png`),await shoot(p));
  }
  results.push({w,scheme,lang,link});console.log(`P424 #127 ${TAG} ${w}/${scheme}`,JSON.stringify(link));await p.dispose();
 }
 if(!BEFORE){   // the full-screen reader stays pure Peachy (Noir ground on every state)
  const p=await pair(390,'light','zh');
  await ev(p,`(()=>{const r=document.getElementById('rdWords');r.innerHTML='<p><a href="https://example.com/x">x</a></p>';})()`);
  assert.equal(nums(await ev(p,`getComputedStyle(document.querySelector('#rdWords a')).color`)).map(Math.round).join(),PEACHY);await p.dispose();
 }
 writeFileSync(join(out,'result.json'),JSON.stringify({ok:true,tag:TAG,results},null,1)+'\n');
 passed=true;console.log(`P424 #127 external link colour (${TAG}) PASS`);
}catch(e){console.error(e);process.exitCode=1;}finally{if(!BEFORE){rec.record('p424-xlink',passed);rec.write();}await B.close();await fake.stop();await web.stop();rmSync(tmp,{recursive:true,force:true});}
