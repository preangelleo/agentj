// #538b: real Chromium font preferences; settings must share the app's explicit root size.
import assert from 'node:assert/strict';
// Font family names below are installed Linux stand-ins, never the literal Chrome UI label ‘Custom’.
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {launch,newPage,navigate,evaluate,shoot,sleep} from './browser.mjs';
import {startWebServer} from './serve.mjs';
const out=resolve(process.env.P538B_OUT||'reports/qa/538b-web');mkdirSync(out,{recursive:true});
const phase=process.env.P538B_PHASE||'after', rows=[];
const server=await startWebServer(process.env.P538B_PUBLIC?{publicDir:resolve(process.env.P538B_PUBLIC)}:{}),browser=await launch();let failure;
try {
 for(const width of [390,1440]) for(const theme of ['light','dark']) for(const size of [16,13]){
  const page=await newPage(browser,width,900,theme,{allow:[server.url]});
  try{
   await page.send('Page.setFontFamilies',{fontFamilies:{standard:'Liberation Serif',serif:'Liberation Serif',sansSerif:'Liberation Sans',fixed:'Liberation Mono'}});
   await page.send('Page.setFontSizes',{fontSizes:{standard:size,fixed:10}});
   await navigate(page,server.url);
   await evaluate(page,`(async()=>{document.body.dataset.view='chat';document.body.dataset.conn='on';document.body.dataset.status='idle';document.querySelector('#om').hidden=false;document.querySelector('#words').className='words';document.querySelector('#words').textContent='聊天正文 ABC / Reply text. 这是一段用于比较字体的测试回复。';document.querySelector('.omtxt').textContent='输入卡 ABC / Input card';document.querySelector('#input').placeholder='输入 ABC';await document.fonts.ready;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));})()`);
   // CDP platform glyphs only exist after a rendered frame, not merely fonts.ready.
   // Capture before querying: screenshot waits for the compositor to consume the fixture DOM.
   await page.send('DOM.enable');await page.send('CSS.enable');const fonts={};
   for(const selector of ['#words','.omtxt','#input','.setrow .setlab']){
    // Startup can redraw the placeholder after the fixture was inserted. Query a freshly
    // painted, visible node rather than racing that redraw or trusting fonts.ready alone.
    let painted=[];
    for(let attempt=0;attempt<12;attempt++){
     if(selector==='#words'||selector==='.omtxt')await evaluate(page,`(()=>{const n=document.querySelector(${JSON.stringify(selector)});n.textContent=${JSON.stringify(selector==='#words'?'聊天正文 ABC / Reply text. 这是一段用于比较字体的测试回复。':'输入卡 ABC / Input card')};})()`);
     await shoot(page);
     const {root}=await page.send('DOM.getDocument');const {nodeId}=await page.send('DOM.querySelector',{nodeId:root.nodeId,selector});
     painted=(await page.send('CSS.getPlatformFontsForNode',{nodeId})).fonts;
     if(painted.length||selector==='.setrow .setlab')break;
     await sleep(100);
    }
    fonts[selector]=painted;
    if(selector!=='.setrow .setlab')assert.ok(painted.length,`Visible ${selector} must have painted glyphs (${width}/${theme}/${size})`);
   }
   const values=await evaluate(page,`Object.fromEntries(['html','body','#words','.omtxt','#input','.setrow .setlab'].map(s=>{const c=getComputedStyle(document.querySelector(s));return[s,{size:c.fontSize,line:c.lineHeight,family:c.fontFamily}]}))`);
   const pref=await evaluate(page,`(async()=>{const m=await import('./js/settings.js');m.setFontStep(4);const a={rs:getComputedStyle(document.querySelector('#words')).fontSize,stored:localStorage.getItem('aj.fontScale')};m.setFontStep(2);return a;})()`);
   rows.push({width,theme,browserStandardSize:size,values,fonts,pref});
   {writeFileSync(resolve(out,`${phase}-${width}-${theme}-${size}-chat.png`),await shoot(page));await evaluate(page,`(async()=>{const m=await import('./js/settings.js');m.open();document.querySelector('#settings').dataset.unpaired='0';document.documentElement.style.scrollBehavior='auto';document.querySelector('#set-main').style.scrollBehavior='auto';const c=document.querySelector('#set-main'),target=document.querySelector('#set-s-app');c.scrollTop+=target.getBoundingClientRect().top-c.getBoundingClientRect().top-12;await document.fonts.ready;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));})()`);await shoot(page);const {root:sr}=await page.send('DOM.getDocument');const {nodeId:sid}=await page.send('DOM.querySelector',{nodeId:sr.nodeId,selector:'#set-s-app .setrow:not([data-paired]) .setlab'});fonts['visible App section label']=(await page.send('CSS.getPlatformFontsForNode',{nodeId:sid})).fonts;assert.ok(fonts['visible App section label'].length,'Visible Settings label must have painted glyphs');writeFileSync(resolve(out,`${phase}-${width}-${theme}-${size}-settings.png`),await shoot(page));}
   if(phase!=='before'){assert.equal(values.html.size,'16px');assert.equal(values['.setrow .setlab'].size,'15px');assert.equal(pref.stored,'1.25');assert.equal(parseFloat(pref.rs),parseFloat(values['#words'].size)*1.25);assert.equal(await evaluate(page,`document.querySelector('#set-typography-value')`),null);}
  }finally{await page.dispose();}
 }
}catch(e){failure=e;}finally{writeFileSync(resolve(out,phase+'.json'),JSON.stringify(rows,null,2)+'\n');await browser.close();await server.stop();}if(failure)throw failure;
if(phase!=='before') for(let i=0;i<rows.length;i+=2) {assert.deepEqual(rows[i].values,rows[i+1].values);assert.deepEqual(rows[i].fonts,rows[i+1].fonts);}
console.log('PASS #538b '+phase+' profiles='+rows.length);
