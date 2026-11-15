// Restore ba3db1b378's accessibility state without its withdrawn global styles.
// Exercise the production painter across untouched -> invalid -> corrected states.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {retainedUiSource} from './release-ui-source.mjs';
const source=retainedUiSource('public/js/forms.js');
const painter=source.slice(source.indexOf('function paintErrs('),source.indexOf('// ---------------------------------------------------------------- submitting'));
function fixture(){
 const fields=['input','select','textarea','radio','checkbox'].map(type=>({type,attrs:{},setAttribute(k,v){this.attrs[k]=v;}}));
 const ordinary={type:'button',attrs:{},setAttribute(k,v){this.attrs[k]=v;}};
 const error={textContent:''},classes=new Set();
 const row={dataset:{q:'name'},querySelector:()=>error,classList:{toggle(k,on){on?classes.add(k):classes.delete(k);}},
  querySelectorAll:selector=>[...fields,ordinary].filter(f=>selector==='*'||(f.type==='button'?selector.split(',').includes('button'):['input','select','textarea'].includes(f.type)?selector.split(',').includes(f.type):selector.includes('button[role='+f.type+']')))};
 const ctx=vm.createContext({t:k=>k});vm.runInContext(painter+';globalThis.paint=paintErrs;',ctx);
 return {fields,ordinary,error,classes,paint:(errs,touched)=>ctx.paint({querySelectorAll:()=>[row]},errs,touched)};
}
test('untouched required fields stay valid until the owner tries to submit, then all field roles expose invalidity',()=>{
 const f=fixture();f.paint({name:{k:'form.err.required'}},new Set());
 for(const e of f.fields)assert.equal(e.attrs['aria-invalid'],'false');
 assert.equal(f.error.textContent,'');assert.equal(f.classes.has('bad'),false);
 f.paint({name:{k:'form.err.required'}},true);
 for(const e of f.fields)assert.equal(e.attrs['aria-invalid'],'true',e.type);
 assert.equal(f.error.textContent,'form.err.required');assert.equal(f.classes.has('bad'),true);
 assert.deepEqual(f.ordinary.attrs,{});
});
test('touching a field reveals its error, correction clears both invalid state and error text',()=>{
 const f=fixture();f.paint({name:{k:'form.err.required'}},new Set(['name']));
 for(const e of f.fields)assert.equal(e.attrs['aria-invalid'],'true');
 f.paint({},new Set(['name']));
 for(const e of f.fields)assert.equal(e.attrs['aria-invalid'],'false');
 assert.equal(f.error.textContent,'');assert.equal(f.classes.has('bad'),false);
});
test('a malformed value exposes its validation error without waiting for submit',()=>{
 const f=fixture();f.paint({name:{k:'form.err.email'}},new Set());
 for(const e of f.fields)assert.equal(e.attrs['aria-invalid'],'true');
 assert.equal(f.error.textContent,'form.err.email');
});
