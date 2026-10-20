// P393 #126 / #127 · real Chromium + real Noise to the local fake host.
// #126: a one-tap answer's label sits in the vertical middle of its pill — one and two lines, Chinese and English,
//       three font sizes, light and dark, every state. #127: an external link in the reply is the Peachy Feeling hue
//       (pure in the dark theme and the reader, lifted toward Lotion where pure is too faint), underlined; a file card's
//       link, the one-tap answers and the #tags are not touched.
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
const out=fileURLToPath(new URL('../../../reports/qa/p393/',import.meta.url));mkdirSync(out,{recursive:true});
const tmp=mkdtempSync(join(tmpdir(),'p393-reply-'));cpSync(PUBLIC_DIR,tmp,{recursive:true});writeFileSync(join(tmp,'i18n.js'),i18nModule());
const rec=parityRecorder('web/test/p393_reply.mjs');let passed=false;
const web=await startWebServer({publicDir:tmp}),fake=await startFakeHost(),B=await launch(),results=[];
const REPLY={
 zh:'选一个：\n\n[明天 16:00 单独升](reply:明天 16:00 单独升) [今天窗口一起升](reply:今天窗口一起升) [这一条按钮的文字故意写得很长很长很长，长到在窄屏上要折成两行才能放下](reply:长)\n\n[打开 Apple 开发者账号页](https://developer.apple.com/account)',
 en:'Pick one:\n\n[Upgrade alone at 16:00 tomorrow](reply:Tomorrow) [Today, in the window](reply:Today) [This answer is deliberately long enough that on a narrow phone it has to wrap onto a second line](reply:Long)\n\n[Open the Apple developer account page](https://developer.apple.com/account)'};
