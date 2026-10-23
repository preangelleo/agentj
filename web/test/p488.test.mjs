import test from 'node:test';import assert from 'node:assert/strict';import {readFileSync} from 'node:fs';
const source=readFileSync(new URL('../public/js/ios-chrome.js',import.meta.url),'utf8');
test('P488 geometry changes refresh the cached bottom after a zero-height transition start',async()=>{
 const saved=Object.fromEntries(['document','navigator','getComputedStyle','MutationObserver','ResizeObserver','location'].map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 const values=new Map(),events={},observed=[];let resize,rect={top:100,bottom:100,height:0};
 const html={dataset:{ios:'1'},style:{getPropertyValue:k=>values.get(k),setProperty:(k,v)=>values.set(k,v)}},body={dataset:{},getBoundingClientRect:()=>({bottom:100})},page={};
 const water={getBoundingClientRect:()=>rect,addEventListener:(k,f)=>events[k]=f};
 try{
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{userAgent:'iPhone Version/18.0 Safari/605.1'}});
 globalThis.document={documentElement:html,body,getElementById:id=>id==='water'?water:id==='chromeProbe'?page:null};globalThis.location={search:''};
 globalThis.getComputedStyle=e=>e===water?{backgroundColor:'rgb(70, 90, 110)',display:'block',visibility:'visible',opacity:'1'}:{backgroundColor:'rgb(90, 110, 130)'};
 globalThis.MutationObserver=class{observe(){}};globalThis.ResizeObserver=class{constructor(f){resize=f}observe(e){observed.push(e)}};
 const {paintIOSChrome}=await import('data:text/javascript,'+encodeURIComponent(source));paintIOSChrome();assert.equal(values.get('--ios-bottom'),'rgb(90, 110, 130)');
 assert.deepEqual(observed,[water]);rect={top:50,bottom:100,height:50};resize();assert.equal(values.get('--ios-bottom'),'rgb(70, 90, 110)');
 for(let i=0;i<5;i++)paintIOSChrome();assert.equal(observed.length,1,'one geometry observer per page');
 rect.height=0;resize();assert.equal(values.get('--ios-bottom'),'rgb(90, 110, 130)');
 rect={top:50,bottom:100,height:50};events.transitionend();assert.equal(values.get('--ios-bottom'),'rgb(70, 90, 110)');
 rect.bottom=90;events.animationend();assert.equal(values.get('--ios-bottom'),'rgb(90, 110, 130)');
 body.dataset.fs='1';events.transitioncancel();assert.equal(values.get('--ios-bottom'),'#2c2b26');
 }finally{for(const [k,d]of Object.entries(saved)){if(d)Object.defineProperty(globalThis,k,d);else delete globalThis[k]}}
});
