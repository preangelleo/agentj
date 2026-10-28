import {test} from 'node:test';
import assert from 'node:assert/strict';
import {APPROVED_CONTRAST,approvedContrast} from './p523_contrast_exceptions.mjs';
test('chairman exceptions only match exact locations, colour pairs and original control-label violation',()=>{
 for(const x of APPROVED_CONTRAST.filter(x=>x.fg!=='state')){const row={approvedLocation:x.id,fg:x.fg,bg:x.bg,ratio:x.ratio,bad:[`control label ${x.ratio.toFixed(2)}:1`]};assert.equal(approvedContrast(row),x.id);
 for(const delta of [{approvedLocation:null},{fg:'rgb(0,0,0)'},{bg:'rgb(255,255,255)'},{ratio:2},{bad:[...row.bad,'dark text on a heavy ground']}])assert.equal(approvedContrast({...row,...delta}),null);
 }
 assert.equal(APPROVED_CONTRAST.length,3);
});
test('P564: the gear digit is exempt only as the page state colour on the Lotion circle',()=>{
 const x=APPROVED_CONTRAST.find(x=>x.id==='P564');
 assert.equal(x.selector,'#setBtn > span.remote-count-badge[data-remote-count]');
 // working / waiting / question / offline (palette hex) on Lotion
 for(const [fg,ratio] of [['rgb(106,155,204)',2.78],['rgb(217,119,87)',2.96],['rgb(130,125,189)',3.55],['rgb(133,133,133)',3.5]]){
  const row={approvedLocation:'P564',fg,stateFg:fg,bg:'rgb(250,249,245)',ratio,bad:[`control label ${ratio.toFixed(2)}:1`]};
  assert.equal(approvedContrast(row),'P564');
  for(const delta of [{approvedLocation:null},{approvedLocation:'P517'},{stateFg:'rgb(20,20,20)'},{stateFg:undefined},{bg:'rgb(20,20,20)'},{bad:[...row.bad,'dark text on a heavy ground']},{bad:['dark text on a heavy ground']}])
   assert.equal(approvedContrast({...row,...delta}),null,JSON.stringify(delta));
 }
});