const rgb=x=>x.replace(/\s/g,'');
const lum=([r,g,b])=>{const f=v=>(v/=255)<=.04045?v/12.92:((v+.055)/1.055)**2.4;return .2126*f(r)+.7152*f(g)+.0722*f(b);};
const ratio=(a,b)=>{const[x,y]=[lum(a),lum(b)].sort((p,q)=>q-p);return (x+.05)/(y+.05);};
const nums=s=>{const m=s.match(/[\d.]+/g).map(Number);if(/^color\(srgb/.test(s))return m.slice(0,3).map(v=>v*255);return m.slice(0,3);};
// #126 is about what the eye sees, so it is measured on the screenshot: the rows of Lotion ink inside each pill
// (the label; the 70 % small words under a different label are dimmer and ignored) → |gap above − gap below| in px.
async function inkGaps(p){
 const boxes=await ev(p,`[...document.querySelectorAll('#words .rbtn')].map(b=>{b.scrollIntoView({block:'center'});return 0;}).length&&
  [...document.querySelectorAll('#words .rbtn')].map(b=>{const r=b.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height};})`);
 const out=[];
 for(let i=0;i<boxes.length;i++){
  await ev(p,`document.querySelectorAll('#words .rbtn')[${i}].scrollIntoView({block:'center'})`);await sleep(60);
  const png=(await shoot(p)).toString('base64');
  out.push(await ev(p,`(async()=>{const b=document.querySelectorAll('#words .rbtn')[${i}].getBoundingClientRect(),im=new Image();
   im.src='data:image/png;base64,${png}';await im.decode();const c=document.createElement('canvas');c.width=im.width;c.height=im.height;
   const x=c.getContext('2d');x.drawImage(im,0,0);const L=Math.ceil(b.left+12),R=Math.floor(b.right-12),T=Math.round(b.top),B=Math.round(b.bottom);
   const d=x.getImageData(L,T,R-L,B-T).data,W=R-L;let top=-1,bot=-1;
   for(let y=0;y<B-T;y++){for(let k=0;k<W;k++){const o=(y*W+k)*4;if(d[o]>215&&d[o+1]>215&&d[o+2]>210){if(top<0)top=y;bot=y;break;}}}
   return {above:top,below:B-T-1-bot,h:B-T,lines:Math.round((bot-top)/parseFloat(getComputedStyle(document.querySelectorAll('#words .rbtn')[${i}]).lineHeight))+1};})()`));
 }
 return out.map(g=>({...g,d:Math.abs(g.above-g.below)}));
}
async function pair(w,scheme,lang){
 fake.reset();await fake.addTurn({k:'phone',dev:'X',name:'Remote',text:'?'},REPLY[lang],'done');
 const p=await newPage(B,w,900,scheme,{allow:[web.url,fake.relay+'/'],touch:w<500});
 await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await waitFor(p,`document.querySelectorAll('#words .rbtn').length===3`);
 await ev(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('input').blur()`);return p;
}
try{
 for(const w of [390,1440])for(const scheme of ['light','dark'])for(const lang of ['zh','en']){
  const p=await pair(w,scheme,lang);
  for(const fz of ['s','m','l']){
   await ev(p,`document.documentElement.dataset.fz=${JSON.stringify(fz==='m'?'':fz)}`);await sleep(60);
   const o=await inkGaps(p);
   for(const b of o)assert.ok(b.above>0&&b.d<=3,`#126 ${w}/${scheme}/${lang}/${fz}: label off-centre by ${b.d}px `+JSON.stringify(o));
   if(w===390)assert.ok(o.some(b=>b.lines>=2),'a two-line answer is measured too: '+JSON.stringify(o));
  }
  await ev(p,`document.documentElement.dataset.fz=''`);
  const link=[];
  for(const status of ['idle','working','waiting','question','unknown']){
   await ev(p,`document.body.dataset.status=${JSON.stringify(status==='question'?'waiting':status)};document.body.dataset.kind=${JSON.stringify(status==='question'?'question':'')}`);await sleep(40);
   const c=await ev(p,`(()=>{const a=document.querySelector('#words a[href^="https://developer.apple.com"]'),s=getComputedStyle(a),
     bg=getComputedStyle(document.querySelector('.rm')).backgroundColor,rb=getComputedStyle(document.querySelector('#words .rbtn'));
     return {color:s.color,line:s.textDecorationLine,deco:s.textDecorationColor,bg,rbtn:rb.color};})()`);
   assert.match(c.line,/underline/);assert.equal(c.color,c.deco,'the underline is the link colour');
   assert.equal(nums(c.rbtn).map(Math.round).join(),'250,249,245','the one-tap answers keep their Lotion words');
   const pure=nums(c.color).map(Math.round).join()==='217,119,87';
   const want=scheme==='dark'&&status!=='waiting';
   assert.equal(pure,want,`#127 ${scheme}/${status}: ${c.color}`);
   const cr=ratio(nums(c.color),nums(c.bg));assert.ok(cr>=(want?2:2.5),`#127 ${scheme}/${status} contrast ${cr.toFixed(2)}`);
   link.push({status,color:c.color,bg:c.bg,contrast:+cr.toFixed(2)});
   if(w===390&&lang==='zh'||w===1440&&lang==='en'){await ev(p,`document.getElementById('main').scrollTop=1e8`);await sleep(60);
    writeFileSync(join(out,`web-reply-${w}-${scheme}-${lang}-${status}.png`),await shoot(p));}
  }
  // The full-screen reader is Noir on every state → pure Peachy there.
  await ev(p,`document.body.dataset.status='idle';document.body.dataset.kind=''`);
  results.push({w,scheme,lang,link});console.log(`P393 reply ${w}/${scheme}/${lang} PASS`);await p.dispose();
 }
 // A reader page: pure Peachy on Noir.
 {const p=await pair(390,'light','zh');
  await ev(p,`(()=>{const r=document.getElementById('rdWords');r.innerHTML='<p><a href="https://example.com/x">外部</a></p>';})()`);
  assert.equal(nums(await ev(p,`getComputedStyle(document.querySelector('#rdWords a')).color`)).map(Math.round).join(),'217,119,87');await p.dispose();}
 writeFileSync(join(out,'web-reply-result.json'),JSON.stringify({ok:true,results},null,1)+'\n');
 passed=true;console.log('P393 #126/#127 reply pills + external links PASS');
}catch(e){console.error(e);process.exitCode=1;}finally{rec.record('p393-reply',passed);rec.write();await B.close();await fake.stop();await web.stop();rmSync(tmp,{recursive:true,force:true});}
