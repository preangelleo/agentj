import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { controlObject, CONTROL_ACTIONS } from '../../protocol/wire.js';
const read = p => readFileSync(new URL(p, import.meta.url), 'utf8');
const src = read('../public/js/assist-terminal.js');
function pure() {
  const pick = name => { const i = src.indexOf(`export function ${name}(`); assert.ok(i >= 0); const first=src.slice(i+7,src.indexOf('\n',i)); return first.endsWith('}') ? first : src.slice(i + 7, src.indexOf('\n}', i) + 2); };
  const b = { Date, t:(key,vars)=>key+' '+Object.values(vars||{}).join(' ') };
  vm.runInNewContext(`const ID=/^[A-Za-z0-9._:-]{1,80}$/; const NONCE=/^[a-f0-9]{32}$/; const fresh=()=>({active:false,id:'',until:0,key_id:'',connected:false});\n${['parseState','validEvent','grantObject','reportText','reasonText','validLogin','challengeTarget','parseLogPage','logEntriesText','logScopeText'].map(pick).join('\n')}\nObject.assign(globalThis,{parseState,validEvent,grantObject,reportText,validLogin,challengeTarget,parseLogPage,logEntriesText,logScopeText});`, b); return b;
}
const p = pure(), plain = x => JSON.parse(JSON.stringify(x));
test('terminal consent has an independent signed scope, old task grant remains unchanged', () => {
  assert.ok(CONTROL_ACTIONS.includes('assist_term_grant'));
  const o = {minutes:30,key_id:'official-a',seat:'seat-a',scope:'terminal'};
  assert.equal(controlObject('assist_term_grant',o),'30\nofficial-a\nseat-a\nterminal');
  assert.equal(controlObject('assist_grant',o),'30\nofficial-a\nseat-a');
  assert.notEqual(controlObject('assist_grant',o),controlObject('assist_term_grant',o));
  assert.deepEqual(plain(p.grantObject(30,o)),o);
});
test('terminal state never activates from task scope, expired authorization, untyped active or invalid grant id', () => {
  const base={scope:'terminal',active:true,id:'g1',until:Date.now()+10000,key_id:'official-a'};
  assert.equal(p.parseState({...base,scope:'tasks'}),null);
  for(const m of [{...base,active:'yes'},{...base,until:1},{...base,id:'<g1>'}]) assert.equal(p.parseState(m).active,false);
  assert.equal(p.parseState(base).active,true);
});
test('observer drops pregrant, mismatched grant/session, replay and oversized output', () => {
  const state={active:true,id:'g1',until:Date.now()+10000}; const event={gid:'g1',event:{t:'term_out',id:'t1',seq:2,d:'ok'}};
  assert.ok(p.validEvent(event,state,'t1',1));
  for(const [m,s,id,last] of [[event,{...state,active:false},'t1',0],[{...event,gid:'g2'},state,'t1',0],[event,state,'t2',0],[event,state,'t1',2],[{...event,event:{...event.event,seq:undefined}},state,'t1',0],[{...event,event:{...event.event,d:'x'.repeat(65537)}},state,'t1',0]]) assert.equal(p.validEvent(m,s,id,last),false);
});
test('read-only renderer exposes no keyboard, clipboard, native reply or wire sender', () => {
 const v=read('../public/js/assist-viewer.js').replace(/^\s*\/\/.*$/gm,'');
 assert.match(v,/disableStdin:\s*true/); assert.doesNotMatch(v,/onData\s*\(|sendApp|term_in|clipboard|navigator\.credentials/);
 const app=read('../public/app.js'); assert.match(app,/m\.assist === true && typeof m\.t === 'string' && m\.t\.startsWith\('term_'\)/);
 assert.match(app,/assistTerminal\.gone\(\)/);
});
test('customer report is bounded plain text and ignores unknown objects',()=>{
 assert.equal(p.reportText({summary:'Fixed',events:[{ts:1,text:'Service restarted'},'<script>',{secret:{nested:'ignored'}}]}).includes('<script>'),false);
 assert.ok(p.reportText({summary:'Fixed',events:[{text:'Service restarted'}]}).includes('Service restarted'));
 assert.ok(p.reportText({summary:'x'.repeat(100000)}).length<=65536);
});

test('dedicated login requires the active full grant marker, exact shortened login target and fresh session',()=>{
 const s={active:true,id:'1234567890abcdef1234567890abcdef',until:Date.now()+10000};const m={kind:'login',assist_gid:s.id,term:s.id.slice(0,16),id:'a'.repeat(32),n:'b'.repeat(32),fa:'customer-key'};
 assert.ok(p.validLogin(m,s));
 for(const bad of [{...m,assist_gid:'other'},{...m,term:'other'},{...m,kind:'sudo'},{...m,fa:''},{...m,id:'invalid'}])assert.equal(p.validLogin(bad,s),false);
 assert.equal(p.validLogin(m,{...s,active:false}),false);assert.equal(p.validLogin(m,{...s,until:1}),false);
 const app=read('../public/app.js');assert.match(app,/m.kind === 'login' && m.assist_gid/);assert.match(read('../public/js/terminal.js'),/S.phase !== 'opening' \|\| !S.r2 \|\| m.term !== S.r2/);
});

test('pre-sign target is scoped to the current duration, channel and unexpired challenge',()=>{
 const now=Date.now(),c={minutes:30,key_id:'official-a',seat:'seat-a',channel:'channel-a',at:now};
 assert.deepEqual(plain(p.challengeTarget(c,30,'channel-a',now)),{minutes:30,key_id:'official-a',seat:'seat-a'});
 for(const args of [[c,15,'channel-a',now],[c,30,'channel-b',now],[c,30,'channel-a',now+100001],[null,30,'channel-a',now]])assert.equal(p.challengeTarget(...args),null);
});
test('repair report renders actual operations and explicitly labels unverified official statements',()=>{
 const s=p.reportText({operations:[{command:'service restart fixture',state:'confirmed',exit_status:null,at:1}],official_statement:{summary:'Fixture service restored',diagnosis:'Fixture process stopped',verification:'Fixture health check'}});
 for(const part of ['service restart fixture','p468.operation.confirmed','p468.exitUnknown','p468.officialStatement','Fixture health check'])assert.ok(s.includes(part),part);
 assert.ok(p.reportText({events:[{ev:'terminal_command',command:'service status fixture'}]}).includes('service status fixture'));
});

test('log pages bind the requested grant and move strictly to a different earlier opaque cursor',()=>{
 const m={t:'assist_log_res',gid:'g1',entries:[{gid:'g1',ev:'terminal_command',command:'fixture'}],more:true,next_cursor:'opaque-earlier'};
 assert.equal(p.parseLogPage(m,'g1').gid,'g1');assert.equal(p.parseLogPage(m,'old-gid'),null);
 assert.equal(p.parseLogPage(m,'g1','opaque-earlier').error,'shape');
 assert.equal(p.parseLogPage({...m,next_cursor:null},'g1').error,'shape');
 assert.equal(p.parseLogPage({...m,entries:[{gid:'g2'}]},'g1').error,'shape');
 assert.equal(p.parseLogPage({...m,entries:Array(201).fill({})},'g1').error,'shape');
 assert.equal(p.parseLogPage({t:'assist_log_res',gid:'g1',why:'pruned'},'g1').error,'pruned');
 assert.equal(p.parseLogPage({t:'assist_log_res',gid:'g1',why:'bad_cursor'},'g1').error,'bad_cursor');
});
test('an empty matching log window still allows reading earlier, and a final page marks the beginning',()=>{
 assert.equal(p.parseLogPage({t:'assist_log_res',gid:'g1',entries:[],more:true,next_cursor:'opaque-next'},'g1').more,true);
 assert.equal(p.parseLogPage({t:'assist_log_res',gid:'g1',entries:[],more:false,next_cursor:null},'g1').more,false);
});
test('bounded log page text does not silently clip a record and preserves command/output order',()=>{
 const long='record-data-'.repeat(7000),text=p.logEntriesText([{ev:'terminal_command',command:'FIRST'},{ev:'terminal_output',data:long},{ev:'terminal_command',command:'LAST'}]);
 assert.ok(text.includes(long));assert.ok(text.indexOf('FIRST')<text.indexOf(long)&&text.indexOf(long)<text.indexOf('LAST'));
});

test('customer log range shows page and abbreviated authorization, never filesystem cursor tokens',()=>{
 const s=p.logScopeText({gid:'log-grant-one',cursor:'v1:gid:dev:inode:private-offset',next_cursor:'v1:other:dev:inode:other-offset',entries:[{ts:1700000001},{ts:1700000002}]},2);
 assert.ok(s.includes('log-gran…')&&s.includes('2'));assert.ok(!s.includes('v1:')&&!s.includes('inode')&&!s.includes('private-offset'));
});

test('bounded report summary shows 3/64 without upgrading unknown exit or official claims into verified repairs',()=>{
 const r={operations_shown:3,operations_total:64,operations_truncated:true,operations:[{command:'fixture repair step',state:'confirmed',exit_status:null}],official_statement:{summary:'Helper-provided repair explanation'},unresolved:['outcome still needs verification']};
 const text=p.reportText(r);assert.ok(text.includes('p468.summaryCount 3 64'));assert.ok(text.includes('p468.exitUnknown'));assert.ok(text.includes('p468.officialStatement'));assert.ok(text.includes('p468.reportUnresolved'));assert.ok(!text.includes('Repair verified'));
 const old=p.reportText({operations:[{command:'legacy operation',state:'unknown',exit_status:null}]});assert.ok(!old.includes('p468.summaryCount'));
});
