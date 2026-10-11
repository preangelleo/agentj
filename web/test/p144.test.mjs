import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../public/js/relay.js',import.meta.url),'utf8');
const fn=source.slice(source.indexOf('function paintCmdx(p)'),source.indexOf('const newestUndoId'));
function render(p){
 const box={children:[],replaceChildren(){this.children=[];},appendChild(b){this.children.push(b);}};
 const sent=[];
 const ctx=vm.createContext({el:()=>box,C:{myDev:()=> 'owner'},t:k=>k,
   document:{createElement:()=>({dataset:{},addEventListener(k,f){this.click=f;}})},
   sendApp:m=>{sent.push(m);return Promise.resolve();},p});
 vm.runInContext(fn+'; paintCmdx(p);',ctx);return {box,sent};
}
test('waiting slash page exposes a cancel bound to its turn for its originating phone',()=>{
 const {box,sent}=render({id:9,end:'open',source:{k:'cmd',dev:'owner'},card:{cancel_wait:true}});
 assert.equal(box.hidden,false);assert.equal(box.children[0].textContent,'sheet.cancel');
 box.children[0].click();assert.equal(JSON.stringify(sent),JSON.stringify([{t:'cmd_cancel',turn:9}]));
 assert.equal(box.children[0].disabled,true);
});
test('no cancel on a different device, sent or finished command, or ordinary turn',()=>{
 for(const p of [
  {end:'open',source:{k:'cmd',dev:'other'},card:{cancel_wait:true}},
  {end:'done',source:{k:'cmd',dev:'owner'},card:{cancel_wait:true}},
  {end:'open',source:{k:'cmd',dev:'owner'},card:{}},
  {end:'open',source:{k:'phone',dev:'owner'},card:{cancel_wait:true}},
 ]){const {box}=render(p);assert.equal(box.hidden,true);assert.equal(box.children.length,0);}
});
