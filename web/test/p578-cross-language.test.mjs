import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {fileAAD,fileNonce,fileGetMessage} from '../public/js/private-transfer.js';
const v=JSON.parse(readFileSync(new URL('./fixtures/p578/host-vector.json',import.meta.url)));
test('#578 actual Python cipher vector decrypts with shipped browser AAD and nonce helpers',async()=>{
 assert.equal(v.synthetic,true);
 const key=await crypto.subtle.importKey('raw',new Uint8Array(v.key_bytes),'AES-GCM',false,['decrypt']);
 const plain=await crypto.subtle.decrypt({name:'AES-GCM',iv:fileNonce(new Uint8Array(v.prefix_bytes),v.index),additionalData:fileAAD({seat:v.channel,id:v.offer,bytes:v.total},v.transfer,v.index)},key,Buffer.from(v.ciphertext,'base64url'));
 assert.equal(createHash('sha256').update(new Uint8Array(plain)).digest('hex'),v.plaintext_sha256);
 const h=v.node_proof.device_headers,m=fileGetMessage(v.path,h['X-AJ-File-Time'],h['X-AJ-File-Nonce'],v.transfer);
 assert.equal(new TextDecoder().decode(m),v.node_proof.canonical);
 const pk=await crypto.subtle.importKey('raw',Buffer.from(h['X-AJ-File-Key'],'base64url'),'Ed25519',false,['verify']);
 assert.ok(await crypto.subtle.verify('Ed25519',pk,Buffer.from(h['X-AJ-File-Signature'],'base64url'),m));
});
test('#578 production relay policy still rejects local WSS while loopback development can use TLS',()=>{
 const source=readFileSync(new URL('../public/app.js',import.meta.url),'utf8'),fn=source.match(/function allowRelay\(url\) \{([\s\S]*?)\n\}/);assert.ok(fn);
 const check=Function('url','location','RELAY_HOSTS',fn[1]),hosts=['relay.agentj.app','alpha-relay.agentjarvis.net'];
 assert.equal(check('wss://127.0.0.1:35555',{hostname:'localhost'},hosts),true);
 assert.equal(check('wss://127.0.0.1:35555',{hostname:'127.0.0.1'},hosts),true);
 assert.equal(check('wss://127.0.0.1:35555',{hostname:'m.agentj.app'},hosts),false);
 assert.equal(check('wss://evil.invalid',{hostname:'m.agentj.app'},hosts),false);
 assert.equal(check('wss://relay.agentj.app',{hostname:'m.agentj.app'},hosts),true);
});
