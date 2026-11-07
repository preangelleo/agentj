import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {resolveDefaults} from '../tools/css-defaults.mjs';
const read=p=>readFileSync(new URL('../public/'+p,import.meta.url),'utf8');
const fn=read('js/relay.js').match(/export function refreshBlocked\([^\n]*\)\{[\s\S]*?\n\}/)[0].replace('export ','');
const modal=id=>({id,getClientRects:()=>[{}]});
const base={C:{connected:()=>true},ptt:false,asrBusy:false,sending:false,atts:[],quotes:[],askBusy:false,apprBusy:false,apprHold:false,askState:()=> 'closed',askSel:{id:null,picks:[]},cur:null,input:{},document:{body:{dataset:{view:'chat'}},querySelectorAll:()=>[modal('settings')],activeElement:null},getComputedStyle:()=>({visibility:'visible'})};
const blocked=(allowSettings,changes={})=>vm.runInNewContext(fn+';refreshBlocked({allowSettings})',{...base,...changes,allowSettings});
test('P571 explicit settings refresh ignores only the settings shell; automatic refresh still waits',()=>{
 assert.equal(blocked(false),true);assert.equal(blocked(true),false);
 for(const key of ['ptt','asrBusy','sending','askBusy','apprBusy','apprHold'])assert.equal(blocked(true,{[key]:true}),true,key);
 for(const change of [{atts:[{}]},{quotes:[{}]},{C:{connected:()=>false}},{document:{...base.document,querySelectorAll:()=>[modal('settings'),modal('real-card')]}},{document:{...base.document,activeElement:{matches:()=>true}}}])assert.equal(blocked(true,change),true);
});
test('P571 header moved into settings keeps icon-only padding and nonshrinking SVG',()=>{
 const css=resolveDefaults(read('menu.css'));assert.match(css,/#settings #set-actions \.bulb\{[^}]*padding:0/);assert.match(css,/#set-actions \.bulb svg\{[^}]*flex:none/);
 assert.match(read('app.js'),/longPress\(\$\('srchBtn'\), \(\) => guardedRefresh\(\{allowSettings:settings.isOpen\(\)\}\)\)/);
});
