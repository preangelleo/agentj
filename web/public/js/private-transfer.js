// #578 ciphertext-only HTTPS data plane. Keys and metadata arrive only through paired Noise.
import { b64u, unb64u, sha256hex } from '../proto/wire.js';
export const FILE_CHUNK = 65536;
export const FILE_BUFFER_MAX = 64 * 1024 * 1024; // one buffered transfer; worst case ~3x with WebCrypto/Blob copies
export const fileRetryDelay = value => 1000*Math.max(1,Math.min(60,/^\d+$/.test(value||'')?Number(value):1));
const fail = code => Object.assign(new Error(code), {code});
const hex32 = value => typeof value==='string'&&/^[0-9a-f]{32}$/.test(value);
export function checkOffer(card, now=Math.floor(Date.now()/1000)) {
  if(!card||card.transport!=='https1'||!hex32(card.id)||!/^[A-Za-z0-9_-]{22}$/.test(card.seat)
      ||!Number.isSafeInteger(card.bytes)||card.bytes<1||card.chunk_size!==FILE_CHUNK||!/^[0-9a-f]{64}$/.test(card.sha256))throw fail('invalid');
  if(card.bytes>FILE_BUFFER_MAX)throw fail('budget');
  if(!Number.isInteger(card.expires)||card.expires<=now||card.expires>now+86400)throw fail('expired');
}
export function fileAAD(card,transfer,index){return new TextEncoder().encode(`agentj-private-file-v1\n${card.seat}\n${card.id}\n${transfer}\n${index}\n${card.bytes}`);}
export function fileNonce(prefix,index){const out=new Uint8Array(12);if(prefix.length!==8||!Number.isSafeInteger(index)||index<0||index>0xffffffff)throw fail('invalid');out.set(prefix);new DataView(out.buffer).setUint32(8,index,false);return out;}
export function fileGetMessage(path,time,nonce,transfer){return new TextEncoder().encode(`agentj-file-get-v1\nGET\n${path}\n${time}\n${nonce}\n${transfer}`);}
export function checkGrant(card,result,transfer,pub,relay,now=Math.floor(Date.now()/1000)) {
  checkOffer(card,now);
  const grant=result?.grant;
  if(result?.direct!==true||!hex32(transfer)||grant?.v!==1||grant.offer!==card.id||grant.transfer!==transfer
      ||grant.device_pk!==b64u(pub)||!Number.isInteger(grant.expires)||grant.expires>card.expires||grant.expires<=now
      ||result.total!==card.bytes||result.chunk_size!==FILE_CHUNK||result.chunks!==Math.ceil(card.bytes/FILE_CHUNK)
      ||(result.sha256??result.card?.sha256)!==card.sha256||!(/^[A-Za-z0-9_-]{43}$/.test(result.key))||!(/^[A-Za-z0-9_-]{11}$/.test(result.nonce_prefix)))throw fail('invalid');
  let endpoint,base;
  try{endpoint=new URL(result.endpoint);base=new URL(relay);}catch{throw fail('invalid');}
  if(base.protocol==='wss:')base.protocol='https:';
  else if(base.protocol==='ws:'&&['localhost','127.0.0.1','[::1]'].includes(base.hostname))base.protocol='http:';
  else throw fail('invalid');
  if(endpoint.origin!==base.origin||endpoint.pathname!==`/v1/file/${card.seat}/${card.id}`||endpoint.search||endpoint.hash||endpoint.username||endpoint.password)throw fail('invalid');
  return {endpoint:endpoint.href,expires:grant.expires,key:unb64u(result.key),prefix:unb64u(result.nonce_prefix)};
}
async function boundedBody(response,expected){
  const length=response.headers.get('content-length');if(length!==null&&(!/^\d+$/.test(length)||Number(length)!==expected))throw fail('invalid');
  const reader=response.body?.getReader();if(!reader)throw fail('invalid');
  const out=new Uint8Array(expected);let at=0;
  try{for(;;){const {value,done}=await reader.read();if(done)break;if(at+value.byteLength>expected)throw fail('invalid');out.set(value,at);at+=value.byteLength;}
    if(at!==expected)throw fail('failed');return out;
  }finally{try{await reader.cancel();}catch{}}
}
/** No request body, bearer URL, plaintext name or key crosses this HTTP interface. */
export async function receiveFile(card,result,{transfer,keypair,relay,live=()=>true,onProgress=()=>{},fetcher=globalThis.fetch,signal}={}){
  const grant=checkGrant(card,result,transfer,keypair.pub,relay),secret=await crypto.subtle.importKey('raw',grant.key,{name:'AES-GCM'},false,['decrypt']);
  grant.key.fill(0);
  const all=new Uint8Array(card.bytes),chunks=Math.ceil(card.bytes/FILE_CHUNK);let next=0,got=0,failed=false;
  const deadline=Math.min(Date.now()+5*60*1000,grant.expires*1000);
  const current=()=>{if(failed||signal?.aborted||!live())throw fail('changed');if(Math.floor(Date.now()/1000)>=grant.expires)throw fail('expired');if(Date.now()>=deadline)throw fail('failed');};
  const pause=async ms=>{const until=Math.min(Date.now()+ms,deadline);while(Date.now()<until){current();await new Promise(r=>setTimeout(r,Math.min(250,until-Date.now())));}current();};
  async function part(index){
    const size=Math.min(FILE_CHUNK,card.bytes-index*FILE_CHUNK),url=new URL(grant.endpoint+'/chunk/'+index);let encrypted;
    for(let attempt=0;;){
      current();const time=String(Math.floor(Date.now()/1000)),nonce=b64u(crypto.getRandomValues(new Uint8Array(16)));
      const sig=b64u(new Uint8Array(await crypto.subtle.sign('Ed25519',keypair.priv,fileGetMessage(url.pathname,time,nonce,transfer))));
      try{
        const response=await fetcher(url.href,{method:'GET',credentials:'omit',cache:'no-store',redirect:'error',mode:'cors',signal,
          headers:{'X-AJ-File-Key':b64u(keypair.pub),'X-AJ-File-Signature':sig,'X-AJ-File-Time':time,'X-AJ-File-Nonce':nonce,'X-AJ-File-Transfer':transfer}});
        if(response.status===429){await pause(fileRetryDelay(response.headers.get('retry-after')));continue;}
        if(!response.ok){if([403,404,409].includes(response.status))throw fail('expired');throw fail('failed');}
        encrypted=await boundedBody(response,size+16);break;
      }catch(error){if(error.code&&error.code!=='failed'||attempt++>=2||signal?.aborted)throw error;await pause(150*attempt);}
    }
    current();let plain;
    try{plain=new Uint8Array(await crypto.subtle.decrypt({name:'AES-GCM',iv:fileNonce(grant.prefix,index),additionalData:fileAAD(card,transfer,index),tagLength:128},secret,encrypted));}catch{throw fail('invalid');}
    if(plain.length!==size)throw fail('invalid');current();all.set(plain,index*FILE_CHUNK);plain.fill(0);got+=size;onProgress(got,card.bytes);
  }
  try{
    await Promise.all(Array.from({length:Math.min(4,chunks)},async()=>{while(!failed){const index=next++;if(index>=chunks)return;try{await part(index);}catch(error){failed=true;throw error;}}}));
    current();if(await sha256hex(all)!==card.sha256)throw fail('invalid');current();return all;
  }catch(error){failed=true;all.fill(0);throw error;}
}
