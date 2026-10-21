// P251: metadata-only checks; matched host/server updates follow only after protected input is idle.
import VERSION from '../version.js';
import {t,onLang} from './t.js';
import {registerLayer} from './layers.js';
import {notifyOnce} from './ui.js';
export function compareVersion(a,b){
 const parse=v=>typeof v==='string'&&/^(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?$/.exec(v);
 const x=parse(a),y=parse(b);if(!x||!y)return null;
 const rank={a:0,b:1,rc:2};
 const parts=m=>[+m[1],+m[2],+m[3],m[4]?rank[m[4]]:3,+(m[5]||0)];
 const p=parts(x),q=parts(y);for(let i=0;i<p.length;i++)if(p[i]!==q[i])return p[i]>q[i]?1:-1;return 0;
}
export function newerVersion(current,host,web){
 return [host,web].filter(v=>compareVersion(v,current)===1).sort((a,b)=>compareVersion(b,a))[0]||null;
}
export async function readWebVersion(){
 const endpoint=new URL('/version.json',location.origin);
 // Both published domains serve this same Worker/manifest. Keep legacy pairing storage and metadata same-origin.
 const response=await fetch(endpoint, {method:'GET',credentials:'omit',cache:'no-store',redirect:'error',referrerPolicy:'no-referrer',signal:AbortSignal.timeout(10000)});
 if(!response.ok||+(response.headers.get('content-length')||0)>65536)return null;
 const raw=await response.text();if(raw.length>65536)return null;
 const manifest=JSON.parse(raw);
 return manifest.v===1&&compareVersion(manifest.version,manifest.version)===0?manifest.version:null;
}
let hostVersion=null,webVersion=null,dismissed=null,refresh=null;
// P240 (Leo 10-13 23:13, a Huawei host updated by hand before batch B): a refresh only helps when the page it fetches is newer.
// The dialog offers what version.json serves (`webVersion`), never what the host runs: a host ahead of every page that can be
// fetched gets one quiet line under the header (ver.later_line) instead of a dialog whose 刷新 reloads the same page forever.
// And once a refresh came back on the same page version, that offer is not repeated until the page itself changes
// (VER_KEY = "aj.verRefresh": {from, to}, two public version strings, nothing else).
const VER_KEY='aj.verRefresh';
function refreshedTo(){
 try{const r=JSON.parse(localStorage.getItem(VER_KEY)||'null');return r&&r.from===VERSION.version&&typeof r.to==='string'?r.to:null;}catch{return null;}
}
export function versionState(current,host,web,triedTo){
 // `offer`: the dialog's version (a refresh delivers it); `later`: the host is ahead of what a refresh would deliver.
 const ahead=v=>compareVersion(v,current)===1;
 let offer=ahead(web)?web:null;
 if(offer&&triedTo&&compareVersion(offer,triedTo)!==1)offer=null;   // refreshed for this already and still on `current`
 const best=offer||current;
 const later=ahead(host)&&compareVersion(host,best)===1;
 return {offer,later};
}
function state(){return versionState(VERSION.version,hostVersion,webVersion,refreshedTo());}
function close(){dismissed=state().offer;render();}
export function followsHost(current,host,web,triedTo){
 return compareVersion(host,web)===0 && !!versionState(current,host,web,triedTo).offer;
}
let guards={blocked:()=>true,save:async()=>{}}, lastInput=Date.now(), refreshing=false, composing=false;
const IDLE_MS=10000, DONE_KEY='aj.verUpdated';
const autoTarget=()=>followsHost(VERSION.version,hostVersion,webVersion,refreshedTo())?webVersion:null;
function idle(){return !document.hidden && !composing && Date.now()-lastInput>=IDLE_MS && !guards.blocked();}
async function follow(){
 const target=autoTarget();
 if (!target || !idle() || refreshing) return;
 refreshing=true;
 try{
  // Confirm metadata again, after the idle wait. A failed check or changed host cancels this attempt.
  const fresh=await readWebVersion();
  if(fresh!==target || autoTarget()!==target || !idle())return;
  if(!await guards.save())return;
  if(autoTarget()!==target || !idle())return;
  await refresh({silent:true,beforeReload:async()=>{
   if(autoTarget()!==target || !idle())return false;
   if(!await guards.save())return false;
   if(autoTarget()!==target || !idle())return false;
   // No draft in localStorage; only public versions. A stale edge gets one attempt, never a reload loop.
   try{localStorage.setItem(VER_KEY,JSON.stringify({from:VERSION.version,to:target}));
       localStorage.setItem(DONE_KEY,target);}catch{return false;}
   return true;
  }});
 }catch{/* Stay on the usable page on network/storage failure. */}
 finally{refreshing=false;}
}
function render(){
 const box=document.getElementById('version-notice');if(!box)return;
 const {offer,later}=state();
 box.hidden=!!autoTarget()||!offer||offer===dismissed;
 // P150: the global centred dialog 「服务器已升级」 + 刷新 (it used to be a card pressed down from the top).
 document.getElementById('version-notice-text').textContent=t('ver.body',{v:offer||''});
 document.getElementById('version-refresh').textContent=t('ver.refresh');
 document.getElementById('version-dismiss').textContent=t('ver.later');
 const line=document.getElementById('version-later');
 if(line){
  // P425 #60: consume once, through the shared Noir Notification component.
  let updated=null;try{updated=localStorage.getItem(DONE_KEY);if(updated)localStorage.removeItem(DONE_KEY);}catch{}
  if(updated===VERSION.version){
   notifyOnce({kind:'success',shape:'rect',text:t('ver.updated',{v:VERSION.version}),ms:3000},'aj.verUpdatedSeen.'+VERSION.version);
  }
  line.hidden=!later||!box.hidden;
  line.textContent=later?t('ver.later_line'):'';
 }
}
// A new host version: read version.json first, then decide (never a dialog on the host's word alone).
export function setHostVersion(v){hostVersion=v;render();if(check&&v&&compareVersion(v,VERSION.version)===1)check();}
let check=null;
export function initVersionNotice(refreshApp, inputGuards){
 refresh=refreshApp; if(inputGuards)guards=inputGuards;
 const activity=e=>{lastInput=Date.now(); if(e.type==='compositionstart')composing=true; if(e.type==='compositionend')composing=false;};
 for(const name of ['input','keydown','pointerdown','compositionstart','compositionend'])document.addEventListener(name,activity,true);
 setInterval(follow,1000);
 document.getElementById('version-refresh').addEventListener('click',()=>{
  const {offer}=state();
  try{if(offer)localStorage.setItem(VER_KEY,JSON.stringify({from:VERSION.version,to:offer}));}catch{/* blocked storage: the dialog may come back once */}
  refresh();
 });
 document.getElementById('version-dismiss').addEventListener('click',close);
 document.getElementById('version-notice').addEventListener('click',e=>{if(e.target===e.currentTarget)close();});   // outside the square = later
 registerLayer({element:()=>document.getElementById('version-notice'),open:()=>!document.getElementById('version-notice').hidden,close,fallback:()=>document.getElementById('setBtn'),trap:false,rank:-10,preserveOutsideFocus:true});
 onLang(render);render();
 check=async()=>{try{webVersion=await readWebVersion();render();}catch{/* Offline/old metadata: no false update warning. */}};
 check();setInterval(()=>{if(!document.hidden)check();},300000);
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)check();});
}
