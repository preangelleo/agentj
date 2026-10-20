// P406 #173 (PROTOCOL §11.2): static checks for the sudo status strip — wire copies agree on the signed Stop, the page routes
// elev_out, every p406.sudo key exists in all four dictionaries, and ESC on the card collapses (never clicks 「拒绝」).
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const read=(p)=>readFileSync(new URL(p,import.meta.url),'utf8');

test('elev_stop / elev_watch: control actions, same object text as controls.py, wire copies byte-identical', async()=>{
  assert.equal(read('../public/proto/wire.js'),read('../../protocol/wire.js'));
  const w=await import('../public/proto/wire.js');
  assert.ok(w.CONTROL_ACTIONS.includes('elev_stop'));
  const id='a'.repeat(32);
  assert.equal(w.controlObject('elev_stop',{id}),id);
  assert.ok(w.CONTROL_ACTIONS.includes('elev_watch'));
  assert.equal(w.controlObject('elev_watch',{id,on:true}),id+'\non');
  assert.equal(w.controlObject('elev_watch',{id,on:false}),id+'\noff');
  assert.match(read('../../host/agentj/controls.py'),/ACTIONS \+= \("elev_watch",\)/);
  const py=read('../../host/agentj/controls.py');
  assert.match(py,/ACTIONS \+= \("elev_stop",\)/);
  assert.match(py,/if action == "elev_stop":[^\n]*\n\s+return str\(obj\["id"\]\)/);
  assert.match(read('../../host/agentj/elevate.py'),/PHONE_TYPES \+= \("elev_stop", "elev_watch"\)/);
});

test('the page routes elev_out and the card ESC is the collapse, not the deny', ()=>{
  assert.match(read('../public/app.js'),/case 'elev_out': elevate\.output\(m\)/);
  const js=read('../public/js/elevate.js');
  assert.match(js,/close: \(\) => collapse\(\) \}\);/);
  assert.doesNotMatch(js,/close: \(\) => document\.getElementById\('elev-deny'\)/);
  // collapse only hides: it never sends and never calls decide(false)
  const body=js.slice(js.indexOf('function collapse()'),js.indexOf('function runBar()'));
  assert.doesNotMatch(body,/sendApp|decide\(|askDeny/);
});

test('dictionaries: every p406.sudo / p406.secret key the code uses exists in en, zh, zh source and override', ()=>{
  const used=[...new Set([...read('../public/js/elevate.js').matchAll(/'(p406\.(?:sudo|secret)\.[A-Za-z]+)'/g)].map((m)=>m[1]))];
  assert.ok(used.length>=15,'keys found: '+used.length);
  for(const f of ['web.en.json','web.zh.json','web.zh.src.json','web.zh.override.json']){
    const d=JSON.parse(read('../i18n/'+f));
    for(const k of used) assert.equal(typeof d[k],'string',f+' '+k);
  }
  const zh=JSON.parse(read('../i18n/web.zh.json')),src=JSON.parse(read('../i18n/web.zh.src.json')),ov=JSON.parse(read('../i18n/web.zh.override.json'));
  for(const k of used) assert.ok(zh[k]===src[k]&&src[k]===ov[k],k);
  for(const f of ['web.en.json','web.zh.json']){
    const d=JSON.parse(read('../i18n/'+f));
    for(const k of used) assert.doesNotMatch(d[k],/试用|trial/i,k);
  }
});
