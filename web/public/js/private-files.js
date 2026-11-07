// Paired Noise controls; v2 files use authenticated ciphertext HTTPS, never plaintext HTTP.
import { ask1, peer, channel, gen, hostId, whenReady } from './session.js';
import { signed, estop } from './controls.js';
import { unb64u, sha256hex } from '../proto/wire.js';
import { t, onLang } from './t.js';
import { registerLayer } from './layers.js';
import { dbGet, signKey } from './store.js';
import { checkOffer, receiveFile, FILE_BUFFER_MAX } from './private-transfer.js';

const fileError = code => Object.assign(new Error(t('p0280.file.' + code)), { code });
export const downloadError = error => error?.code==='budget'?t('p578.file.budget',{mb:FILE_BUFFER_MAX/1048576}):error?.code==='claimed'?t('p578.file.claimed'):t('p0280.file.' + (['unsupported','unavailable','changed','invalid','expired'].includes(error?.code) ? error.code : 'failed'));
const transfers=new Map();let active=null;
export function forgetDownloads(){for(const x of transfers.values())x.controller?.abort();transfers.clear();active=null;if(locator){wipeReady();locator.root.hidden=true;locator.seq++;}}

async function directDownload(card,name,onProgress){
  checkOffer(card);if(!peer.privatefiles2||card.seat!==channel()||!gen())throw fileError('unavailable');
  if(locator?.ready&&locator.card?.id===card.id&&locator.ready.host===hostId()){locator.root.hidden=false;return {ready:true};}
  if(active||estop.on)throw fileError('unavailable');
  const id=hostId(),generation=gen(),record=await dbGet('host'),keypair=await signKey();
  if(!record?.approved||record.channel!==card.seat||!keypair||generation!==gen()||hostId()!==id)throw fileError('changed');
  if(active||estop.on)throw fileError('unavailable');
  const old=transfers.get(card.id);
  if(old&&(old.host!==id||old.sha256!==card.sha256||old.bytes!==card.bytes))throw fileError('changed');
  if(old?.completed)throw fileError('claimed');
  for(const [key,value]of transfers)if(value.expires<=Date.now()/1000)transfers.delete(key);
  if(!old&&transfers.size>=64)throw fileError('unavailable');
  const state=old||{host:id,sha256:card.sha256,bytes:card.bytes,expires:card.expires,transfer:[...crypto.getRandomValues(new Uint8Array(16))].map(x=>x.toString(16).padStart(2,'0')).join('')};
  const controller=new AbortController();state.controller=controller;transfers.set(card.id,state);active=state;
  const timer=setTimeout(()=>controller.abort(),10*60*1000);
  const live=()=>generation===gen()&&hostId()===id&&channel()===card.seat&&!estop.on&&!controller.signal.aborted;
  let bytes;
  try{
    const request={id:card.id,seat:card.seat,claim:true,transfer:state.transfer,...(state.failed?{retry:true}:{})};let result;
    for(;;){
      if(!live())throw fileError('changed');
      result=await ask1(await signed('private_file_get',{request},{t:'private_file_get',request}),'private_file_res',30000);
      if(!live())throw fileError('changed');if(!result.ok)throw fileError(result.retryable?'failed':result.why==='claimed'?'claimed':result.why==='changed'?'changed':'expired');
      if(!result.pending)break;onProgress(Math.min(card.bytes,Number(result.uploaded)||0)/2,card.bytes);await new Promise(r=>setTimeout(r,500));
    }
    bytes=await receiveFile(card,result,{transfer:state.transfer,keypair,relay:record.relay,live,onProgress:(n,total)=>onProgress(total/2+n/2,total),signal:controller.signal});
    if(!live())throw fileError('changed');
    // A downloaded HTML/SVG file must never execute with this page's paired origin.
    const pdf=card.mime==='application/pdf'&&new TextDecoder().decode(bytes.subarray(0,5))==='%PDF-';
    const blob=new Blob([bytes],{type:pdf?'application/pdf':'application/octet-stream'});bytes.fill(0);bytes=null;
    state.completed=true;showReady(card,name,blob);
    // Cleanup is best effort after end-to-end verification; it cannot withhold this owner's verified copy.
    const complete={...request,complete:true};
    if(live())void signed('private_file_get',{request:complete},{t:'private_file_get',request:complete})
      .then(message=>ask1(message,'private_file_res',5000)).catch(()=>{});
    return {ready:true};
  }catch(error){state.failed=true;controller.abort();throw error;}finally{clearTimeout(timer);if(bytes)bytes.fill(0);if(active===state)active=null;}
}

