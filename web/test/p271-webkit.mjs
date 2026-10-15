// WebKit parses the shipped stylesheet, with safe-area env values substituted by fixture constants.
// No iPhone OS is simulated: this is an engine regression for the band, theme, reader and keyboard CSS.
import { createRequire } from 'node:module';
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url);
const {webkit}=require(process.env.AJ_PLAYWRIGHT_MODULE||'/tmp/aj-webmcp-playwright/node_modules/playwright');
const css=readFileSync(new URL('../public/app.css',import.meta.url),'utf8').replace(/env\(safe-area-inset-bottom(?:,0px)?\)/g,'34px').replace(/env\(safe-area-inset-top(?:,0px)?\)/g,'47px');
const palette=readFileSync(new URL('../public/brand/palette.css',import.meta.url),'utf8');
const B=await webkit.launch();const results=[];
try {
 const page=await B.newPage({viewport:{width:390,height:844}});
 await page.setContent(`<style>${palette}\n${css}</style><body data-conn="on" data-status="idle"><i id="satband" class="satband"></i><i id="sabband" class="sabband"></i><div style="flex:1"></div><div class="composer"><textarea id="input"></textarea></div></body>`);
 for(const scheme of ['light','dark'])for(const state of ['idle','working','waiting','question','offline']){
  await page.evaluate(({scheme,state})=>{document.documentElement.dataset.theme=scheme;Object.assign(document.body.dataset,{status:state==='question'?'waiting':state==='offline'?'unknown':state,kind:state==='question'?'question':'',conn:state==='offline'?'off':'on'});},{scheme,state});
  const r=await page.evaluate(()=>{const band=document.getElementById('sabband'),b=band.getBoundingClientRect();return {body:getComputedStyle(document.body).backgroundColor,band:getComputedStyle(band).backgroundColor,top:getComputedStyle(document.getElementById('satband')).backgroundColor,height:b.height,bottom:b.bottom,inputBottom:document.getElementById('input').getBoundingClientRect().bottom};});
  assert.equal(r.body,r.band);assert.equal(r.body,r.top);assert.equal(r.height,34);assert.equal(r.bottom,844);assert.ok(r.inputBottom<=810);results.push({scheme,state,...r});
 }
 await page.evaluate(()=>document.body.dataset.reader='1');
 assert.equal(await page.evaluate(()=>getComputedStyle(document.getElementById('sabband')).backgroundColor),'rgb(20, 20, 20)');
 await page.evaluate(()=>document.body.dataset.kb='1');
 assert.equal(await page.evaluate(()=>getComputedStyle(document.getElementById('sabband')).display),'none');
 mkdirSync('reports/qa/p271',{recursive:true});writeFileSync('reports/qa/p271/webkit.json',JSON.stringify({engine:'WebKit',physical:false,fixture_insets:true,results},null,2)+'\n');console.log('PASS P271 WebKit: 10 state/theme cases + reader + keyboard');
}finally{await B.close();}
