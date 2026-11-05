import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { ASR_KEYS, DEFAULT_HOTWORDS, validAsr, bindAsr } from '../public/js/asr-settings.js';
const custom = {mode:'custom',base_url:'http://asr.local/v1',model:'qwen-asr',key_env:'',hotwords:DEFAULT_HOTWORDS};
test('#440: custom ASR permits unauthenticated LAN and named secrets, rejects URL secrets', () => {
  assert.equal(validAsr(custom),true);
  assert.equal(validAsr({...custom,key_env:'ASR_API_KEY'}),true);
  for (const base_url of ['http://u:p@asr.local/v1','http://asr.local/v1?key=secret','file:///etc/passwd','https://asr.local/#key']) assert.equal(validAsr({...custom,base_url}),false);
  assert.equal(validAsr({...custom,model:''}),false);
  assert.equal(validAsr({...custom,key_env:'not a key name'}),false);
  assert.equal(ASR_KEYS.includes('voice.asr.key'),false);
});
test('#440: hotwords bounded; built-in local and provider routes remain available', () => {
  assert.equal(validAsr({...custom,hotwords:Array(101).fill('word')}),false);
  assert.equal(validAsr({...custom,mode:'local',model:'',base_url:''}),true);
  assert.equal(validAsr({...custom,mode:'cloud',base_url:''}),true);
  assert.equal(validAsr({...custom,mode:'cloud',model:''}),false);
});
test('#440: fixed notice survives blob and API boundaries in both success and failure', () => {
  for (const file of ['blobs','api']) {
    const text=readFileSync(new URL(`../public/js/${file}.js`,import.meta.url),'utf8');
    assert.ok((text.match(/notice: [mr]\.notice/g)||[]).length>=2,file);
  }
  const text=readFileSync(new URL('../public/js/relay.js',import.meta.url),'utf8');
  assert.match(text,/\['local_fallback', 'remote_unavailable'\]\.includes\(r.notice\)/);
  assert.match(text,/pttMode.*asr440.sendAudio.*asr440.sendText/);
});

test('#440: keyed route changes require the Agent; key card never binds a host secret', async () => {
  const nodes = new Map(), writes = [], cards = [], notices = [];
  for (const [id,value] of Object.entries({mode:'custom',url:'http://asr.local/v1',model:'qwen-asr','key-env':'ASR_API_KEY',hotwords:'Agent J',save:'',key:''})) nodes.set('set-asr-'+id,{value,disabled:false,handlers:{},addEventListener(type,fn){this.handlers[type]=fn;}});
  nodes.set('set-asr',{dataset:{}});
  const previous=globalThis.document;
  globalThis.document={getElementById:id=>nodes.get(id),querySelectorAll:()=>[]};
  try {
    bindAsr({set:async(k,v)=>{writes.push([k,v]);return true},failed:()=>assert.fail('unexpected save failure'),keysSet:async name=>{cards.push(name);return {ok:true}},t:(key,vars)=>({key,vars}),notify:n=>notices.push(n)});
    await nodes.get('set-asr-save').handlers.click();
    assert.deepEqual(writes,[]);assert.equal(notices.at(-1).text.key,'asr440.agentSetup');
    assert.equal(notices.at(-1).text.vars.name,'ASR_API_KEY');
    await nodes.get('set-asr-key').handlers.click();assert.deepEqual(cards,['ASR_API_KEY']);assert.deepEqual(writes,[]);
    nodes.get('set-asr-key-env').value='';
    await nodes.get('set-asr-save').handlers.click();
    assert.deepEqual(writes.slice(0,2),[['voice.asr.mode','local'],['voice.asr.key_env','']]);
    assert.deepEqual(writes.at(-1),['voice.asr.mode','custom']);
    writes.length=0;nodes.get('set-asr-mode').value='cloud';
    await nodes.get('set-asr-save').handlers.click();assert.deepEqual(writes,[]);
  } finally {globalThis.document=previous;}
});
