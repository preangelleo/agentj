// P340 C (owner 10-16 15:1x, Bots / 客服 page, Android Chrome dark): 「我们用了一个 logo 和底色相同的 logo，这个做法就不对……
// 在比较 heavy 的颜色上，字体不能用黑色，要用我们指定的白色。」 The colour audit that keeps both from coming back:
//
//  ① TEXT — every visible text on the page, against the background it really sits on (the composited stack up its tree):
//     · on a heavy ground (relative luminance < HEAVY_L: the Dark Cluster, Black Mana, Noir, Sooty, the state colours)
//       the text is lighter than the ground; on a chromatic heavy ground (a coloured fill, not Noir / Sooty) it is Lotion
//       #faf9f5 — never black / near-black (agentj-visual-language: 「深底必白字」「动作按钮统一 logo 绿底白字」);
//     · a control's label (button / link / tab / chip / badge) reads ≥ 4.5:1 (≥ 3:1 large text), except Lotion on a
//       chromatic heavy fill, which is the owner's rule (2.8–3.6:1 on the Dark Cluster, the P244 menu-card decision).
// P448 supersedes the legacy heavy/white heuristic for button/[role=button]/a.aj-btn: always >=4.5:1, including large labels.
//  ② ICONS — every img / svg / canvas / mask / background-image mark (logo, robots, line icons) is actually visible on its
//     container: four 1× screenshots of its box (as shown · icon hidden · icon forced black · icon forced white). The icon's
//     own footprint = the pixels where black ≠ white; it is visible where shown ≠ hidden. FAIL when less than MIN_SHOWN of
//     the footprint can be told from the ground (CIEDE2000, per pixel against its coverage) — the green robot on the green
//     button kept only its eyes (≈ 0.2).
//
// Surfaces: chat (idle, a reply, the approval sheet), the menu and its five pages, Settings and every sub-page / view a
// Settings row opens (Bots, Friends, AI rescue, mail, …), the robot card and a toast — each scroller stepped to its end.
// Matrix: zh / en × dark / light at 390 × 844, plus zh dark / light at 1440 × 900.
// `node test/p340_contrast.mjs before` = shots + audit report only; no tag = shots + audit + assert (gate p340-contrast).
// Own Chromium (never :9222), the real client against fakehost.mjs. Output: reports/qa/p340/c/<tag>/.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {inflateSync} from 'node:zlib';
import {randomBytes} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate,navigate,waitFor,waitState,shoot,sleep,key} from './browser.mjs';
import {deltaE} from '../public/js/robot-color.js';

const tag=process.argv[2]||'after', CHECK=tag==='after';
const root=fileURLToPath(new URL('../../../',import.meta.url));const out=root+`reports/qa/p340/c/${tag}/`;mkdirSync(out,{recursive:true});
export const HEAVY_L=0.36, MIN_SHOWN=0.5;
const KEY_SHOTS=/^(01-chat|02-approval|03-menu|06-toast|07-settings|08-open-bots|08-open-friends|09-bots|09-friends)$/;   // P340_SHOTS=all keeps every surface
const MATRIX=[['zh','dark',390,844],['zh','light',390,844],['en','dark',390,844],['en','light',390,844],['zh','dark',1440,900],['zh','light',1440,900]]
  .filter(([l,s,w])=>!process.env.P340_ONLY||process.env.P340_ONLY.split(',').includes(`${l}-${s}-${w}`));

