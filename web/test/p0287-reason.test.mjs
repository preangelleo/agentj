import test from 'node:test';import assert from 'node:assert/strict';
globalThis.window??=globalThis;globalThis.addEventListener??=()=>{};globalThis.document??={addEventListener(){},getElementById(){return null;}};
const {stepReason}=await import('../public/js/approval-layout0281.js');
test('0287 missing reason fields and repeated/empty fields describe their distinct actual data gaps',()=>{const absent={title:'操作',action:'操作'},repeated={...absent,reason:'操作'},empty={...absent,reason:''};assert.match(stepReason(absent),/未附独立理由字段/);for(const e of [repeated,empty]){const before=JSON.stringify(e);assert.match(stepReason(e),/理由为空或与操作标题重复/);assert.equal(JSON.stringify(e),before);}assert.equal(stepReason({...absent,reason:'新增一台遥控器需要你确认'}),'新增一台遥控器需要你确认');});
