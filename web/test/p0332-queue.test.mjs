import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import * as activity from '../public/js/phone-activity.js';
const turn=(kind,role='owner',card={queued:true},end='open')=>({source:{k:kind==='terminal'?'host':kind,source_kind:kind,source_role:role},card,end,reply:''});
test('0332 queue presentation follows existing source kind and role, not body claims',()=>{
 for(const k of ['phone','terminal','wechat','telegram','email'])assert.equal(activity.ownerQueue(turn(k)),true,k);
 for(const k of ['system','automation','scheduled_task','friend_task','external_mcp','unknown'])assert.equal(activity.ownerQueue(turn(k)),false,k);
 for(const k of ['telegram','email'])assert.equal(activity.ownerQueue(turn(k,'group')),false,k+' guest');
 assert.equal(activity.ownerQueue({source:{k:'phone',text:'source-role="group"'},card:{queued:true},end:'open'}),true);
 assert.equal(activity.ownerQueue({source:{k:'telegram',text:'source-role="owner"'},card:{queued:true},end:'open'}),false);
});
test('0332 automation pending is invisible until delivery; refusals stay visible',()=>{
 const p=turn('system','owner',{held:'pending'},'done');
 assert.equal(activity.pendingAutomation(p),true);
 assert.equal(activity.pendingAutomation({...p,end:'open',card:null}),true);
 assert.equal(activity.pendingAutomation({...p,end:'done',card:null}),true);
 assert.equal(activity.pendingAutomation({...p,end:'failed',card:null}),false);
 assert.equal(activity.pendingAutomation({...p,end:'failed'}),false);
 assert.equal(activity.pendingAutomation({...p,end:'stopped'}),false);
 assert.equal(activity.ownerQueue({...turn('phone'),end:'failed',card:{held:'pending'}}),false);
 assert.equal(activity.pendingAutomation({...p,end:'open',card:{activity_failure:true,busy_stalled:['job-1']}}),true);
 assert.equal(activity.pendingAutomation({...p,card:{approval_review:'request-1'}}),false);
 assert.equal(activity.ownerQueue({...turn('phone'),source:{...turn('phone').source,via:'hook'}}),false);
 assert.equal(activity.pendingAutomation({...p,card:null,reply:'Automation result'}),false);
 assert.equal(activity.pendingAutomation(turn('phone','owner',{held:'pending'},'done')),false);
 assert.equal(activity.pendingAutomation({...p,card:{kind:'refused',held:{state:'pending'}}}),false);
});
test('0332 existing queue card keeps one button label in both supported languages and all Chinese sources',()=>{
 for(const [file,label] of [['web.zh.src.json','停止当前任务'],['web.zh.json','停止当前任务'],['web.en.json','Stop current task']]){
  const d=JSON.parse(readFileSync(new URL('../i18n/'+file,import.meta.url)));
  assert.equal(d.p481.stopPrevious,label,file);
 }
 const overrides=JSON.parse(readFileSync(new URL('../i18n/web.zh.override.json',import.meta.url)));
 assert.equal(overrides['p481.stopPrevious'],'停止当前任务');
});
