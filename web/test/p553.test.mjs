import test from 'node:test';
import assert from 'node:assert/strict';
globalThis.window = {};
globalThis.document = { documentElement: { lang: 'zh', dataset: {} }, getElementById: () => null };
globalThis.addEventListener = () => {}; // ui.js is imported transitively by busy.js.
const {sanitizeRes} = await import('../public/js/busy.js');
test('#553 disk scope and capacity validate additive host metadata',()=>{
 const current=sanitizeRes({disk:{free:.8,free_gb:1600,total_gb:2000,scope:'workdir',path:'/private/untrusted'}}).disk;
 assert.equal(current.scope,'workdir');assert.equal(current.total_gb,2000);assert.equal('path' in current,false);
 for(const total_gb of [null,undefined,NaN,Infinity,-1,0,'2000',{}]) assert.equal(sanitizeRes({disk:{total_gb,scope:'bad'}}).disk.total_gb,null);
 assert.equal(sanitizeRes({disk:{free:.7,free_gb:79}}).disk.scope,'home');
 assert.equal(sanitizeRes({disk:{total_gb:2e9}}).disk.total_gb,1e6);
 assert.equal(sanitizeRes({}).disk,null);
});
