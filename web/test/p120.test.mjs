import test from 'node:test';
import assert from 'node:assert/strict';
globalThis.window={};
const {compareVersion,newerVersion,readWebVersion,versionState}=await import('../public/js/version-notice.js');
test('version ordering rejects malformed metadata and handles alpha, beta, rc and final numerically',()=>{
 assert.equal(compareVersion('0.17.3a1','0.17.2a9'),1);
 assert.equal(compareVersion('0.17.3a10','0.17.3a2'),1);
 assert.equal(compareVersion('0.17.3','0.17.3rc9'),1);
 assert.equal(compareVersion('0.17.3b1','0.17.3a9'),1);
 assert.equal(compareVersion('<img>','0.17.3a1'),null);
 assert.equal(newerVersion('0.17.2a1','0.17.3a1','0.17.4a1'),'0.17.4a1');
 assert.equal(newerVersion('0.17.3a1','0.17.2a1','nonsense'),null);
});
test('metadata check carries no credentials or customer data; rejects HTTP errors and malformed manifests',async()=>{
 const oldFetch=globalThis.fetch,oldLoc=globalThis.location;globalThis.location={origin:'https://m.agentj.app',hostname:'m.agentj.app'};
 try{
  globalThis.fetch=async(url,opts)=>{assert.equal(url.href,globalThis.location.origin+'/version.json');assert.equal(opts.method,'GET');assert.equal(opts.credentials,'omit');assert.equal(opts.redirect,'error');assert.equal(opts.cache,'no-store');assert.equal(opts.body,undefined);return new Response(JSON.stringify({v:1,version:'0.17.3a1'}));};
  assert.equal(await readWebVersion(),'0.17.3a1');
  globalThis.location={origin:'https://alpha-web.agentjarvis.net',hostname:'alpha-web.agentjarvis.net'};
  assert.equal(await readWebVersion(),'0.17.3a1');
  globalThis.fetch=async()=>new Response('{}',{status:503});assert.equal(await readWebVersion(),null);
  globalThis.fetch=async()=>new Response(JSON.stringify({v:1,version:'invalid'}));assert.equal(await readWebVersion(),null);
 }finally{globalThis.fetch=oldFetch;globalThis.location=oldLoc;}
});
test('P240: the dialog offers only what a refresh can fetch; a host ahead of every page gets the quiet line; a tried offer is not repeated',()=>{
 // Leo 10-13 23:13: host updated by hand to 0.20.2a1 while m.agentj.app still served 0.20.1a1 → 刷新 three times, same dialog.
 assert.deepEqual(versionState('0.20.1a1','0.20.2a1','0.20.1a1',null),{offer:null,later:true});
 assert.deepEqual(versionState('0.20.1a1','0.20.2a1',null,null),{offer:null,later:true});          // version.json unreadable: no dialog
 assert.deepEqual(versionState('0.20.1a1','0.20.2a1','0.20.2a1',null),{offer:'0.20.2a1',later:false}); // batch B out: refresh helps
 assert.deepEqual(versionState('0.20.1a1','0.20.1a1','0.20.2a1',null),{offer:'0.20.2a1',later:false}); // web first (host upgrades at night)
 assert.deepEqual(versionState('0.20.1a1','0.20.3a1','0.20.2a1',null),{offer:'0.20.2a1',later:true});   // refresh helps, host still ahead
 assert.deepEqual(versionState('0.20.1a1',null,null,null),{offer:null,later:false});
 // refreshed for 0.20.2a1 and the page is still 0.20.1a1 (edge cache): no dialog again, the line instead when the host is ahead
 assert.deepEqual(versionState('0.20.1a1','0.20.2a1','0.20.2a1','0.20.2a1'),{offer:null,later:true});
 assert.deepEqual(versionState('0.20.1a1','0.20.1a1','0.20.2a1','0.20.2a1'),{offer:null,later:false});
 // a still newer page after that is offered again
 assert.deepEqual(versionState('0.20.1a1','0.20.3a1','0.20.3a1','0.20.2a1'),{offer:'0.20.3a1',later:false});
});
