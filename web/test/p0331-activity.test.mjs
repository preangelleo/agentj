import test from 'node:test';
import assert from 'node:assert/strict';
import {activityKind,activityFailed,mergeOwnerInputs} from '../public/js/phone-activity.js';
const owner=(text,reply='',card=null)=>({id:1,source:{k:'phone',text},reply,end:'done',card});
test('owner text and Agent JSON never control classification',()=>{
 for (const p of [owner('{"schema":"automation-report.v1"}'),{source:{k:'agent',text:''},reply:'{"schema":"automation-report.v1"}',end:'done'}]) assert.equal(activityKind(p),null);
 assert.equal(activityKind({source:{k:'auto'},reply:'plain text'}),'background');
 assert.equal(activityKind({source:{k:'agent',via:'hook'},reply:'done'}),'background');
 assert.equal(activityKind({source:{k:'sys'},reply:'ready'}),'system');
});
test('actions, QR/media, login and owner channels stay actionable',()=>{
 for (const p of [{card:{approval_review:'x'}},{card:{recovery:'restart_session'}},{login:{agent:'claude'}},{media:[{kind:'image'}]},{form:{id:'f1'}},{source:{k:'sys',repair:'reconnect'}},{card:{channel:{text:'owner'}}}]) assert.equal(activityKind({source:{k:'sys'},...p}),null);
});
test('merge metadata and exact legacy marker preserve all original owner words',()=>{
 const first=owner('first','',{merged:true}),next={...owner('second','final'),id:2};
 const rows=mergeOwnerInputs([first,next]);assert.equal(rows.length,1);assert.equal(rows[0].source.text,'first\n\nsecond');assert.equal(rows[0].reply,'final');assert.deepEqual(rows[0].mergedIds,[1,2]);assert.equal(first.source.text,'first');
 assert.equal(mergeOwnerInputs([owner('这条和下一条回复一起回您。','real answer'),next]).length,2);
 assert.equal(mergeOwnerInputs([owner('first','这条和下一条回复一起回您。'),next]).length,1);
 assert.equal(mergeOwnerInputs([first])[0].source.text,'first');
 assert.equal(mergeOwnerInputs([first,{source:{k:'sys'},reply:'intermediate'},next].filter(p=>!activityKind(p)))[0].source.text,'first\n\nsecond');
});
test('failure comes from host state, never text',()=>{
 assert.equal(activityFailed({end:'failed'}),true);assert.equal(activityFailed({end:'done',reply:'failed'}),false);
});
