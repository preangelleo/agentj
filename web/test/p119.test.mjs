import {focusDraft} from '../public/js/composer-focus.js';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
function fixture(){
 const handlers={},nodes=[];
 const document={activeElement:null,body:{},addEventListener(k,fn){handlers[k]=fn;},querySelectorAll(){return [];}};
 const node=id=>{const classes=new Set(),styles=new Map();const n={id,isConnected:true,tabIndex:0,children:[],dataset:{},inert:false,classList:{contains:c=>classes.has(c),toggle(c,on){if(on)classes.add(c);else classes.delete(c);}},style:{getPropertyValue:k=>styles.get(k)||'',setProperty:(k,v)=>styles.set(k,v)},getClientRects:()=>[{}],closest:()=>null,matches:()=>true,contains(e){return e===this||this.children.includes(e);},querySelectorAll(){return this.children;},focus(){document.activeElement=this;}};nodes.push(n);return n;};
 const ctx=vm.createContext({focusDraft,document,MutationObserver:class{observe(){}},console});
 vm.runInContext(readFileSync(new URL('../public/js/layers.js',import.meta.url),'utf8').replace(/^import .*;\n/gm,'').replaceAll('export ','' )+';globalThis.api={registerLayer,initLayers};',ctx);ctx.api.initLayers();
 const key=(key,shiftKey=false)=>{const e={key,shiftKey,preventDefault(){this.prevented=true;},stopImmediatePropagation(){this.stopped=true;}};handlers.keydown(e);return e;};
 return {ctx,document,node,key,handlers};
}
test('Escape closes only the top owner, cancellation/focus do not leak to lower layers',()=>{
 const f=fixture(),trigger=f.node('trigger'),outer=f.node('outer'),inner=f.node('inner');trigger.focus();let a=true,b=true,closed=[];
 f.ctx.api.registerLayer({element:()=>outer,open:()=>a,close:()=>{closed.push('outer');a=false;},fallback:()=>trigger,rank:10});
 f.ctx.api.registerLayer({element:()=>inner,open:()=>b,close:()=>{closed.push('cancel');b=false;},fallback:()=>trigger,rank:50});
 const e=f.key('Escape');assert.deepEqual(closed,['cancel']);assert.ok(a);assert.ok(e.prevented&&e.stopped);assert.equal(f.document.activeElement,trigger);
 f.key('Escape');assert.deepEqual(closed,['cancel','outer']);assert.equal(f.key('Escape').prevented,undefined);
});
test('Tab and Shift+Tab wrap at modal ends and leave interior DOM order alone',()=>{
 const f=fixture(),box=f.node('box'),a=f.node('a'),b=f.node('b');box.children=[a,b];
 f.ctx.api.registerLayer({element:()=>box,open:()=>true,close(){},fallback:()=>a});
 b.focus();assert.ok(f.key('Tab').prevented);assert.equal(f.document.activeElement,a);
 assert.ok(f.key('Tab',true).prevented);assert.equal(f.document.activeElement,b);
 a.focus();assert.equal(f.key('Tab').prevented,undefined);
});
test('shared spacing is semantic, with narrow screens retaining the same tokens',()=>{
 const palette=readFileSync(new URL('../public/brand/palette.css',import.meta.url),'utf8');
 for(const [role,token] of [['control','3'],['content','4'],['section','5']])assert.ok(palette.includes(`--space-${role}: var(--sp-${token})`));
 const flow=readFileSync(new URL('../public/brand/base.css',import.meta.url),'utf8');assert.match(flow,/\.aj-flow\.aj-flow > :not\(\[hidden\]\) ~ :not\(\[hidden\]\)/);
});

test('removed or unfocusable opener uses the visible owner fallback',()=>{
 const f=fixture(),body=f.node('body'),fallback=f.node('fallback'),box=f.node('box');body.matches=()=>false;body.focus();let open=true;
 f.ctx.api.registerLayer({element:()=>box,open:()=>open,close:()=>{open=false;},fallback:()=>fallback});
 f.key('Escape');assert.equal(f.document.activeElement,fallback);
});

test('an unregistered native modal keeps Escape and Tab above its parent layer',()=>{
 const f=fixture(),outer=f.node('outer'),native=f.node('native-dialog'),input=f.node('native-input');let closed=0;
 input.closest=sel=>sel==='dialog[open]'?native:null;
 f.ctx.api.registerLayer({element:()=>outer,open:()=>true,close:()=>closed++,rank:10});input.focus();
 assert.equal(f.key('Escape').prevented,undefined);assert.equal(closed,0);
 assert.equal(f.key('Tab').prevented,undefined);
});
test('a registered native modal still runs its own explicit close action',()=>{
 const f=fixture(),native=f.node('native-dialog'),input=f.node('native-input');let closed=0;
 input.closest=sel=>sel==='dialog[open]'?native:null;native.children=[input];
 f.ctx.api.registerLayer({element:()=>native,open:()=>closed===0,close:()=>closed++,rank:60});input.focus();
 assert.equal(f.key('Escape').prevented,true);assert.equal(closed,1);
});

test('a disposed transient layer releases keyboard ownership, idempotently',()=>{
 const f=fixture(),box=f.node('one-shot');let closed=0;
 const layer=f.ctx.api.registerLayer({element:()=>box,open:()=>true,close:()=>closed++});
 layer.dispose();layer.dispose();assert.equal(f.key('Escape').prevented,undefined);assert.equal(closed,0);
});
