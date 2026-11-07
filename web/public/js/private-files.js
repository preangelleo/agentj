// Paired-only Noise download, constant memory. Never use fetch or an HTTP file URL.
import { ask1, peer, channel, gen, whenReady } from './session.js';
import { signed } from './controls.js';
import { unb64u, sha256hex } from '../proto/wire.js';
import { t, onLang } from './t.js';
import { registerLayer } from './layers.js';

const fileError = code => Object.assign(new Error(t('p0280.file.' + code)), { code });
export const downloadError = error => t('p0280.file.' + (['unsupported','unavailable','changed','invalid','expired'].includes(error?.code) ? error.code : 'failed'));

export async function downloadFile(card, name, onProgress = () => {}) {
  if (!peer.privatefiles1 || card.seat !== channel()) throw fileError('unavailable');
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
  if (!peer.privatefiles1 || card.seat !== channel()) throw fileError('unavailable');
  const request = { id: card.id, seat: card.seat };
  const response = await ask1(await signed('private_file_revoke', { request }, { t: 'private_file_revoke', request }), 'private_file_res');
  if (!response.ok) throw fileError('expired');
}

// A fixed seat page plus opaque fragment is a private locator, never a public endpoint.
let locator;
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
  const hide = () => { root.hidden = true; locator.seq++; };
  close.onclick = hide;
  root.addEventListener('click', e => { if (e.target === root) hide(); });
  actions.append(close,save); square.append(title,text,actions); root.append(square); document.body.append(root);
  locator = {root,title,text,close,save,seq:0,card:null,name:'',error:'',busy:false};
  registerLayer({element:()=>root,open:()=>!root.hidden,close:hide,rank:50,fallback:()=>document.getElementById('input')});
  save.onclick = async () => {
    if (!locator.card || locator.busy) return;
    const seq=locator.seq; locator.busy=true; paintLocator();
    try { await downloadFile(locator.card,locator.name); if(locator.seq===seq)hide(); }
    catch (error) { if(locator.seq===seq)locator.error=downloadError(error); }
    finally { if(locator.seq===seq){locator.busy=false;paintLocator();} }
  };
  return locator;
}
function paintLocator() {
  if(!locator)return;
  const v=locator;
  v.title.textContent=t('p0280.file.save'); v.close.textContent=t('p0280.file.close');
  v.text.textContent=(v.name?v.name+' · ':'')+t('p0280.file.locator')+(v.error?' '+v.error:'');
  const supported=typeof globalThis.showSaveFilePicker==='function';
  if(v.card&&!supported)v.text.textContent+=' '+t('p0280.file.unsupported');
  v.save.textContent=t(v.busy?'p0280.file.saving':'p0280.file.save'); v.save.disabled=v.busy; v.save.hidden=!v.card||!supported;
}
onLang(paintLocator);
async function openLocator() {
  const match = /^#file=([0-9a-f]{32})$/.exec(location.hash);
  if (!match || (locator && !locator.root.hidden)) return;
  const v=locatorView(),seq=++v.seq;
  Object.assign(v,{card:null,name:'',error:'',busy:false});v.root.hidden=false;paintLocator();v.close.focus({preventScroll:true});
  if (!await whenReady() || !peer.privatefiles1 || v.seq!==seq) return;
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
}
