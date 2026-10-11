// P142 追加 1 (Leo 10-11 13:49, iPhone home-screen app): after the lock screen the page used only the top ~60 % of the screen.
// Safari's engine (Playwright WebKit) runs the shipped viewport rule (public/js/viewport.js) on the app's own body CSS.
// iOS standalone fires no visualViewport resize on resume, so the stale keyboard-time height is reproduced exactly:
// visualViewport stays reporting 500 px while the layout viewport is 844 px.
//  1. keyboard up (a text field focused + visual viewport > 120 px shorter): the page follows it (500 px)        — unchanged
//  2. lock → unlock: the field lost focus, NO resize event; visibility hidden → visible → full height (844 px)    — the fix
//  3. pageshow / window focus alone also restore it, after the next frame + 300 ms re-check
//  4. negative control: with no resume event the stale height stays (what the iPhone showed; a plain re-check keeps it too)
//  5. negative: a resume while the keyboard is genuinely up keeps the keyboard layout (no jump under the keyboard)
// Not physical iPhone acceptance; that stays with Leo's device.
import {createRequire} from 'node:module';
import {readFileSync,mkdirSync,writeFileSync} from 'node:fs';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url);
const {webkit}=require(process.env.AJ_PLAYWRIGHT_MODULE||'/tmp/aj-webmcp-playwright/node_modules/playwright');
const src=readFileSync(new URL('../public/js/viewport.js',import.meta.url),'utf8').replaceAll('export function','function').replaceAll('export const','const');
const css=readFileSync(new URL('../public/app.css',import.meta.url),'utf8');
const body=css.slice(css.indexOf('body{\n  margin:0;'),css.indexOf('}',css.indexOf('body{\n  margin:0;'))+1);
assert.ok(body.includes('var(--vvh,100dvh)'),'the app body rule is the one under test');
const browser=await webkit.launch();
const out={engine:'WebKit',physical:false,steps:[]};
const ok=(v,n,extra)=>{assert.ok(v,n+' '+JSON.stringify(extra??''));out.steps.push({n,...(extra?{extra}:{})});console.log('PASS '+n);};
try{
 const page=await browser.newPage({viewport:{width:390,height:844}});
 await page.setContent(`<style>html{height:100%;overflow:hidden}${body}</style><textarea id="input"></textarea>`);
 await page.addScriptTag({content:`
  const fakeVV=new EventTarget();Object.assign(fakeVV,{height:innerHeight,offsetTop:0,width:innerWidth});
  Object.defineProperty(window,'visualViewport',{configurable:true,value:fakeVV});
  let hiddenNow=false;Object.defineProperty(document,'hidden',{configurable:true,get:()=>hiddenNow});
  window.setHidden=(h)=>{hiddenNow=h;document.dispatchEvent(new Event('visibilitychange'));};
  ${src};
  window.fits=0;watchViewport(()=>{window.fits++;applyViewport();});
  window.bodyH=()=>Math.round(document.body.getBoundingClientRect().height);`});
 const H=()=>page.evaluate(()=>bodyH());
 ok(await H()===844,'start: full height',await H());
 // 1. keyboard up
 await page.evaluate(()=>{document.getElementById('input').focus();visualViewport.height=500;visualViewport.dispatchEvent(new Event('resize'));});
 await page.waitForTimeout(500);   // past the +300 ms re-check of that resize
 ok(await H()===500,'keyboard up: the page follows the visual viewport',await H());
 // 4. negative control first: lock (focus lost, no events at all) → the stale 500 stays
 await page.evaluate(()=>document.getElementById('input').blur());
 await page.waitForTimeout(400);
 ok(await H()===500,'negative control: no resume event → still the stale keyboard height (the iPhone symptom)',await H());
 await page.evaluate(()=>window.dispatchEvent(new Event('resize')));await page.waitForTimeout(400);
 ok(await H()===500,'negative control: a plain re-check (not a resume) does not guess either',await H());
 // 2. unlock: visibility round trip; visualViewport still (wrongly) says 500
 await page.evaluate(()=>{setHidden(true);setHidden(false);});
 await page.waitForTimeout(400);
 ok(await H()===844,'visibility hidden → visible: full height again although visualViewport still reports 500',await H());
 // 3. pageshow / focus each restore it on their own
 for(const ev of ['pageshow','focus']){
  await page.evaluate(()=>{document.documentElement.style.setProperty('--vvh','500px');});
  ok(await H()===500,`(${ev}) stale height re-planted`,await H());
  const n0=await page.evaluate(()=>fits);
  await page.evaluate((e)=>window.dispatchEvent(new Event(e)),ev);
  await page.waitForTimeout(400);
  const n=await page.evaluate(()=>fits)-n0;
  ok(await H()===844&&n>=3,`${ev}: full height; re-checked now, next frame and +300 ms (${n} fits)`,{h:await H(),n});
 }
 // 5. keyboard genuinely up during a resume: stays on the keyboard layout
 await page.evaluate(()=>{document.getElementById('input').focus();visualViewport.height=500;visualViewport.dispatchEvent(new Event('resize'));});
 await page.waitForTimeout(500);
 await page.evaluate(()=>{setHidden(true);setHidden(false);});
 await page.waitForTimeout(400);
 ok(await H()===500,'resume with the keyboard really up keeps the keyboard layout',await H());
 // a real layout-viewport change (Safari resizes the window): never stuck
 await page.evaluate(()=>{document.getElementById('input').blur();visualViewport.height=600;});
 await page.setViewportSize({width:390,height:600});await page.waitForTimeout(400);
 ok(await H()===600,'window resized to 600: body follows (100dvh)',await H());
 await page.setViewportSize({width:390,height:844});await page.evaluate(()=>{visualViewport.height=844;});await page.waitForTimeout(400);
 ok(await H()===844,'window back to 844: full height',await H());
 mkdirSync('reports/qa/p142',{recursive:true});writeFileSync('reports/qa/p142/webkit-viewport.json',JSON.stringify(out,null,2)+'\n');
}finally{await browser.close();}
