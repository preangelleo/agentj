import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const source = readFileSync(new URL('../public/js/termhook.js', import.meta.url), 'utf8');
const nodes = new Map();
const node = id => { if (!nodes.has(id)) nodes.set(id, { hidden:false, textContent:'', addEventListener(type, fn) { this[type] = fn; } }); return nodes.get(id); };
const opens = [], calls = [];
const ctx = vm.createContext({ URL, navigator:{}, el:node, t:k=>k, onLang(){}, myIdSync:()=>null,
  signedWrite(){ calls.push('signed'); }, window:{open:(...a)=>opens.push(a)} });
vm.runInContext(source.replace(/^import .*;.*$/gm,'').replace(/export /g,'').replace(/import\.meta\.url/g, '"https://local.test/js/termhook.js"') + '\nthis.api={render,state,usable,enable,open,bang,msg,configure,externalUrl};',ctx);
const api=ctx.api, origin='https://terminal.owner.private.example';
const block = x => ({terminal:{on:false,os:false,kind:'desktop',pk:false,...x}});
test('Olares URL is canonical HTTPS DNS origin only; no arbitrary navigation forms',()=>{
  assert.equal(api.externalUrl(origin),origin);
  for(const u of ['',null,42,'http://terminal.example','https://localhost','https://127.0.0.1','https://terminal.example/','https://terminal.example:443','https://terminal.example?cmd=id','https://terminal.example#x','https://owner@terminal.example','https://terminal.example/exec','https://TERMINAL.example','https://terminal.example\n','https://-bad.example','https://a..example']) assert.equal(api.externalUrl(u),null,String(u));
});
test('external entry is explicit navigation, never the native terminal or command shortcut',async()=>{
  api.render(block({external_url:origin}));
  assert.equal(api.state(),'external'); assert.equal(api.usable(),false);
  assert.equal(node('set-term-external').hidden,false);
  assert.equal(node('set-term-state').textContent,'olaresTerm.state');
  api.configure({});node('set-term-external-open').click();
  assert.deepEqual(opens,[[origin,'_blank','noopener,noreferrer']]);
  for(const id of ['set-term-en','set-term-acts','set-term-pk','set-term-cmdline','set-term-or']) assert.equal(node(id).hidden,true,id);
  assert.equal(await api.open('id'),false); assert.equal(await api.enable(),false); assert.equal(await api.bang('id','x'),false);
  api.msg({t:'term_go',cmd:'id'}); assert.deepEqual(calls,[]);
  api.render(block({external_url:'https://evil.example/path'}));assert.equal(api.state(),'os');assert.equal(node('set-term-external').hidden,true);
  api.render({});assert.equal(api.state(),'none');
  node('set-term-external-open').click();assert.equal(opens.length,1);
  api.render({terminal:{on:true,os:true,me:true,pk:true,kind:'desktop'}});assert.equal(api.state(),'ok');assert.equal(api.usable(),true);
});
