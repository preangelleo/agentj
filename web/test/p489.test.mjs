import test from 'node:test';import assert from 'node:assert/strict';import {readFileSync} from 'node:fs';
const source=readFileSync(new URL('../public/js/ios-chrome.js',import.meta.url),'utf8');
for(const mode of ['safari','chrome','standalone'])test(`P489 #265 ${mode} bottom follows visible water, including zero/unknown`,async()=>{
 const saved=Object.fromEntries(['document','navigator','getComputedStyle','MutationObserver','ResizeObserver','location'].map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 const values=new Map(),events={};let resize,rect={top:844,bottom:844,height:0},display='block',visibility='visible',opacity='1';
 const html={dataset:{ios:'1',standalone:mode==='standalone'?'1':'0'},style:{getPropertyValue:k=>values.get(k),setProperty:(k,v)=>values.set(k,v)}},body={dataset:{},getBoundingClientRect:()=>({bottom:844})},page={};
 const water={getBoundingClientRect:()=>rect,addEventListener:(k,f)=>events[k]=f};
 try{
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{userAgent:mode==='chrome'?'iPhone CriOS/130.0 Mobile Safari/604.1':'iPhone Version/18.0 Safari/605.1'}});
 globalThis.document={documentElement:html,body,getElementById:id=>id==='water'?water:id==='chromeProbe'?page:null};globalThis.location={search:''};
 globalThis.getComputedStyle=e=>e===water?{backgroundColor:'rgb(69, 102, 92)',display,visibility,opacity}:{backgroundColor:'rgb(76, 115, 103)'};
 globalThis.MutationObserver=class{observe(){}};globalThis.ResizeObserver=class{constructor(f){resize=f}observe(){}};
 const {paintIOSChrome}=await import('data:text/javascript,'+encodeURIComponent(source)+'#'+mode);paintIOSChrome();
 const check=(expected,label)=>assert.equal(values.get('--ios-bottom'),expected,label),pageColor='rgb(76, 115, 103)',waterColor='rgb(69, 102, 92)';
 check(pageColor,'known zero has no water layer');
 rect={top:742.72,bottom:844,height:101.28};resize();check(waterColor,'12% touches bottom');
 display='none';paintIOSChrome();check(pageColor,'unknown is hidden');display='block';
 opacity='0';paintIOSChrome();check(pageColor,'transparent water');opacity='1';
 visibility='hidden';paintIOSChrome();check(pageColor,'invisible water');visibility='visible';
 rect={top:850,bottom:951.28,height:101.28};events.animationend();check(pageColor,'compaction entirely below bottom');
 rect={top:742.72,bottom:834,height:91.28};events.transitionend();check(pageColor,'does not reach bottom');
 rect={top:742.72,bottom:844,height:101.28};events.transitionend();check(waterColor,'settled after compact');
 for(const overlay of ['reader','fs']){body.dataset[overlay]='1';paintIOSChrome();check('#2c2b26',overlay);delete body.dataset[overlay]}
 html.dataset.ios='0';const prev=values.get('--ios-bottom');paintIOSChrome();check(prev,'non-iOS untouched');
 }finally{for(const [k,d]of Object.entries(saved)){if(d)Object.defineProperty(globalThis,k,d);else delete globalThis[k]}}
});
