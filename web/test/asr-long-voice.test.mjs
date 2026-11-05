import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const src=readFileSync(new URL('../public/js/relay.js',import.meta.url),'utf8');
const block=src.slice(src.indexOf('function asrBlock(a){'),src.indexOf('function setOm(open){'));
function render(a){
  const context={document:{createElement(tag){return {tag,children:[],style:{},textContent:'',append(...nodes){this.children.push(...nodes)},appendChild(node){this.children.push(node)}}}},t:key=>({'asr440.local_fallback':'Recovered locally','p363.asrTitle':'Transcript','p363.asrFailTitle':'Transcription failed'}[key]||key),asrWhy:()=>''};
  vm.createContext(context);vm.runInContext(block,context);return context.asrBlock(a);
}
test('long voice recovery notice remains in the always-visible transcript summary',()=>{
  const d=render({ok:true,text:'<img src=x onerror=alert(1)>',notice:'local_fallback'});
  assert.equal(d.tag,'details');assert.equal(d.open,true);
  assert.equal(d.children[0].children.length,1);
  assert.equal(d.children[0].children[0].textContent,'Recovered locally');
  assert.equal(d.children[0].children[0].style.flexBasis,'100%');
  assert.equal(d.children[1].textContent,'<img src=x onerror=alert(1)>');
  d.open=false;assert.equal(d.children[0].children[0].textContent,'Recovered locally');
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
