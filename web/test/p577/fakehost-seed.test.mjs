import test from 'node:test';
import assert from 'node:assert/strict';
import { startFakeHost } from '../fakehost.mjs';
test('P577 fixture seeds only the public channel; default channels stay independent', async () => {
  const hosts = [];
  try {
    for (const options of [{
      channelSeed: new Uint8Array(32).fill(42)
    }, {
      channelSeed: new Uint8Array(32).fill(42)
    }, {}, {}]) hosts.push(await startFakeHost(options));
    assert.equal(hosts[0].channel, hosts[1].channel);
    assert.notEqual(hosts[2].channel, hosts[3].channel);
    assert.notEqual(hosts[0].hostPub, hosts[1].hostPub, 'host Noise keys remain fresh');
  } finally {
    await Promise.all(hosts.map(h => h.stop()));
  }
});
test('P577 public visual seed fixes protocol pairing inputs without changing the default random path', async () => {
  const hosts = [];
  const read = link => JSON.parse(Buffer.from(link.split('#p=')[1], 'base64url'));
  try {
    for (const options of [{visualSeed:'public-test-v1',channelSeed:new Uint8Array(32).fill(42)}, {visualSeed:'public-test-v1',channelSeed:new Uint8Array(32).fill(42)}, {}, {}]) hosts.push(await startFakeHost(options));
    const pairs = hosts.map(h => read(h.newPairing('http://localhost/')));
    assert.equal(pairs[0].k, pairs[1].k);
    assert.equal(pairs[0].i, pairs[1].i);
    assert.equal(pairs[0].p, pairs[1].p);
    assert.notEqual(pairs[2].k, pairs[3].k);
    assert.notEqual(pairs[2].i, pairs[3].i);
    assert.notEqual(pairs[2].p, pairs[3].p);
    assert.notEqual(read(hosts[0].newPairing('http://localhost/')).i, pairs[0].i, 'a seed still produces fresh sequence inputs');
  } finally { await Promise.all(hosts.map(h => h.stop())); }
});
import { createContext, runInContext } from 'node:vm';
import { webcrypto } from 'node:crypto';
import { publicVisualCrypto } from './visual-crypto.mjs';
test('P577 browser-only vectors preserve real crypto and requested private-key extractability', async () => {
  const context = hostname => {
    const memory = new Map();
    const subtle = Object.fromEntries(['generateKey','importKey','exportKey','digest','sign','verify'].map(k => [k, webcrypto.subtle[k].bind(webcrypto.subtle)]));
    const crypto = {subtle, getRandomValues:webcrypto.getRandomValues.bind(webcrypto)};
    const sandbox=createContext({location:{hostname},crypto,sessionStorage:{getItem:k=>memory.get(k),setItem:(k,v)=>memory.set(k,v)},Map,Number,ArrayBuffer,Uint8Array,Float32Array,Float64Array,DataView,DOMException,TextEncoder,atob});
    runInContext(`(${publicVisualCrypto.toString()})()`,sandbox);
    return crypto;
  };
  const a=context('localhost'),b=context('localhost');
  for(const name of ['X25519','Ed25519']){
    const usages=name==='X25519'?['deriveBits']:['sign','verify'];
    const keys=await Promise.all([a,b].map(c=>c.subtle.generateKey({name},false,usages)));
    assert.equal(keys[0].privateKey.extractable,false);
    assert.deepEqual(await a.subtle.exportKey('raw',keys[0].publicKey),await b.subtle.exportKey('raw',keys[1].publicKey));
    if(name==='Ed25519'){
      const input=new TextEncoder().encode('public test input');
      const signature=await a.subtle.sign(name,keys[0].privateKey,input);
      assert.equal(await b.subtle.verify(name,keys[1].publicKey,signature,input),true);
    }
  }
  assert.deepEqual(a.getRandomValues(new Uint8Array(32)),b.getRandomValues(new Uint8Array(32)));
  const remote=context('example.com');
  assert.notDeepEqual(await remote.subtle.exportKey('raw',(await remote.subtle.generateKey('X25519',false,['deriveBits'])).publicKey),await remote.subtle.exportKey('raw',(await remote.subtle.generateKey('X25519',false,['deriveBits'])).publicKey),'outside localhost is untouched');
});
