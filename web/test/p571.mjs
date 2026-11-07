import {mkdirSync,writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,evaluate,sleep,shoot,waitFor} from './browser.mjs';
const out=fileURLToPath(new URL('../../../reports/qa/571/',import.meta.url));mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await launch(),results=[],checks=[];
const luminance=c=>{const v=c.match(/[0-9.]+/g).slice(0,3).map(Number).map(x=>{x/=255;return x<=.04045?x/12.92:((x+.055)/1.055)**2.4});return .2126*v[0]+.7152*v[1]+.0722*v[2]};
const contrast=(a,b)=>{const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05)};
const check=(ok,label)=>{checks.push({ok,label});console.log(`${ok?'PASS':'FAIL'} ${label}`)};
try{for(const width of (process.env.P571_QUICK?[390]:[390,414,1440]))for(const theme of ['light','dark']){
 fake.reset();const p=await newPage(B,width,900,theme,{touch:width<500,allow:[web.url,fake.relay+'/']});
 try{
 await navigate(p,fake.newPairing(web.url+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('input').blur()`);
 for(const page of ['chat','settings']){
 if(page==='settings')await evaluate(p,`document.getElementById('setBtn').click()`);
 await sleep(150);
 const icons=await evaluate(p,`['scanBtn','srchBtn','setBtn'].map(id=>{const b=document.getElementById(id),s=b.querySelector('svg'),r=b.getBoundingClientRect(),v=s.getBoundingClientRect(),c=getComputedStyle(s);return {id,x:r.x,y:r.y,w:r.width,h:r.height,svgW:v.width,svgH:v.height,display:c.display,visibility:c.visibility,color:c.color,background:getComputedStyle(document.querySelector('#settings:not([hidden]) .setbox')||document.body).backgroundColor,stroke:c.stroke,opacity:c.opacity,svgFlex:c.flex,svgComputedWidth:c.width,buttonPadding:getComputedStyle(b).padding,buttonDisplay:getComputedStyle(b).display,hit:document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.closest('button')?.id}})`);
 console.log(page,JSON.stringify(icons));
 for(const i of icons){check(i.svgW>=20&&i.svgH>=20&&i.visibility==='visible'&&i.opacity!=='0'&&i.hit===i.id,`${width}/${theme}/${page}/${i.id} visible and owns hit target`);check(contrast(i.color,i.background)>=4.5,`${width}/${theme}/${page}/${i.id} contrast >=4.5:1`);}
 writeFileSync(out+`${process.env.P571_PHASE||'after'}-${width}-${theme}-${page}.png`,await shoot(p));results.push({width,theme,page,icons});
 if(!process.env.P571_PHASE||process.env.P571_PHASE==='after'){
 await evaluate(p,`window.__p571Reload=1;window.__p571Mic=0;const gum=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);navigator.mediaDevices.getUserMedia=(...a)=>{window.__p571Mic++;return gum(...a)};document.getElementById('input').value='P571 preserved draft';document.getElementById('input').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('input').blur()`);
 const point=await evaluate(p,`(()=>{const r=document.getElementById('srchBtn').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
 await p.send('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...point});await sleep(700);
 const pre=await evaluate(p,`({mic:window.__p571Mic,reloading:!window.__p571Reload})`);check(!pre.mic,`${width}/${theme}/${page} hold never acquires microphone`);
 await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...point});
 await waitFor(p,`!window.__p571Reload&&document.getElementById('input')?.value==='P571 preserved draft'`,15000);
 check(await evaluate(p,`document.getElementById('srch').hidden && document.getElementById('settings').hidden`),`${width}/${theme}/${page} hold reloads once without opening search`);
 if(page==='chat')await waitState(p,'ready');
 }
 
 }
 }finally{await p.dispose()}
}}finally{await B.close();await fake.stop();await web.stop()}
writeFileSync(out+`${process.env.P571_PHASE||'after'}.json`,JSON.stringify({checks,results},null,2));if(checks.some(c=>!c.ok))process.exitCode=1;
