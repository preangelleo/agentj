import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {generateNodeOwners,loadNodeOwners,checkNodeOwners,parseHtml} from '../tools/generate-node-owners.mjs';
const registry=JSON.parse(await readFile(new URL('../component-ownership.json',import.meta.url),'utf8'));
const html=await readFile(new URL('../public/index.html',import.meta.url),'utf8');
const relay=await readFile(new URL('../public/js/relay.js',import.meta.url),'utf8');
const sources={'public/js/relay.js':relay};
test('ID cache is generated from current audited registry and source, with no unowned static IDs',async()=>{
 const actual=await loadNodeOwners();const cached=JSON.parse(await readFile(new URL('../css-node-owners.json',import.meta.url),'utf8'));
 assert.equal(checkNodeOwners(cached,actual),true);assert.deepEqual(actual.unowned,[]);
 for(const [id,owner]of Object.entries({rmPart:'aj-reply',rmTop:'aj-reply',rmIcons:'aj-reply-action',why:'aj-approval','error-text':'aj-recovery',rdFsN:'aj-reader',fsSend:'aj-editor', 'rm-code':'aj-remotes'}))assert.equal(actual.idOwners[id].owner,owner,id);
 for(const id of ['rdReadBtn','rdSpeakBtn','rdFwdBtn','rdMailBtn','rdCopyBtn','rdReplyBtn'])assert.equal(actual.idOwners[id].owner,'aj-reader',id);
});
test('live connection dialog replaces retired root and finished usage stays reply text, not an action',()=>{
 const actual=generateNodeOwners({html,registry,sources});
 assert.equal(actual.idOwners.connDlg.owner,'aj-recovery');
 assert.equal(actual.idOwners.rmUse.owner,'aj-reply');
 assert.equal(actual.idOwners.offDlg,undefined);
 const wrong=html.replace('id="rmUse" hidden data-aj-owner="aj-reply"','id="rmUse" hidden data-aj-owner="aj-reply-action"');
 assert.notEqual(wrong,html);
 assert.throws(()=>generateNodeOwners({html:wrong,registry,sources}),/Known-but-wrong direct owner rmUse/);
});
test('known but wrong cached semantic owners fail; current source cannot silently revalidate stale IDs',async()=>{
 const actual=await loadNodeOwners();for(const [id,owner]of Object.entries({rmPart:'aj-remotes',rmIcons:'aj-remotes',rdFsN:'aj-editor',fsSend:'aj-composer',why:'aj-channel',rdReadBtn:'aj-reply-action'})){
 const wrong=structuredClone(actual);wrong.idOwners[id].owner=owner;assert.throws(()=>checkNodeOwners(wrong,actual),/cache drift/,id);
 }
});
test('a known but wrong class map or direct marker conflicts with canonical family and is rejected',()=>{
 const wrong=structuredClone(registry);wrong.classOwners.rmpart=['aj-remotes'];assert.throws(()=>generateNodeOwners({html,registry:wrong,sources}),/Exact ID family conflict rmPart/);
 const wrongHtml=html.replace(/(id="fsSend"[^>]*data-aj-owner=")aj-editor/, '$1aj-composer');assert.notEqual(wrongHtml,html);assert.throws(()=>generateNodeOwners({html:wrongHtml,registry,sources}),/Known-but-wrong direct owner fsSend/);
});
test('dynamic clones require explicit reader ownership and business mirror may never overwrite it',()=>{
 const wrong=relay.replace("b.setAttribute('data-aj-owner', 'aj-reader');","b.setAttribute('data-aj-owner', 'aj-reply-action');");assert.notEqual(wrong,relay);
 assert.throws(()=>generateNodeOwners({html,registry,sources:{'public/js/relay.js':wrong}}),/Known-but-wrong dynamic clone owner/);
});
test('unregistered neutral IDs cannot acquire ownership from rm/rd/fs/wh name prefixes',()=>{
 const result=generateNodeOwners({html:html.replace('</body>','<div id="rmPretender"></div><div id="rdPretender"></div><div id="fsPretender"></div><div id="whPretender"></div></body>'),registry,sources});
 assert.deepEqual(result.unowned.map(x=>x.id),['rmPretender','rdPretender','fsPretender','whPretender']);
});
test('HTML lexer preserves quoted >, script text, template elements and explicit ancestor relationships',()=>{
 const nodes=parseHtml('<div id="root" title="a > b"><script>const s="<div id=bad>";</script><template id="icons"><svg id="symbol"></svg></template></div>');
 assert.deepEqual(nodes.filter(n=>n.attrs.id).map(n=>n.attrs.id),['root','icons','symbol']);assert.equal(nodes.at(-1).parent.attrs.id,'icons');assert.equal(nodes[0].attrs.title,'a > b');
});

test('explicit contextual root owners are finite, registered and cannot be ambiguous',()=>{const extended=html.replace('</body>','<div id="rescue-contract-fixture" class="rscmdbar rscmdacts"></div></body>');const actual=generateNodeOwners({html:extended,registry,sources});assert.equal(actual.idOwners['rescue-contract-fixture'].owner,'aj-rescue');const bad=structuredClone(registry);bad.nodeOwnership.scopedClassRoots.find(e=>e.selector==='.rscmdbar.rscmdacts').owner='aj-unregistered';assert.throws(()=>generateNodeOwners({html:extended,registry:bad,sources}),/Unknown explicit root owner/);const duplicate=structuredClone(registry);duplicate.nodeOwnership.scopedClassRoots.push({selector:'.rscmdbar.rscmdacts',owner:'aj-reader'});assert.throws(()=>generateNodeOwners({html:extended,registry:duplicate,sources}),/Ambiguous explicit class roots/);});