// ---------------------------------------------------------------- in-page audit (text rules + icon candidates)
// P448 text contract: actual button pairs take precedence over the historical hue heuristic.
export function p448TextFailures({p448,ctrl,colour,lotion,heavy,fgL,bgL,ratio,large}) {
 const why=[];
 if(p448){if(ratio<4.5)why.push('P448 control label '+ratio.toFixed(2)+':1');return why;}
 if(colour){if(!lotion)why.push(fgL<=bgL?'dark text on a heavy ground':'a coloured heavy fill takes Lotion text');}
 else if(heavy&&(fgL<=bgL||ratio<3))why.push('dark text on a heavy ground');
 if(ctrl&&!(colour&&lotion)&&ratio<(large?3:4.5))why.push('control label '+ratio.toFixed(2)+':1');
 return why;
}
// end P448 text contract
const AUDIT=`((HEAVY_L)=>{
 const classify=${p448TextFailures.toString()};
 const rgb=s=>{const m=(s.match(/[\\d.]+/g)||[0,0,0,0]).map(Number),k=s.startsWith('color(')?255:1;return {c:m.slice(0,3).map(v=>v*k),a:m.length>3?m[3]:1};};
 const lum=c=>{const [r,g,b]=c.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4});return .2126*r+.7152*g+.0722*b;};
 const cr=(a,b)=>{const x=lum(a),y=lum(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);};
 const pseudoBg=n=>{for(const w of ['::after','::before']){const q=getComputedStyle(n,w);if(q.content==='none'||q.content==='normal'||q.position!=='absolute')continue;const b=rgb(q.backgroundColor);if(b.a>=.99)return b;}return null;};
 const ground=e=>{let n=e,stack=[];while(n&&n.nodeType===1){const pb=pseudoBg(n);if(pb){stack.push(pb);break;}const b=rgb(getComputedStyle(n).backgroundColor);if(b.a>0){stack.push(b);if(b.a>=.99)break;}n=n.parentElement;}
  let base=stack.length&&stack.at(-1).a>=.99?stack.pop().c:[250,249,245];while(stack.length){const b=stack.pop();base=base.map((v,i)=>v*(1-b.a)+b.c[i]*b.a);}return base.map(Math.round);};
 const inView=r=>r.width>0&&r.height>0&&r.bottom>0&&r.right>0&&r.top<innerHeight&&r.left<innerWidth;
 const onTopAll=(e,r)=>[[.5,.5],[.2,.2],[.8,.2],[.2,.8],[.8,.8]].every(([a,b])=>{const x=r.left+r.width*a,y=r.top+r.height*b;if(x<0||y<0||x>=innerWidth||y>=innerHeight)return false;const h=document.elementFromPoint(x,y);return !!h&&(h===e||e.contains(h)||h.contains(e));});
 const onTop=(e,r)=>{const x=Math.min(innerWidth-1,Math.max(0,r.left+r.width/2)),y=Math.min(innerHeight-1,Math.max(0,r.top+r.height/2));const h=document.elementFromPoint(x,y);return !!h&&(h===e||e.contains(h)||h.contains(e));};
 const faded=e=>{let o=1;for(let n=e;n&&n.nodeType===1;n=n.parentElement)o*=parseFloat(getComputedStyle(n).opacity);return o<.6;};
 const name=e=>{const p=[];for(let n=e,i=0;n&&n.nodeType===1&&i<3;n=n.parentElement,i++){p.unshift(n.id?'#'+n.id:n.tagName.toLowerCase()+(typeof n.className==='string'&&n.className.trim()?'.'+n.className.trim().split(/\\s+/).slice(0,2).join('.'):''));if(n.id)break;}return p.join('>');};
 const CTRL='button,a[href],[role=button],[role=tab],[role=switch],[role=menuitem],summary,[class*=chip],[class*=badge],[class*=pill],[class*=tag]';
 const text=[],icons=[];
 for(const e of document.querySelectorAll('body *')){
  if(/^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE|OPTION|TEXTAREA|INPUT|SELECT)$/.test(e.tagName)||e.closest('[hidden],[aria-hidden=true] svg,svg *'))continue;
  const s=getComputedStyle(e);if(s.visibility==='hidden'||s.display==='none')continue;
  const r=e.getBoundingClientRect();if(!inView(r)||r.width<3||r.height<3)continue;
  // text: a direct, non-blank text node
  const own=[...e.childNodes].filter(n=>n.nodeType===3&&n.textContent.trim()).map(n=>n.textContent.trim()).join(' ');
  if(own&&onTop(e,r)&&!faded(e)&&!e.closest('[disabled],[aria-disabled=true]')&&parseFloat(s.fontSize)>0&&!(s.clip&&s.clip.startsWith('rect(0'))&&s.clipPath!=='inset(50%)'){
   const bg=ground(e),f=rgb(s.color),fc=f.c.map((v,i)=>Math.round(v*f.a+bg[i]*(1-f.a))),L=lum(bg),ratio=cr(fc,bg);
   const chroma=Math.max(...bg)-Math.min(...bg),heavy=L<HEAVY_L,colour=heavy&&chroma>=40,lotion=f.a>=.6&&Math.abs(f.c[0]-250)<=8&&Math.abs(f.c[1]-249)<=8&&Math.abs(f.c[2]-245)<=8;   // Lotion, or an alpha of it (palette rule)
   const ctrl=!!e.closest(CTRL),large=parseFloat(s.fontSize)>=24||(parseFloat(s.fontSize)>=18.66&&+s.fontWeight>=700);
   const p448=!!e.closest('button,[role="button"],a.aj-btn');
   const why=classify({p448,ctrl,colour,lotion,heavy,fgL:lum(fc),bgL:L,ratio,large});
   text.push({k:name(e)+'「'+own.slice(0,18)+'」',fg:'rgb('+fc+')',bg:'rgb('+bg+')',ratio:+ratio.toFixed(2),heavy,ctrl,bad:why});
  }
  // icon candidates
  const tagn=e.tagName.toLowerCase(),mask=(s.maskImage&&s.maskImage!=='none')||(s.webkitMaskImage&&s.webkitMaskImage!=='none'),bgi=/url\\(/.test(s.backgroundImage);
  if((tagn==='img'||tagn==='svg'||tagn==='canvas'||mask||bgi)&&r.width>=10&&r.height>=10&&r.width<=200&&r.height<=200&&!e.closest('.md,.reply .att,.turn .att,.qrbox,#scan-view video')&&onTopAll(e,r)&&!faded(e)){
   if(tagn==='img'&&!(e.complete&&e.naturalWidth))continue;
   const ob=rgb(s.backgroundColor);
   if(!e.dataset.p340i)e.dataset.p340i=String(Math.random()).slice(2,10);
   icons.push({id:e.dataset.p340i,k:name(e),src:(e.currentSrc||'').split('/').pop().split('?')[0]+' '+(bgi?s.backgroundImage.split('/').pop().slice(0,40):''),x:r.left,y:r.top,w:r.width,h:r.height,ownBg:ob.a>=.99,ground:'rgb('+ground(e)+')'});
  }
 }
 return {text,icons};
})`;
// icon render modes (page-wide style; the icon carries data-p340m)
const MODES=`[data-p340m]{animation-play-state:paused!important;transition:none!important;caret-color:transparent!important}
[data-p340m=hide]:not([data-p340own]){opacity:0!important}
[data-p340m=hide][data-p340own]{object-position:-99999px -99999px!important;background-image:none!important}
[data-p340m=hide][data-p340own] *{visibility:hidden!important}
[data-p340m=blk],[data-p340m=wht]{box-shadow:none!important}
[data-p340m=blk][data-p340own],[data-p340m=wht][data-p340own]{background-color:transparent!important}
[data-p340m=blk]{filter:brightness(0)!important}
[data-p340m=wht]{filter:brightness(0) invert(1)!important}`;

