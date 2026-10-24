import test from 'node:test';
import assert from 'node:assert/strict';
import {workflowResumeMessage,controlObject,canonicalJson} from '../../protocol/wire.js';
const c={action:'wf_resume',channel:'fixture',device:'device',id:'daily',run_id:'a'.repeat(32),tsha:'b'.repeat(64),run_sha:'c'.repeat(64),nonce:'d'.repeat(32),issued:1792812000,expires:1792812120};
test('P498 workflow signature binds every exact checkpoint field in its own domain',async()=>{
 const k=await crypto.subtle.generateKey('Ed25519',true,['sign','verify']);
 const bytes=workflowResumeMessage(c),sig=await crypto.subtle.sign('Ed25519',k.privateKey,bytes);
 assert.equal(new TextDecoder().decode(bytes),'agentjarvis-wf-resume-v1\n'+canonicalJson(c));
 assert.ok(await crypto.subtle.verify('Ed25519',k.publicKey,sig,workflowResumeMessage(c)));
 for(const key of ['channel','device','id','run_id','tsha','run_sha','nonce'])assert.equal(await crypto.subtle.verify('Ed25519',k.publicKey,sig,workflowResumeMessage({...c,[key]:c[key]+'x'})),false,key);
 for(const patch of [{source:'phone'},{yes:true},{expires:c.expires+1},{issued:'1792812000'},{action:'resume'}])assert.throws(()=>workflowResumeMessage({...c,...patch}));
});
test('P498 reply signature covers only explicit report ID and reply; local remark is separately bound',()=>{
 assert.equal(controlObject('fr_report_reply',{id:'report',text:'Wednesday\nOnly this'}),'report\nWednesday\nOnly this');
 assert.equal(controlObject('fr_report_reply',{id:'a'.repeat(64),text:'Wednesday',resend:true}),'resend\n'+'a'.repeat(64)+'\nWednesday');
 assert.notEqual(controlObject('fr_report_reply',{id:'a'.repeat(64),text:'Wednesday',resend:true}),controlObject('fr_report_reply',{id:'a'.repeat(64),text:'Wednesday'}));
 assert.equal(controlObject('fr_report_remark',{friend:'friend',name:'My colleague'}),'friend\nMy colleague');
});
