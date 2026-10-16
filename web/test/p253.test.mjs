import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const src = readFileSync(new URL('../public/js/relay.js',import.meta.url),'utf8');
const pick = (name,end) => src.slice(src.indexOf(`function ${name}(`),src.indexOf(end,src.indexOf(`function ${name}(`)));
test('P253: host feedback command appears once among Agent J tools and typeahead, including configured menus',()=>{
  const ctx=vm.createContext({MENU_LIMITS:{items:60,cmd:64,desc:200,group:32},MENU_CMD_RE:/^\/[a-z0-9-]+$/,SWITCH_CMD:'/switch-harness',t:k=>k});
  vm.runInContext(pick('menuFromHost','const IN_APP')+pick('toolItems','// P238: /switch-harness'),ctx);
  for (const desc of ['反馈给 Agent J Father','Feedback to Agent J Father']) {
    ctx.payload={items:[{cmd:'/own',desc:'Own'},{cmd:'/feedback-to-agentj-father',desc}],skills:[{cmd:'/feedback-to-agentj-father'}]};
    vm.runInContext('hostMenu=menuFromHost(payload); result=toolItems(true)',ctx);
    assert.equal(ctx.result.filter(x=>x[0]==='/feedback-to-agentj-father').length,1);
    assert.equal(ctx.result.find(x=>x[0]==='/feedback-to-agentj-father')[1],desc);
    assert.equal(ctx.hostMenu.items.length,1);
    assert.equal(ctx.hostMenu.skills.length,0);
  }
  ctx.payload={items:Array.from({length:60},(_,i)=>({cmd:`/own-${i}`,desc:'Own command'})).concat([
    {cmd:'/switch-harness',desc:'Switch'}, {cmd:'/feedback-to-agentj-father',desc:'Feedback to Agent J Father'}])};
  vm.runInContext('hostMenu=menuFromHost(payload);result=toolItems(true)',ctx);
  assert.equal(ctx.result.filter(x=>x[0]==='/feedback-to-agentj-father').length,1,'full owner menu still keeps the built-in command');
  assert.equal(ctx.hostMenu.items.length,60,'owner row cap stays 60');
  vm.runInContext('hostMenu=menuFromHost({items:[]});result=toolItems(true)',ctx);
  assert.equal(ctx.result.some(x=>x[0]==='/feedback-to-agentj-father'),false);
  assert.match(src,/toolItems\(true\)/); // same tool list supplies slash typeahead
});
