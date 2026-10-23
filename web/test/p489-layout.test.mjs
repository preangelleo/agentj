import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
test('diagnostic collector and bilingual rescue button exist', async()=>{
 const m=await import('../public/js/layout-sample.js');
 assert.equal(typeof m.capture,'function');
 const html=readFileSync(new URL('../public/index.html',import.meta.url),'utf8');
 assert.match(html,/id="layout-diag-send"/);
 for(const lang of ['zh','en'])assert.ok(JSON.parse(readFileSync(new URL(`../i18n/web.${lang}.json`,import.meta.url))).p489.send);
});
test('collector projects fixed fields and never reads URL, storage, text or raw UA into payload',async()=>{
 const {capture}=await import('../public/js/layout-sample.js');
 const saved={document:globalThis.document,window:globalThis.window,navigator:Object.getOwnPropertyDescriptor(globalThis,'navigator'),getComputedStyle:globalThis.getComputedStyle};
 const e={dataset:{ios:'1',standalone:'0',theme:'dark',status:'working',conn:'on',known:'1',compacting:'0',secret:'do-not-collect'},style:{getPropertyValue:()=>'.12'},getBoundingClientRect:()=>({x:0,y:700,width:390,height:144}),clientHeight:844};
 Object.defineProperty(e,'textContent',{get(){throw Error('content read')}});
 try{
  globalThis.document={documentElement:e,body:e,getElementById:()=>e};globalThis.window={innerHeight:844,visualViewport:{height:844,offsetTop:0,scale:1}};
  Object.defineProperty(globalThis,'navigator',{configurable:true,value:{userAgent:'iPhone CriOS SECRET'}});
  globalThis.getComputedStyle=()=>({backgroundColor:'rgb(69, 102, 92)',display:'block',opacity:'1',transform:'none',getPropertyValue:()=> 'rgb(69, 102, 92)'});
  const s=capture({build:{version:'0.25.3a1',combined:'a'.repeat(64)},meter:{ctx:{pct:12},h5:{pct:4},week:{pct:68},secret:'not copied'},look:{id:'custom private title',factory:'robots'}});
  assert.equal(s.ua,'chrome');assert.equal(s.conn,'on');assert.equal(s.theme,'dark');assert.equal(s.level,.12);assert.equal(s.meter.ctx,12);assert.equal(s.look.custom,true);
  assert.ok(!JSON.stringify(s).includes('SECRET'));assert.ok(!JSON.stringify(s).includes('not copied'));assert.ok(!JSON.stringify(s).includes('private title'));assert.ok(!JSON.stringify(s).includes('do-not-collect'));
 }finally{globalThis.document=saved.document;globalThis.window=saved.window;globalThis.getComputedStyle=saved.getComputedStyle;Object.defineProperty(globalThis,'navigator',saved.navigator);}
});