// ---------------------------------------------------------------- PNG (8-bit RGB / RGBA, non-interlaced: Chromium's screenshots)
function png(buf){let o=8,w=0,h=0,ct=6;const idat=[];
 while(o<buf.length){const n=buf.readUInt32BE(o),t=buf.toString('ascii',o+4,o+8);if(t==='IHDR'){w=buf.readUInt32BE(o+8);h=buf.readUInt32BE(o+12);ct=buf[o+17];}if(t==='IDAT')idat.push(buf.subarray(o+8,o+8+n));o+=12+n;}
 const bpp=ct===6?4:3,raw=inflateSync(Buffer.concat(idat)),st=w*bpp,px=new Uint8Array(w*h*3);let prev=new Uint8Array(st);
 for(let y=0;y<h;y++){const f=raw[y*(st+1)],line=raw.subarray(y*(st+1)+1,(y+1)*(st+1)),cur=new Uint8Array(st);
  for(let i=0;i<st;i++){const a=i>=bpp?cur[i-bpp]:0,b=prev[i],c=i>=bpp?prev[i-bpp]:0;let v=line[i];
   if(f===1)v+=a;else if(f===2)v+=b;else if(f===3)v+=(a+b)>>1;else if(f===4){const p=a+b-c,pa=Math.abs(p-a),pb=Math.abs(p-b),pc=Math.abs(p-c);v+=pa<=pb&&pa<=pc?a:pb<=pc?b:c;}cur[i]=v&255;}
  for(let x=0;x<w;x++)for(let k=0;k<3;k++)px[(y*w+x)*3+k]=cur[x*bpp+k];prev=cur;}
 return {w,h,px};}

