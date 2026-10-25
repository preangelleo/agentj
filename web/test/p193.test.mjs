// P193 (B10): the page does not pull the whole epoch's history on every sync, and a streaming reply is not re-parsed as
// Markdown for every chunk.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const src=readFileSync(new URL('../public/js/relay.js',import.meta.url),'utf8');
const history=src.slice(src.indexOf('const hist ='),src.indexOf('function hhmm')).replaceAll('export function','function');
const words=src.slice(src.indexOf('const NO_ECHO_MS'),src.indexOf('function fill('));

/** A host with turns 1..n (every `silent`-th reply silent), answering history({after|before|limit}) like hist_page. */
function host(n,silentEvery=0){
  const turns=Array.from({length:n},(_,i)=>({id:i+1,reply:silentEvery&&(i+1)%silentEvery===0?'〔不回群〕':'answer '+(i+1)}));
  const calls=[];
  const history=async q=>{calls.push(q);let out=turns;
    if(q.after)out=turns.filter(t=>t.id>q.after);
    if(q.before)out=turns.filter(t=>t.id<q.before);
    out=q.after?out.slice(0,q.limit||50):out.slice(-(q.limit||50));
    return {epoch:0,first_id:1,count:n,turns:out};};
  return {calls,history};
}
function page(h){
  const ctx=vm.createContext({C:{api:{history:h.history}},showPage(){},renderSilent(){},announceTurn(){},el:()=>({hidden:true})});
  vm.runInContext(history,ctx);
  return x=>vm.runInContext(x,ctx);
}

test('P193 sync loads the newest batch and stops once the newest page and the one before are real', async()=>{
  const h=host(1000),run=page(h);
  await run('syncHistory(1000)');
  assert.equal(h.calls.length,1,'one request for a 1000-turn epoch, not 20: '+JSON.stringify(h.calls));
  assert.equal(run('hist.turns.length'),50);
  assert.equal(run('hist.lastId'),1000);
  assert.equal(run('olderLeft()'),true,'older history stays on the host until asked for');
  // paging back still reaches the oldest turn on demand
  run('follow=false;pageAt=0');
  while(await run('loadOlder()')){}
  assert.equal(run('hist.turns[0].id'),1);
});

test('P193 a newest batch of silent turns keeps loading until a real page is found (no empty first screen)', async()=>{
  const h=host(130,1),run=page(h);       // every turn silent …
  await run('syncHistory(130)');
  assert.equal(run('olderLeft()'),false,'… so everything is fetched looking for a page (bounded by the epoch)');
  const g=host(200),r2=page(g);          // negative: plenty of real pages → a single request
  await r2('syncHistory(200)');
  assert.equal(g.calls.length,1);
});

// Fake clock + timers inside the vm context (as in p135.test.mjs).
function clock(){
  const c={now:1000,timers:[],seq:0};
  c.setTimeout=(f,ms)=>{const id=++c.seq;c.timers.push({id,at:c.now+ms,f});return id;};
  c.clearTimeout=id=>{c.timers=c.timers.filter(x=>x.id!==id);};
  c.Date={now:()=>c.now};
  c.advance=ms=>{c.now+=ms;for(;;){const due=c.timers.filter(x=>x.at<=c.now).sort((a,b)=>a.at-b.at)[0];if(!due)break;c.clearTimeout(due.id);due.f();}};
  return c;
}
function wordsCtx(){
  const c=clock(),painted=[],refreshes=[],w={dataset:{}};
  const ctx=vm.createContext({setTimeout:c.setTimeout,clearTimeout:c.clearTimeout,Date:c.Date,historySync:false,shownKey:'7|x',wordsSrc:null,cardReplyKey:null,refreshCards:()=>refreshes.push(1),
    cur:{status:'working',hostStatus:'working'},t:x=>x,el:()=>w,fill:(_,x)=>painted.push(x),afterWords(){},rdFollow(){}});
  vm.runInContext(words,ctx);
  ctx.showPage=()=>vm.runInContext('renderWords(globalThis.latest,globalThis.open)',ctx);
  const say=(text,open=true)=>{ctx.latest=text;ctx.open=open;vm.runInContext('renderWords(globalThis.latest,globalThis.open)',ctx);};
  return {c,painted,say,refreshes,run:js=>vm.runInContext(js,ctx)};
}

test('P193 streaming reply: at most one Markdown redraw per 150 ms, the latest text always lands', ()=>{
  const {c,painted,say}=wordsCtx();
  let text='';
  for(let i=0;i<100;i++){text+='chunk '+i+' ';say(text);c.advance(10);}   // 100 chunks over 1 s
  c.advance(200);
  assert.ok(painted.length<=10,`redraws: ${painted.length} for 100 chunks`);
  assert.equal(painted.at(-1),text.trim(),'the trailing redraw shows the whole text');
});

test('P193 not throttled: a finished reply, a different page, or the first chunk draw at once', ()=>{
  const {c,painted,say}=wordsCtx();
  say('first');assert.deepEqual(painted,['first'],'first chunk at once');
  say('first and more');assert.equal(painted.length,1,'a fast second chunk waits');
  say('first and more, done',false);assert.equal(painted.at(-1),'first and more, done','done draws at once');
  say('another page entirely');assert.equal(painted.at(-1),'another page entirely','a non-continuation draws at once');
  c.advance(500);assert.equal(painted.filter(x=>x==='another page entirely').length,1,'no stale trailing redraw');
});


test('P519 registry refresh waits for a settled Agent reply and runs once per page/text', ()=>{
  const w=wordsCtx();
  w.say('new card',true);assert.equal(w.refreshes.length,0,'streaming does not poll');
  w.say('new card',false);assert.equal(w.refreshes.length,1,'completion refreshes even if the final text did not change');
  w.say('new card',false);assert.equal(w.refreshes.length,1,'catalogue redraw cannot start a refresh loop');
  w.say('another reply',false);assert.equal(w.refreshes.length,2);
  w.run("renderWords('system notice',false,'',false,true,false)");assert.equal(w.refreshes.length,2);
  w.run("renderWords('data only',false,'',false,false,true)");assert.equal(w.refreshes.length,2);
});
