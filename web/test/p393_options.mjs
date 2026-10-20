// P393 #136 · real Chromium + real Noise to the local fake host.
// The /model card's option lines (the id · description · price line, mono) wrap inside the card: 320 / 390 / 1440 px,
// three font sizes, light and dark, Chinese and English. Nothing reaches past the option's right edge, the long price
// string breaks (at its 「·」 first), and a short line still sits on one line.
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
const tmp=mkdtempSync(join(tmpdir(),'p393-options-'));cpSync(PUBLIC_DIR,tmp,{recursive:true});writeFileSync(join(tmp,'i18n.js'),i18nModule());
const rec=parityRecorder('web/test/p393_options.mjs');let passed=false;
const web=await startWebServer({publicDir:tmp}),fake=await startFakeHost(),B=await launch(),results=[];
// The customer's /model list (#136 screenshot), word for word.
const MODELS=[
 {id:'default',name:'Default (recommended)',desc:'Use the default model (currently Opus 5.5) · $4/$20 per Mtok'},
 {id:'opus',name:'Opus',cur:true,desc:'Opus 5.5 · Best for everyday, complex tasks · $4/$20 per Mtok'},
 {id:'fable',name:'Fable',desc:'Fable 5.1 · Most capable for your hardest and longest-running tasks'},
 {id:'sonnet',name:'Sonnet',desc:'Sonnet 5.5 · Efficient for routine tasks · $2/$10 per Mtok'},
 {id:'haiku',name:'Haiku',desc:'Haiku 5.5 · Fastest for quick answers · $0.10/$0.50 per Mtok ($0.50/$2.50 per Mtok above 200k tokens)'},
 {id:'x',name:'X',desc:'短'}];
const TEXT={zh:'当前模型：opus。点一个切换（之后的对话都用它）：',en:'Current model: opus. Tap one to switch (later turns use it):'};
async function pair(w,scheme,lang){
 fake.reset();await fake.addTurn({k:'cmd',text:'/model'},TEXT[lang],'done',{cmd:'model',ok:true,kind:'ok',models:MODELS});
 const p=await newPage(B,w,900,scheme,{allow:[web.url,fake.relay+'/'],touch:w<500});
 await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await waitFor(p,`document.querySelectorAll('#cmdx .cmdmodel').length===${MODELS.length}`);
 await ev(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('input').blur()`);return p;
}
const measure=p=>ev(p,`[...document.querySelectorAll('#cmdx .cmdmodel')].map(b=>{const s=b.querySelector('span'),n=b.querySelector('b'),
 br=b.getBoundingClientRect(),cs=getComputedStyle(b),pr=parseFloat(cs.paddingRight),r=document.createRange();r.selectNodeContents(s);
 const rects=[...r.getClientRects()],right=Math.max(...rects.map(x=>x.right)),nr=n.getBoundingClientRect(),box=document.getElementById('cmdx').getBoundingClientRect();
 const lh=parseFloat(getComputedStyle(s).lineHeight),lines=Math.round(s.getBoundingClientRect().height/lh);
 return {id:b.dataset.model,overflow:+(right-(br.right-pr)).toFixed(1),nameOver:+(nr.right-(br.right-pr)).toFixed(1),
  cardOver:+(br.right-box.right).toFixed(1),scroll:b.scrollWidth-b.clientWidth,lines,text:s.textContent};})`);
try{
 for(const w of [320,390,1440])for(const scheme of ['light','dark'])for(const lang of ['zh','en']){
  const p=await pair(w,scheme,lang),row={w,scheme,lang,fz:{}};
  for(const fz of ['s','m','l']){
   await ev(p,`document.documentElement.dataset.fz=${JSON.stringify(fz==='m'?'':fz)}`);await sleep(80);
   const m=await measure(p);
   for(const o of m){
    assert.ok(o.overflow<=0.5,`#136 ${w}/${scheme}/${lang}/${fz} ${o.id}: description runs ${o.overflow}px past the option `+JSON.stringify(o));
    assert.ok(o.nameOver<=0.5&&o.cardOver<=0.5&&o.scroll<=0,`#136 ${w}/${scheme}/${lang}/${fz} ${o.id}: option wider than the card `+JSON.stringify(o));
   }
   const short=m.find(o=>o.id==='x');assert.equal(short.lines,1,'a short line stays on one line');
   if(w<=390)assert.ok(m.find(o=>o.id==='haiku').lines>=2,`the long price line wraps at ${w}px: `+JSON.stringify(m));
   row.fz[fz]=m.map(({id,overflow,lines})=>({id,overflow,lines}));
   if(fz!=='s'&&(w===320||w===390&&lang==='en'||w===1440&&lang==='zh')){
    await ev(p,`document.querySelector('#cmdx .cmdmodel[data-model=haiku]').scrollIntoView({block:'center'})`);await sleep(60);
    writeFileSync(join(out,`web-options-${w}-${scheme}-${lang}-${fz}.png`),await shoot(p));
   }
  }
  // The 「·」 is where a long line breaks first: no line of the price description starts in the middle of a word.
  await ev(p,`document.documentElement.dataset.fz=''`);await sleep(60);
  if(w===320){
   const starts=await ev(p,`(()=>{const s=document.querySelector('#cmdx .cmdmodel[data-model=haiku] span'),t=s.firstChild,r=document.createRange(),out=[];let top=-1;
    for(let i=0;i<t.length;i++){r.setStart(t,i);r.setEnd(t,i+1);const y=Math.round(r.getBoundingClientRect().top);if(y>top+2){top=y;out.push(i);}}
    return out.map(i=>[t.data[i-1]||'',t.data[i]]);})()`);
   for(const [prev] of starts.slice(1))assert.ok(prev===''||/[\s·/(]/.test(prev),'#136 a line starts at a word or 「·」 boundary: '+JSON.stringify(starts));
   row.breaks=starts.length;
  }
  results.push(row);console.log(`P393 options ${w}/${scheme}/${lang} PASS`);await p.dispose();
 }
 writeFileSync(join(out,'web-options-result.json'),JSON.stringify({ok:true,results},null,1)+'\n');
 passed=true;console.log('P393 #136 option lines wrap inside the card PASS');
}catch(e){console.error(e);process.exitCode=1;}finally{rec.record('p393-options',passed);rec.write();await B.close();await fake.stop();await web.stop();rmSync(tmp,{recursive:true,force:true});}