export async function downloadFile(card, name, onProgress = () => {}) {
  if(card.transport==='https1')return directDownload(card,name,onProgress);
  if ((!peer.privatefiles1&&!peer.privatefiles2) || card.seat !== channel()) throw fileError('unavailable');
  if (typeof globalThis.showSaveFilePicker !== 'function') throw fileError('unsupported');
  const generation = gen();
  const handle = await showSaveFilePicker({ suggestedName: name });
  const sink = await handle.createWritable();
  const transfer = [...crypto.getRandomValues(new Uint8Array(16))].map(x => x.toString(16).padStart(2, '0')).join('');
  try {
    let offset = 0;
    while (offset < card.bytes) {
      if (!generation || generation !== gen() || card.seat !== channel()) throw fileError('changed');
      const request = { id: card.id, seat: card.seat, transfer, offset };
      const response = await ask1(await signed('private_file_get', { request }, { t: 'private_file_get', request }), 'private_file_res', 20000);
      if (generation !== gen() || !response.ok || response.offset !== offset) throw fileError('expired');
      const bytes = unb64u(response.d);
      if (bytes.length !== Math.min(45056, card.bytes - offset) || response.last !== (offset + bytes.length === card.bytes)
          || await sha256hex(bytes) !== response.sha256) throw fileError('invalid');
      await sink.write(bytes);
      offset += bytes.length;
      onProgress(offset, card.bytes);
    }
    await sink.close();
  } catch (error) {
    await sink.abort();
    throw error;
  }
}

export async function revokeFile(card) {
  if ((!peer.privatefiles1&&!peer.privatefiles2) || card.seat !== channel()) throw fileError('unavailable');
  const request = { id: card.id, seat: card.seat };
  const response = await ask1(await signed('private_file_revoke', { request }, { t: 'private_file_revoke', request }), 'private_file_res');
  if (!response.ok) throw fileError('expired');
  const state=transfers.get(card.id);state?.controller?.abort();transfers.delete(card.id);
  if(locator?.card?.id===card.id){wipeReady();locator.root.hidden=true;}
}