async function iconScore(p,ic){
 const {cssLayoutViewport:v}=await p.send('Page.getLayoutMetrics');
 const x=Math.max(0,Math.floor(ic.x)),y=Math.max(0,Math.floor(ic.y)),w=Math.ceil(ic.x+ic.w)-x,h=Math.ceil(ic.y+ic.h)-y;
 const clip={x:x+v.pageX,y:y+v.pageY,width:w,height:h,scale:1};
 const grab=async mode=>{await evaluate(p,`(()=>{const e=document.querySelector('[data-p340i="${ic.id}"]');if(!e)return;${ic.ownBg?`e.dataset.p340own='1';`:''}e.dataset.p340m=${JSON.stringify(mode)};})()`);
  await sleep(30);
  return png(Buffer.from((await p.send('Page.captureScreenshot',{format:'png',clip})).data,'base64'));};
 const A=await grab('show'),B=await grab('hide'),K=await grab('blk'),W=await grab('wht');
 await evaluate(p,`(()=>{const e=document.querySelector('[data-p340i="${ic.id}"]');if(e){delete e.dataset.p340m;delete e.dataset.p340own;}})()`);
 let foot=0,shown=0;const at=(I,i)=>[I.px[i*3],I.px[i*3+1],I.px[i*3+2]];
 for(let i=0;i<A.w*A.h;i++){const d=deltaE(at(K,i),at(W,i));if(d<20)continue;foot++;if(deltaE(at(A,i),at(B,i))>=0.12*d)shown++;}
 return {foot,score:foot?+(shown/foot).toFixed(3):1};
}

