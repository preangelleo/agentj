// P120: metadata-only version checks; never reload or move focus without an owner click.
import VERSION from '../version.js';
import {t,onLang} from './t.js';
import {registerLayer} from './layers.js';
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
function close(){dismissed=newerVersion(VERSION.version,hostVersion,webVersion);render();}
function render(){
 const box=document.getElementById('version-notice');if(!box)return;
 const latest=newerVersion(VERSION.version,hostVersion,webVersion);
 box.hidden=!latest||latest===dismissed;
 document.getElementById('version-notice-text').textContent=t('ver.new',{v:latest||''});
 document.getElementById('version-refresh').textContent=t('set.refresh');
 document.getElementById('version-dismiss').textContent=t('ver.later');
}
export function setHostVersion(v){hostVersion=v;render();}
export function initVersionNotice(refreshApp){
 refresh=refreshApp;
 document.getElementById('version-refresh').addEventListener('click',()=>refresh());
 document.getElementById('version-dismiss').addEventListener('click',close);
 registerLayer({element:()=>document.getElementById('version-notice'),open:()=>!document.getElementById('version-notice').hidden,close,fallback:()=>document.getElementById('setBtn'),trap:false,rank:-10});
 onLang(render);render();
 const check=async()=>{try{webVersion=await readWebVersion();render();}catch{/* Offline/old metadata: no false update warning. */}};
 check();setInterval(()=>{if(!document.hidden)check();},300000);
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)check();});
}
