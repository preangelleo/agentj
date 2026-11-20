import test from 'node:test';import assert from 'node:assert/strict';
globalThis.window??=globalThis;globalThis.addEventListener??=()=>{};globalThis.document??={addEventListener(){},getElementById(){return null;}};
const {stepReason}=await import('../public/js/approval-layout0281.js');
test('0287 missing reason fields and repeated/empty fields describe their distinct actual data gaps',()=>{const absent={title:'操作',action:'操作'},repeated={...absent,reason:'操作'},empty={...absent,reason:''};assert.match(stepReason(absent),/未附独立理由字段/);for(const e of [repeated,empty]){const before=JSON.stringify(e);assert.match(stepReason(e),/理由为空或与操作标题重复/);assert.equal(JSON.stringify(e),before);}assert.equal(stepReason({...absent,reason:'新增一台遥控器需要你确认'}),'新增一台遥控器需要你确认');});

test('a verified host rescue card explains why it waits for confirmation', () => {
  const envelope = {title: '确认登录', action: 'AuthRescue', details: '确认登录', effects: []};
  const card = {envelope_verified: true, host_tool: true, tool: 'AuthRescue', envelope, summary: '确认登录'};
  assert.equal(stepReason(envelope, card), '只有你确认，Agent J 才会执行这一步。');
  for (const overrides of [{tool: 'Bash'}, {envelope_verified: false}, {host_tool: false}, {summary: '另一个画面'}, {envelope: {...envelope}}]) {
    assert.notEqual(stepReason(envelope, {...card, ...overrides}), stepReason(envelope, card));
  }
  assert.notEqual(stepReason(undefined, {...card, envelope: undefined, summary: undefined}), stepReason(envelope, card));
  const withReason = {...envelope, reason: '登录需要重新验证身份'};
  assert.equal(stepReason(withReason, {...card, envelope: withReason}), withReason.reason);
});