// A fixed seat page plus opaque fragment is a private locator, never a public endpoint.
let locator;
function wipeReady(){if(locator?.ready){clearTimeout(locator.ready.timer);URL.revokeObjectURL(locator.ready.url);locator.ready=null;}if(locator?.open){locator.open.hidden=true;locator.open.removeAttribute('href');}}
function showReady(card,name,blob){
  const v=locatorView();wipeReady();v.seq++;v.card=card;v.name=name;v.error='';v.busy=false;
  v.ready={blob,url:URL.createObjectURL(blob),host:hostId(),timer:setTimeout(()=>{wipeReady();paintLocator();},120000)};
  v.root.hidden=false;paintLocator();v.save.focus({preventScroll:true});
}
function saveReady(){
  const v=locator,r=v?.ready;if(!r||!gen()||estop.on||v.card.seat!==channel()||r.host!==hostId()){wipeReady();throw fileError('changed');}
  const file=new File([r.blob],v.name,{type:r.blob.type||'application/octet-stream'});
  const ios=/iPhone|iPad|iPod/.test(navigator.userAgent)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
  const standalone=navigator.standalone===true||globalThis.matchMedia?.('(display-mode: standalone)').matches;
  if(ios&&standalone&&navigator.canShare?.({files:[file]})&&navigator.share){
    return navigator.share({files:[file],title:v.name}); // synchronous native call within this new explicit tap
  }
  const a=document.createElement('a');a.href=r.url;a.download=v.name;a.rel='noopener';document.body.append(a);a.click();a.remove();
}
function locatorView() {
  if (locator) return locator;
  const root = document.createElement('div'); root.id = 'private-file-locator'; root.className = 'gdlg'; root.setAttribute('data-aj-owner','aj-dialog'); root.hidden = true;
  root.setAttribute('role','dialog'); root.setAttribute('aria-modal','true'); root.setAttribute('aria-labelledby','private-file-title');
  const square = document.createElement('div'); square.className = 'sq'; square.setAttribute('data-aj-owner','aj-dialog');
  const title = document.createElement('h2'); title.id = 'private-file-title'; title.setAttribute('data-aj-owner','aj-dialog');
  const text = document.createElement('p'); text.setAttribute('role','status'); text.setAttribute('data-aj-owner','aj-dialog');
  const actions = document.createElement('div'); actions.className = 'gdlg-row';actions.setAttribute('data-aj-owner','aj-dialog');
  const close = document.createElement('button'); close.type = 'button';close.className='aj-dialog-action';close.setAttribute('data-aj-owner','aj-dialog-action');
  const save = document.createElement('button'); save.type = 'button'; save.className = 'aj-dialog-action aj-dialog-action--primary';save.setAttribute('data-aj-owner','aj-dialog-action'); save.hidden = true;
  const open=document.createElement('a');open.className='aj-dialog-action';open.setAttribute('data-aj-owner','aj-dialog-action');open.target='_blank';open.rel='noopener noreferrer';open.hidden=true;
  const hide = () => { root.hidden = true; locator.seq++;wipeReady();active?.controller?.abort(); };
  close.onclick = hide;
  root.addEventListener('click', e => { if (e.target === root) hide(); });
  const openRow=document.createElement('div');openRow.className='private-file-open';openRow.append(open);
  actions.append(close,save); square.append(title,text,openRow,actions); root.append(square); document.body.append(root);
  locator = {root,title,text,close,save,open,seq:0,card:null,name:'',error:'',busy:false,ready:null};
  registerLayer({element:()=>root,open:()=>!root.hidden,close:hide,rank:50,fallback:()=>document.getElementById('input')});
  save.onclick = async () => {
    if (!locator.card || locator.busy) return;
    if(locator.ready){try{await saveReady();}catch(error){locator.error=downloadError(error);paintLocator();}return;}
    const seq=locator.seq; locator.busy=true; paintLocator();
    try { const result=await downloadFile(locator.card,locator.name,n=>{if(locator.seq===seq){locator.progress=Math.floor(n/locator.card.bytes*100);paintLocator();}}); if(locator.seq===seq&&!result?.ready)hide(); }
    catch (error) { if(locator.seq===seq)locator.error=downloadError(error); }
    finally { if(locator.seq===seq){locator.busy=false;paintLocator();} }
  };
  return locator;
}
function paintLocator() {
  if(!locator)return;
  const v=locator;
  v.title.textContent=t('p0280.file.save'); v.close.textContent=t('p0280.file.close');
  v.text.textContent=(v.name?v.name+' · ':'')+t(v.ready?'p578.file.ready':'p0280.file.locator')+(v.error?' '+v.error:'');
  const supported=v.card?.transport==='https1'&&peer.privatefiles2||typeof globalThis.showSaveFilePicker==='function';
  if(v.card&&!supported)v.text.textContent+=' '+t('p0280.file.unsupported');
  v.save.textContent=v.ready?t('p578.file.saveReady'):v.busy?t('p578.file.preparing',{n:v.progress||0}):t('p0280.file.save'); v.save.disabled=v.busy; v.save.hidden=!v.card||!supported;
  v.open.hidden=!v.ready||v.ready.blob.type!=='application/pdf';if(v.ready){v.open.textContent=t('p578.file.open');v.open.href=v.ready.url;}
}
onLang(paintLocator);
async function openLocator() {
  const match = /^#file=([0-9a-f]{32})$/.exec(location.hash);
  if (!match || (locator && !locator.root.hidden)) return;
  const v=locatorView(),seq=++v.seq;
  Object.assign(v,{card:null,name:'',error:'',busy:false});v.root.hidden=false;paintLocator();v.close.focus({preventScroll:true});
  if (!await whenReady() || (!peer.privatefiles1&&!peer.privatefiles2) || v.seq!==seq) return;
  const request = { id: match[1], seat: channel(), info: true };
  let response;
  try { response = await ask1(await signed('private_file_get', { request }, { t: 'private_file_get', request }), 'private_file_res'); }
  catch { if(v.seq===seq){v.error=t('p0280.file.unavailable');paintLocator();}return; }
  if(v.seq!==seq)return;
  if(!response.ok){v.error=t('p0280.file.expired');paintLocator();return;}
  v.card=response.card;v.name=response.name;paintLocator();
}
if (globalThis.document && globalThis.location) {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', openLocator, { once: true });
  else void openLocator();
  globalThis.addEventListener('hashchange', openLocator);
  globalThis.addEventListener('pagehide',forgetDownloads);
}
