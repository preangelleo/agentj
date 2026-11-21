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
test('verified Plaza install metadata explains a direct request without changing the envelope',()=>{
 const pkg={package:'color-of-the-year-palette',version:'1.2.0',certified:true,target:'~/skills/demo',sha256:'1234567890ab'.repeat(5)+'1234',digest:'1'.repeat(16)};
 const card=(input,source={},context={})=>{
  const details='agentj plaza\n'+JSON.stringify(input),e={title:'agentj plaza',action:'agentj plaza',details,source};
  const p={tool:'agentj plaza',envelope_verified:true,summary:details,envelope:e,...context};
  const before=JSON.stringify(p),reason=stepReason(e,p);
  assert.equal(JSON.stringify(p),before);
  return reason;
 };
 const owner={verified:true,suspicious:false,source_trust:'owner',source_type:'owner_remote',text:'装 color-of-the-year-palette',message_id:42,trigger_message_id:42};
 const data={...pkg,items:[]};
 assert.equal(card(data,owner),'你刚才请求从广场安装 color-of-the-year-palette 1.2.0（Agent J 官方签名已校验）。摘要 SHA-256 1234567890ab…');
 for(const source of [{},{...owner,suspicious:true},{...owner,trigger_message_id:7},{...owner,history_outside_window:true},
  {...owner,source_trust:'host',source_type:'scheduled_task'}, {...owner,source_trust:'external',source_type:'same_owner'}]){
  assert.match(card({...data,certified:false},source),/^请确认安装广场包 .*无官方签名/);
 }
 const group={packages:[pkg,{...pkg,package:'second',certified:false},{...pkg,package:'third'}],group_sha256:'8'.repeat(64),items:[]};
 assert.match(card(group,owner),/color-of-the-year-palette 1\.2\.0（Agent J 官方签名已校验）、second 1\.2\.0（无官方签名），另有 1 个见详情。摘要 SHA-256 888888888888…/);
 for(const [input,context] of [[data,{envelope_verified:false}],[data,{summary:'unbound'}],[data,{tool:'Bash'}],
  [{...data,operation:'uninstall'},{}],[{...data,digest:undefined},{}],[{...data,sha256:'x'},{}],[{...data,certified:'true'},{}]]){
  assert.match(card(input,owner,context),/未附独立理由字段/);
 }
});
