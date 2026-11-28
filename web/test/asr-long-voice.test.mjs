import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const src=readFileSync(new URL('../public/js/relay.js',import.meta.url),'utf8');
const block=src.slice(src.indexOf('function asrBlock(a){'),src.indexOf('function setOm(open){'));
function render(a){
  const context={document:{createElement(tag){return {tag,children:[],style:{},textContent:'',attributes:{},setAttribute(k,v){this.attributes[k]=String(v)},append(...nodes){this.children.push(...nodes)},appendChild(node){this.children.push(node)}}}},t:key=>({'asr440.local_fallback':'Recovered locally','p363.asrTitle':'Transcript','p363.asrFailTitle':'Transcription failed'}[key]||key),asrWhy:()=>''};
  vm.createContext(context);vm.runInContext(block,context);return context.asrBlock(a);
}
test('long voice keeps its transcript and folds without leaving a fallback notice in history',()=>{
  const d=render({ok:true,text:'<img src=x onerror=alert(1)>',notice:'local_fallback'});
  assert.equal(d.tag,'details');assert.equal(d.open,true);
  assert.equal(d.children[0].textContent,'Transcript');
  assert.equal(d.children[0].children.length,0);
  assert.equal(d.children[1].textContent,'<img src=x onerror=alert(1)>');
  d.open=false;assert.equal(d.children[0].children.length,0);
});
test('ordinary success, unknown notices and total failure do not claim successful local recovery',()=>{
  for(const a of [{ok:true,text:'ordinary'},{ok:true,text:'unknown',notice:'<script>bad()</script>'},{ok:false,why:'broken',notice:'remote_unavailable'},{ok:false,why:'broken',notice:'local_fallback'}]){
    const d=render(a);assert.equal(d.children[0].children.length,0);
    assert.equal(d.children[0].textContent,a.ok?'Transcript':'Transcription failed');
  }
});

test('snapshot history preserves only fixed ASR notices, including read-back failure status',()=>{
  const snap=readFileSync(new URL('../public/js/snap.js',import.meta.url),'utf8');
  const source=snap.slice(snap.indexOf('export function asrOf(a)'),snap.indexOf('/** Agent J turn (wire)'));
  const ctx={dropReplacement:s=>s};vm.createContext(ctx);vm.runInContext(source.replace('export function','function'),ctx);
  for(const notice of ['local_fallback','remote_unavailable']){
    assert.equal(ctx.asrOf({ok:true,text:'transcript',notice}).asr.notice,notice);
    assert.equal(ctx.asrOf({ok:false,why:'broken',notice}).asr.notice,notice);
  }
  for(const notice of [undefined,'https://endpoint.invalid/?key=value','<script>bad()</script>']){
    assert.equal(Object.hasOwn(ctx.asrOf({ok:true,text:'transcript',notice}).asr,'notice'),false);
    assert.equal(Object.hasOwn(ctx.asrOf({ok:false,why:'broken',notice}).asr,'notice'),false);
  }
});

const asrStart = src.indexOf('let asrFallbackShown');
const asrCode = src.slice(asrStart,src.indexOf('async function takeFinished',asrStart));
const fallback = {notice:'local_fallback'};
function noticeFixture(history){
  const counts = {shown:0,dismissed:0};
  const timers = new Set(), observers = new Set();
  let visible = false;
  const box = {firstElementChild:null,style:{setProperty(name,value){
    assert.equal(name,'--nt-accent');assert.equal(value,'var(--windstorm)');
  }},classList:{contains:()=>visible,remove:()=>{
    if (visible) counts.dismissed++;
    visible=false;
  }}};
  const ctx = vm.createContext({
    history,t:k=>k,el:id=>id==='toast'?box:null,
    toast(key,ms){
      assert.equal(key,'asr440.local_fallback');assert.equal(ms,3000);
      counts.shown++;
      box.firstElementChild={};
      visible=true;
      return box;
    },
    MutationObserver:class {
      constructor(fn){this.fn=fn;}
      observe(){observers.add(this.fn);}
      disconnect(){observers.delete(this.fn);}
    },
    setTimeout(fn,ms){assert.equal(ms,3000);timers.add(fn);return fn;},
    clearTimeout(fn){timers.delete(fn);},
  });
  assert.ok(asrStart >= 0,'live notice handler exists');
  vm.runInContext(asrCode,ctx);
  return {ctx,counts,get visible(){return visible;},
    cover(){visible=true;box.firstElementChild={};},
    uncover(){visible=false;for (const fn of [...observers]) fn();},
    waitExpired(){for (const fn of [...timers]) fn();},
  };
}

test('live ASR outage is shown once per seat and tab, and only remote success rearms it',()=>{
  const historyEntry = () => ({state:{ajReader:1},replaceState(state){this.state=state;}});
  const history = historyEntry();
  const f = noticeFixture(history);
  f.ctx.asrRouteNotice(fallback);
  f.ctx.asrRouteNotice({});
  f.ctx.asrRouteNotice(fallback);
  assert.deepEqual(f.counts,{shown:1,dismissed:0});
  assert.equal(history.state.ajAsrFallback,true);
  assert.equal(history.state.ajReader,1);
  const reload = noticeFixture(history);
  reload.ctx.asrRouteNotice(fallback);
  assert.equal(reload.counts.shown,0,'same history entry after reload');
  f.ctx.asrRouteNotice({remote:true});
  assert.deepEqual(f.counts,{shown:1,dismissed:1});
  assert.equal(Object.hasOwn(history.state,'ajAsrFallback'),false);
  assert.equal(history.state.ajReader,1);
  f.ctx.asrRouteNotice(fallback);
  f.ctx.resetAsrFallback();
  assert.deepEqual(f.counts,{shown:2,dismissed:2});
  f.ctx.asrRouteNotice(fallback);
  assert.equal(f.counts.shown,3);
  const other = noticeFixture(historyEntry());
  other.ctx.asrRouteNotice(fallback);
  assert.equal(other.counts.shown,1,'a different history entry remains separate');
  const blocked = noticeFixture(Object.create(null));
  blocked.ctx.asrRouteNotice(fallback);
  blocked.ctx.asrRouteNotice(fallback);
  assert.equal(blocked.counts.shown,1,'in-memory dedup when updating history is blocked');
});

test('ASR preserves an active unrelated notice and ignores stale wait or dismissal',()=>{
  const f = noticeFixture();
  f.cover();
  f.ctx.asrRouteNotice(fallback);
  assert.equal(f.counts.shown,0,'active action keeps its place');
  assert.equal(f.visible,true);
  f.waitExpired();
  f.uncover();
  assert.equal(f.counts.shown,0,'no delayed warning after wait expires');
  f.cover();
  f.ctx.asrRouteNotice(fallback);
  f.ctx.asrRouteNotice({remote:true});
  f.uncover();
  assert.equal(f.counts.shown,0,'remote recovery cancels a waiting warning');
  f.cover();
  f.ctx.asrRouteNotice(fallback);
  f.uncover();
  assert.equal(f.counts.shown,1,'one warning when the slot clears in time');
  f.cover();
  f.ctx.asrRouteNotice({remote:true});
  assert.equal(f.visible,true,'old handle leaves the newer unrelated notice visible');
  assert.equal(f.counts.dismissed,0);
});
