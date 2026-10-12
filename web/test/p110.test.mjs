import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';
import {handle} from '../worker.ts';
const publicDir = new URL('../public/', import.meta.url);
function worker(){
 const listeners = new Map(); let now = 1000;
 const context = vm.createContext({URL,Response,Request,Blob,File,crypto:webcrypto,Date:{now:()=>now},self:{
  location:new URL('https://m.agentj.app/sw.js'),addEventListener:(n,f)=>listeners.set(n,f),
 }});
 vm.runInContext(readFileSync(new URL('sw.js',publicDir),'utf8'),context);
 return {listeners, advance:n=>now+=n};
}
function form(images=[new File(['synthetic image'],'screen.png',{type:'image/png'})], text='look at this'){
 const body=new FormData(); for(const f of images)body.append('images',f); body.append('text',text); body.append('url','https://example.invalid/private-link');
 return new Request('https://m.agentj.app/share-target',{method:'POST',body});
}
async function post(w,req){let response;w.listeners.get('fetch')({request:req,respondWith:p=>response=p});return response && await response;}
function claim(w,id,url='https://m.agentj.app/?share='+id){let payload;w.listeners.get('message')({data:{t:'share-take',id},source:{url},ports:[{postMessage:p=>payload=p}]});return payload;}
test('manifest registers POST multipart images and basic share fields',()=>{
 const m=JSON.parse(readFileSync(new URL('manifest.webmanifest',publicDir)));
 assert.deepEqual(m.share_target,{action:'./share-target',method:'POST',enctype:'multipart/form-data',params:{title:'title',text:'text',url:'url',files:[{name:'images',accept:['image/*']}]}});
});
test('SW local multipart intake: files/text never in URL, claim once by landing page',async()=>{
 const w=worker(),r=await post(w,form([new File(['one'],'a.png',{type:'image/png'}),new File(['two'],'b.jpg',{type:'image/jpeg'})]));
 assert.equal(r.status,303);const u=new URL(r.headers.get('location')),id=u.searchParams.get('share'); assert.match(id,/^[a-f0-9-]{36}$/);
 assert.ok(!u.href.includes('private-link'));assert.equal(claim(w,id,'https://evil.invalid/?share='+id),undefined);
 assert.equal(claim(w,id,'https://m.agentj.app/'),undefined);
 const v=claim(w,id);assert.equal(v.images.length,2);assert.equal(await v.images[0].text(),'one');assert.match(v.text,/look at this/);
 assert.equal(claim(w,id).error,'expired');
});
test('TTL, concurrent bound and rejected types/size/count/malformed forms fail closed',async()=>{
 const w=worker();const a=await post(w,form()),b=await post(w,form());assert.ok(!b.headers.get('location').endsWith('failed'));
 assert.ok((await post(w,form())).headers.get('location').endsWith('failed'));
 w.advance(300000); assert.equal(claim(w,new URL(a.headers.get('location')).searchParams.get('share')).error,'expired');
 for(const request of [form([new File(['x'],'a.txt',{type:'text/plain'})]),form(Array.from({length:11},()=>new File(['x'],'a.png',{type:'image/png'}))),form([new File([new Uint8Array(26214401)],'large.png',{type:'image/png'})]),form([], 'x'.repeat(16001)),new Request('https://m.agentj.app/share-target',{method:'POST',body:'not multipart'})]){
  assert.ok((await post(w,request)).headers.get('location').endsWith('failed'));
 }
});
test('only the exact share POST is handled; network Worker still rejects all POST without reading body',async()=>{
 const w=worker(); for(const req of [new Request('https://m.agentj.app/share-target'),new Request('https://m.agentj.app/other',{method:'POST'}),new Request('https://evil.invalid/share-target',{method:'POST'})])assert.equal(await post(w,req),undefined);
 let fetched=false;const r=await handle(form(),{WEB_HOST:'m.agentj.app',RELAY_URL:'wss://relay.agentj.app',ASSETS:{fetch:async()=>{fetched=true;throw Error('body must not reach assets')}}});assert.equal(r.status,404);assert.equal(fetched,false);
 const sw=readFileSync(new URL('sw.js',publicDir),'utf8');assert.doesNotMatch(sw,/\bfetch\s*\(|\bcaches\b|indexedDB|console\./);
});
test('no automatic Clipboard read or send; one import uses existing encrypted composer path',()=>{
 const s=readFileSync(new URL('js/share.js',publicDir),'utf8');assert.match(s,/addEventListener\('click'/);assert.match(s,/pasteClipImages\(\)/);assert.doesNotMatch(s,/fetch\(|sendApp\(|localStorage|dbPut/);
 const app=readFileSync(new URL('app.js',publicDir),'utf8');assert.match(app,/await initShare\(!!host\?\.approved && !lost\)/);assert.match(app,/forgetShare\(\)/);
});
