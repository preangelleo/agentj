import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import { parityRecorder } from '../../parity/lib.mjs';
const rec=parityRecorder('web/test/p517.mjs');
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
const root=fileURLToPath(new URL('../../../',import.meta.url));
const baseline=root+'web/test/fixtures/p517/';
const {startWebServer}=await import(root+'web/test/serve.mjs');
const {startFakeHost}=await import(root+'web/test/fakehost.mjs');
const {launch,newPage,navigate,waitState,evaluate,waitFor,shoot,sleep}=await import(root+'web/test/browser.mjs');
const out=root+'reports/qa/p517/';mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0});const fake=await startFakeHost();const B=await launch();const evidence=[];
try{
for(const [name,w,h,scheme,lang,n,settings] of [
 ['phone-home-light',390,844,'light','zh',5,false],
 ['phone-home-dark',390,844,'dark','zh',5,false],
 ['phone-settings-light',390,844,'light','zh',5,true],
 ['phone-settings-dark',390,844,'dark','en',10,true],
 ['desktop-home-light',1440,900,'light','en',10,false],
 ['desktop-home-dark',1440,900,'dark','en',5,false]]){
 fake.reset();fake.st.prefs={appearance:{language:lang,theme:scheme},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
 fake.remotes({limit:10,items:Array.from({length:n},(_,i)=>({id:i?'preview-device-'+i:'ME',name:i?'Remote '+(i+1):'iPhone',me:!i,online:!i,paired_at:1792886400,seen:1792886400}))});
 const p=await newPage(B,w,h,scheme,{touch:w<500,allow:[web.url,fake.relay+'/']});
 try{
 await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');assert.equal(await evaluate(p,`[...document.querySelectorAll('[data-remote-count]')].every(b=>b.hidden)`),true);await fake.approve();await waitState(p,'ready');
 await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
 await fake.addTurn({k:'phone',text:lang==='zh'?'看看今天的安排':'Show today’s plan'},lang==='zh'?'今天的安排已经整理好了，随时可以开始。':'Today’s plan is ready. We can start whenever you are.');
 await waitFor(p,`document.querySelector('#setBtn [data-remote-count]').textContent === '${n}'`);
 if(settings){await evaluate(p,`document.getElementById('setBtn').click()`);await waitFor(p,`!document.getElementById('settings').hidden`);}
 await sleep(350);
 const meta=await evaluate(p,`(()=>{const b=document.querySelector('${settings?'#set-remotes-open':'#setBtn'} [data-remote-count]'),s=getComputedStyle(b),r=b.getBoundingClientRect();return {count:b.textContent,width:r.width,height:r.height,background:s.backgroundColor,color:s.color,pointer:s.pointerEvents,hit:document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.closest('button')?.id,theme:document.documentElement.dataset.theme,lang:document.documentElement.lang,overflow:document.documentElement.scrollWidth>innerWidth}})()`);
 const shot=await shoot(p,'unused');writeFileSync(out+name+'.png',shot);
 const region=await evaluate(p,`(()=>{const r=document.querySelector('${settings?'#set-remotes-open':'#setBtn'}').getBoundingClientRect();return [Math.floor(r.x),Math.floor(r.y),Math.ceil(r.right),Math.ceil(r.bottom)]})()`);
 const expected=JSON.parse(readFileSync(baseline+'manifest.json'))[name];
 assert.equal(createHash('sha256').update(readFileSync(baseline+name+'.png')).digest('hex'),expected.sha256,'approved baseline bytes');
 // Compare the complete entry button (digit, circle, position, icon and spacing). Other page animations are outside this feature.
 const visual=await evaluate(p,`(async()=>{
   const load=async(src)=>{const i=new Image();i.src=src;await i.decode();return i;};
   const base=await load('data:image/png;base64,${readFileSync(baseline+name+'.png').toString('base64')}');
   const actual=await load('data:image/png;base64,${shot.toString('base64')}');
   if(base.width!==actual.width||base.height!==actual.height)throw Error('viewport changed');
   const box=${JSON.stringify(region)},w=box[2]-box[0],h=box[3]-box[1];
   const pixels=(img)=>{const c=document.createElement('canvas');c.width=w;c.height=h;const ctx=c.getContext('2d');ctx.drawImage(img,-box[0],-box[1]);return ctx.getImageData(0,0,w,h).data;};
   const a=pixels(base),b=pixels(actual);let different=0;
   for(let i=0;i<a.length;i+=4)if(Math.max(Math.abs(a[i]-b[i]),Math.abs(a[i+1]-b[i+1]),Math.abs(a[i+2]-b[i+2]))>16)different++;
   return {different,pixels:w*h,box};
 })()`);
 assert.ok(visual.different/visual.pixels<=0.005,JSON.stringify(visual));
 assert.equal(meta.width,20);assert.equal(meta.height,20);assert.equal(meta.background,'rgb(250, 249, 245)');assert.equal(meta.color,'rgb(98, 152, 135)');assert.equal(meta.overflow,false);assert.equal(meta.pointer,'none');assert.equal(meta.hit,settings?'set-remotes-open':'setBtn');
 meta.visual=visual;
evidence.push({name,w,h,...meta,problems:p.problems});
 rec.record('visual-'+name,true);console.log('PASS visual',name,JSON.stringify(meta));
 if(name==='phone-home-light') {
   const check=async(n)=>{await waitFor(p,`[...document.querySelectorAll('[data-remote-count]')].every(b=>b.textContent==='${n}' && b.hidden===${n===0})`);assert.match(await evaluate(p,`document.getElementById('set-remotes-count').textContent`),new RegExp('^\\D*'+n+'\\D'));};
   for(const n of [1,10,9,0,5]) {const items=Array.from({length:n},(_,i)=>({id:i?'changed-'+i:'ME',me:!i}));fake.remotes({limit:10,items});await fake.send({t:'devs',limit:10,items});await check(n);}
   console.log('PASS count: shared N/10, 1/10/9/0/5 broadcasts without opening Settings');
   fake.remotes({limit:10,items:Array.from({length:3},(_,i)=>({id:'reconnected-'+i}))});
   fake.kick(); await waitFor(p,`window.__ajState!=='ready'`); await waitState(p,'ready'); await check(3);
   console.log('PASS reconnect: refreshes changed host list');
   // Hold a real encrypted read reply across forgetting the host, then deliver it late.
   let held=null;fake.st.remotes=null;fake.answer('dev_list',m=>{held=m;return [];});
   await evaluate(p,`(async()=>{const r=await import('./js/remotes.js');window.__p517Pending=r.refresh();})()`);
   for(let i=0;i<100&&!held;i++)await sleep(10);assert.ok(held,'host received pending dev_list');
   await evaluate(p,`(async()=>{const s=await import('./js/settings.js');s.configure({paired:()=>false});s.connectionChanged(false);})()`);
   await fake.send({t:'devs',r:held.r,limit:10,items:[{id:'forgotten-host'}]});
   await evaluate(p,`window.__p517Pending`);
   await waitFor(p,`[...document.querySelectorAll('[data-remote-count]')].every(b=>b.hidden && b.textContent==='')`);
   assert.equal(await evaluate(p,`(async()=>{const r=await import('./js/remotes.js');return r.count()})()`),null);
   // Paired but no authoritative data: do not invent a digit from old preferences.
   await evaluate(p,`(async()=>{const s=await import('./js/settings.js');s.configure({paired:()=>true});s.setPrefs(null,null);})()`);
   assert.equal(await evaluate(p,`[...document.querySelectorAll('[data-remote-count]')].every(b=>b.hidden)`),true);
   console.log('PASS unknown/unpaired and late reply: clears prior host count');
   rec.record('count-lifecycle',true);
 }
 assert.deepEqual(p.problems,[]);

 }finally{await p.dispose();}
}
writeFileSync(out+'evidence.json',JSON.stringify(evidence,null,2));
}finally{rec.write();await B.close();await fake.stop();await web.stop();}
