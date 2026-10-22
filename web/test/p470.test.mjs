import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const source=readFileSync(new URL('../public/js/ios-chrome.js',import.meta.url),'utf8');
test('P470 bottom follows only visible water at the bottom, otherwise page, with Noir failure/fullscreen',async()=>{
 const saved=Object.fromEntries(['document','navigator','getComputedStyle','MutationObserver','location'].map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 const values=new Map(),html={dataset:{ios:'1'},style:{getPropertyValue:k=>values.get(k),setProperty:(k,v)=>values.set(k,v)}};
 let rect={top:60,bottom:100,height:40},waterStyle={backgroundColor:'rgb(1, 2, 3)',display:'block',visibility:'visible',opacity:'1'},pageStyle={backgroundColor:'rgb(4, 5, 6)'};
 const body={dataset:{},getBoundingClientRect:()=>({bottom:100})},water={getBoundingClientRect:()=>rect},page={};
 try{
  Object.defineProperty(globalThis,'navigator',{configurable:true,value:{userAgent:'iPhone Version/18.0 Safari/605.1'}});
  globalThis.document={documentElement:html,body,getElementById:id=>id==='water'?water:id==='chromeProbe'?page:null};
  globalThis.getComputedStyle=e=>e===water?waterStyle:e===page?pageStyle:{getPropertyValue:()=> '#2c2b26'};
  globalThis.MutationObserver=class{observe(){}};globalThis.location={search:''};
  const {paintIOSChrome}=await import('data:text/javascript,'+encodeURIComponent(source));
  const expect=c=>{paintIOSChrome();assert.equal(values.get('--ios-bottom'),c);assert.equal(values.has('--ios-chrome'),false)};
  expect('rgb(1, 2, 3)');assert.equal(html.dataset.iosSafari,'1');
  rect.height=0;expect('rgb(4, 5, 6)');rect.height=40;
  rect.bottom=90;expect('rgb(4, 5, 6)');rect.bottom=100;
  for(const [key,value] of [['display','none'],['visibility','hidden'],['opacity','0']]){const old=waterStyle[key];waterStyle[key]=value;expect('rgb(4, 5, 6)');waterStyle[key]=old}
  for(const key of ['fs','reader']){body.dataset[key]='1';expect('#2c2b26');delete body.dataset[key]}
  waterStyle.backgroundColor='transparent';expect('#2c2b26');waterStyle.display='none';pageStyle.backgroundColor='transparent';expect('#2c2b26');
  pageStyle.backgroundColor='rgb(4, 5, 6)';html.dataset.standalone='1';expect('rgb(4, 5, 6)');assert.equal(html.dataset.iosSafari,'0');
  html.dataset.standalone='0';navigator.userAgent='iPhone CriOS/131.0 Mobile Safari/605.1';waterStyle.backgroundColor='rgb(1, 2, 3)';expect('rgb(1, 2, 3)');assert.equal(html.dataset.iosSafari,'0');
 }finally{for(const [key,descriptor] of Object.entries(saved)){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key]}}
});
