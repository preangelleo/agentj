// Local capability probe only. No receiver discovery result proves physical playback.
import {createRequire} from 'node:module';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
const {chromium,webkit}=createRequire(import.meta.url)(process.env.AJ_PLAYWRIGHT_MODULE||'/tmp/aj-webmcp-playwright/node_modules/playwright');
const web=await startWebServer(),out=[];
try {
 for(const [engine,type,options] of [['Chromium',chromium,{executablePath:'/usr/bin/chromium'}],['WebKit',webkit,{}]]) {
  let browser;
  try {
   browser=await type.launch(options);const page=await browser.newPage();await page.goto(web.url);
   const result=await page.evaluate(async data=>{
    const v=document.createElement('video');v.src=URL.createObjectURL(new Blob([Uint8Array.from(atob(data),c=>c.charCodeAt(0))],{type:'video/webm'}));v.setAttribute('x-webkit-airplay','allow');document.body.append(v);
    const r={fixture:'valid local WebM',sourceScheme:'blob:',airplayPicker:typeof v.webkitShowPlaybackTargetPicker==='function',airplayAvailabilityEvent:typeof window.WebKitPlaybackTargetAvailabilityEvent==='function',remoteAPI:!!v.remote,physicalPlayback:'not-tested',availability:[]};
    v.addEventListener('webkitplaybacktargetavailabilitychanged',e=>r.availability.push(e.availability));
    if(v.remote){try{const id=await Promise.race([v.remote.watchAvailability(a=>r.availability.push(a)),new Promise((_,reject)=>setTimeout(()=>reject(new Error('probe-timeout')),1500))]);r.watch='registered';await new Promise(r=>setTimeout(r,500));await v.remote.cancelWatchAvailability(id);}catch(e){r.watch=e.name+':'+e.message;}}
    URL.revokeObjectURL(v.src);v.remove();return r;
   },readFileSync(new URL('./fixtures/p196/clip.webm',import.meta.url)).toString('base64'));out.push({engine,...result});
  } catch(e){out.push({engine,error:e.name,physicalPlayback:'not-tested'});}finally{await browser?.close();}
 }
 mkdirSync('reports/qa/p260',{recursive:true});writeFileSync('reports/qa/p260/cast-probe.json',JSON.stringify(out,null,2)+'\n');console.log(JSON.stringify(out));
}finally{await web.stop();}
