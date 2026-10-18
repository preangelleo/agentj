// WebKit engine regression with fixed env() fixture insets; no physical iOS OS-chrome claim.
import {createRequire} from 'node:module';
import {readFileSync,mkdirSync,writeFileSync} from 'node:fs';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url),{webkit}=require(process.env.AJ_PLAYWRIGHT_MODULE||'/tmp/aj-webmcp-playwright/node_modules/playwright');
const read=f=>readFileSync(new URL('../public/'+f,import.meta.url),'utf8');
const css=[read('brand/palette.css'),read('app.css')].join('\n').replace(/env\(safe-area-inset-bottom(?:,0px)?\)/g,'34px').replace(/env\(safe-area-inset-top(?:,0px)?\)/g,'47px');
const out=new URL('../../../reports/qa/p384/web/',import.meta.url);mkdirSync(out,{recursive:true});
const B=await webkit.launch(),results=[];
try{
 for(const ipad of [false,true])for(const standalone of [false,true]){
  const p=await B.newPage({viewport:{width:390,height:844}});
  await p.setContent(`<meta name="theme-color" content="#faf9f5"><meta name="apple-mobile-web-app-status-bar-style" content="default"><style>${css}</style><body data-conn="on" data-status="idle"><i id="satband" class="satband"></i><i id="sabband" class="sabband"></i><div style="flex:1"></div><div class="composer"><textarea id="input"></textarea></div></body>`);
  await p.evaluate(({ipad,standalone})=>{Object.defineProperties(navigator,{userAgent:{get:()=>ipad?'Macintosh Safari':'iPhone Safari',configurable:true},platform:{get:()=>ipad?'MacIntel':'iPhone',configurable:true},maxTouchPoints:{get:()=>5,configurable:true},standalone:{get:()=>standalone,configurable:true}});},{ipad,standalone});
  await p.addScriptTag({content:read('js/boot.js')});
  for(const scheme of ['light','dark'])for(const state of ['idle','working','waiting','question','offline','reader','settings']){
   await p.evaluate(({scheme,state})=>{document.documentElement.dataset.theme=scheme;Object.assign(document.body.dataset,{status:state==='question'?'waiting':state==='offline'?'unknown':state,kind:state==='question'?'question':'',conn:state==='offline'?'off':'on',reader:state==='reader'?'1':'0'});document.getElementById('settings-fixture')?.remove();if(state==='settings'){const s=document.createElement('div');s.id='settings-fixture';s.style.cssText='position:fixed;inset:0;z-index:46;background:var(--c-noir)';document.body.append(s);}},{scheme,state});
   const r=await p.evaluate(()=>{const s=document.getElementById('sabband'),b=s.getBoundingClientRect();return {top:getComputedStyle(document.getElementById('satband')).backgroundColor,bottom:getComputedStyle(s).backgroundColor,height:b.height,bottomY:b.bottom,meta:document.querySelector('meta[name=theme-color]').content,apple:document.querySelector('meta[name=apple-mobile-web-app-status-bar-style]').content};});
   assert.equal(r.top,'rgb(20, 20, 20)');assert.equal(r.bottom,r.top);assert.equal(r.meta,'#141414');assert.equal(r.apple,'black-translucent');assert.equal(r.height,34);assert.equal(r.bottomY,844);results.push({ipad,standalone,scheme,state,...r});
   if(state==='settings')await p.screenshot({path:new URL(`webkit-${ipad?'ipad':'iphone'}-${standalone?'pwa':'safari'}-${scheme}.png`,out).pathname});
  }
  await p.evaluate(()=>document.body.dataset.kb='1');assert.equal(await p.evaluate(()=>getComputedStyle(document.getElementById('sabband')).display),'none');await p.close();
 }
 writeFileSync(new URL('webkit-results.json',out),JSON.stringify({engine:'WebKit',physical:false,fixture_insets:true,cases:results},null,2)+'\n');console.log(`P384 WebKit PASS (${results.length} cases)`);
}finally{await B.close();}
