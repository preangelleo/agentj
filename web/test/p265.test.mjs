import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, existsSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {GLOBAL, KEYMAP, lookup, dispatchFunctional} from '../public/js/interactions.gen.js';
const src = new URL('../interactions.json', import.meta.url);
const have = existsSync(src);
const haveTool = have && existsSync(new URL('../../tools/interactions', import.meta.url)) && existsSync(new URL('../../site/build.mjs', import.meta.url));   // private authoring tool + site (not in the public export)
const bindings = Object.entries(KEYMAP).filter(([key]) => key.startsWith('alt+shift:'));
for (const [match, [id]] of bindings) for (const focus of ['textarea', 'input', 'contenteditable', 'body']) {
  test(`${match} → ${id}, ${focus}: physical code dispatch, no Option text, repeat swallowed`, () => {
    let calls=0, prevented=0, stopped=0;
    const e={key:'ﬂ',code:match.split(':')[1],altKey:true,shiftKey:true,target:{tagName:focus},preventDefault(){prevented++;},stopImmediatePropagation(){stopped++;}};
    assert.ok(GLOBAL.includes(id));
    assert.equal(dispatchFunctional(e,{[id]:()=>calls++}),true);
    assert.equal(calls,1); assert.equal(prevented,1); assert.equal(stopped,1);
    assert.equal(dispatchFunctional({...e,repeat:true},{[id]:()=>calls++}),true);
    assert.equal(calls,1); assert.equal(prevented,2);
    assert.equal(dispatchFunctional(e,{[id]:()=>calls++},true,true),true);
    assert.equal(calls,1,'sensitive layers consume the shortcut without navigation');
  });
}
test('single-key aliases and IME / extra modifier / unpaired / reserved keys retain their ownership',()=>{
  const old={j:'page.prev',k:'page.next',g:'page.newest',G:'page.earliest',u:'page.first_unread',q:'page.quoted',o:'page.expand',f:'card.reader',s:'app.settings','?':'app.keys',p:'compose.photo',P:'compose.paste_image',c:'compose.camera',C:'app.scan',r:'card.reply',e:'card.mail',E:'file.mail'};
  for(const [key,id] of Object.entries(old)) assert.equal(lookup({key})?.id,id);
  const base={key:'Ø',code:'KeyO',altKey:true,shiftKey:true,preventDefault(){throw Error('must not consume');}};
  for(const over of [{isComposing:true},{keyCode:229},{ctrlKey:true},{metaKey:true},{code:'KeyI'},{altKey:false},{shiftKey:false}]) assert.equal(dispatchFunctional({...base,...over},{}),false);
  assert.equal(dispatchFunctional(base,{},false),false);
});
test('every functional entry has one globally unique table binding', {skip: !have},()=>{
 const d=JSON.parse(readFileSync(src));
 for(const e of d.entries) for(const k of e.keys.filter(k=>k.combo.startsWith('Alt+Shift+'))){assert.equal(k.scope||e.scope,'any');assert.equal(k.impl||e.impl,'table');}
 assert.equal(new Set(bindings.map(([k])=>k)).size,bindings.length);
 assert.equal(KEYMAP['alt+shift:KeyP'][0],'app.pair_add');assert.equal(KEYMAP['alt+shift:KeyM'][0],'app.model_default');
});
test('site preflight rejects stale generated Chinese and English pages', {skip: !haveTool && 'no agentjarvis/tools/interactions (public export)'},()=>{
 const tool=new URL('../../tools/interactions',import.meta.url).pathname;
 const script=`import runpy,tempfile,pathlib,types\nm=runpy.run_path(${JSON.stringify(tool)})\nf=m['cmd_check_pages']; g=f.__globals__; g['zh_chunks']('prime')\nwith tempfile.TemporaryDirectory() as t:\n g['PAGE']=pathlib.Path(t); g['AJ']=pathlib.Path(t).parent\n data=g['load']()\n for p,s in g['render_page'](data).items(): p.write_text(s)\n assert f(types.SimpleNamespace())==0\n for lang in ('en.md','zh.md','zh.src.md','zh.override.json'):\n  p=g['PAGE']/lang; old=p.read_text(); p.write_text(old+'\\nstale')\n  assert f(types.SimpleNamespace())==1,lang\n  p.write_text(old)\n`;
 const r=spawnSync('python3',['-c',script],{encoding:'utf8'});assert.equal(r.status,0,r.stdout+r.stderr);
 const build=readFileSync(new URL('../../site/build.mjs',import.meta.url),'utf8');assert.ok(build.indexOf("'check-pages'") < build.indexOf('const docs = loadDocs(docsDir)'));
});

test('every baseline table key retains its original action (including capital and Alt aliases)', {skip: !have},()=>{
 const d=JSON.parse(readFileSync(new URL('./fixtures/p265-legacy-keys.json',import.meta.url)));
 for(const [combo,id] of d.keys){
  const k={combo};
  const parts=k.combo.split('+'),last=parts.pop(),alt=parts.includes('Alt'),shift=parts.includes('Shift');
  const key=last==='Space'?' ':last,code=/^[a-z]$/i.test(last)?'Key'+last.toUpperCase():last==='/'?'Slash':last;
  assert.equal(lookup({key,code,altKey:alt,shiftKey:shift})?.id,id,k.combo);
 }
});

test('the authoring tool refuses any-scope table keys that the global dispatcher cannot handle', {skip: !haveTool && 'no agentjarvis/tools/interactions (public export)'},()=>{
 const tool=new URL('../../tools/interactions',import.meta.url).pathname;
 for(const combo of ['h','Alt+J','Alt+Shift+/']){
  const r=spawnSync('python3',[tool,'add','--id','app.p265_probe','--target-selector','#x','--target-zh','测试','--target-en','Test','--zh','测试','--en','Test','--scope','any','--key',combo,'--dry-run'],{encoding:'utf8'});
  assert.notEqual(r.status,0);assert.match(r.stderr,/any table keys must use Alt\+Shift/);
 }
});