// ---------------------------------------------------------------- the walk
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch();
const report={heavyL:HEAVY_L,minShown:MIN_SHOWN,views:{}},fails=[],pageshot=[];let audited=0,iconsN=0;
try{
 for(const [lang,scheme,w,h] of MATRIX){const vname=`${lang}-${scheme}-${w}`;const seenT=new Map(),seenI=new Map();
  fake.reset();fake.st.prefs={appearance:{language:lang,theme:'system'},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
  const p=await newPage(B,w,h,scheme,{allow:[web.url,fake.relay+'/'],touch:w<500});
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
  await evaluate(p,`document.getElementById('phone-env-dismiss')?.click()`);
  await evaluate(p,`(()=>{const s=document.createElement('style');s.id='p340-modes';s.textContent=${JSON.stringify(MODES)};document.head.append(s);})()`);
  await fake.say(lang==='zh'?'早上好，这是一条用来检查颜色的回复。':'Good morning — a reply to check the colours.','agent');await sleep(400);
  const esc=async()=>{await key(p,'Escape');await sleep(300);};
  const audit=async(surface,{shot=true}={})=>{const t0=Date.now();
   if(surface!=='06-toast')await evaluate(p,`import('./js/ui.js').then(m=>m.toastOff&&m.toastOff())`).catch(()=>{});
   await sleep(250);if(shot&&(process.env.P340_SHOTS==='all'||KEY_SHOTS.test(surface)))writeFileSync(out+`${surface}-${vname}.png`,await shoot(p,''));
   // every scroller on top, stepped to its end
   const scrollers=await evaluate(p,`(()=>{const out=[];for(const e of document.querySelectorAll('body *')){const s=getComputedStyle(e);if(!/(auto|scroll)/.test(s.overflowY)||e.scrollHeight<=e.clientHeight+20||e.closest('[hidden]'))continue;const r=e.getBoundingClientRect();if(r.height<120||r.width<200)continue;const h=document.elementFromPoint(r.left+r.width/2,r.top+Math.min(r.height/2,60));if(!h||!e.contains(h))continue;if(!e.dataset.p340s)e.dataset.p340s=String(Math.random()).slice(2,9);out.push(e.dataset.p340s);}return out;})()`);
   const steps=[null];for(const sid of scrollers.slice(0,2)){const n=await evaluate(p,`(()=>{const e=document.querySelector('[data-p340s="${sid}"]');return Math.min(8,Math.ceil((e.scrollHeight-e.clientHeight)/(e.clientHeight*0.8)));})()`);for(let i=1;i<=n;i++)steps.push([sid,i]);}
   const starts=await evaluate(p,`[...document.querySelectorAll('[data-p340s]')].map(e=>[e.dataset.p340s,e.scrollTop])`);
   for(const st of steps){
    if(st)await evaluate(p,`(()=>{const e=document.querySelector('[data-p340s="${st[0]}"]');e.scrollTop=${st[1]}*e.clientHeight*0.8;})()`),await sleep(150);
    const r=await evaluate(p,`${AUDIT}(${HEAVY_L})`);
    for(const t of r.text){audited++;const k=t.k+' '+t.fg+' '+t.bg;if(seenT.has(k))continue;seenT.set(k,{...t,surface});}
    for(const ic of r.icons){const k=ic.k+' '+ic.ground+' '+ic.src;if(seenI.has(k))continue;iconsN++;const s=await iconScore(p,ic);seenI.set(k,{k:ic.k+(ic.src.trim()?' ('+ic.src.trim()+')':''),ground:ic.ground,surface,...s});}
   }
   if(process.env.P340_DEBUG)console.error(vname,surface,'steps',steps.length,'icons',seenI.size,Date.now()-t0,'ms');
   for(const [sid,top] of starts)await evaluate(p,`(()=>{const e=document.querySelector('[data-p340s="${sid}"]');if(e)e.scrollTop=${top};})()`);
  };
  // ---- chat
  await audit('01-chat');
  await fake.ask({id:randomBytes(16).toString('hex'),tool:'Bash',summary:'rm -rf ./build && npm run build',ttl:120,cat:['delete'],why:'rm -rf'});
  await waitFor(p,`document.body.textContent.includes('npm run build')`).catch(()=>{});await audit('02-approval');
  await evaluate(p,`document.querySelector('#appr .deny,#deny,[data-act=deny]')?.click()`);await esc();await sleep(300);
  await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});await sleep(300);
  // ---- menu + five pages
  const orb=`(()=>{const e=document.getElementById('orb');e.dispatchEvent(new PointerEvent('pointerdown',{button:0}));e.dispatchEvent(new PointerEvent('pointerup',{button:0}));})()`;
  await evaluate(p,orb);await waitFor(p,`!document.getElementById('five-menu').hidden`);await audit('03-menu');
  const pages=await evaluate(p,`[...document.querySelectorAll('.fp-menu-card[data-page]')].map(b=>b.dataset.page)`);
  for(const pg of pages){
   if(await evaluate(p,`document.getElementById('five-menu').hidden`)){await evaluate(p,orb);await waitFor(p,`!document.getElementById('five-menu').hidden`);}
   await evaluate(p,`document.querySelector('.fp-menu-card[data-page="${pg}"]').click()`);await sleep(600);await audit('04-page-'+pg);
   if(await evaluate(p,`!!document.querySelector('#five-body .fp-card-head')`)){await evaluate(p,`document.querySelector('#five-body .fp-card-head').click()`);await sleep(300);await audit('04-page-'+pg+'-open',{shot:false});}
   await esc();if(!(await evaluate(p,`document.getElementById('five-page').hidden`)))await esc();
  }
  if(!(await evaluate(p,`document.getElementById('five-menu').hidden`)))await esc();
  // ---- the robot card
  await fake.send({t:'busy',by:'Agent J',tasks:[{id:'p340',who:'P340',kind:'temp',label:'colour audit',c:1,since:Math.floor(Date.now()/1000)}]});
  if(await waitFor(p,`!document.getElementById('busyDots').hidden`,3000).catch(()=>false)){await evaluate(p,`document.getElementById('busyDots').click()`);await sleep(400);await audit('05-busy');await esc();}
  await fake.send({t:'busy',by:'Agent J',tasks:[]});
  // ---- a toast (Noir Notification)
  await evaluate(p,`import('./js/ui.js').then(m=>m.notify?m.notify({kind:'success',title:'OK',text:'Saved',ms:0}):m.toast('Saved',0))`).catch(()=>{});await sleep(300);await audit('06-toast');
  await evaluate(p,`import('./js/ui.js').then(m=>m.toastOff&&m.toastOff())`).catch(()=>{});await esc();
  // ---- Settings, every row that opens a page or a view
  await evaluate(p,`document.getElementById('setBtn').click()`);await waitFor(p,`!document.getElementById('settings').hidden`);await audit('07-settings');
  const navs=await evaluate(p,`[...document.querySelectorAll('#settings .setnav')].filter(b=>b.id&&b.getClientRects().length&&!b.closest('[hidden]')).map(b=>b.id)`);
  for(const id of navs){
   if(await evaluate(p,`document.getElementById('settings').hidden`)){await evaluate(p,`document.getElementById('setBtn').click()`);await waitFor(p,`!document.getElementById('settings').hidden`).catch(()=>{});}
   await evaluate(p,`(()=>{const b=document.getElementById('${id}');b.scrollIntoView({block:'center'});b.click();})()`);await sleep(700);
   if(await evaluate(p,`!document.getElementById('confirm')?.hidden`))await evaluate(p,`document.getElementById('confirm-no')?.click()`);
   await audit('08-'+id.replace(/^set-|-nav$/g,''));
   await esc();
   if(!(await evaluate(p,`['chat'].includes(document.body.dataset.view)||!document.getElementById('settings').hidden`)))await esc();
  }
  if(!(await evaluate(p,`document.getElementById('settings').hidden`)))await esc();
  // ---- Bots and Friends (Alt+Shift+B / G) — the owner's screenshot
  for(const [code,kind] of [['KeyB','bots'],['KeyG','friends']]){
   await evaluate(p,`document.dispatchEvent(new KeyboardEvent('keydown',{key:'${code.slice(3)}',code:'${code}',altKey:true,shiftKey:true,bubbles:true}))`);
   await waitFor(p,`document.body.dataset.view==='${kind}'`).catch(()=>{});await sleep(500);await audit('09-'+kind);
   const shotOrb=await evaluate(p,`(async()=>{const m=await import('./js/pageshot.js');const f=await m.capturePage();const img=new Image();img.src=URL.createObjectURL(f);await img.decode();
     const k=img.width/innerWidth,c=document.createElement('canvas');c.width=img.width;c.height=img.height;const x=c.getContext('2d');x.drawImage(img,0,0);
     const part=(el)=>{const r=el.getBoundingClientRect(),d=x.getImageData(Math.round((r.left+r.width*.25)*k),Math.round((r.top+r.height*.25)*k),Math.max(1,Math.round(r.width*.5*k)),Math.max(1,Math.round(r.height*.5*k))).data;let n=0,ink=0;for(let i=0;i<d.length;i+=4){n++;if(Math.abs(d[i]-250)+Math.abs(d[i+1]-249)+Math.abs(d[i+2]-245)>90)ink++;}return +(ink/n).toFixed(3);};
     const data=await new Promise(r=>{const q=new FileReader();q.onload=()=>r(q.result.split(',')[1]);q.readAsDataURL(f);});URL.revokeObjectURL(img.src);
     return {orb:part(document.getElementById('orb')),data};})()`);
   writeFileSync(out+`10-pageshot-${kind}-${vname}.png`,Buffer.from(shotOrb.data,'base64'));
   pageshot.push([vname,kind,shotOrb.orb]);if(shotOrb.orb<0.08)fails.push(`${vname} [pageshot ${kind}] the long-press screenshot lost the header robot (${Math.round(shotOrb.orb*100)}% ink in the orb)`);
   await esc();
  }
  const tx=[...seenT.values()],ix=[...seenI.values()];
  const badT=tx.filter(t=>t.bad.length),badI=ix.filter(i=>i.foot>=12&&i.score<MIN_SHOWN);
  report.views[vname]={text:tx.length,icons:ix.length,badText:badT,badIcons:badI,iconScores:ix.map(i=>[i.k,i.surface,i.score,i.foot])};
  for(const t of badT)fails.push(`${vname} [${t.surface}] text ${t.k} ${t.fg} on ${t.bg} (${t.ratio}:1): ${t.bad.join('; ')}`);
  for(const i of badI)fails.push(`${vname} [${i.surface}] icon ${i.k} on ${i.ground}: ${Math.round(i.score*100)}% of it visible`);
  await p.dispose();
 }
 report.fails=fails;report.pageshot=pageshot;writeFileSync(out+'audit.json',JSON.stringify(report,null,1));
 console.log(`P340 contrast: ${MATRIX.length} views, ${audited} text checks, ${iconsN} icons, ${fails.length} failures → ${out}audit.json`);
 for(const f of fails.slice(0,80))console.log('  FAIL',f);
 if(CHECK)assert.equal(fails.length,0,`${fails.length} colour failures (see ${out}audit.json)`);
 if(CHECK)console.log('P340 contrast PASS');
}finally{await B.close();await fake.stop();await web.stop();}
