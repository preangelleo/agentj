// Replay real Host history snapshots through Noise into both shipped and candidate clients.
// Producer: host test_p0336_reply_slot exporter, supplied by P0336_FIXTURES.
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdtempSync,mkdirSync,readFileSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {startWebServer,PUBLIC_DIR} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,shoot,sleep} from './browser.mjs';
assert.ok(process.env.P0336_FIXTURES,'P0336_FIXTURES must name actual Host-produced snapshots');
const fixture=JSON.parse(readFileSync(process.env.P0336_FIXTURES,'utf8'));
assert.ok(fixture.scenarios.length>=3,'producer must run all scenarios, including failed/red snapshots');
assert.deepEqual([...new Set(fixture.scenarios.map(s=>s.name))].sort(),['owner_reply','owner_silent','webhook_silent']);
const baseline=execFileSync('git',['rev-parse','70c8cf3e5'],{encoding:'utf8'}).trim();
const source=execFileSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).trim();
const out=join(process.env.OLARES_RUN_OUT||'reports/qa/p0336','compat');mkdirSync(out,{recursive:true});
const temp=mkdtempSync(join(tmpdir(),'aj-p0336-'));
execFileSync('tar',['-xf','-','-C',temp],{input:execFileSync('git',['archive',baseline,'web/public'],{maxBuffer:50*1024*1024})});
const old=await startWebServer({publicDir:join(temp,'web/public')}),fresh=await startWebServer({publicDir:PUBLIC_DIR});
const fake=await startFakeHost(),browser=await launch(),rows=[];
const snap=p=>evaluate(p,`({words:document.getElementById('words').textContent,input:document.getElementById('omText').textContent,page:document.getElementById('pg').textContent,unread:document.getElementById('omUnread')?.dataset.n||'0',pulse:document.getElementById('pgCorner')?.dataset.unread||'0'})`);
try{
 for(const [version,web] of [['0.30.21',old],['0.30.22',fresh]])for(const theme of ['light','dark'])for(const scenario of fixture.scenarios){
  fake.reset();fake.st.prefs={appearance:{language:'zh',theme}};
  const seed={id:1,ts:Date.now()-60000,src:{k:'phone',text:'P0336 previous owner input'},reply:{text:'P0336 previous reply'},end:'done'};
  fake.st.turns=[seed];fake.st.nextId=2;
  const p=await newPage(browser,390,900,theme,{touch:true,allow:[web.url,fake.relay+'/']});
  const row={version,theme,scenario:scenario.name,wrapped:scenario.wrapped,delayed:scenario.delayed,native_end:scenario.native_end,steps:[],errors:[]};
  try{
   await navigate(p,fake.newPairing(web.url+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await waitFor(p,`document.getElementById('words').textContent.includes('P0336 previous reply')`);
   const initial=await snap(p);
   for(const step of scenario.steps){
    // Reserve ID1 for a preceding page. Host IDs are remapped uniformly, preserving all ownership relationships.
    const turns=step.turns.map(t=>({...structuredClone(t),id:t.id+1}));
    fake.st.turns=[seed,...turns];fake.st.nextId=Math.max(...fake.st.turns.map(t=>t.id))+1;
    for(const turn of turns)await fake.send({t:'hist_turn',epoch:fake.st.epoch,turn});
    await sleep(170);
    const state=await snap(p);row.steps.push({event:step.event,state});
    if(/（无文字回复）/.test(state.words))row.errors.push(step.event+': empty reply card');
    if(/主动汇报/.test(state.input))row.errors.push(step.event+': unsolicited active report');
   }
   const final=await snap(p);
   if(scenario.name==='owner_reply'){
    assert.match(final.words,new RegExp(scenario.expected_reply||'P0336 real owner reply'));
    assert.match(final.input,new RegExp(scenario.expected_input||'P0336 owner instruction'));
    assert.equal(final.page,'2 / 2','one original Send page, no separate proactive reply');
   }else{
    assert.equal(final.page,'1 / 1','silent entire turn is absent from main pages');
    assert.equal(final.words,initial.words,'silent turn leaves prior reply visible');
    assert.equal(final.unread,initial.unread,'silent turn does not increment unread');
    assert.equal(final.pulse,initial.pulse,'silent turn does not light new-reply pulse');
   }
   assert.deepEqual(row.errors,[],'no transient completed empty card or proactive classification');
   assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);
   row.result='PASS';
  }catch(error){row.result='FAIL';row.errors.push(String(error));}
  finally{writeFileSync(join(out,`${version}-${theme}-${scenario.name}-${Number(scenario.wrapped)}-${Number(scenario.delayed)}-${Number(scenario.native_end)}.png`),await shoot(p));await p.dispose();rows.push(row);console.log(JSON.stringify({version,theme,scenario:scenario.name,result:row.result,errors:row.errors}));}
 }
 writeFileSync(join(out,'results.json'),JSON.stringify({source,baseline,baseline_public_tree:execFileSync('git',['rev-parse',baseline+':web/public'],{encoding:'utf8'}).trim(),fixture_source:fixture.source_commit,rows},null,2));
 assert.equal(rows.filter(r=>r.result!=='PASS').length,0,'P0336 two-version compatibility must pass every scenario');
 console.log(`p0336_compat PASS ${rows.length} real Chromium/Noise journeys`);
}finally{await browser.close();await fake.stop();await old.stop();await fresh.stop();rmSync(temp,{recursive:true,force:true});}
